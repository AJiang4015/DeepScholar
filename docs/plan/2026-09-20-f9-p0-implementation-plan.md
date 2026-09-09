# F9-P0 Implementation Plan（Evidence-Driven Adaptive Research Loop）— Rev2（定向修订）

> 状态：**PLAN（未实现）。Rev2 = 按用户审阅意见锁死 5 项工程边界（R1–R5），并完成一致性检查。**
> 依据：F9-P0 Rev2 Spec（2026-09-19）＋ Final Readiness Review（PASS）＋ F1–F8 冻结契约。
> 首日冻结 6 项 Decisions（§0）+ 本修订 R1–R5。范围 Guard 同前（禁止第二 Runtime/新 Agent
> 类型/Planner 系/Redis/Kafka/Neo4j/VectorDB/scheduler/multi-instance/UI redesign/F1–F8
> schema breaking）；发现需改 F1–F8 frozen contract → 立即停止报告。

## 0. 冻结的 6 项 Implementation Decisions
### D1 — Orchestrator Seam（修订 R1：fallback 必须在同一次 Controller.execute 内）
```text
Controller.execute(task_id, f9_orchestrator(...), policy)
   └─ f9_orchestrator
        ├─ F9 adaptive path
        └─ F9 unavailable
             └─ baseline（run_deep_agent 作为 execution primitive/fallback 实现，在同一 coroutine 内）
   → 同一 Controller.execute / task_id / run_id / GovernanceExecution / BudgetCounter /
     deadline/watchdog / cancellation
   → normal return → F7 exactly once
```
- **禁止**：F9 failure 后 server 重新 submit baseline task；新建 TaskRecord/task_id/run_id/
  ResearchRun/Controller；重置 BudgetCounter；新建 watchdog/deadline；第二次 F7。
- `run_deep_agent` 是 **baseline execution primitive / fallback implementation**（F9 unavailable
  时由 f9_orchestrator 在其内部调用），**不是第二条 submission/runtime path**。

### D2 — Judge 调用模式（不变）
`handler = ctx.make_handler()`；`await judge_model.ainvoke(messages, config={"callbacks": [handler]})`；
计入 max_llm_calls；禁裸调；不增 agent_step；timeout/cancel 受同一 F8；Judge failure → 同一 F8 run
内 baseline research/synthesis。

### D3 — Projection Deterministic Ordering（不变）
best_verdict `ORDER BY created_at ASC, verification_id ASC` 取最新 succeeded；failed 不参与；
无 succeeded→null；claim/evidence/source/conflict/corroboration/reconciliation/top-K evidence
全部显式排序键。

### D4 — Query Identity vs Dedup Identity（修订 R4：二者不同一）
- `query_identity = SHA256(canonicalized query)` —— **只表示 query 本身**。
- `dedup_identity = SHA256(query_identity + objective_identity + source_universe_identity +
  time_window)`（或等价 deterministic tuple）—— **用于 dedup 判断**。
- **`query_identity != dedup_identity`**（两个不同字段/概念，禁止实现成一个）。
- canonicalize：空白→单空格 strip；大小写 lower（URL host 按 URL 规则保留原义）；URL 去跟踪参数
  并按参数排序；objective_identity=SHA256(target_sub_question_id + normalize(objective))；
  source_universe_identity=SHA256(provider+库标识+配置指纹)；time_window 缺省 sentinel `NONE`。
- retry：不创建新 dedup_identity；attempt metadata 表示；仅上次失败且无新 source 允许。
- re_verify：独立 namespace，不参与 research_query 的 dedup。
- 防绕过：canonicalize 去除无意义 whitespace/case/tracking 参数等变化。

### D5 — Threshold Constants（不变）
MIN_EVIDENCE / independent-source threshold / FRESHNESS_WINDOW / max_research_rounds =
显式配置/常量；初始值由现有 F2–F6 测试 + Eval calibration 决定，禁止为过测试临时调参。

### D6 — Round State / Projection Persistence（不变）
Projection 只读（view + assembler，不改 F1–F8 表）；round state 如持久化用独立 additive
research-plane 表 `research_rounds`（不 ALTER 既有表）；idempotency=(run_id, round)。

## 修订 R1 — fallback 生命周期（并入 §A/B/C/I/K）
- §A 架构图：f9_orchestrator 内 F9 adaptive path 与 baseline fallback 为**同一 coroutine 的分支**；
  normal return 唯一 → F7 once。
- §B：`run_deep_agent` 列为 **baseline execution primitive（fallback 实现）**，由 orchestrator
  内部调用；删除“server 走原路径”表述。
- §C：同一 task_id/run_id/ResearchRun/GovernanceExecution/BudgetCounter/deadline/cancellation
  贯穿 adaptive 与 fallback。
- §I/K：Batch 顺序与 Gate 相应引用上述单执行体不变式。

