# F9-P0 Batch 6 — Round Orchestrator：Decision Closure + Implementation Plan（L3）

> 状态：**IMPLEMENTATION PLAN READY**（L3；等待用户 Plan Review；不进入实现）。
> 依据：Batch6 Readiness（CONDITIONAL）＋用户裁决 **D1（Option A F9-scoped graph invocation）/
> D2（round0=baseline 图调用，全额计费）/ D3（round in-memory）/ D4（每轮 changed claims 增量
> F4→F5→F6）/ D5（Research Policy 临时默认）/ D6（Final Synthesis=最后图阶段 + finalize_run
> 恰一次）**；Batch6 按 **L3** 执行（D1 涉及 ARCHITECTURE §3 适用范围）。
> 本文件 = Decision Closure + Architecture Boundary Closure + How to implement；不复制 Spec。

## 1. Scope

- In（实现目标模块，后续阶段）：`app/f9/orchestrator.py`（+ governed adapter for real-LLM F4/F5/F6
  路径）、`tests/test_f9_orchestrator*.py`、对 `ARCHITECTURE.md §3` 的最小精确适用范围回填、
  Batch6 report；复用 Batch1–5 public 模块与 F8/public seams。
- Out（严格）：不修改 `run_deep_agent`/`main_agent`/F8 Controller/F1–F7 实现与 schema；不新建
  Runtime/Controller/Agent/task/ResearchRun/migration；无 Redis/Kafka/Neo4j/vector；不改
  Batch1–5 implementation；不在本轮写实现代码。

## 2. D1–D6 Final Decisions（冻结）

- D1：**Option A F9-scoped 专用图调用路径**：同一 `Controller.execute` 内多次
  `get_main_agent()` graph astream；graph 不触发 F7、不建第二 ResearchRun；共享
  task_id/run_id/GovernanceExecution/BudgetCounter/deadline/cancel；收尾仅调 public
  `research_bridge.finalize_run()` 一次。拒绝 Option B（不改 run_deep_agent/F1–F8 path）。
- D1 Architecture Boundary：Batch6 正式 **L3**；需最小、精确回填 ARCHITECTURE §3（见 §3），
  不做含糊"临时例外"。
- D2：round0 = baseline research graph invocation，正常计入 llm/tool/search/agent_step（无免费
  baseline）。
- D3：round state = in-memory orchestration state；禁止 research_rounds/migration/F1–F8 改动；
  恢复能力留未来 Gate。
- D4：每轮 Targeted Research + 增量 F3 后，对**本轮 changed claims** 增量 F4→F5→F6（claim
  level、changed-only、复用 frozen public semantics）；real-LLM（F4 detector/F5 reviewer/F6
  reviewer）经 public 注入 seam 挂 F8 handler 计费；ordinary failure fail-open 记录；
  GovernanceLimitExceeded/Cancel/Timeout 原样传播；不改 F4/F5/F6 实现。
- D5（临时默认，Batch8 校准前非冻结产品参数）：max_research_rounds=3（硬上限）、
  max_no_progress_rounds=1、repeated_gap_threshold=2、diminishing_return=本轮新增有效
  evidence/binding 不足以改变研究状态；no-gap/sufficient/all-gaps-unimportant/no-progress 可
  提前 STOP；stopping 不依赖剩余 budget；F8 budget 耗尽仍 = budget_exceeded。
- D6：Final Synthesis = 同一 execution 内最后一个 graph invocation（bounded projection/
  context 输入；正常计费）；成功产出 final_content 后 `finalize_run(...)` 恰一次；不重写 F7。

## 3. Architecture Boundary Closure（ARCHITECTURE §3 红线适用回填——实施阶段最小精确修改）

- 现状（§3）：`MUST NOT 绕过 run_deep_agent 直接调用 main_agent.astream`（会话目录/ContextVar/
  monitor 初始化理由）。
