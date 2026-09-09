# F9-P0 Batch 7 — Eval / Behavioral Quality Validation：Implementation Plan

> 状态：**IMPLEMENTATION PLAN（L2；feature branch `feature/f9-batch7-eval`）— 等待 Plan Review。
> 本轮不实现 Eval、不修改 F8/F1–F7/Batch1–6、不改 main_agent.py/orchestrator.py。**
> 依据：用户 2026-09-30 批准（D1–D6 Decision Closure CLOSED；Batch7 = Eval / Behavioral
> Quality Validation，L2）；Batch7 Readiness `docs/plan/2026-09-30-f9-p0-batch7-readiness-
> review.md`（CONDITIONAL）；Decision Closure `docs/plan/2026-09-30-f9-p0-batch7-decision-
> closure.md`；Spec Rev2 §14 Eval Acceptance。
> Git Workflow（AGENTS.md §10 / PROCESS.md §12）：本 Plan 在 feature branch 内产出；
> Implementation 开始前须经本 Plan Review（用户批准后在同一 branch 实施）。

## 1. Scope / Deliverables

- In（本批实现，L2）：
  1. Eval harness（确定性脚本化运行 baseline 与 adaptive，各自独立 execute）；
  2. deterministic scripted benchmark 6–8 scenarios（positive + negative/control）；
  3. deterministic / rubric-based evaluator；
  4. metrics collection（research registry / projection 只读 + TaskRecord counters_snapshot
     + orchestrator summary）；
  5. verification tests（harness 自测 + 回归）；
  6. Batch7 Implementation / Verification Report。
- Out（严格）：不修改 F8 / F1–F7 / Batch1–6 / `main_agent.py` / `orchestrator.py` /
  schema·migration / runtime infra / UI / `_govadapt` real 实现 / Redis·Kafka·Neo4j·Vector /
  durable round state；不新增数据库 schema；不做 baseline-only mode。
- 代码落点（新增，不改冻结文件）：`app/f9/eval/`（harness + scenarios + evaluator +
  metrics）与 `tests/test_f9_eval*.py`（或 `tests/` 下对应文件）；详细文件清单见 §11。

## 2. 实验契约（D2/D3 落实）

- Baseline = `run_deep_agent(task_query, session_id)`；Adaptive = `f9_orchestrator(...)`；
  两者**分别**由各自独立 `Controller.execute(task_id, coroutine, policy)` 执行，独立
  task/run；相同 benchmark task spec、相同 F8 policy、相同 deterministic world。
- 禁止同一 task/run 内顺序执行 baseline+adaptive；禁止 orchestrator baseline-only mode。
- deterministic scripted = Gate 主路径；real provider = non-gate supplementary（本会话无
  凭据 → 仅文档化说明，不跑）。

## 3. Baseline Deterministic 执行 seam（read-only 核实结论，不改 main_agent.py）

核实事实（只读）：`run_deep_agent` 唯一 agent 入口 = `await get_main_agent()`（模块级
lazy 单例，main_agent.py:66–90 / 201）；其 graph 由 `create_deep_agent(model=…, tools=
[文件类], subagents=[network_search/database/knowledge])` 组装；`run_deep_agent` 无参数注入
seam；monitor 为无 WS 亦 fail-open 的单例（monitor.py:111–160），脚本环境可运行。

Seam 方案（测试/harness 层，**不改文件**）：
- Eval harness 以 `unittest.mock`/pytest monkeypatch 在 **Controller.execute 的 coroutine
  内、调用 run_deep_agent 之前**替换 `app.agent.main_agent.get_main_agent` 为返回
  scripted agent 的工厂（monkeypatch 模块属性 → 生效于 run_deep_agent 内部调用点）。
