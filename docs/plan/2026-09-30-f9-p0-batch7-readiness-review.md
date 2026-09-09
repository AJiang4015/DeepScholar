# F9-P0 Batch 7 — Eval / Behavioral Quality Readiness Review

> 状态：**READINESS REVIEW（read-only）→ 结论 CONDITIONAL（见 §10 Final Verdict）**。
> 流程：PROCESS §11 L2/L3 工作流第一阶段（Readiness Review only）；只读检查、不修改代码/
> F8/F1–F7/Batch1–6/本报告不落实现；发现冻结 contract 冲突 → 记 COMPATIBILITY GAP 并 STOP。
> 依据：用户 2026-09-30 指令（Batch6 Freeze + Batch7 只做 Readiness Review）、F9-P0 Spec Rev2
> §14（Eval Acceptance）、Plan Rev2 §I Batch7/8 + 修订 R5、Batch6 report（D6/F7-once matrix）。
> 日期：2026-09-30。分级建议：**L2（条件满足）/ L3（若触及 orchestrator·main_agent seam）**，
> 见 §8。

---

## 0. 编号重映射声明（需用户 Decision，非假设）

- 冻结 Plan Rev2（2026-09-20）定义：Batch 7 = **F7 Seam Verification**（修订 R5，不实现/重构
  F7）、Batch 8 = **Eval / 验证（baseline vs F9）**。
- Batch 6（2026-09-30 FROZEN）已通过 D6 Final Synthesis + **F7-once adversarial matrix**
  （controller-level normal finalize==1、cancel/timeout/budget finalize==0、judge/plan
  failure→fallback→F7 once）覆盖了原 Batch7 "F7 seam verification" 的验收目标（F7 语义本身
  未改、未复制）。
- 用户本次指令将 **Batch 7 定义为 Eval / Behavioral Quality 验证**（baseline-vs-adaptive）。
  → 本 Readiness Review 按用户当前定义（Batch7 = Eval）撰写；若用户希望保留 Plan Rev2 编号
  （Eval 归 Batch8），仅需改批次标签，不影响本文件的分析内容与 Verdict。
  **Required Decision D1：确认 Batch 7 编号/内容 = Eval（用户定义），并注明 Batch7
  "F7 seam verification" 原目标视为已由 Batch6 覆盖（冻结记录），不另行成批。**

## 1. Scope（本 Readiness 检查什么）

只评估：Batch6 完成后 F9-P0 是否具备 Eval / Behavioral Quality 验证条件：
baseline vs adaptive 可重复比较；统一 F8 budget/timeout/cancel；指标数据面；deterministic
benchmark 可行性；实验边界与 harness 语义中立；schema/fixture 需求；L 级；Scope 边界。

Out（本批 Readiness 阶段）：不实现任何 Eval 代码/runner/fixture；不改 orchestrator/
main_agent/F8/F1–F7/Batch1–6；不新增 schema；不跑真实 provider。

## 2. 现状能力清单（只读核实）

| 能力 | 现状 | 证据 |
|---|---|---|
| F8 接受任意 coroutine 且统一 policy | Controller.execute(task_id, coroutine, policy)：counter/deadline/cancel 单例 | Batch6 report §2/§6；F8 step3/4 |
| Baseline 执行体 = run_deep_agent | main_agent.py async run_deep_agent：graph astream + create_run_and_root + F7 finalize_run（normal only）+ set status | Batch6 report §0（仅 read 引用）；main_agent.py 实读 |
| F9 执行体 = f9_orchestrator | 单 execute：round0 graph → projection/gap/judge/plan/targeted/verify 轮 → final synthesis graph → F7 once；支持注入 graph runner / judge·plan model / search tool / verifier / F4–F6 detector·reviewer | Batch6 report §2/§4/§6 |
| real 图调用一致性 | orchestrator 默认 runner = lazy `main_agent.get_main_agent()` astream（真实 product graph）；两侧可注入同一 fake/scripted runner | orchestrator.py `_default_graph_runner` |
| 指标数据面 | research_runs + sub_questions/queries/sources/evidences/claims/verifications/conflicts/corroborations/reconciliations（state 经 bridge/projection 只读聚合）；F8 TaskRecord.counters_snapshot（llm/tool/search/agent_step）+ effective_limits | bridge.py build_run_research_state / projection.py / controller store 实读 |
| 成本/轮数可收集 | rounds/judged_rounds/executed_plans/changed_claims/finalize_calls 由 orchestrator 返回 summary；F8 counters 由 controller 收敛 | Batch6 report §2；orchestrator summary |
| Deterministic 全链可注入 | judge/plan/targeted/verifier/F4–F6 + graph runner 全 seam 可注入 fake/scripted | Batch3–6 tests（fake chat model / fake ingest tool / fake runner 驱动 orchestrator 17 tests） |
| Spec 验收标准 | §14 Eval Acceptance：grounding 改善为主、不以加搜为指标、cost 不显著超支、answer quality ≥ baseline | Spec Rev2 §14 |
| Baseline-only 执行模式 | orchestrator **无** "仅 round0 baseline（无 adaptive 轮）" 显式模式（adaptive_enabled=False 连 round0 也跳过） | orchestrator.py 实读（round0 在 `if adaptive_enabled` 内） |
| glue 对称性 | run_deep_agent 注入 path_instruction + session-dir + monitor；orchestrator 图调用仅 prompt（+可选 emit_monitor），无 path_instruction/session-dir | main_agent.py 与 orchestrator.py 实读对比 |

