# F6 Capability Audit, Direction Decision & Spec rev2 — Conflict Reconciliation（non-adjudicating）& Contested Claim Register

> 状态：**F6 Spec rev2 — Conditional PASS 修订版，待 F6 Final Gate Review；禁止 implementation**
> rev1 Gate 结论：**Conditional PASS，暂不冻结**——主方向不变，rev2 完成 9 项修订（见 §R 修订记录）。
> 红线：本轮仅修改本 Spec 文件（rev1 → rev2）；未修改任何 app/migration/tests/Agent/Tool/Checkpoint/依赖/基础设施。
> 阶段链：F1 ✅ → F2 ✅ → F3 ✅(Frozen) → F4 ✅(Frozen) → F5 ✅(Frozen，Independent Corroboration / Source Independence Measurement)
> → **F6 Conflict Reconciliation（解释/登记冲突状态，不裁决）** → F7+（Research Control / Synthesis，Runtime 边界待明确）

---

## 0. 审计与选型结论（本次审计基于真实仓库实现，非假设）

审计证据见 §1–§2。核心结论：

- F1–F5 已建成"证据记录 + claim/citation + 结构校验 + 语义验证 + 冲突发现 + 独立性计量"的**分析面**；
  但**真实 run 中 claims 从未被创建、F3–F5 从未被 runtime/agent/tool 调用**（审计证据：app/ 下
  `create_claim`/`apply_validation_outcome`/`semantic_verify_*`/`detect_*_conflicts`/`compute_*_corroboration`
  均无 runtime 调用者；仅 research 模块互调与测试使用）。F2 spec §12.4 report integration 与 §12.2
  finalizer workflow 为**已设计未实现**的消费契约。
- 因此真实 gap 分层为：
  - **G1（分析面内，F6 可关）**：F4 停在 pair 事实、F5 停在独立性计量——同一个 claim 名下"哪些冲突是
    同源自我矛盾（非独立证据）、哪些是独立来源间的真实争点、哪些是 F4 已识别的细节/口径不一致"
    从未被判定与登记；F3 的 `contested` 聚合无法表达这一层含义。
  - **G2（Runtime/Control 面，禁止本轮进入）**：artifacts 不被真实 run 消费、无 stop/replan/budget 决策、
    无 research-level feedback loop。该 gap 是真实的，但进入它**必须先解决 claim 物化（真实 run 谁创建
    claim）+ Agent-visible context 变更**，超出本轮 Spec 允许触碰的边界（见 §4 候选 B 裁决）。
- **选型结论（rev1 保持，rev2 不改方向）**：F6 Main Capability = **Conflict Reconciliation（非裁决型）**
  与 **Contested Claim Register**，作为 F5 spec rev1 审计中"B（Resolution）依赖 A（独立性）暂缓"的直接
  后继。B（Research Control）继续后置为 F7 候选；C（Synthesis / Claim Graph）与 D（Source/Evidence
  Quality）明确不做（理由见 §4 比较表）。
- **rev2 精确定位（修订 7）**：F6 的能力是——
  > **将 F4 conflict fact 与 F5 independence signal 组合成可审计的 claim-level conflict state / contested register。**
  F6 **不是**"解决冲突"，也**不判断 claim 是否正确**。
  > **F6 explains/registers conflict state; it does not adjudicate truth.**

---

# Spec: F6 Conflict Reconciliation · rev2

## 1. Problem Statement

- F4 能把同一 claim 的 SUPPORTS×CONTRADICTS 证据对判为 confirmed，但**不回答这对 claim 意味着什么**；
- F5 能计量冲突两侧 cluster 是否独立，但**不把独立性转化到 claim 级"争点/同源"语义**；
- F3 aggregate 只能给出粗粒度 `contested` 标记，**无法区分**："同一原始来源自己前言矛盾"（同源自我
  不一致）、"独立来源间 F4 已识别的 INCONSISTENCY"、"独立来源间正面互斥（真争点）"；
- 报告/未来 finalizer 因而无法安全陈述一个 contested claim 的 conflict state，也无法产出 "unresolved
  conflict 保留"清单——这是 evidence-grounded 结论（而非 LLM 自由发挥）缺失的一环。

## 2. Current Capability Audit（2026-09-12，实际代码为准）