- 提议最小回填（后续实现阶段经 Plan Review 批准后改动，仅 §3 红线段追加一行限定）：

  > F9 governed orchestrator 例外（范围收紧，Review Clarification 1）：F9-scoped 例外**只**
  > 允许绕过 `run_deep_agent` 的"生命周期 + F7 wrapper"（不建 run/不提前 F7），**不允许变成裸
  > `main_agent.astream()`**——F9 orchestrator 内每次 graph invocation 必须完成与既有 governed
  > execution 等价的 glue/context 注入：governance callback、recursion limit、research context、
  > session/thread context、run_id、task_id、monitor context、cancellation/timeout 语义与必要
  > agent config；且保持同一 Controller.execute、同一 GovernanceExecution、同一 BudgetCounter、
  > 同一 deadline/cancel、同一 ResearchRun（不新建）、不触发 F7、不新建 Runtime/Controller/
  > task。产品外部入口仍必须走 `run_deep_agent`（本条红线对非 F9 编排路径语义不变）。

- 前置条件/禁止事项（D1 要求）：仅 orchestrator 内调用；astream config 注入 handler（S1 同款
  复制自 main_agent 的 governance 注入模式：callbacks+recursion_limit，见 main_agent.py 168-174）；
  每次图调用使用**独立 thread_id**（fresh，避免 checkpoint 累积整段 parent history —— 保证
  bounded context 与"不复制父历史"纪律）；thread 持久化仅 checkpoint 行（每 run 图调用次数
  bounded ≤ round0 + synthesis + fallback ≤ 5，行数小）。
- 若实现中发现仍需修改 F8/F1–F7 runtime contract → **立即 STOP 并报告 L3 compatibility gap**
  （不自行改）。

## 4. Single `Controller.execute` Invariant

- 入口 coroutine：`async def f9_orchestrator(task_query, session_id, *, research_policy=None)`
  （新模块）；服务层（后续 submit/EVAL 接线）以 coroutine 传入 `Controller.execute(task_id,
  f9_orchestrator(...), policy)`——Batch6 测试直接在 execute 内跑该 coroutine。
- orchestrator 内部**绝不**调用 controller/submit/新 task/new execute；run_id 取自
  GovernanceExecution（controller 已绑定 TaskRecord.run_id 同源回填）。

## 5. Round0 Baseline（D2）

- 一次性 glue（复制 run_deep_agent 模式，不改其文件）：session dir + session/thread ctx +
  `create_run_and_root(session_id, query, run_id=run_id)`（**仅一次**）→ research ctx set →
  monitor task_started/session_dir → 首次 graph astream（round0 baseline，config handler 注入）
  → 汇总最后 model 文本（非最终内容；仅记录状态）。
- 全额计费：graph 内 llm/tool/search/agent_step 全部经 handler 计数（同 run_deep_agent 路径）。
- **Exactly-once（Review Clarification 2）**：round0 baseline 在一个 orchestrator execution 中
  **最多执行一次**；orchestrator 以显式状态 `baseline_executed: bool`（初始 False，round0 成功
  进入后置 True）保证该 invariant。允许流程：
  `round0 → adaptive rounds → ordinary failure → same-execution fallback(不重跑 round0) → final
  synthesis`；**禁止**：`round0 → adaptive failure → 再次执行 round0`（fallback 直接进入 Final
  Synthesis 或基于已产出研究状态收尾，绝不重放 round0）。

## 6. Round State Machine（D3，in-memory）

- 状态：ROUND_STARTED → PROJECTION_READY → GAPS_READY → JUDGED → PLAN_VALIDATED →
  RESEARCH_EXECUTED → EVIDENCE_UPDATED → VERIFICATION_UPDATED → ROUND_EVALUATED →
  (FOLLOW_UP | STOPPED | NO_PROGRESS)；非法转移 fail-closed（记 diagnostic、停止 adaptive）。
- F9 STOPPED/NO_PROGRESS 仅 orchestrator 层状态；**不映射** F8 TaskStatus（F8 terminal 由 F8
  收敛）。

## 7. 每轮管道（调用 Batch1–5 public 模块；顺序确定性）