## 修订 R2 — Final Synthesis 执行方式（精确化；不模糊“复用 agent”）
**决策（冻结）：Final Synthesis 是一次额外 LLM/Agent execution，属于同一 f9_orchestrator
coroutine 的最后一个图阶段。**
- 使用同一 GovernanceExecution 与同一 callback handler（ctx.make_handler()）；
- 每次 provider LLM call 计入 F8 `max_llm_calls`；若发生 tool/search 分别计入 tool_calls/
  search_calls；**不新增 F8 execution**；不创建新 task/run；不重置任何 counter。
- 若 F9 未进入 adaptive（纯 baseline），Final Synthesis 即 baseline 自身的最终回答阶段（同一
  计数规则），不额外增加第二次 synthesis LLM 阶段。
- 同步更新：§A（节点标注“Final Synthesis = 同一执行体内最后一次 agent 阶段，计费进 F8”）、
  §D Budget 表（Final Synthesis 行：agent_step 按 F8 现有、llm_call+1/次、tool/search 如有则+1）、
  §I Batch6/7（Batch7 验证 final synthesis 阶段计数与 F7 once）、§J 测试（synthesis 额外 LLM 计费、
  不重置 counter、无新 task/run）。

## 修订 R3 — Projection 的 round 来源（不自行推断）
- **Research Plane Projection 不推断当前 round。**
- 输入 = Research Plane facts（evidence/source/claim/verification/conflict/corroboration/
  reconciliation）+ **F9 Orchestrator 的 current_round**：
  `project(run_id, round=current_round)`。
- F9 orchestration state 持有 current_round / round state / follow-up plan / stopping state；
  SQL view 只从 run_id 出 fact，**不从 created_at 等猜测 round**。
- 若持久化 `research_rounds`：(run_id, round) 是 **F9 round identity**，不是 Runtime execution
  identity（Runtime identity 仍 task_id/run_id）。

## 修订 R4 — Query Identity / Dedup Identity（并入 D4/G/Batch4/测试）
见 D4：query_identity ≠ dedup_identity；dedup 判定只用 dedup_identity；
retry 不改 dedup_identity（attempt metadata）；re_verify 独立 namespace；
测试需断言二者不同、canonicalize 防绕过、retry 不产生新 dedup identity。

## 修订 R5 — Batch 7 严格限定为 F7 Seam Verification（不重设计 F7）
Batch 7 **不是**重新实现/重构 F7：F7 是 F1–F8 frozen capability，唯一既有 finalize_run。
Batch 7 只验证：
```text
F9 normal completion → Final Synthesis → existing finalize_run → exactly once →
orchestrator normal return → F8 completed
```
以及：cancel / timeout / budget_exceeded / recursion / governance_control_failure /
ordinary agent failure → **NO F7**。
禁止：修改 F7 核心语义；新建 F7 implementation / 第二个 finalization hook；改 F7 identity；
复制 F7 logic 到 F9；baseline fallback 再触发一次 F7。

## A. Architecture（职责归属；R1/R2 修订）
```text
F8 Controller (runtime control plane)
 └─ F9 Orchestrator (F9 orchestration；唯一研究执行体；单 execute)
     ├─ Round 0 research（main agent/subagents/tools；research plane 写入）
     ├─ Research State Projection（research plane facts + current_round → deterministic JSON）
     ├─ Deterministic Gap Detection（code）
     ├─ Semantic Judge（LLM；显式 governance callback）
     ├─ Validated Follow-up Plan（LLM 提议 → deterministic validation）
     ├─ Targeted Research（复用 agent/tools/subagents）
     ├─ Incremental Verification（复用 F3–F6 APIs）
     ├─ Stopping（semantic 建议 + deterministic policy）
     ├─ F9 unavailable → baseline（run_deep_agent 作为 fallback 实现，同一 coroutine）
     ├─ Final Synthesis（同一执行体内最后一次 agent 阶段；计费进 F8）
     └─ F7 Finalization（仅 normal 完成一次；既有 finalize_run）
```
节点归属：runtime control / research plane / F9 orchestration / LLM semantic —— 不混淆。

## B. Existing API Reuse（R1 修订）
复用：Supervisor/main_agent（round0/targeted/synthesis）；Research Registry；F3–F6 APIs；
F7 finalize_run（唯一）；F8 Controller/GovernanceExecution/counters/callbacks/funnel。
`run_deep_agent`：baseline execution primitive（fallback 实现，orchestrator 内调用）。
不新建：Planner/Verification/Citation/Conflict/Research Manager Agent、第二套 Runtime、
第二条 submission 路径。

## C. Context / Identity（R1）
thread_id=UI/session；task_id=lifecycle；run_id=execution 相关键（F1 同源）。
adaptive 与 fallback 共享同一 task_id/run_id/ResearchRun/GovernanceExecution/BudgetCounter/
deadline/cancellation；round 禁止新建 task/run/controller。