| 层 | 模块/表 | 真实状态（代码审计） |
|---|---|---|
| F1 ResearchRun/SubQuestion/SearchQuery/Source/Evidence + provenance | `app/research/{context,registry,store,migrations,provenance}` | ✅ 完整；**runtime 真实消费**（main_agent 建 run/root；tavily/db/ragflow 工具注册 query/source/evidence） |
| F2 Claim/ClaimEvidence/Citation + validator R1–R10 + finalizer | `app/research/{registry,validate}`；0002 | ✅ 模块+测试完备；**runtime 零调用**（无 claim 物化路径；`apply_validation_outcome` 无 runtime 调用者；F2 spec §12.2/§12.4 标注"设计契约不实现"） |
| F3 semantic verification（5 verdict；VerifyStatus 3） | `app/research/verify.py`；0003 | ✅ 模块+测试完备；`aggregate_claim_verdict` 输出 supported/contested/partially_supported/unverified；**runtime 零调用** |
| F4 conflict detection（candidate/confirmed/rejected/failed；6 type；genuine/type invariant；signal 快照） | `app/research/conflict.py`；0004 | ✅ 模块+测试完备；confirmed 仅限 genuine（CONTRADICTION/INCONSISTENCY）；**runtime 零调用** |
| F5 corroboration（global clustering、independent_count、cross-side independence、source_profile） | `app/research/corroboration.py`；0005 | ✅ 模块+测试完备；corroboration.support/contradict.cluster_ids 与 `conflicts_independence[].{side_a,side_b}.cluster_ids` 为**同源事实依据**（F6 只读，不自算）；**runtime 零调用** |
| Agent Runtime（一主三从；astream；monitor；checkpoint） | `app/agent/*`、`app/runtime/checkpoint.py` | F1 旁路写入（create_run_and_root/set_run_status）；**无 evidence-driven control、无 research-level feedback、无 stop/replan/budget 决策**；run 收尾 = 最后一段 LLM 文本 |

Audit 关键结论：分析面（F2–F5）与执行面（runtime）当前解耦。F6 **在分析面内**闭合 G1，
不触碰执行面（G2 归 F7 候选，需先做 claim 物化/context 决策）。

## 3. Gap Definition（G1 精确化）

同一 claim 的证据面缺少一个**确定性的 reconciliation 层**，把 F4 pair 事实 + F5 cluster 独立性
合并为 claim 级 conflict state / contested register，产出并可持久化：

1. 每条 F4 confirmed 冲突的 reconciliation outcome（同源自我矛盾 / F4-INCONSISTENCY / 真争点）；
2. claim 级 register（唯一终态，precedence 锁死，见 §8b）：`genuine_contested`、
   `detail_inconsistency`、`same_origin_contradiction_only`、`verified_consistent`、`unverified`；
   另有派生显式态 `incomplete_input`（required inputs 缺项时，见 §8b）；
3. run 级 unresolved 摘要（供未来报告 finalizer/用户阅读：哪些 claim 处于 genuine_contested）。

> 明确：这不是"裁决谁对"，而是把"这个 claim 的 conflict state 是什么、哪些冲突不可在证据面消解"
> 变成**可查询、可审计、幂等、双后端**的 artifact——它是 F3–F5 分析面的**语义收口**，也是未来
> F7 report/control 的只读输入。

## 4. Direction Decision（候选比较与裁决）

### 4.1 候选比较表

| 维度 | A: Conflict Reconciliation（本轮） | B: Evidence-driven Research Control | C: Synthesis / Claim Graph | D: Source/Evidence Quality |
|---|---|---|---|---|
| 当前真实 Gap | F3–F5 停在 pair/聚合事实，claim conflict state 无语义收口（G1） | artifacts 不被真实 run 消费、无 stop/replan/budget（G2） | 跨 claim 结论/依赖关系不存在 | source_profile 仅 descriptive 分布 |
| 对研究质量提升 | 中–高：报告能区分"同源自我矛盾 vs 独立真争点"，可安全陈述 contested | 高：从 plan→finalize 进入证据驱动循环 | 中：更完整视图，但报告消费路径未建立 | 低–中：易滑向 authority（F5 红线） |
| 对 F1–F5 依赖 | F4+F5 直接输入；F3 聚合辅助 | F2–F5 全部 + **claim 物化先决** | F2–F5 + 报告物化先决 | F5 profile 扩展 |
| 是否需新 Agent | 否（可选 LLM 受控评审，非 Agent） | **是（需闭环决策/执行方）** | 否 | 否 |
| 是否需 Runtime 改造 | **否（纯分析面）** | **是（突破 Data→Control 边界；须先解 claim 物化）** | 是（报告/答案消费）或否（纯 artifact） | 否 |
| 是否需 LLM | 可选（默认 OFF，同 F4/F5 纪律） | 决策本身可 deterministic-first，但"下一步检索"需语义 | 高度语义化 | 否（确定性统计） |
| deterministic-first | ✅（默认策略全 deterministic；LLM 只做可选复核且不改 outcome） | ⚠️ 部分（stop 判定可 deterministic，re-search 生成需语义） | ❌（claim 间关系提取本质语义） | ✅ |
| 是否需新基础设施 | 否（沿用 research 表族 + 现有双后端） | 否（但需 runtime 钩子） | 若引入 Graph DB 则违反原则 2 | 否 |
| 实现复杂度 | 低–中 | 高（跨面 + claim 物化 + 循环改造） | 高 | 低 |
| Failure/Idempotency 风险 | 低（沿用 F5 corroboration 单 artifact 模式） | 高（runtime 中断/重放/预算耦合） | 中 | 低 |
| Interview Value | 中（冲突分类 + 证据状态建模） | 高（research control loop） | 中 | 低 |
| 是否值得作为 F6 主线 | ✅ **是** | F7 候选（本轮不做） | 否 | 否 |

