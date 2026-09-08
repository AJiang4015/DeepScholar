# F7 Capability Audit, Direction Decision & Spec rev2 — Research State Bridge（Claim Materialization + F2–F6 orchestration）

> 状态：**F7 Spec rev2 — Conditional PASS 修订版，待 F7 Final Gate Review；禁止 implementation**
> rev1 Gate 结论：**Conditional PASS，暂不实现**——方向不变；rev2 完成 7 项修订（§R 修订记录）。
> 红线：本轮仅修改本 Spec 文件（rev1 → rev2）；未创建/修改任何代码、migration、tests、Agent、Tool、Checkpoint、依赖、基础设施。
> 阶段链：Phase0/F1 ✅ → F2 ✅ → F3 ✅ → F4 ✅ → F5 ✅ → F6 ✅(均 Final Gate PASS/FROZEN)
> → **F7 Research State Bridge（首次把 F2–F6 orchestration 接入真实 run 的 terminal finalization）** → F8+（Control Loop，另行 Gate）

---

## 0. 审计结论（真实代码为准，非假设；rev1 保留，仍有效）

### 0.1 分层状态（implemented / tested / runtime-consumed / control-loop-consumed）

| 能力 | implemented | tested | runtime-consumed | control-loop-consumed |
|---|---|---|---|---|
| F1 ResearchRun/SubQuestion/SearchQuery/Source/Evidence + provenance | ✅ | ✅ | ✅（main_agent 建 run+root；internet_search/db/ragflow 工具注册 query/source/evidence，fail-open） | ❌（只记录，不回读） |
| F2 Claim/ClaimEvidence/Citation + validator + finalizer 入口 | ✅（registry/validate 模块+测试） | ✅ | ❌（`create_claim`/`bind_claim_evidence`/`create_citation`/`apply_validation_outcome`/`validate_run` 在 app/ 下 **零调用者**；F2 spec §12.2 finalizer / §12.4 report integration 为"设计契约，不实现"） | ❌ |
| F3 semantic verification（5 verdict/3 status/aggregate） | ✅ | ✅ | ❌（`semantic_verify_*` 零 runtime caller） | ❌ |
| F4 conflict detection | ✅ | ✅ | ❌（`detect_*_conflicts` 零 runtime caller） | ❌ |
| F5 corroboration（clustering/independence/source_profile） | ✅ | ✅ | ❌（`compute_*_corroboration` 零 runtime caller） | ❌ |
| F6 reconciliation（outcome/register/run_unresolved） | ✅ | ✅ | ❌（`reconcile_*`/`conflict_register`/`run_unresolved` 零 runtime caller） | ❌ |
| Agent Runtime（main agent + 3 子智能体 + astream + monitor + checkpoint） | ✅ | ✅ | — | ❌（无 evidence-driven 决策；run 收尾 = 最后一段 LLM 文本作 task_result） |

### 0.2 审计证据（代码位置）

- `app/agent/main_agent.py`：`run_deep_agent` 内 `research_reg.create_run_and_root(session_id, task_query, run_id)`（L144）+ 收尾 `set_run_status`（L214/221/227）——**只有 F1 run 生命周期写**；astream 结束后仅取 `final_content`（L210）发 task_result，无任何 F2–F6 调用。
- `app/tools/{tavily_tool,db_tools,ragflow_tools}.py`：工具内 `artifacts_guard` 包裹 `record_search_query/upsert_source/append_evidence`——**只写 F1**；返回值不变（Agent-visible 零改动）。
- `app/research/context.py`：ContextVar 只携带 (run_id, root sub_question_id)；无 claim/state 载体。
- grep `app/`（agent/api/runtime/tools）：`create_claim|bind_claim_evidence|create_citation|apply_validation_outcome|semantic_verify|detect_*conflict|compute_*corroboration|reconcile_|conflict_register|run_unresolved` → **0 个 runtime 调用者**（仅 research 模块互调 + 测试）。
- `app/runtime/checkpoint.py`：LangGraph checkpoint 按 thread_id 管理执行态；research run_id 每次 `run_deep_agent` 新建（uuid4），与 checkpoint 无表级/事务级关联（仅同源 id 注释）。
- Agent-visible context = prompts.yml system_prompt + `run_deep_agent` 附加的 path_instruction；不含任何 F2–F6 state。

### 0.3 数据流现状 vs 目标（哪条边缺失）