1. `projection.project(run_id, round=r, budget=ctx_counter.to_dict(), store=…)`
2. `gaps.detect_gaps(projection)`
3. 若 signals==[] → **STOP（no-gap，不调 Judge）**
4. 否则 `judge.judge_gaps(...)`（handler 计费；judge failure → §10 baseline）
5. `plan.select_candidates + propose_followup_plans(...)`（handler 计费；proposal failure → baseline）
6. 对每个 validated plan（accepted/retry queries）：`targeted.execute_targeted_plan(plan, run_id,
   …)`（同 run；工具 ingest + binding cap 8 + 增量 F3）
7. changed claims = 该轮 verification entries 中 `new_bindings>0` 的 claim 集合（sorted）
8. **D4 增量 F4→F5→F6**（见 §8）
9. `projection.project(...)` 重投影（供 stopping/diminishing-return 判定）→ ROUND_EVALUATED
10. stopping 判定（§9）→ FOLLOW_UP（r+1）或 STOPPED/NO_PROGRESS

## 8. Changed-Claim 增量 F4/F5/F6（D4）

- candidates = §7.7 changed claims；对每个 claim（升序）：
  - F4：`conflict.detect_claim_conflicts(claim_id, detector=…)`（默认 Fake；real gated 经注入
    seam adapter 挂 handler）→ confirmed 结果集合
  - F5：`corroboration.compute_claim_corroboration(claim_id, reviewer=…)`（同上 seam）
  - F6：仅对存在 confirmed conflicts 的 claim：`reconciliation.reconcile_claim_conflicts(
    claim_id, reviewer=…)`
- ordinary failure（gate/读取/detector/reviewer provider）→ fail-open 记录进 round outcome；
  GovernanceLimitExceeded/CancelledError → 原样传播（不 catch）。
- real-LLM 计费：F4 detector / F5・F6 reviewer 若走 real（VERIFY_REAL_LLM/对应 gated env）必须
  使用 governed adapter（respond 内 `model.invoke(config={"callbacks":[handler]})`，同
  Batch5 D-C′ 模式）；默认 Fake → deterministic；adapter 禁止改 F4/F5/F6 模块。
- 目的：conflict/independence/reconciliation 更新进入下一轮 Projection/Gap/Stopping。

## 9. Stopping（D5；不依赖剩余 budget）

- no-gap STOP；required 覆盖达标 ∧ 无未缓解 critical conflict ∧ claims 充分 → STOP；
- all important gaps 已 follow-up 且无新计划（all unimportant）→ STOP；
- no-progress（连续 max_no_progress_rounds=1 轮无状态变化）→ STOP；
- repeated_gap（同 gap ≥ repeated_gap_threshold=2 轮未缓解）→ 僵局 STOP；
- diminishing_return（本轮新增有效 evidence/binding 不足以改变研究状态）→ 评估 STOP；
- r ≥ max_research_rounds=3 → STOP（硬上限）；
- F8 budget/timeout/cancel → 让 F8 收敛（不 swallow、不映射为 F9 STOP）。
- 常量放 orchestrator 模块 `RESEARCH_POLICY_DEFAULTS`（可注入覆盖，Batch8 校准前非冻结）。

## 10. Baseline Fallback（R2-1；配合 Clarification 2 的 round0 exactly-once）

- Judge/Plan/adaptive 普通失败（或 projection 失败）→ 丢弃 partial → 同一 execute 内进入
  **Final Synthesis**（图调用）——**不重跑 round0**（`baseline_executed` once 不变式；即便
  round0 baseline 自身 ordinary-failed 也以已产出状态（可为空）直接进入 Final Synthesis，
  绝不第二次执行 round0）；
- 不建新 task/controller/ResearchRun；不重复 F7；baseline/synthesis 正常 → §11 收尾恰一次。

## 11. Final Synthesis + F7 Exactly-Once（D6）