### 4.2 Why now

F5 spec rev1 审计原判："B（Resolution）依赖 A（独立性）暂缓"；A=F5 已于 2026-09-11 Final Gate PASS 并冻结。
独立性计量是 reconciliation 的**必要前提**（不判独立性就无法区分"同源自我矛盾"与"独立真争点"），
前提已就绪 → 现在进入解释/登记层，是 F 系列分析面最自然的收口，**不依赖任何 Runtime/Agent 前置**。

### 4.3 Why not the other candidates

- **B（Research Control）**：G2 真实存在，但进入它必须先回答"真实 run 谁创建 claim、Agent 如何读证据状态、
  stop/replan 语义与 budget 如何耦合"，即 Data→Control 边界变更 + Agent-visible context 变更 +
  claim 物化。这些是独立且更大的决策面，放 F6 会把"分析面收口"与"控制面建设"混为一谈；F6 先让
  F3–F5 的语义输出可被安全消费，F7 才有干净的只读输入（reconciliation register）。
- **C（Synthesis / Claim Graph）**：F5 审计已拒绝"为 graph 引入 graph"；且真实 run 连 claim 都未物化，
  跨 claim 关系提取本质语义、难 deterministic-first、无报告消费路径。不做。
- **D（Source/Evidence Quality）**：与 F5 核心原则冲突风险最高（profile→authority 是明确红线）；
  当前缺的是"冲突状态语义"而非"更多质量分布"。不做。
- **E（其他）**：审计中未发现比 A 更高价值且不越界的分析面缺口；G2 已明确记为 F7 候选，不在 F6 偷做。

### 4.4 Dependency on F1–F5

- F2 claim/citation（确定性输入：claim 存在、evidence 绑定）；
- F3 verifications（verdict 来源，读侧；register 的 verified/support 判定用）；
- F4 conflicts confirmed 行（**直接输入**：conflict_id/type/genuine/signal 快照）；
- F5 corroboration artifact（**直接输入**：support/contradict 的 cluster_ids 与
  `conflicts_independence[].{side_a,side_b}.cluster_ids` 及 `independent_between_sides`）。

### 4.5 Expected architectural impact

- 分析面新增一个只读消费者 + 一个 artifact 表（0006），**零 ALTER F1–F5、零 Runtime/Agent 改动**；
- F6 模块消费 F4/F5（同 run 级"先 F4→F5→F6"调用次序），可独立于 runtime 测试（沿用既有测试纪律）。

### 4.6 What new capability the system gains

- 把 "contested" 从黑盒聚合变成**结构化可审计的 claim-level conflict state / contested register**：
  同源自我矛盾（非独立证据冲突）与独立真争点（不可消解）可区分、可审计、可展示；
- run 级 unresolved 清单（register = genuine_contested）成为未来 finalizer/报告/control 的只读契约输入；
- 全程保持 F5 原则：**F6 explains/registers conflict state; it does not adjudicate truth.**

### 4.7 What explicitly remains outside F6

- ❌ winner/truth/reliability/authority/trust scoring（继承 F5 Non-goal）；
- ❌ "解决"冲突 / 判断 claim 是否正确 / 合成唯一事实版本；
- ❌ 修改 F3 verdict、F4 conflict row、claim 内容/status；
- ❌ Research Control / stop/replan/budget（F7 候选，需 Runtime/claim 物化决策）；
- ❌ 跨 claim / run 级合成、claim graph；
- ❌ 新 Agent / 基础设施 / 依赖；❌ Agent-visible context 变更。

## 5. Goals

1. 对每个 F4 confirmed 冲突产出确定性 reconciliation outcome（同源判定基于 F5 cluster 交集；
   真争点登记；F4-INCONSISTENCY 登记）；
2. 汇总为 claim 级 register（precedence 锁死）+ run 级 unresolved 摘要；
3. 全部 deterministic-first；可选受控 LLM 复核默认 OFF（不进自动化 Gate，且**不得改变 outcome 族**）；
4. artifact 幂等、可审计、双后端；失败语义明确（unknown/missing signal 绝不当作同源）；
5. 零 ALTER F1–F5；不改 Runtime/Agent。