```text
Agent Execution（astream，一主三从）         ✅ 存在
      │
      ▼
Tool Results（internet_search/db/ragflow）   ✅ 存在（monitor 上报 + F1 旁路注册）
      │
      ▼
Research Data Plane：Evidence（F1）          ✅ 存在（runtime-consumed）
      │
      ▼  ←──── 边缺失：真实 run 从未产生 Claim（无 producer / 无 finalizer 调用）
Claim（F2）                                  ⚠️ 模块存在，runtime 零产出
      │
      ├── F3 Verification                    ⚠️ 模块存在，零 runtime caller
      ├── F4 Conflict                        ⚠️ 模块存在，零 runtime caller
      ├── F5 Corroboration                   ⚠️ 模块存在，零 runtime caller
      └── F6 Reconciliation                  ⚠️ 模块存在，零 runtime caller
      │
      ▼
Research State（F2–F6 artifacts）            ⚠️ 只在测试/手工 API 下存在
      │
      ▼  ←──── 边缺失：无 run 级 state 聚合/暴露给 runtime
Control / Context Decision                   ❌ 不存在（本轮不建）
      │
      ▼
Agent Next Action                            ❌ 不存在（本轮不建）
```

### 0.4 核心判断（瓶颈定位）

- 真实 run 中 **Claim 从未被创建**（F2 无 producer、finalizer 无调用方）→ F3–F6 输入为空。
- Evidence-driven Control 与 Research State→Agent Context 都**依赖 Claim 先物化**；直接做会建在空状态上。
- 本轮应补的边 = **Evidence → Candidate Claim →（F2 structural）→（F3/F4/F5/F6 按配置执行）**，让 F2–F6 首次在真实 run 被 orchestrate，并产出 run 级 research state（只读聚合/暴露）。
- F7 不做"Agent Next Action"（Control）；那是 F8（见 §4.3 / §10）。

---

## 1. Capability Positioning

> **F7 Main Capability: Research State Bridge — 在真实 Agent Run 的 terminal finalization 阶段，把最终答案物化为 F2 Candidate Claims（含候选 Evidence Anchors），并首次在 runtime orchestrate F2（structural）与 F3–F6 分析链，产出可查询的 run 级 research state。**

一句话：**F7 是 Data Plane 的"producer + 首个运行时 orchestrator"接线（bridge）——它是 orchestration layer，不是新 artifact 堆叠，不是新算法实现，也不是 control loop。**

- F7 **不重新实现** F2–F6 任何算法/规则（不重算 verification、不重新 detect conflict、不重新 cluster source、不重新做 reconciliation）——只调用其既有 public API（§5.4 orchestration invariant）。
- 产出（不新增表）：run 中新增 **claims/claim_evidences/citations**（F2 表，registry 写）+ 复用 F3–F6 各自 artifact。
- 暴露（派生只读，不落表）：run 级 research state summary（§12/§16）。

## 2. Current Runtime Gap

- 缺 Claim producer 与 finalizer 调用方（F2 spec §12.2 设计的 finalizer workflow 从未被上层调用）；
- F3–F6 是"library"而非"被 orchestrate 的 consumer"：没有一条真实 run 数据流过它们；
- Agent 收尾的 final_content 是自由文本，没有结构化 claim/evidence anchor 被提取、结构校验、落库；
- checkpoint/monitor/Agent-visible context 与 research state 完全隔离。

## 3. Goals

1. 在真实 run 收尾（astream 正常结束后、terminal 事件前）增加一个 **fail-open research finalizer** 阶段；
2. 经受控 ClaimExtractor 从 final_content 提取 **Candidate Claims + Candidate Evidence Anchors**（仅提取，不做支持性判定，见 §5.1）；
3. F2 structural validation 必须执行；F3–F6 作为既有 public API 按配置 orchestrate（enabled→调用；disabled→显式 skipped/off，不伪造 artifact）；
4. 产出 run 级 research state summary（派生只读）并暴露只读 API；
5. 全程 fail-open；不改 Agent-visible context / checkpoint / monitor 语义；
6. Claim 物化与 F3–F6 artifact 各自沿用其既有 artifact-level 幂等契约（§13）。

## 4. Direction Decision（rev1 保持）

### 4.1 Capability Matrix