## 3. 检查项结论（对应用户 Readiness 清单 1–10）

1. **baseline 与 adaptive 可重复、可比较实验**：**具备条件，但需先定 baseline 对照定义**。
   - 同任务、同 F8 policy 下：baseline = run_deep_agent（或等价"单轮 main agent 图"），
     adaptive = f9_orchestrator，两个独立 execute 即可并排比较（同一 ResearchRun 内不允许
     第二 execute——冻结语义；比较是**跨 run / 跨 execute** 的 A/B，不是同 run 内对照）。
   - 现 orchestrator 无"仅 round0 + 无 adaptive 轮"对照模式 → 若要在**同一 orchestrator 执行
     路径**上做 baseline 分支（如 mode="baseline"），需为该模式新增显式行为，而不改变
     adaptive_enabled=True 冻结语义。此为 **Required Decision D2**（见 §5）。
   - 可重复性：真实 provider 图执行受凭据/非确定性影响 → 主实验应走 deterministic
     scripted（第 4 点）；real-provider 仅作为受控补充（不做 Gate 依据）。

2. **统一 F8 budget / timeout / cancellation**：**已具备**。两侧都经 `Controller.execute`
   同一 policy（max_llm_calls/max_tool_calls/max_search_calls/max_agent_steps/
   wall_clock_timeout/deadline/cancel），TaskRecord 收敛并持久化 counters_snapshot；比较
   成本时直接读 TaskRecord 即可，两侧语义同构。

3. **同任务、同预算约束下可比较的指标**：**数据面全部可观测**（无新 schema 必需）：
   - evidence coverage：evidences/evidence_count per sq / binding 数（projection/只读查询）
   - claim verification：verifications（succeeded/verdict 分布；F3）
   - conflict detection / reconciliation：conflicts confirmed/unresolved（F4/F6）
   - independent corroboration：corroborations support.independent_count（F5）
   - unresolved gaps：gap signals（当前轮 projection/gaps）与 unresolved conflicts
   - research rounds：orchestrator summary.rounds（内存态，经返回值收集）
   - tool/search/LLM cost：TaskRecord.counters_snapshot + effective_limits（F8）
   - final answer quality：run metadata final_content_hash + final_content（上限 20000；
     bridge 已物化 claims/验证）；质量判据按 §14 需确定性/半确定性评估器
   - rounds 如需**跨 execute 持久化**再做聚合 → 才需要新 schema（决策点，见 §5 D4）。

4. **deterministic scripted benchmark（避免 real-provider 不稳定）**：**推荐为主路径且可行**。
   Batch3–6 已证明 judge/plan/targeted/verifier/F4–F6/graph runner 全 seam 注入能力；可构造
   scripted "世界模型"（确定性 tool 返回 → 确定性 evidence 内容 + scripted 语义判定
   judge/plan/verify/F4–F6），在同一 F8 execute 内重复跑 baseline 与 adaptive 分支，结果
   完全可复现；real-provider 只在凭据环境做冒烟补充。

5. **Batch7 验证的是 Research Intelligence 行为收益，不是 Runtime Infrastructure**：**是**。
   验收按 Spec §14：同 budget 下 F9 至少改善 ≥1 项 grounding 指标（unsupported claim rate↓ /
   citation coverage↑ / evidence sufficiency↑ / conflict coverage↑ / redundant searches↓），
   cost 不显著超支、answer quality ≥ baseline；**禁止以"搜索/轮次增加"为指标**（R2-6）。
   Batch7 的 Deliverable 形态应是 eval harness + 指标报告（或断言），而非新增 runtime。