## 6. Non-goals（写死）

- ❌ 裁决谁对、选出 winner、合成唯一事实版本、判断 claim 是否正确；
- ❌ 对证据做 authority/reliability/trust 评价；
- ❌ 重跑/重写 F3 verdict 或 F4 conflict_type；❌ 重新聚类 / 重新计算 independence；
- ❌ 修改 claim/evidence/citation 内容；
- ❌ 进入 run loop / Agent / tool / checkpoint；
- ❌ 跨 claim 或跨 run 聚合结论。

## 7. Architecture Position

```text
Research Data Plane（analysis 面，本 Spec 全部落点）
 ├─ F1 record → F2 claim/citation → F3 verify → F4 conflicts → F5 corroboration（Frozen）
 └─ F6 reconciliation（0006，新）：只读消费 F3/F4/F5 → 每条 confirmed 冲突 outcome + claim register
Run 边界（run_deep_agent / main_agent / checkpoint / monitor）——本轮零改动
```

- 依赖方向与现有一致：F6 模块 → app/research 内部（ids/store/validate/verify/conflict/corroboration 读侧）；
  **app/research 不得 import app/agent**；工具签名、Agent-visible context、checkpoint、monitor schema 均不动。

## 8. Data Model（0006 新增；F1–F5 零 ALTER）

```text
reconciliations:
  reconciliation_id     PK
  run_id, claim_id      FK（research_runs/claims，ON DELETE CASCADE）
  conflict_id           FK conflicts（ON DELETE CASCADE）—— 每条 F4 confirmed 冲突一行
  method_spec           JSONB/TEXT NOT NULL        # reconciliation.v1 方法快照
  method_fingerprint    TEXT NOT NULL
  status                TEXT NOT NULL              # 'complete' | 'failed'（无 partial，同 F5）
  outcome               TEXT                       # 见 §8a（complete 时有值）
  detail                TEXT                       # 仅解释性标签（如 reviewer 给的 detail），可空；不参与判定
  error                 TEXT
  computed_at           TIMESTAMPTZ/TEXT NOT NULL
  metadata              JSONB/TEXT NOT NULL DEFAULT '{}'   # review_enabled/review_failed/review_failed_reason/rationale 等
  UNIQUE (conflict_id, method_fingerprint)
```

- 双后端同语义（sqlite/postgres 各一文件：`db/migrations/0006_reconciliation.{sqlite,postgres}.sql`，
  命名遵循 runner 版本递增与"只新增、零 ALTER"契约）；rollback = 移除 0006 双文件 + 测试库 DROP。
- **不新增** claim_register/run_summary 表：claim 级 register 与 run 级 unresolved 摘要是**派生只读视图**
  （同 `aggregate_claim_verdict` 模式），由 §16 API 计算返回。

### 8a. ReconciliationOutcome（状态枚举，rev2 锁死；**不含** UNRESOLVABLE_SEMANTIC）

```text
SAME_ORIGIN_CONTRADICTION   # 同源自我矛盾：冲突两侧 cluster 交集非空（见下方锁死判定）
DETAIL_INCONSISTENCY        # 独立 + F4 conflict_type=INCONSISTENCY → 仅登记 F4 已识别的类别（不裁决影响）
GENUINE_CONTESTED           # 独立 + F4 conflict_type=CONTRADICTION → 真争点；证据面默认不可消解，登记保留
```

**outcome 判定规则（rev2 锁死；顺序无关，条件互斥）**：

```text
cluster intersection != empty
    → SAME_ORIGIN_CONTRADICTION

independent_between_sides == true
+ F4 conflict_type == INCONSISTENCY
    → DETAIL_INCONSISTENCY

independent_between_sides == true
+ F4 conflict_type == CONTRADICTION
    → GENUINE_CONTESTED

independence / cluster signal missing or malformed
    → deterministic failure → status=failed（error 注明缺哪项）
```

- **原则（修订 1，必须）**：`Unknown ≠ Not Independent`。
  同源判定的事实来源（修订 2）：
  - **优先**以 F5 已持久化的 cluster 交集为据——该冲突两侧（evidence 侧）所属 cluster 的
    `cluster_ids` 交集非空，即 `support/contradict` 侧的 cluster 有共享成员；
  - `independent_between_sides` 是 F5 已计算的**派生信号**，仅作一致性展示，不作同源的独立事实源；
  - F6 **不重新聚类、不重新计算 independence**——只读 corroboration artifact（按 claim 对齐）。
  - 任一侧 cluster 缺失、信号 malformed、conflict 不在 corroboration.conflicts_independence 中 →
    **failed**（不得落入 SAME_ORIGIN，也不得落入其余 outcome）。