| Direction | 解决什么问题 | 当前 Gap | 是否需要改 Agent Runtime | 是否需要 Agent-visible Context | 是否依赖 Claim Materialization | 复杂度 | 面试价值 |
|---|---|---|---|---|---|---|---|
| **A. Claim Materialization / Research State Bridge（本轮）** | 真实 run 从无 Claim 到有 Candidate Claim，并让 F2–F6 首次被 orchestrate | Evidence→Claim 边缺失；F2–F6 零 runtime caller | 最小：run 收尾一个 fail-open finalizer 阶段 | **否**（收尾后执行，不注入运行中 prompt） | 本身即解决 | 中 | 高 |
| B. Evidence-driven Research Control | stop/continue/replan/budget 由 evidence state 驱动 | 无 run 级 state；claims 不存在；无决策点 | 大：中途 checkpoint/replan 需 LangGraph 结构调整 | 是 | **是** | 高 | 高 |
| C. Research State → Agent Context | 把 F2–F6 state 以最小 envelope 注入 Agent | 无 claims/state 可注入 | 中 | **是** | **是** | 中–高 | 中–高 |
| D. Runtime Control Boundary | 明确 deterministic/semantic 归属 | 原则性，非独立 deliverable | — | — | — | 低 | 中 |

### 4.2 裁决

**A = F7 Main Capability。** 理由（audit 驱动）：
1. 真实 run 中 F2–F6 无输入（无 Claim），B/C 都建立在其上 → 先补上游断流；
2. F2 spec §12.2/§12.4 本就把 finalizer/report-integration 设计为未来消费方——F7 落实该"设计契约"；
3. 不违反"不改变 Agent-visible context"红线（收尾阶段，非运行中注入）；
4. 不新增 Agent / 基础设施 / 表 / migration——bridge 不建表（claims 等全走 F2–F6 既有 artifact）。

### 4.3 Why not B/C/D（以及 F8 预告）

- B/C 依赖 claim 先物化 + verification 在 runtime 真实产出；在无 claims 的今天实现 = 假闭环。B 的正确次序是 F8：在 F7 已把 F2–F6 orchestrate 起来、state summary 可读后，再设计"run 中途 checkpoint → 读 state → decide(continue/verify/stop) → 回写执行意图"，且必须单独过 Runtime Gate。
- C 的 envelope schema 可作为 F8 的输入契约，本轮只定义 state summary 的形状（§16）。
- D 是 F8 的设计纪律，本轮在 §10（Deterministic vs Semantic）先固化边界。

## 5. Goals & Invariants（承接 §3，写死）

### 5.1 ClaimExtractor semantic boundary（rev2 锁定）

- ClaimExtractor **只负责**从 final_content 提取 **Candidate Claim**（statement/claim_type）与 **Candidate Evidence Anchor**（evidence_id 候选 + 可选 quote/locator）。
- ClaimExtractor **不能**判断"Evidence 是否真正支持 Claim"——那是 F3 semantic verification 的职责。
- 数据流严格单向：

```text
ClaimExtractor
  ↓
Candidate Claim + Candidate Evidence Binding（候选，非支持性判定）
  ↓
F2 structural validation（statement/type/anchor 可解析性、R8 quote 子串、同 run 守卫）
  ↓
F3 semantic verification（唯一允许给 SUPPORTS/CONTRADICTS/… verdict 的层）
  ↓
F4/F5/F6
```

- 明确写死：**"Extractor 选择 Evidence" ≠ "Evidence 支持 Claim"**。anchor 仅表示"该证据被候选为与该断言相关的材料来源"，其语义支持状态一律由 F3 判定。
- **ClaimExtractor 不得输出 verdict/confidence/support 语义字段**；输出 schema 里不存在任何 verdict 键（AC-Extractor）。

### 5.2 invalid anchor 语义（rev2 锁定，删除"reject 或 unanchored"二选一）

```text
Claim statement/type 合法
+ Evidence anchor 不存在/无法解析（不在 run evidence universe / quote 非 content 子串等）
        ↓
Claim 仍可落库（该断言是 final_content 中真实存在的研究断言）
        ↓
claim 标记 unanchored（metadata.unanchored=true；不创建无效 ClaimEvidence/Citation）
        ↓
F3 不对该 Claim 产生任何 evidence verification（无绑定即无候选，符合 F3 既有候选语义）
```

- 唯一二态不是"reject vs unanchored"，而是：**statement/type 非法 → 该 candidate claim 不入库（并记入 extractor 降级记录）；statement/type 合法但 anchor 非法/缺失 → unanchored 落库**。
- AC2 必须体现此语义。

### 5.3 F3–F6 execution semantics（rev2 统一 §8 与 §10 的矛盾）

F7 的硬保证重述为：

> **首次在真实 run 中建立并执行 F2–F6 pipeline orchestration。**

每阶段语义：