- Final Synthesis = 最后一个 graph invocation（输入 = 最终 Research State 的 bounded
  projection/context + task 指令；**不复制 parent history/evidence 正文**）；正常计费。
- 产出 final_content（最后 model 文本；若无 → ""）。
- normal completion（terminal_guard 恰一次触发）：`research_bridge.finalize_run(run_id,
  final_content or "")`（与 run_deep_agent 现有调用同形、默认 stage 配置）→
  `research_reg.set_run_status(run_id, "finished")` → monitor task_result。
- CancelledError/Exception 分支：set_run_status cancelled/failed、monitor 对应事件；**不执行
  finalize**（mirror run_deep_agent 249-263 语义）。

## 12. Identity / Context / Persistence / Budget

- Identity 链（校验项）：TaskRecord.run_id == GovernanceExecution.run_id == ResearchRun.run_id
  == SearchQuery/Source/Evidence/Claim/Verification.run_id；task_id=lifecycle；
  run_id=correlation；thread_id=session/UI（Batch6 不改）。
- Context：每图调用独立 thread_id（§3）；下游只传 bounded projection/gaps/judge/plan/round
  计数。
- Persistence：in-memory round state；零新表；仅 checkpoint 行（thread）与既有 research 表。
- SQLite/PG：orchestrator 行为与后端无关（round 纯内存）；research/F8 seam 双后端由既有门控
  覆盖；Batch6 PG 验证跑通 orchestrator+research 闭环（真实 PG gate 一个）。

## 13. Ordinary vs Governance Failure

- ordinary（tool/verifier/detector/reviewer/judge/plan provider・malformed・no candidate・
  no-progress）→ 记录/fail-open/baseline；
- GovernanceLimitExceeded / CancelledError / F8 timeout → **不 catch**（向 Controller 传播 →
  budget_exceeded/cancelled/timed_out）。

## 14. File Change Plan（实施阶段，非本轮）

- 新增：`app/f9/orchestrator.py`；`app/f9/_govadapt.py`（real-LLM detector/reviewer governed
  adapter，可选最少实现）；`tests/test_f9_orchestrator.py`（sqlite/scripted agent + governance
  seam）；`tests/test_f9_orchestrator_postgres.py`（真实 PG 闭环，最小）；Batch6 report。
- 文档最小改动（Plan Review 批准后）：`ARCHITECTURE.md §3` 红线段 +1 限定句（§3 提议文本）。
- 零改动：`main_agent.py`、`run_deep_agent`、F8、F1–F7、Batch1–5、tavily_tool、migration。

## 15. Test Plan（实现阶段）

- 单 execute invariant：orchestrator 全程 handles=1、无第二 run（research_runs count==1）。
- round0：baseline 图调用计费 llm/tool/search/agent_step（scripted agent + fake tools）。
- 每轮管道：no-gap→STOP 不调 Judge；gap→judge→plan→targeted→F3→changed-claims F4/F5/F6
  增量（fake detector/reviewer 全确定性）；changed-only（未变 claim 不重跑，调用计数断言）。
- stopping：all-unimportant / no-progress / repeated-gap / diminishing / max_rounds=3（可注入
  小值）/ budget/timeout/cancel → F8 终态。
- baseline fallback：judge/plan failure → baseline 分支 → completed（无新 task）。
- F7-once：normal → finalize_run 恰 1 次；cancel/timeout/budget → 0 次（seam 断言，finalize 计数
  recorder）。
- **F7-once adversarial matrix（Review Clarification 3）**——normal：
  `normal completion → finalize_run exactly once → set_run_status(finished) → terminal result`；
  governance terminal：`cancel / timeout / budget → finalize_run exactly 0 → F8 terminal
  convergence`。必须至少覆盖以下场景（均经 scripted/fake agent 与 controller-level 测试，不改
  F8/F1–F7）：
  1. cancel during round0
  2. cancel during targeted research（Batch5 seam）
  3. cancel during F4/F5/F6（changed-claim 增量段）
  4. timeout during final synthesis
  5. budget exhausted during Judge
  6. budget exhausted during final synthesis
  7. ordinary Judge failure → fallback → synthesis → F7 exactly once
  8. ordinary Plan failure → fallback → synthesis → F7 exactly once
  （1–6 断言 finalize_run==0；7–8 断言 finalize_run==1 且无第二 task/run）