6. **baseline / adaptive 实验边界（避免 harness 改变研究执行语义）**：
   - 实验 harness 只做：构造任务 → 注入同一 policy/scripted 依赖 → 跑 baseline 或 F9 →
     读指标；**不得**改写 run_deep_agent / orchestrator / F8 的语义；
   - **glue 不对称风险（重点）**：run_deep_agent 注入 path_instruction + session 工作目录
     指令，orchestrator graph runner 仅发 task prompt → 若直接拿两侧跑真实图，模型看到的
     system/环境指令不同，行为差异可能来自 glue 而非 research intelligence。对策（决策点）：
     要么两侧统一走"同一 agent config/prompt 模板"的 eval runner（不改冻结模块，仅在
     eval seam 注入），要么在报告明确记录该差异为限制；
   - ARCHITECTURE §3 例外仅允许 orchestrator 内图调用，不允许外部入口绕过 run_deep_agent：
     baseline 对照必须经 run_deep_agent（或等价的受控 runner），不得新建 bypass。

7. **新 Eval schema / fixture / dataset 需求**：**按需提出、不直接建**。
   - 不必要（minimal）：指标全部可从事务库 + summary + counters 读取，rounds 走返回值；
     若走 deterministic scripted，fixture = 小脚本任务集（不落库）。
   - 可选（需要时才新增，先 Decision）：固定 dataset 文件/期望答案；跨 execute rounds
     持久化（research_runs/round 表 additive，research plane）；eval 结果登记表。
   - 结论：Batch7 默认**无需新 schema/migration**；若选定持久化 rounds/登记表则另案
     additive（F9-P1 方向），并需 L3 + 迁移评审。

8. **L 级分类（建议）**：
   - 若 Batch7 交付 = eval harness + fixtures/tests/报告，**不改 orchestrator/main_agent/
     F8/F1–F7/Batch1–6 语义、不加 schema/migration** → **L2**。
   - 若为公平 baseline 对照需给 orchestrator 增加"baseline-only 模式"或统一 agent
     config/prompt seam（触及 app/f9/orchestrator.py 冻结边界或 main_agent 只读复用面），
     或需新增迁移/schema → **L3（需 Decision Closure 后按 L3 执行）**。
   - 本 Readiness 不做最终 L 级裁决（留给用户 Review 该文件后裁决，或随 Decision Closure）。

9. **Scope / Out-of-Scope（Batch7 提议）**：
   - In：eval harness（构造任务 + 统一 policy/scripted 依赖注入 + baseline/F9 各跑 +
     指标收集）；deterministic scripted benchmark 若干场景；只读指标断言/报告；Eval 报告
     文档；可选 real-provider 冒烟（凭据环境）。
   - Out：修改 run_deep_agent / main_agent / F8 / F1–F7 / Batch1–6；_govadapt real-provider
     实现；durable round state；Claim–Evidence Graph；新 infra（Redis/Kafka/Neo4j/Vector）；
     polling/status-sync；UI；migration（除非 §5 D4 选持久化才另议）；F9-P1/P2。

10. **Readiness Verdict：CONDITIONAL**（见 §10）。

## 4. Risks / Compatibility Gaps

| # | 风险/缺口 | 影响 | 缓解/所需决策 |
|---|---|---|---|
| G1 | orchestrator 无 baseline-only（round0 无 adaptive）显式模式 | 无法在**同一执行路径**做 A/B；只能 run_deep_agent vs orchestrator 跨 execute 对照 | D2：定义 baseline 对照入口（run_deep_agent 跨 execute 对照 或 orchestrator 新增 mode 参数——后者触及 Batch6 边界需 L3） |
| G2 | run_deep_agent 与 orchestrator graph runner glue 不对称（path_instruction/session-dir） | 真实图下行为差异污染研究智能归因 | D3：eval 统一 agent config/prompt 模板（不改冻结模块，eval seam 注入），或在报告中限制性声明 |
| G3 | real-provider 非确定性/凭据缺失 | Eval 不可复现、不可 Gate | 主路径 deterministic scripted；real 仅冒烟 |
| G4 | rounds 内存态（D3） | 跨 execute 仅经返回值可读，DB 聚合不可行 | D4：接受返回值收集（无 schema）或选持久化（另案 additive） |
| G5 | 编号重映射（原 Batch7 F7 seam → 现 Batch7 Eval） | 文档/历史批次口径 | D1：用户确认编号语义与 F7-seam 覆盖记录 |
| G6 | final answer quality 无真实质量裁判 | §14 "answer quality ≥ baseline" 需要判定器 | scripted 半确定性评估器 + 显式规则（citation/claim 覆盖可判），real-LLM 质量评测仅受控补充 |

**COMPATIBILITY GAP 检查**：本 Readiness 未发现"必须修改 F8/F1–F7/Batch6 frozen contract 才能
完成 Eval 目标"的硬缺口；全部候选改动（D2 baseline mode / D3 agent config seam）都可限定为
Batch7 eval 层新增 seam 或经 L3 Decision Closure 后按新范围实施。若用户选择"orchestrator 增加
baseline-only mode 且需修改 Batch6 frozen orchestrator 行为"，则触发 L3 流程，不在此批实施。