- **DETAIL_INCONSISTENCY 语义（修订 3，必须）**：仅登记
  "F4 已识别的 INCONSISTENCY 类别（独立来源间）"；**不得**附加
  "因此一定不影响 claim 核心" 或任何对 claim 影响程度的裁决。detail 只允许解释性标签。
- `detail`：仅解释性/展示性（deterministic 时可为空；reviewer 的 detail 见 §10/§14），
  **不参与 outcome 判定**，防把"语境差异/可能不影响"偷判为可消解或改变 outcome。

### 8b. Claim Register（派生；precedence 锁死，rev2）

对每个 claim，先看其**全部** F4 confirmed 冲突是否都已有 complete reconciliation（active method
fingerprint）。缺任一条 complete（含 reconciliation 为 failed / 未算 / corroboration 缺失）→
register = `incomplete_input`（派生层显式状态，非 outcome；附带缺项清单，绝不静默回落其他状态）。
否则按如下 precedence 取唯一终态：

```text
GENUINE_CONTESTED
    >
DETAIL_INCONSISTENCY
    >
SAME_ORIGIN_CONTRADICTION_ONLY
    >
VERIFIED_CONSISTENT
```

> 注：上表大写为可读性展示；register 落值一律用下方小写终态名。映射：
> `GENUINE_CONTESTED→genuine_contested`、`DETAIL_INCONSISTENCY→detail_inconsistency`、
> `SAME_ORIGIN_CONTRADICTION_ONLY→same_origin_contradiction_only`、`VERIFIED_CONSISTENT→verified_consistent`、
> `UNVERIFIED→unverified`。

- `UNVERIFIED`：claim 没有满足 verified evidence 条件的证据（无任何 succeeded SUPPORTS verification）；
  优先级高于/覆盖其余由冲突驱动的状态（无 verified support 时不得进入任何 conflict 驱动状态，
  更不得进入 verified_consistent）。
- `VERIFIED_CONSISTENT` 的必要条件（全部满足才可给）：
  1. 至少一个 succeeded SUPPORTS verification；
  2. 不存在更高优先级 conflict state（即该 claim 没有任何 confirmed 冲突 reconciliation 产出
     GENUINE_CONTESTED / DETAIL_INCONSISTENCY / SAME_ORIGIN_CONTRADICTION）；
  3. required F6 inputs（claim 行、verifications、corroboration artifact 对齐）全部可解析。
  - **不得**仅以"没有 contradiction"推出 verified_consistent——上述 1/3 缺一即不得判定；
- 终态命名（claim 级）仅此五态 + `incomplete_input`（派生显式缺项态）：
  `genuine_contested / detail_inconsistency / same_origin_contradiction_only / verified_consistent /
  unverified / incomplete_input`。

## 9. Algorithms / Decision Rules（deterministic 默认）

```
reconcile_conflict(conflict_id)
 1. gate：validate_run(run_id).errors 为空（沿用 F2–F5 gate；F4/F5 deterministic 错误 → 拒绝）
 2. 输入行：conflicts WHERE conflict_id=… AND status='confirmed'
    （confirmed 保证 genuine∈{CONTRADICTION,INCONSISTENCY}）
 3. 从该 claim 的 corroboration artifact 只读对齐该 conflict：
    - 取 corroboration.support/contradict 视图与 conflicts_independence[conflict_id].{side_a,side_b}.cluster_ids
    - cluster intersection = side_a.cluster_ids ∩ side_b.cluster_ids
    - independent_between_sides 作为派生信号（不替代 intersection 判定；缺失/malformed → failed）
    （corroboration 缺失或该 conflict 不在其中 → deterministic 失败 → failed artifact，
      error 注明：先跑 compute_claim_corroboration / conflict 未覆盖）
 4. outcome 判定（§8a 表，纯 deterministic，无额外阈值）
 5. 可选受控 review（默认 OFF，VERIFY_REAL_LLM=1；Fake reviewer 测试）：
    仅允许输出 rationale/detail/metadata（修订 5）；**不得新增/改变 outcome 语义族**
 6. 持久化（fingerprint 幂等，§13）
```

- 同一条 confirmed conflict 同一 method fingerprint → 单行；不同 review_enabled/方法版本 → 新行。
- claim 级 register（派生，§8b）：依该 claim 的 reconciliation 行集合 + F3 succeeded SUPPORTS
  事实合成，precedence 取唯一终态。
- run 级 unresolved 摘要（派生，修订 6）：返回 **register = `genuine_contested`** 的 claim，
  及每个此类 claim 对应的 confirmed conflicts（及 reconciliation outcome=GENUINE_CONTESTED 的行），
  供报告/未来 finalizer 引用。