## D. Budget Accounting（R2 修订）
| Operation | agent_step | llm_call | tool_call | search_call |
|---|---|---|---|---|
| baseline agent（round0 / fallback） | 按 F8 现有 | +1/调用 | +1/调用 | internet_search +1 |
| targeted research | 同 baseline 规则 | +1/调用 | +1/调用 | 同左子集 |
| Judge | 0（非业务回合） | +1/次（显式 callback） | 0 | 0 |
| Final Synthesis（额外 LLM 阶段） | 按 F8 现有 | +1/次 | 如有 +1 | 如有 +1（同 agent 规则） |
| verification | 不增（F9 不新计） | 按实际 LLM（real verifier 经同样 callback） | 0 | 0 |
对抗测试：judge 无 callback / synthesis 无 callback → 计数未 +1 → fail（可被发现）。

## E. Projection Contract（R3 修订）
字段映射同前（run_id/round/budget/sub_questions/required_uncovered/claims/best_verdict/
independent_flag/fresh/conflicts/evidence_summary/gap_signals/open_questions/size_limits），
其中：`round` 来自 orchestrator current_round 输入（project(run_id, round=current_round)），
view 只出 run_id facts；required_uncovered=缺失覆盖集（无 evidence 且无 source）；
best_verdict 排序键固定；evidence_summary 仅 domain 计数（无 authority）。

## F. Gap Detection（不变）
先 deterministic（missing evidence/source、独立源不足、stale、未缓解冲突、INSUFFICIENT、
coverage gap）后 semantic；Judge 输入=Projection + task-specific context（不复制父历史）。

## G. Follow-up Plan（R4 修订）
实现 objective/query/source_universe/time_window identity；**dedup 用 dedup_identity
（≠query_identity）**；query budget；plan 结构含 plan_id/target/objective/queries/priority/
verification_target/stop_condition/why；deterministic validation（查重/上限/合法性）；
非法 → 不执行 → Rev2 failure semantics（fallback baseline）。

## H. Round State（不变）
合法转移/失败路径/非法保护同 Spec §7（Judge failure→adaptive unavailable→baseline 非 new
round；F8 terminal→立即终止 F9；F9 STOPPED 不映射 F8 TaskStatus）。

## I. Implementation Batches（R1/R2 修订措辞）
1. Batch 1 — Research State Projection（project(run_id, round=current_round)；read-only/
   deterministic/bounded/stable ordering/no-LLM/no schema mutation）。
2. Batch 2 — Deterministic Gap Detection。
3. Batch 3 — Semantic Judge（显式 callback；计数/超时/取消；structured/malformed；failure→
   同一 execute 内 baseline）。
4. Batch 4 — Follow-up Plan + validation + dedup（query_identity ≠ dedup_identity）。
5. Batch 5 — Targeted Research + Incremental Verification（复用 agent/tools/F3–F6；回写 plane）。
6. Batch 6 — Round Orchestrator（Round0→Projection→Gap→Judge→Plan→Research→Verify→Evaluate→
   loop；adaptive 分支与 baseline fallback 同协程）。
7. Batch 7 — F7 Seam Verification（**不实现/重构 F7**；验证 normal→Final Synthesis→finalize_run
   once；non-normal→no F7）。
8. Batch 8 — Eval / 验证（baseline vs F9、same F8 budget、same task class；grounding 等指标；
   Governance adversarial + Behavioral 10 场景）。

## J. 测试矩阵（R2/R4/R5 补充）
- Governance adversarial：judge 无 callback 可被测试发现、多次 judge 计数、synthesis 额外 LLM
  计费、synthesis 不重置 counter/不新 task·run、judge timeout→timed_out、cancel→cancelled、
  targeted budget exceeded→budget_exceeded、round 内不 reset counter/watchdog、round 内不生成
  新 task/run、F8 terminal 后 F9 不继续、F7 exactly once。
- Behavioral：evidence 足够→不 follow-up；不足→targeted；contradiction→research/verify；
  stale→refresh；独立源不足→diversify；Judge insufficient→fallback baseline（同一 execute）；
  invalid plan→fallback；no progress→STOP→F7 once；budget exhausted→terminal→no F7；
  cancellation→no F7。
- Dedup 专项：query_identity ≠ dedup_identity；retry 不改 dedup_identity；canonicalize 防
  whitespace/case/tracking 绕过；re_verify 独立 namespace。

## K. Implementation Gate 交付（不变+R1）
产出本 Plan 与 `docs/plan/2026-XX-f9-p0-implementation-report.md`；自审 13 项（含单 execute 内
adaptive+fallback、Final Synthesis 计费、F7 once、无第二 Runtime、scope 无越界）；全 PASS →
**F9-P0 IMPLEMENTATION COMPLETE / PASS / FROZEN**；任一 FAIL 先报告再重跑 Gate。