```text
F2 structural validation（validate + 物化守卫 + apply_validation_outcome）
    → 必须执行（deterministic，无配置关闭）

F3 semantic_verify_run / F4 detect_run_conflicts / F5 compute_run_corroborations /
F6 reconcile_run_conflicts
    → enabled（相应 config/VERIFY_REAL_LLM=1 + budget）→ 调用现有 public API
    → disabled（默认）→ 明确记录 skipped/off（不伪造任何 artifact）
```

- **不得把 skipped 当作 verified / no-conflict / independent / reconciled**：disabled 阶段在 run state summary 中记录 `stage=skipped/off`，对应计数/结论一律留空或显式 `not_computed`，绝不回填默认语义。
- F7 **不得修改 F3–F6 的既有规则**（gate、verdict、候选、聚类、register、failure 语义全部 frozen）。
- 语义 stage enabled 但执行中产生各自模块定义的 failed/skip artifact：按该模块语义表达，F7 不重写。

### 5.4 orchestration invariant（rev2 新增，写死）

> **F7 是 orchestration layer，不重新实现 F3/F4/F5/F6 任何算法。**

允许的调用路径（唯一）：

```text
F7
 ↓
existing F2 public APIs（registry create_claim/bind/create_citation、validate_run、apply_validation_outcome）
 ↓
existing F3 public APIs（semantic_verify_run / semantic_verify_claim）
 ↓
existing F4 public APIs（detect_run_conflicts / detect_claim_conflicts）
 ↓
existing F5 public APIs（compute_run_corroborations / compute_claim_corroboration）
 ↓
existing F6 public APIs（reconcile_run_conflicts / reconcile_claim_conflicts / run_unresolved / conflict_register）
```

禁止：
- ❌ F7 自己重新计算 semantic verification（不得给任何 claim 赋 verdict）；
- ❌ F7 自己重新 detect conflict（不得自行生成 conflict pair/type/genuine）；
- ❌ F7 自己重新 cluster source / 计算 independence（不得自行运行任何相似度/聚类）；
- ❌ F7 自己重新 reconciliation（不得自行产出 outcome/register）；
- ❌ F7 内嵌 F3/F4/F5/F6 的私有实现副本。

F1–F6 的既有规则保持 frozen（不改规则、不改表、不改公共签名）。

## 6. Existing F1–F6 dependency

- F1：真实 run 的 research_runs/root sub_question 已存在；run 内 Evidence 已由工具注册（finalizer 的输入 universe）。
- F2：registry 提供 create_claim/bind_claim_evidence/create_citation；validate_run（纯只读）+ apply_validation_outcome——F7 首次真实调用。
- F3–F6：公共 API 如 §5.4；内部各自 gate（validate）与幂等沿用既有语义。
- 只读面：provenance 供 state summary 聚合。

## 7. Runtime Boundary（本轮最小且唯一）

```text
run_deep_agent (app/agent/main_agent.py)
  ├─ 现有：create run/root → astream → final_content 捕获 → set_run_status → task_result
  └─ F7 新增（唯一接线点，位于 astream 正常结束后、task_result 之前）：
       try:
           research_state = await research_bridge.finalize_run(run_id, final_content)
       except Exception:
           logger.warning(...)          # fail-open：绝不阻断 task_result / 主链路
```

- 不修改 astream 循环内部；不加 Agent/工具；不改 monitor/report 事件 payload；
- 取消/异常路径不触发 finalizer（final_content 不完整）；
- 幂等：同一 finalization identity 只产生一代（§13）。

## 8. Data flow（F7 目标态；rev2 与 §5.3 一致）

```text
astream 结束 → final_content
      │
      ▼
F7 Research Bridge.finalize_run(run_id, final_content)
  0. 计算 finalization identity（§13；含 final_content_hash + evidence_universe_hash）
     已存在同代 → 直接返回既有 state（不重复物化）
  1. ClaimExtractor.extract(final_content, run evidence universe)
     → candidate claims: [{statement, claim_type, evidence_anchor[]}]
       （只提取；不判定支持；无 verdict 字段）
  2. F2 structural materialization（必须执行）：
     - statement/type 合法 → create_claim（幂等）；
       anchor 可解析且同 run → bind_claim_evidence（quote 存在时 create_citation，R8 写前）
       anchor 缺失/不可解析 → claim 落库 + metadata.unanchored=true，不建 binding/citation
     - validate_run(run_id) → apply_validation_outcome(run_id, report)
  3. F3（enabled 才调 semantic_verify_run；否则 skipped/off）        # 唯一 verdict 层
  4. F4（enabled 才调 detect_run_conflicts；否则 skipped/off）
  5. F5（enabled 才调 compute_run_corroborations；否则 skipped/off）
  6. F6（enabled 才调 reconcile_run_conflicts；否则 skipped/off）
  7. build_run_research_state(run_id) → summary（派生只读；含各 stage 状态）  # §16
      │
      ▼
（F8：summary → Context/Control 决策；本轮不做）
```