- Scripted agent 为 harness 内定义的确定性对象，实现 run_deep_agent 消费的最小契约：
  `.astream(input, config)` 产出含 `{"model": {"messages": [...]}}` chunk 序列；scripted
  agent 通过 harness 注入的 scripted `internet_search`（写 research registry 的 fake tool
  镜像，复用 Batch5/6 fake tool 模式；research ctx 由 run_deep_agent 自行 create+set）产
  生确定性 evidence，再产出最终 model content。
- final_content 由 harness 从 scripted agent 实例状态读取（不改 monitor/registry）。
- 该 seam 与 adaptive 侧 `graph_runner=` 注入对称；报告记录 baseline/adaptive 的 graph
  glue 差异为限制（D3）。

## 4. Adaptive Deterministic 执行 seam（复用 Batch6 冻结 seam）

- `f9_orchestrator(graph_runner=scripted_runner, judge_model=scripted_judge,
  plan_model=scripted_plan, search_tool=scripted_search, verifier=scripted_verifier,
  conflict_detector/corroboration_reviewer/reconciliation_reviewer=scripted 或 None,
  create_run=True, finalize_sink=捕获 final_content, status_sink=捕获)`；
- 同一 deterministic world（scripted search/evidence 内容、judge/plan 决策）用于 adaptive，
  与 baseline 使用相同 world 工厂（scenario 级注入，保证可比）。

## 5. Scenarios（D6：第一版 8 个，positive + negative/control）

确定性 scripted world：每个 scenario = {task spec, scripted search 结果集（deterministic
content/locator/url/source），scripted judge/plan 行为（如需），预期行为标签}。

1. **already-sufficient / no unnecessary follow-up**（positive-control：adaptive 不应加搜/
   不应多余 follow-up；baseline 单轮完成）→ 断言 adaptive 无额外 search（redundant=0）。
2. **missing required coverage → targeted follow-up**（positive：adaptive 应补 required
   coverage；baseline 单轮不足）→ 断言 required coverage↑、targeted search 发生。
3. **insufficient/unverified claim → re-verification**（positive：F3 增量再验证）→ 断言
   claim verification 状态改进。
4. **conflict → additional research / reconciliation**（positive：F4 检测 + F6 缓解）→
   断言 conflict coverage↑ / unresolved conflicts↓。
5. **insufficient independent corroboration**（positive：F5 佐证不足 → 补独立来源）→
   断言 independent corroboration↑。
6. **diminishing return / correct stopping**（control：无进展即停）→ 断言 stopping 早于
   max_rounds、不无谓加搜。
7. **near-budget stopping**（control：预算近限，F9 应合理停止不超支）→ 断言 budget
   compliance（counters ≤ effective_limits）、不 timeout/cancel。
8. **Judge/Plan failure → same-execution baseline fallback**（negative→fallback：
   judge/plan scripted 抛 ordinary failure）→ 断言 orchestrator 同 execute 内
   `adaptive_fallback` → completed，无第二 run（Batch6 frozen 语义回归）。

每个 scenario：baseline execute + adaptive execute 各一次（独立 task/run），收集
指标对比。

## 6. Evaluator（D5：deterministic / rubric-based，Gate 主判据）

rubric（对 final research state + 最终 answer 应用确定性规则，无 real LLM）：
- required coverage（required sub-question evidence 是否达标）；
- citation coverage（最终 answer 引用 evidence 的占比/是否引用检索到的 source）；
- evidence sufficiency（claims 绑定证据量 / 佐证充分性 F5）；
- unsupported claim penalty（无证据/低置信 claims 计数惩罚）；
- conflict coverage（F4 confirmed / F6 缓解状态）；
- redundant search / unnecessary follow-up（重复 dedup identity / no-gap 后仍加搜惩罚）；
- cost / budget compliance（counters_snapshot vs effective_limits）；
- answer quality **relative to baseline**（rubric 总分差：F9 ≥ baseline 或与 baseline
  持平且 ≥1 项 grounding 改善）。