## 5. Required Decisions（供用户 Review / Decision Closure）

- **D1**：确认 Batch 7 = Eval / Behavioral Quality（用户 2026-09-30 定义）；注明原 Plan Rev2
  Batch7（F7 seam verification）目标视为已由 Batch6 D6/F7-once matrix 覆盖（冻结记录），不另
  成批。
- **D2**：baseline 对照入口——(a) 跨 execute：run_deep_agent vs f9_orchestrator（无
  orchestrator 改动，推荐先行）；或 (b) orchestrator 增加显式 baseline-only mode（需 L3）。
- **D3**：公平 glue——两侧统一 agent prompt/config 模板（eval seam 注入）或记录限制。
- **D4**：rounds / eval 结果是否需要 DB 持久化（默认不需要：返回值 + counters + 研究库
  state 已覆盖全部指标）。
- **D5**：final answer quality 的判定器形态（scripted 规则评估器 为主；real-LLM 质量评测是否
  纳入受限补充）。
- **D6**：Eval 场景集规模/来源（脚本任务集 → deterministic；是否引入固定 dataset 文件）。

## 6. Proposed Scope（Batch7 Implementation 候选，待 Decision 后展开）

1. eval harness（test 层或独立 script）：统一入口在 F8 execute 内以同一 policy 跑
   baseline 分支与 F9 分支（注入同一 scripted 依赖），输出可比指标。
2. deterministic scripted benchmark 场景（fake tool 世界 + scripted judge/plan/verify/F4–F6），
   断言 Spec §14 至少 ≥1 grounding 指标改善且 cost 不显著超支。
3. 指标采集器：research state（只读）+ TaskRecord counters + orchestrator summary →
   结构化比较表。
4. 报告：baseline-vs-adaptive 行为收益结论 + Limitations（glue 差异、deterministic 范围）。

## 7. Test / Eval Strategy（Readiness 级提议）

- 主路径：deterministic scripted——每场景固定 seed/工具内容/语义判定 → baseline 与 F9 各跑
  至少 2 次确认稳定 → 断言指标方向。
- 统一 budget 场景：同一 policy 应用到两侧；预算边界场景（接近 budget 上限时 adaptive 的
  stopping 是否给出不劣于 baseline 的 grounding）需专门覆盖。
- real-provider：凭据可用时做有限冒烟并标记 non-gate。
- 现有 711 sqlite / 81 PG 回归全绿作为 harness 改动不破坏冻结面的基线。

## 8. L-level Classification（建议，待用户裁决）

- 无冻结面改动 → **L2**（新增 tests/报告；流程产出 = Readiness → Plan → Implementation
  Report）。
- 含 orchestrator baseline-only mode 或新 schema → **L3**（本 Readiness 后走 L3 上报）。
- 分类依据：PROCESS.md §11（改动是否触及 ARCHITECTURE 红线/冻结契约/公共 seam）。

## 9. Scope Audit（本 Readiness）

- 本批 read-only：实读 main_agent.py / orchestrator.py / bridge.py / controller·store /
  Spec Rev2 §14 / Plan Rev2 §I+修订 R5 / Batch6 report。
- 未修改任何代码、测试、Spec/Plan/Harness、PROJECT_CONTEXT 之外的文档；本文件为唯一新增。
- （PROJECT_CONTEXT 的 Batch6 Freeze 状态同步已于 Freeze 指令中完成，非本 Readiness 产物。）

## 10. Final Verdict

```text
READINESS: CONDITIONAL
```

具备 Eval / Behavioral Quality 验证的主体条件（统一 F8 budget·timeout·cancel、指标数据面
齐全、deterministic scripted 全链可注入、Spec §14 验收标准明确、Batch6 完成 baseline 图 +
adaptive 轮 + F7 once 的执行骨架）。缺少的是**对照定义与公平性决策**（G1/G2：baseline 入口
与 glue 对称）与 **quality 判定器形态**（G6），外加编号重映射确认（D1）。未发现必须修改
F8/F1–F7/Batch1–6 frozen contract 的硬缺口；Batch6 frozen 语义应保持不变，Eval 只应验证
其行为收益，不反向改动 orchestrator 语义。

等待用户 Review 本文件（Readiness / Decision Closure）。未获批准前不进入 Batch7 Implementation。

```text
COMPATIBILITY GAP: 无（Readiness 阶段）
Affected frozen contract: —
Reason: 全部候选改动可在 Batch7 eval 层新增 seam，或经 L3 Decision Closure 后按新范围实施
Required decision: D1–D6（见 §5）
```