## 9. State model

- run 生命周期维持现状（running → finished/cancelled/failed，F1）。
- 新增**派生运行期概念** `research_state(run_id)`：
  - `claims_total / claims_validated / claims_unanchored`
  - `verified_claims`（F3 aggregate supported 且非 contested——仅当 F3 enabled 且有数据）
  - `contested_claims`（F3 aggregate contested——仅当 F3 enabled）
  - `unresolved_claims`（F6 run_unresolved：register==genuine_contested——仅当 F6 enabled）
  - `pipeline`: 各阶段 `{stage: f2/f3/f4/f5/f6, status: executed|skipped_off|failed, reason?, artifact_counts?}`
  - `materialized_at / finalization_fingerprint / extractor`
- disabled/未跑阶段的计数：显式 `null` 或 `not_computed`，**绝不**显示为 0（防把 skipped 读成 verified/no-conflict）。
- 不新增表/枚举列。

## 10. Deterministic vs Semantic Boundary（rev2）

| 层 | 性质 | 默认 | 说明 |
|---|---|---|---|
| finalizer 编排、F2 structural、state 聚合、finalization identity | deterministic | 总是执行 | code only |
| ClaimExtractor（final_content → candidate claims+anchors） | semantic（提取） | **OFF**（Fake 用于测试/Gate；real 须 `VERIFY_REAL_LLM=1`） | 仅提取候选；输出 schema 无 verdict；不得判定支持 |
| F3 semantic_verify | semantic（verdict） | OFF（Fake 测试 / real gated，沿用 F3 纪律） | **唯一允许赋 verdict 的层** |
| F4 detector / F6 reviewer | semantic | OFF（同 F4/F6 纪律） | |
| F5 corroboration | deterministic（v1 无 LLM） | 可随 orchestration 执行（无 semantic 通道；是否执行仍由 config/budget 决定） | |
| 失败语义 | — | fail-open | 见 §14 |

## 11. Context Boundary

- 本轮不向 Agent 注入任何 research state。
- ClaimExtractor 输入信封：final_content + run evidence universe（evidence_id/title/url/content 摘要 ≤4k/条，cap 见 §15）；不含 Agent message history、monitor 事件、checkpoint 内容、其它 run。

## 12. Claim materialization contract（F7 核心契约；rev2 依 §5.1/§5.2）

- 输入：`final_content: str` + run evidence universe。
- 输出 schema（无 verdict 字段；违者视为 malformed → 该批 fail-open 降级为"无 claims 物化"）：

```json
{"claims": [{"statement": "…", "claim_type": "FACT|STATISTIC|PREDICTION|COMPARISON|INFERENCE|OPINION",
             "evidence": [{"evidence_id": "…", "quote": "可选，须为 evidence.content 连续子串",
                           "locator": "可选"}]}]}
```

- F2 structural 守卫（deterministic，F7 实现但规则属于 F2 既有语义）：
  1. statement 非空且 ≤2000；claim_type ∈ 枚举 → 不合法：该 candidate 不入库，记 extractor 降级记录；
  2. statement/type 合法 + evidence anchor 无法解析（evidence_id ∉ run universe / quote 非 content 子串）→ **claim 落库 + metadata.unanchored=true；不创建 ClaimEvidence/Citation**（§5.2）；
  3. quote 若给且 evidence 可解析：必须是该 evidence.content 的连续子串（R8 语义），否则按 2 处理为 anchor 非法（仍允许 claim 落库为 unanchored，**不静默改写 quote**）；
  4. 同 statement 幂等（F2 registry 内容级 sha 去重）；
  5. 数量上限 `max_claims_per_run`（默认 12，超限按出现顺序截断并记 metadata）。
- FakeExtractor（Gate/测试）：可注入确定输出；RealLLMExtractor：仅 `VERIFY_REAL_LLM=1` 且 key 存在时构造，失败/超时/malformed → 该批 fail-open。

## 13. Idempotency（rev2 锁定 identity）

### 13.1 finalization identity（F7 层）