- D-C′/D4 real-LLM accounting：adapter（fake model）→ llm+1/step0；无裸调可被测试发现。
- 身份：orchestrator 透传 run_id 全链一致。
- 回归：Batch1–5 + F1–F8 sqlite & PG（既有套件全绿）。

## 16. End-to-End Verification Strategy（实现阶段）

- scripted/fake agent 的 controller-level E2E（one execute 全流程：round0→no-gap 或 gap→…→
  synthesis→F7 once）；真实 PG gate 一个闭环；real-provider 图执行 E2E 受凭据限制（记录）。

## 17. L3 Escalation Conditions（实现阶段）

- 若需修改 F8/F1–F7 runtime contract、run_deep_agent/main_agent、新建第二执行体、新 schema/
  基础设施 → 立即 STOP → Reclassify L3（当前 Batch6 已按 L3 执行，仍遵守相同条件上报）。

## 18. 与现有模块的职责边界（防越界）

- Batch6 不重实现 Judge/Plan/Gap/Dedup/Targeted/F3；只按序调用 Batch1–5 public 模块；
  F4–F6 仅 changed-claim 增量 + adapter seam；F7 只复用 finalize_run；round state 纯内存。

## 19. Deviations / Open Items

- glue 复制（session/monitor/research ctx/status/finalize 收尾）在 orchestrator 内复制
  run_deep_agent 既有模式 → 存在维护重复风险；后续架构整理（Batch8/未来）可将公共 glue 抽为
  shared helper——**本阶段不改 main_agent**（记录为 Known Risk）。
- monitor 事件在 orchestrator 内部实现同 run_deep_agent 的 run 级一次性终态（复用 monitor
  public API），保证 governed E2E 前端语义一致。

## 20. Final Status

```text
IMPLEMENTATION PLAN READY — REVIEW CLOSURES APPLIED（L3）
```

等待用户 Plan Review（含 §3 ARCHITECTURE 回填文本确认）；本轮未实现任何代码。

## 21. Review Closures（Plan Review PASS with 3 Contract Clarifications，2026-09-29）

- C1（D1 边界收紧）→ §3：F9-scoped 例外**只**绕过 run_deep_agent 的"生命周期 + F7 wrapper"，
  **不允许裸 `main_agent.astream()`**；每次 graph invocation 必须完成与既有 governed execution
  等价的全部 glue/context 注入（governance callback / recursion / research ctx / session·thread
  ctx / run_id / task_id / monitor ctx / cancel·timeout / agent config），并保持单
  Controller.execute / GovernanceExecution / BudgetCounter / deadline·cancel / ResearchRun（不
  新建）/ 不触发 F7 / 不新建 Runtime·Controller·task；ARCHITECTURE §3 回填文本已据此更新。
- C2（round0 exactly-once）→ §5/§10：round0 baseline 最多一次；orchestrator 用显式
  `baseline_executed` 状态保证；允许
  `round0 → adaptive → ordinary failure → same-execution fallback（不重跑 round0）→ final
  synthesis`；禁止 `round0 → adaptive failure → 再次执行 round0`。
- C3（F7-once adversarial matrix）→ §15：明确 normal（finalize==1 + finished + terminal）与
  governance terminal（cancel/timeout/budget → finalize==0 + F8 收敛）并列出 8 个必测场景
  （cancel in round0 / targeted / F4–F6、timeout in final synthesis、budget in Judge / final
  synthesis、ordinary Judge failure→fallback→synthesis→F7 once、ordinary Plan
  failure→fallback→synthesis→F7 once）；全部经 scripted/fake agent controller-level 测试，
  不改 F8/F1–F7。

D1–D6 与 Option A 均未改变（仅补契约澄清）。