## 10. Deterministic vs Semantic Boundary（rev2）

- deterministic（默认，进自动化 Gate）：输入读取、cluster 交集同源判定、outcome 映射、
  claim register（precedence + verified 必要条件）、run 摘要、指纹、失败语义、provenance——
  全部无需 LLM；
- semantic（可选、受控、默认 OFF）：对 `GENUINE_CONTESTED` 的解释性复核——
  **最多产出 rationale / detail / metadata**（如 detail=`semantic_unresolvable`）；
  Fake reviewer 测试，real 须 `VERIFY_REAL_LLM=1`，不进自动化 Gate。
  **Reviewer 不得产生新的 outcome semantic family**（UNRESOLVABLE_SEMANTIC 已被删除，rev1→rev2）。

## 11. Runtime Boundary

- 本轮零 Runtime 改动：不新增 run 钩子、不修改 main_agent/server/checkpoint/monitor；
- F6 是分析面收口（纯 artifact + 只读消费方）；run 级 unresolved 摘要的**消费**（报告 finalizer /
  control）是 F2 §12.4 / F7 的接口契约，本 Spec 只定义输出形状，不实现消费方。

## 12. Context Boundary

- F6 语义层（若启用 review）输入仅限该冲突的 evidence/claim 最小包（同 F4 build_conflict_context +
  F5 review envelope 纪律）：claim statement、两侧 evidence 内容（≤8000/侧）、F4 signal 快照、
  F5 独立性/簇结论；**不接 Agent 历史、run plan、其他 claims、source 权威信息**；
  reviewer 不做信任/权威评级、不判断 claim 对错。

## 13. Idempotency

- identity = `(conflict_id, method_fingerprint)`；同指纹收敛（单行）；
- complete artifact 复用；failed artifact 重算并 UPDATE 同行（同 F5 corroboration 收敛语义）；
- 方法升级（method_spec 变化，如 review_enabled/detail 规则版本）→ 新 fingerprint/新行；
- 不引入 task_call_id。

## 14. Failure Semantics（rev2 锁死）

```text
deterministic 成功 + optional review 失败 → status=complete；metadata.review_failed=true（携带 reason）
deterministic 计算本身失败（gate/读取/缺 corroboration/缺 cluster signal/malformed signal）→ status=failed + error
```

- **missing/unknown independence 或 cluster signal 一律 → failed**，绝不映射为任何 outcome
  （尤其不映射为 SAME_ORIGIN；`Unknown ≠ Not Independent`）；
- 可选 reviewer 失败**不得**把 artifact 置 failed；reviewer 失败最多置
  `metadata.review_failed=true` + reason；
- Fake reviewer 默认；real LLM controlled/off（`VERIFY_REAL_LLM=1`），不进自动化 Gate。

## 15. Budget / Limits

- 每 claim 处理的 confirmed 冲突数上限 `max_conflicts_per_claim`（默认 50，与 F5 max_sources 同级约束），
  超限截断需 deterministic（按 conflict_id 排序取前 N，注释说明）；