- canonical fingerprint = sha256(canonical JSON of)：
```text
{
  extractor_spec,                      # extractor 方法/版本/参数
  stages: {f3: spec?, f4: spec?, f5: method?, f6: method?},   # 各阶段 relevant method/spec（含 disabled 标记）
  final_content_hash,                  # sha256(final_content)
  evidence_universe_hash               # sha256(canonical(run evidence universe: evidence_id+content hash 列表))
}
```
- 语义要求：
  - same run + same content + same evidence universe + same methods → same fingerprint → **idempotent reuse**（不重复物化，直接返回既有 state）；
  - same run + changed final_content → **different fingerprint → 新 finalization generation**（新一代物化/执行，记录 supersede）；
  - 禁止：changed final_content + same config + same fingerprint → 静默复用旧 research state；
  - evidence universe 变化（run 内新增/删除 evidence）→ fingerprint 变化 → 新 generation（generation 记录在 run metadata 历史键，不删旧 artifacts——旧 claim 保留，F2 幂等防重）。
- 每代在 research_runs.metadata 记录：`finalizations: [{fingerprint, materialized_at, extractor, stages_status, supersedes?}]`（registry 增补函数 `record_run_finalization(run_id, record)`，只写该键）。

### 13.2 artifact-level idempotency（不改）

- Claim 幂等 = F2 内容级去重（不变）；F3/F4/F5/F6 各自沿用其既有 (…, spec/method fingerprint) 幂等契约与 failed→recompute 语义。
- **F7 不重新定义 F3/F4/F5/F6 的幂等语义**；只负责"要不要发起一代 finalization"。

## 14. Failure / fail-open semantics

- finalizer 外层 catch-all：任何异常只记日志，不阻断 task_result / 主链路；
- 提取失败/malformed → 降级"no claims materialized"（summary.extractor_failed=true）；
- 单阶段失败（含 F3–F6 各自 failed artifact/skip）→ pipeline 状态如实记录，**不中断后续阶段**；
- semantic disabled → 阶段 skipped/off（§5.3/§9），不伪造 artifact；
- store 不可用 / research disabled：finalizer no-op。

## 15. Budget / Limits

- `max_claims_per_run`=12；每 claim `max_evidence_bindings`=8；提取信封 evidence snippet ≤4000 字符/条、最多 20 条；
- semantic 调用上限沿用 F3/F4 budget 默认；`extractor_timeout` 默认 30s；
- 无新增基础设施/外部服务。

## 16. API / Internal Interface（设计）

```text
# app/research/bridge.py（纯 Data Plane 侧编排；无 app/agent 依赖）
finalize_run(run_id, final_content, *, extractor=None,
             verify_spec=None, conflict_spec=None, corroborate_method=None,
             reconcile_method=None, budget=None) -> run_research_state   # 幂等（§13）；fail-open
build_run_research_state(run_id) -> summary                                # 派生只读
get_run_research_state(run_id) -> summary | None

# run_deep_agent 唯一接线（§7）：finalize_run(run_id, final_content) try/except fail-open
# registry 增补（不动既有行语义）：record_run_finalization(run_id, record)  # 只写 metadata.finalizations 键
```

- 依赖方向：bridge → app/research 内部（registry/validate/verify/conflict/corroboration/reconciliation/provenance）；main_agent → bridge（runtime→research 单向）；research 不得 import app/agent。

## 17. Provenance

- claim/binding/citation 走 F2 registry；finalizer 在 run.metadata.finalizations 记每代（fingerprint/materialized_at/stages）；
- 每 claim 保留 evidence_id 锚点与 quote/locator（或 unanchored 标记）；F3–F6 artifacts 自带 spec/fingerprint/computed_at；
- run_research_state 为派生视图，引用上述 artifacts 而非复制 payload。

## 18. Test Strategy

- `tests/test_research_bridge.py`（sqlite 全量）+ `tests/test_research_bridge_postgres.py`（PG 镜像，RESEARCH_DSN_TEST 门控）：
  - FakeExtractor 确定性输入 → claims 落库、binding/citation 守卫（同 run、R8 quote 子串、数量 cap）；
  - **anchor 语义**：合法 claim + 非法/缺失 anchor → 落库 + unanchored + 无 binding/citation + F3 无该 claim 的 verification（AC2）；
  - **extractor 边界**：FakeExtractor 输出含 verdict 字段 → malformed 拒绝；断言 extractor 从未产生 verdict artifact（AC-Extractor）；
  - orchestration：F2 全链 + （Fake 注入 enabled 时）F3/F4/F5/F6 被调用且产出真实 artifact；disabled 时各阶段 skipped/off、无伪 artifact、summary 计数 not_computed（AC3/AC8）；
  - finalization identity：同 content/universe/methods → 单代复用；改 final_content / 改 evidence universe → 新 generation（AC5/AC6/AC-new）；同代不重复物化；
  - fail-open：extractor 抛错 / 阶段 failed / store disabled（AC7/AC10）；
  - F1–F6 回归：bridge 只复用不修改既有 API/表/migration；断言 0001–0006 不变。