`answer quality >= baseline` **不得**由单一 real LLM judge 决定；real-LLM judge（如未来）
仅 supplementary，本批不实现 real judge。

## 7. Metrics Collection（D4：零 schema）

- rounds：orchestrator summary.rounds（返回值）；baseline rounds=1（单轮语义）或由
  scripted agent 报告；
- research metrics：对 run_id 以 Research Registry / projection **只读**查询（evidences/
  claims/verifications/conflicts/corroborations/bindings）；
- cost：governance store `get_task(task_id)` → `counters_snapshot` + `effective_limits`
  （controller 收敛写库，已核实 controller.py:116/store.py:239–254）；
- final answer：harness 捕获（adaptive finalize_sink / scripted agent state）。

## 8. Verification Tests

- `tests/test_f9_eval_harness.py`：harness 自身（world 工厂、双 execute 编排、metrics
  读取）确定性断言；
- `tests/test_f9_eval_scenarios.py`：8 scenarios 的 baseline-vs-adaptive 指标断言
  （Gate：adaptive 在至少 1 个 grounding 指标上 ≥ baseline 且 cost 不显著超支——
  逐 scenario 明确判定与 negative-control 防"为指标加搜"）；
- `tests/test_f9_eval_rubric.py`：evaluator 单测（rubric 对已知 state 输出确定分数）；
- 回归：sqlite 全量 + PG（既有套件须全绿；新增测试不进 PG 或单独门控说明）。

## 9. Failure Semantics

- scenario 执行中 baseline/adaptive 任一侧 F8 budget/timeout/cancel → 该 scenario 标记
  对应 terminal（不吞）；不影响其它 scenario；
- world 装配失败 / 断言前置不满足 → scenario error（fail loud，不 pad PASS）；
- real provider 不执行（凭据限制）；报告中单列 supplementary 状态 = 未执行。

## 10. Frozen Boundary / Compatibility

- 不改：app/runtime/governance/*、app/agent/main_agent.py、app/f9/orchestrator.py、
  F1–F7、Batch1–6、schema/migration、runtime infra、UI、_govadapt、Redis/Kafka/Neo4j/
  Vector、durable round。
- harness 对 run_deep_agent 的注入仅在测试进程内 monkeypatch `get_main_agent`（不改文件、
  不 bypass ARCHITECTURE §3 —— 仍经 run_deep_agent 入口）。
- 若实现发现必须修改冻结边界 → STOP → COMPATIBILITY GAP → 回 L3 Decision Closure。

## 11. File Change Plan（待 Plan Review 批准后执行）

- 新增：`app/f9/eval/__init__.py`、`app/f9/eval/world.py`（deterministic world /
  scripted search / scripted agent factory）、`app/f9/eval/harness.py`（双 execute 编排 +
  metrics）、`app/f9/eval/rubric.py`（deterministic evaluator）、
  `app/f9/eval/scenarios.py`（8 scenarios 定义）；
- 新增 tests：`tests/test_f9_eval_harness.py`、`tests/test_f9_eval_rubric.py`、
  `tests/test_f9_eval_scenarios.py`；
- 新增 docs：本 Plan → Batch7 Implementation / Verification Report；
- 零修改：冻结模块（§10 清单）。

## 12. Verification / Gate（Implementation 完成后）

- sqlite 全量回归（≥ 既有 711 全绿）+ 新增 eval tests 全绿；
- PG 全量（既有 81/77 口径全绿，若 eval 无 PG 门控则说明）；
- ruff / compileall 全绿；
- 8 scenarios 的 baseline-vs-adaptive 指标表 + Gate 判定（每 scenario：PASS / 条件 /
  判定依据）；
- 报告 Limitations（glue 差异、deterministic 边界、real-provider 未执行）。

## 13. Final Status（Plan Review 前）

```text
IMPLEMENTATION PLAN READY（L2）— feature/f9-batch7-eval
等待用户 Plan Review / 批准进入 Implementation
```