- review 上限 `max_review_pairs`（默认 0=OFF，与 F5 一致）；每次 review 输出长度上限（rationale ≤500）；
- 复杂度：O(#confirmed conflicts per claim)；无新增基础设施/外部服务。

## 16. API / Internal Interface（设计）

```text
reconcile_claim_conflicts(claim_id, *, method?, reviewer?) -> {claim_id, run_id, items[], register, llm_calls?}
reconcile_run_conflicts(run_id, *, ...) -> run 级摘要
get_reconciliation(claim_id, *, conflict_id?, method?) -> 单条
list_reconciliations(run_id, *, claim_id?) -> 列表
conflict_register(claim_id) -> claim 级派生 register（只读；含 incomplete_input 与缺项清单）
run_unresolved(run_id) -> register == genuine_contested 的 claims + 对应 confirmed conflicts（只读）
```

- 与 F4/F5 对齐：方法 dataclass（name=`reconciliation.v1`）、fingerprint=sha256(canonical method_spec)；
- 调用次序契约：先 F4（detect）→ F5（corroboration）→ F6（reconcile）；F6 对缺 corroboration / 缺
  cluster signal 的 conflict 报 failed（error 提示先算 corroboration 或 conflict 未被 F5 覆盖），
  不静默自算（防聚类口径漂移）。

## 17. Provenance

Conflict → Reconciliation（outcome/detail/rationale）→ Claim → Evidence；method_spec/fingerprint/computed_at；
optional review 的输入快照与失败原因入 metadata（防 provenance 丢失，同 F4 signal 快照纪律）。

## 18. Test Strategy

- sqlite 全量（`tests/test_reconciliation.py`）+ PG 镜像（`tests/test_reconciliation_postgres.py`，
  `RESEARCH_DSN_TEST` 门控，独立库）；沿用双后端同一套 %s SQL 与迁移断言 0001–0006；
- 矩阵：
  - outcome：同源（同 cluster）→ SAME_ORIGIN_CONTRADICTION；独立+INCONSISTENCY → DETAIL_INCONSISTENCY；
    独立+CONTRADICTION → GENUINE_CONTESTED；
  - **unknown/missing/malformed signal → failed（绝不 SAME_ORIGIN）**；
  - register：多 conflict 混合时 precedence 唯一终态；无 SUPPORTS → unverified；
    verified_consistent 三必要条件缺一不可（不得仅"无 contradiction"）；
    reconciliation 缺 complete → INCOMPLETE_INPUT；
  - reviewer：只产出 rationale/detail/metadata；不得改变 outcome 族；
    review 失败 → complete+review_failed；
  - gate/缺 corroboration failure；fingerprint 幂等/升级；run_unresolved 只含 genuine_contested；
  - 双后端同语义；F1–F5 回归（含迁移断言更新到 0006）。

## 19. Acceptance Criteria（可验证 Given/When/Then；rev2）

| AC | 内容 |
|---|---|
| AC1 | Given 0006 幂等 runner + F1–F5 库，When ensure_schema，Then 仅新增 reconciliations 表、applied versions = 0001–0006、F1–F5 零 ALTER |
| AC2 | Given claim 有 F4 confirmed 冲突且 F5 显示两侧 cluster 交集非空，When reconcile，Then outcome=SAME_ORIGIN_CONTRADICTION 且 register 不含 genuine_contested |
| AC3 | Given 独立两侧（cluster 交集为空且 independent_between_sides=true）+ F4 type=INCONSISTENCY，When reconcile，Then outcome=DETAIL_INCONSISTENCY（仅登记类别；artifact 无"不影响 claim"声明） |
| AC4 | Given 独立两侧 + F4 type=CONTRADICTION，When reconcile，Then outcome=GENUINE_CONTESTED；register=genuine_contested 且 run_unresolved 含该 claim 与其 confirmed conflicts |
| AC5 | Given 任一侧 cluster_ids 缺失 / conflict 不在 corroboration / signal malformed，When reconcile，Then status=failed + error（**不得**产出 SAME_ORIGIN 或任何 outcome） |
| AC6 | Given 同 (conflict_id, method_fingerprint) 重跑，When 再算，Then 单行收敛；方法升级 → 新 fingerprint 新行 |
| AC7 | Given optional review 启用且 reviewer 失败，When reconcile，Then status=complete + metadata.review_failed=true（reason 非空） |
| AC8 | Given reviewer 正常输出，When reconcile，Then outcome 族不变（仅 rationale/detail/metadata 变化，无 UNRESOLVABLE_SEMANTIC） |
| AC9 | Given claim 同时有 GENUINE_CONTESTED 与 DETAIL_INCONSISTENCY 冲突，When 派生 register，Then 唯一终态=genuine_contested（precedence 锁死，deterministic） |
| AC10 | Given claim 有 CONTRADICTS/无 confirmed 冲突但无任何 succeeded SUPPORTS，When 派生 register，Then =unverified；Given 有 SUPPORTS 但缺 corroboration/required 输入，When 派生，Then=incomplete_input（不得 verified_consistent） |
| AC11 | Given claim 有 succeeded SUPPORTS 且无更高优先级 conflict state 且 inputs 可解析，When 派生 register，Then=verified_consistent（非仅因"无 contradiction"） |
| AC12 | Given 双后端，When 同 fixture 全流程，Then outcome/register/幂等一致（PG 镜像） |
| AC13 | Given 全仓，When 回归，Then F1–F5 测试全绿 + F6 新增绿 + ruff/compileall 绿；scope audit 无越界文件 |

## 20. Open Questions（rev1 关闭；rev2 复核/重关）

1. register 是否落表？→ **不落**：派生只读（沿用 aggregate 模式）。✅
2. review 是否可改变 outcome 语义族？→ **不可**（rev2 强化）：只产 rationale/detail/metadata；
   UNRESOLVABLE_SEMANTIC 已删除；GENUINE_CONTESTED + detail=`semantic_unresolvable` 即可。✅
3. 同源判定的 cluster 数据从哪来？→ **只读 corroboration**（support/contradict.cluster_ids 与
   conflicts_independence[].side_*.cluster_ids），不自算聚类/independence。✅
4. threshold/retry？→ 无额外阈值；review retry ×1（同 F4/F5）。✅
5. budget？→ max_conflicts_per_claim=50（截断 deterministic）；review 默认 0=OFF。✅
6. 是否允许 LLM？→ 允许但受控（VERIFY_REAL_LLM=1），默认 OFF，不进自动化 Gate；不改 outcome。✅
7. 是否进入 Runtime？→ 本轮否（G2 归 F7）。✅
8. (rev2) unknown signal 怎么办？→ **failed**（Unknown ≠ Not Independent；绝不 SAME_ORIGIN）。✅

## 21. Self Review（adversarial，rev2）

按 Gate 指定重点重新攻击：

1. **unknown 是否可能被错误解释成 same-origin？** → 否：判定表把 missing/malformed signal 独立列为
   deterministic failure；`cluster intersection != empty` 是 SAME_ORIGIN **唯一**触发条件；
   §14 与 AC5 双重锁定（绝不 SAME_ORIGIN）。✅
2. **verified_consistent 是否可能在无 verified support 时产生？** → 否：必要条件①至少一个 succeeded
   SUPPORTS verification；无 SUPPORTS → unverified；AC10/AC11 覆盖。✅
3. **一个 claim 多个 conflict 时 register 是否 deterministic？** → 是：§8b precedence 锁死
   （GENUINE > DETAIL > SAME_ORIGIN_ONLY > VERIFIED）；缺任一 complete → INCOMPLETE_INPUT（显式非静默）；
   AC9。✅
4. **LLM reviewer 是否可能偷偷改变 outcome？** → 否：语义边界只允许 rationale/detail/metadata；
   outcome 由 deterministic 判定独占；UNRESOLVABLE_SEMANTIC 已删除；AC7/AC8。✅
5. **DETAIL_INCONSISTENCY 是否偷偷变成"不会影响 claim"？** → 否：语义收敛为"仅登记 F4 已识别类别"；
   禁止影响程度声明；§8a 修订 3 + AC3 文本断言。✅
6. **F6 是否偷偷变成 Conflict Resolution？** → 否：定位锁死为"将 F4 conflict fact 与 F5 independence
   signal 组合成可审计的 claim-level conflict state / contested register"；非裁决、非消解、非 winner；
   §0/§1/§4.7/§6。✅
7. **是否提前进入 F7 Runtime Control？** → 否：Runtime Boundary §11 零改动；G2 明示外置；
   §4.3/§5。✅
8. （保持 rev1 其余自检）CRUD/换名/authority/F1–F5 污染/不可测项——rev1 十项已过，方向未变，保持有效。✅

## 22. Scope Audit

rev1→rev2 本轮仅修改 `docs/spec/2026-09-12-f6-conflict-reconciliation.md`（Spec 内容修订）；
未创建 0006、未写代码/测试、未改 Agent/Tool/Checkpoint/依赖/基础设施/F1–F5。

## R. rev1 → rev2 修订记录

| # | Gate 要求 | 落点 |
|---|---|---|
| 1 | independence/cluster signal missing → **failed**，绝不 SAME_ORIGIN（Unknown ≠ Not Independent） | §8a 判定表、§14、AC5、OQ8 |
| 2 | 同源事实来源 = F5 cluster 交集（support/contradict.cluster_ids / side_*.cluster_ids）；F6 不重聚类、不重算 independence；independent_between_sides 仅派生信号 | §2 audit、§4.4、§8a、§9 step3 |
| 3 | DETAIL_INCONSISTENCY 仅登记 F4 类别；删除"不影响 claim 核心"暗示 | §8a、AC3 |
| 4 | Claim Register precedence 锁死 + UNVERIFIED + verified_consistent 必要条件（SUPPORTS/无更高状态/inputs 可解析）；不得仅因"无 contradiction"判 verified | §8b、AC9–AC11 |
| 5 | 删除 UNRESOLVABLE_SEMANTIC；reviewer 只产 rationale/detail/metadata | §8a、§10、§14、AC8、OQ2 |
| 6 | run_unresolved = register==genuine_contested 的 claims + confirmed conflicts | §9、§16、AC4 |
| 7 | 定位收紧："将 F4 conflict fact 与 F5 independence signal 组合成可审计的 claim-level conflict state / contested register"；F6 explains/registers, does not adjudicate truth | §0、§1、§3、§4.6、Self Review #6 |
| 8 | 保持项（方向/F1–F5 frozen/0006 只新增/零 ALTER/零 Runtime 改动/deterministic-first/LLM OFF/不做 winner 等） | 全文保持 |
| 9 | Rev2 Self Review 7 项重点重攻 | §21 |

**STOP**：F6 Spec rev2 + Self Review 完成，等待 **F6 Final Gate Review**；Gate 通过前不实现。