## 19. Acceptance Criteria（rev2 强化）

| AC | 内容 |
|---|---|
| AC1 | Given 真实 run（F1 evidence 已注册）收尾，When finalize_run（FakeExtractor 确定输出），Then candidate claims 物化且全部 binding/citation 的 evidence_id ∈ 该 run universe（或按 §5.2 落 unanchored） |
| AC2 | Given claim statement/type 合法但 anchor 缺失/不可解析（evidence ∉ run / quote 非 content 子串），When finalize_run，Then claim 落库且 metadata.unanchored=true、**不创建** ClaimEvidence/Citation、F3 对该 claim 无 evidence verification（不存在"reject 或 unanchored"二选一的第二种通道之外的分支） |
| AC2b | Given claim statement/type 非法，When finalize_run，Then 该 candidate 不入库并记入 extractor 降级记录（唯一被拒通道是 statement/type 结构非法） |
| AC-Extractor | Given FakeExtractor 输出含 verdict/support 字段，When finalize_run，Then 该批判 malformed 拒绝；任何情况下 extractor 输出不产生 verdict artifact（verdict 仅能由 F3 产生） |
| AC3 | Given F3/F4/F6 enabled（Fake 注入），When finalize_run，Then verifications/conflicts/reconciliations 对真实 run 有产出，且全部经既有 public API 生成 |
| AC4 | Given run 有 verified/contested/unresolved claims（对应阶段 enabled），When get_run_research_state，Then 计数与 F3/F6 派生一致；disabled 阶段的计数为 not_computed（≠0） |
| AC5 | Given 同 finalization identity（同 content+universe+methods），When 重复 finalize_run，Then 不重复物化，返回同代 state |
| AC6 | Given final_content 变化（universe/config 不变），When 再次 finalize_run，Then fingerprint 变化 → 新 generation 物化（绝不静默复用旧 state） |
| AC7 | Given extractor 抛错 / malformed / 超时，When finalize_run，Then 不冒泡、不阻断 task_result，summary.extractor_failed=true、claims 为空 |
| AC8 | Given semantic stage disabled（默认），When finalize_run，Then 该阶段 status=skipped_off、无伪 artifact、相关计数 not_computed（不得把 skipped 当 verified/no-conflict/independent/reconciled） |
| AC9 | Given budget 输入 >12 claims，When finalize，Then 截断至 12 并记 metadata；每 claim bindings ≤8 |
| AC10 | Given run_deep_agent 侧异常（store disabled 等），When 收尾，Then finalizer no-op / fail-open，task_result 语义不变 |
| AC11 | Given 双后端，When 同 fixture 全流程（sqlite + PG16），Then 行为一致（bridge PG 镜像） |
| AC12 | Given 全仓，When 回归，Then F1–F6 全绿 + bridge 新增绿 + ruff/compileall 绿；scope audit 无越界（未改 F1–F6 migration/公共 API/monitor/checkpoint） |
| AC-Orch | Given F7 代码审查/测试，Then 断言 F7 未内嵌任何 verification/conflict/cluster/reconciliation 计算（只调用既有 public API；Orchestration invariant §5.4） |

## 20. Security / Scope Boundary

- finalizer 只读 run 自身 evidence 并写同 run F2–F6 artifact；跨 run 拒绝由 registry 守卫兜底；
- 不接触 session 文件系统 / 上传 / monitor / checkpoint；不新增凭据/网络面；
- Agent-visible context 与 WS schema 零改动；无新依赖/基础设施。

## 21. Open Questions（rev2 关闭项）

1. finalize 触发点 async 包装 vs 同步直调？→ 语义无差，实现期最小决定。✅
2. 是否允许 agent 显式产出结构化 claims？→ 本轮否（需改 Agent-visible 输出契约），F8 候选。✅
3. summary 是否持久化新表？→ 否（派生只读 + run.metadata.finalizations 记录代际）。✅
4. record_run_finalization 写 research_runs.metadata 是否触及 F1 契约？→ metadata 本为 JSON 可写列，新增 registry 函数只写 `finalizations` 键，不改既有行语义。✅（Gate 复核）
5. F3–F6 收尾全执行是否过重？→ semantic 通道默认 OFF；enabled 由配置/budget 决定；F5 无 semantic 但执行与否仍由 config/budget 决定。✅
6. (rev2) evidence universe 变化 → 新 generation，旧 claims 保留 → 是否产生重复？→ 不会：F2 claim 内容级幂等去重 + 新 generation 只补新物化；generation 记录在 metadata 历史键。✅

## 22. Self Review（adversarial，rev2；按 Gate 指定重点重攻）

1. **ClaimExtractor 是否越界判支持？** 否：§5.1 锁死"只提取候选"，schema 无 verdict 键，唯一 verdict 层是 F3；AC-Extractor 测试锁定。
2. **invalid anchor 语义是否仍有二选一歧义？** 否：§5.2 锁死唯一路径（statement/type 合法→unanchored 落库、不建 binding/citation、F3 无该 claim verification）；唯一不入库通道是 statement/type 结构非法（AC2/AC2b）。
3. **F3–F6 execution 是否自相矛盾？** 否：§8/§5.3 统一——硬保证=建立并执行 F2–F6 orchestration；F2 必须执行，F3/F4/F5/F6 由 config/budget 决定 enabled/skipped_off；不伪造 artifact、不把 skipped 当结论（AC3/AC4/AC8）。
4. **F7 是否重新实现 F3/F4/F5/F6？** 否：§5.4 orchestration invariant 写死仅调既有 public API；AC-Orch 断言。
5. **finalization 幂等是否会把"改了内容"静默复用旧 state？** 否：§13 fingerprint 含 final_content_hash + evidence_universe_hash + 各阶段 spec/method；changed content → 新 generation（AC6）。
6. **artifact 级幂等是否被 F7 重定义？** 否：§13.2 明确沿用 F2–F6 既有契约。
7. **是否引入新 Agent / 改 Agent-visible context / 偷做 F8？** 否：Non-goal 保持；Runtime 唯一接线点 §7 收尾阶段；Control 明确 F8。
8. **是否破坏 F1–F6 frozen？** 否：仅调公共 API；metadata.finalizations 单键写入待 Gate 复核（OQ4）。
9. **是否有不可测 semantic 要求？** 否：Fake 注入 + Given/When/Then AC。
10. **是否退化成 CRUD？** 否：交付"真实 run 首次物化 claim + F2–F6 首次被 orchestrate + run 级 state"的接线能力。

## 23. Scope Audit

rev1 → rev2 本轮仅修改 `docs/spec/2026-09-13-f7-research-state-bridge.md`（Spec 内容修订）；
未创建/修改代码、migration、tests、Agent/Tool/Checkpoint/依赖/基础设施/F1–F6。

## R. rev1 → rev2 修订记录

| # | Gate 要求 | 落点 |
|---|---|---|
| 1 | 锁定 ClaimExtractor semantic boundary（只提取候选，不判支持；verdict 仅 F3） | §5.1、§10、§12、AC-Extractor |
| 2 | 锁定 invalid anchor 语义（合法 claim → unanchored 落库；不建无效 binding/citation；F3 无该 claim verification；删除二选一表述） | §5.2、§8 step2、§12、AC2/AC2b |
| 3 | 统一 F3–F6 execution（硬保证=建立并执行 F2–F6 orchestration；enabled→调既有 API；disabled→skipped/off 不伪造；F2 必须执行） | §3、§5.3、§8、§9、AC3/AC4/AC8 |
| 4 | finalization identity 含 extractor+F3/F4/F5/F6 spec+final_content_hash+evidence_universe_hash；changed content → 新 generation；artifact 级幂等沿用 F2–F6 | §13、AC5/AC6 |
| 5 | orchestration invariant（F7 不重实现 F3/F4/F5/F6 算法） | §5.4、AC-Orch |
| 6 | AC 强化（extractor 无 verdict；invalid anchor→unanchored；disabled→skipped_off 无伪 artifact；changed content→新 fingerprint；同 identity→不重复；不重实现算法） | §19（AC2/AC2b/AC-Extractor/AC5/AC6/AC8/AC-Orch） |
| 7 | 最终 scope：F1–F6 继续 FROZEN；本轮只修改 F7 Spec | 全文保持、§23 |

**STOP**：F7 Spec rev2 + Self Review 完成，等待 **F7 Final Gate Review**；Gate 通过前不实现。
