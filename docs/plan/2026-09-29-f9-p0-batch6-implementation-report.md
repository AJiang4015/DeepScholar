# F9-P0 Batch 6 — Round Orchestrator Implementation Report

> 状态：**BATCH 6 PASS / FROZEN（用户 2026-09-30 正式 Freeze）**。
> 依据：F9-P0 Spec Rev2、Plan Rev2 §I Batch6、Batch6 Readiness（CONDITIONAL）、Batch6
> Implementation Plan（**L3**，Review Closures C1–C3 已应用）＋用户裁决 D1–D6（2026-09-29）。
> 分级：**L3**（D1 涉及 ARCHITECTURE §3 适用范围回填；已按 L3 执行并记录）。

## 1. Scope / Deliverables

- 新增：`app/f9/orchestrator.py`（Round Orchestrator）、`tests/test_f9_orchestrator.py`
  （17 tests：flow 6 + D4 changed-only 1 + controller seam 10）、本报告。
- 文档最小改动：`ARCHITECTURE.md` §3 红线段追加 F9 governed orchestrator 例外（+9 行，
  仅 §3；文本 = Plan §3 提议/C1 措辞）。
- 零修改：`main_agent.py`、`run_deep_agent`、F8 Controller/Governance、F1–F7、Batch1–5、
  `tavily_tool.py`、migration、依赖、既有 tests、Spec/Plan/Harness（本批未改 AGENTS/PROCESS/
  PROJECT_CONTEXT；仅 ARCHITECTURE §3）。
- 未实现（Plan §14 标注"可选最少实现"）：`app/f9/_govadapt.py`（见 §8 Deviation 1）。

## 2. Design（数据流与 seam）

`Controller.execute(task_id, f9_orchestrator(task_query, session_id, *, research_policy=…), policy)`
（Batch6 测试直接把 orchestrator coroutine 放入 `ctl.execute`）。

1. 校验 governance ctx（无 ctx → `OrchestratorError`）；run_id 取自 GovernanceExecution。
2. run 装配：`create_run_and_root(…, run_id=execution.run_id)`（仅一次）；若 create 失败
   （如 run 已存在/库不可用）→ 从 provenance 读 root sub-question 并 set research ctx
   （复用既有 run 分支，不新建第二 run）。
3. **round0 baseline（D2）**：graph runner 一次（真实默认 = lazy `main_agent.get_main_agent()`
   astream 汇总最后 model 文本；测试注入 fake runner），handler/recursion_limit/research ctx/
   session·thread ctx/monitor 由 orchestrator 注入；`baseline_executed` exactly-once（C2）。
4. **adaptive rounds（D3，in-memory）**：`projection.project → gaps.detect_gaps → no-gap STOP
   → judge_gaps → all-unimportant STOP → propose_followup_plans → execute_targeted_plan × plans`
   （同一 run；Batch5 targeted 自带增量 F3/binding/cap）→ changed claims（本轮 verification
   `new_bindings>0`，sorted 去重）→ **D4 增量 F4→F5→F6**（仅 changed claims；F6 只在存在
   confirmed conflicts 时调用）→ 下一轮或 stopping（no_gap / all_gaps_unimportant /
   no_progress / repeated_gap / max_rounds；Research Policy 注入覆盖）。
5. **Final Synthesis（D6）**：同一 execute 内最后一个 graph invocation（bounded prompt 输入），
   normal completion 后 F7 exactly once（`finalize_run(run_id, final_content or "")` 或注入
   finalize_sink）＋ `set_run_status(finished)`（或 status_sink）→ monitor result。
6. **普通 vs governance failure（§13）**：judge/plan/ordinary 失败 → `adaptive_fallback`
   收尾（final synthesis，**不重放 round0**，C2）；GovernanceLimitExceeded / CancelledError /
   F8 timeout → 不 catch，向 Controller 传播 → budget_exceeded / cancelled / timed_out。
7. 每图调用独立 thread_id（避免 checkpoint 父 history 累积）；graph 调用每次让步
   （`await asyncio.sleep(0)` 测试 runner / 真实 astream 天然让步）→ F8 cancel/timeout
   可在任何段投递。

身份链（校验项）：TaskRecord.run_id == GovernanceExecution.run_id == ResearchRun.run_id
== 全链 research 表 run_id；task_id=lifecycle / run_id=correlation / thread_id=UI·session。

## 3. Round0 Exactly-Once / Round State（D2/D3/C2）

- 显式 `baseline_executed: bool`（初始 False，round0 进入后置 True）；adaptive/fallback 永不
  重跑 round0（`baseline_executed` 检查由结构保证：round0 只在 adaptive 循环前执行一次）。
- F9 STOPPED/NO_PROGRESS 为 orchestrator 状态（不映射 F8 TaskStatus）；F8 terminal 由 F8
  收敛（funnel）。

## 4. D4 — Changed-Claim 增量 F4→F5→F6

- changed claims = 该轮 Batch5 result `verification[].new_bindings>0` 的 claim_id（sorted
  union）；空 changed → F456 零调用（spy 断言）。
- 每 claim：F4 `detect_claim_conflicts`（默认 FakeDetector）→ F5
  `compute_claim_corroboration` → F6 仅当 F4 confirmed conflicts 非空
  （`reconcile_claim_conflicts`）；ordinary 失败 fail-open 记入 round outcome。
- 注入 seam：`conflict_detector / corroboration_reviewer / reconciliation_reviewer` 参数
  （默认 None → 各模块 Fake）；real-LLM governed adapter 由调用方经 seam 注入
  （_govadapt 可选实现，本批 deferred，见 §8.1）。
- F456 段每 claim 让步点（`await asyncio.sleep(0)`）：保证段内 F8 cancel/timeout 可投递且
  不 catch（CancelledError 语义实测 controller 级，见 §6 矩阵 #3）。

## 5. Identity / Persistence / Budget

- 单 Controller.execute / GovernanceExecution / BudgetCounter / deadline·cancel / ResearchRun
  （不新建）；round state 纯内存；零新表；thread 仅 checkpoint 行。
- SQLite/PG：orchestrator 行为与后端无关（round 纯内存）；research/F8 seam 双后端由既有
  PG 门控覆盖（§7）。

## 6. Tests（AC → Test → Evidence；matrix §15 全场景）

文件：`tests/test_f9_orchestrator.py`（17 passed；flow 6 + D4 1 + controller seam 10）。

| AC（Plan §15/裁决） | Implementation | Test | Evidence |
|---|---|---|---|
| 单 execute invariant、无第二 run | 不建 controller/task/Run；复用 execution.run_id | `test_normal_completed_one_execution`；flow | TaskStatus COMPLETED、version==1；research_runs count==1 |
| round0 baseline 全额计费、最多一次 | `baseline_executed` once | `test_no_gap_stop_finalize_once`；judge-failure flow | stages==[round0, final_synthesis]；round0 恰 1 次 |
| no-gap → STOP 不调 Judge | signals 空直接 break | `test_no_gap_stop_finalize_once` | stopped_reason=no_gap；finalize==1、status finished |
| all gaps unimportant → STOP | 无 important 判定 break | `test_all_gaps_unimportant` | stopped_reason=all_gaps_unimportant；finalize==1 |
| adaptive 真执行 targeted + evidence | 每轮 judge→plan→targeted | `test_adaptive_adds_evidence_then_stops` | executed_plans>=1；finalize==1 |
| requires governance ctx | 入口 ctx 校验 | `test_requires_governance_context` | OrchestratorError |
| D4 changed-only F4→F5→F6（f6 无冲突跳过） | _run_f456_incremental | `test_f456_only_changed_claims` | f4/f5 只对 changed；f6 空 conflicts 零调用 |
| **Matrix #1 cancel during round0** | runner hang round0 | `test_cancel_during_round0_no_finalize` | CANCELLED、finalize==0 |
| **Matrix #2 cancel during targeted research** | hang search tool | `test_cancel_during_targeted_no_finalize` | CANCELLED、finalize==0 |
| **Matrix #3 cancel during F4/F5/F6** | F456 spy + cancel | `test_cancel_during_changed_claims_f456_no_finalize` | CANCELLED、finalize==0、spy 证实段已执行 |
| **Matrix #4 timeout during final synthesis** | watchdog timeout | `test_timeout_during_final_synthesis` | TIMED_OUT、finalize==0 |
| **Matrix #5 budget during Judge** | max_llm_calls=0 | `test_budget_during_judge_no_finalize` | BUDGET_EXCEEDED、finalize==0 |
| **Matrix #6 budget during final synthesis** | final-synthesis LLM 消耗 | `test_budget_during_final_synthesis_no_finalize` | BUDGET_EXCEEDED、finalize==0 |
| **Matrix #7 ordinary Judge failure → fallback → F7 once** | JudgeError → fallback | flow + `test_judge_failure_fallback_completed_finalize_once` | finalize==1、无 round0 重放、COMPLETED、run==1 |
| **Matrix #8 ordinary Plan failure → fallback → F7 once** | PlanProposalFailure → fallback | flow + `test_plan_failure_fallback_completed_finalize_once` | finalize==1、COMPLETED、run==1 |
| D5 policy 注入覆盖 | RESEARCH_POLICY_DEFAULTS + _policy | flow（round cap 语义） | 默认常量存在；可注入 dict 覆盖 |
| monitor 可选 | emit_monitor + report_* | 单元/controller（同 graph runner 路径） | 调用不抛（fake runner 场景） |

## 7. Regression

- sqlite 全量：**711 passed / 88 skipped / 0 failed**（=694 + Batch6 17；排除
  `test_db_tools_mysql_integration.py` 既有环境污染文件，同 Batch1–5 纪律）。
- PG 双 DSN（research F1–F7 + governance + F9 projection/gaps/targeted，同 Batch5 口径，
  governance 表先 reset）：**77 passed / 0 failed**（与 Batch5 记录一致，无回归）。
- PG 全量 14 个 `*postgres*.py` 文件（另含 `test_checkpoint_postgres.py` 4 例）：
  **81 passed / 0 failed**（口径说明：Batch5 报告的 77 不含该文件；本批两口径均绿）。
- ruff / compileall（app/f9）全绿；git diff/status 仅新增 `app/f9/orchestrator.py`、
  `tests/test_f9_orchestrator.py`、本报告，另 ARCHITECTURE.md §3 例外段（+9 行）。

## 8. Deviations from Plan（如实记录）

1. **`app/f9/_govadapt.py` 未实现（Plan §14 标注"可选最少实现"）**：D4 real-LLM
   detector/reviewer 的 governed adapter 本批 deferred。orchestrator 保留注入 seam
   （`conflict_detector / corroboration_reviewer / reconciliation_reviewer`）与 handler
   透传；F4/F5/F6 默认走各自模块 Fake（deterministic），与 Batch3–5 纪律一致。原因：
   (a) 真实通道受凭据环境限制（OPENAI_API_KEY 未解析 / VERIFY_REAL_LLM 未启用，不进自动化
   Gate）；(b) 复用面需为 F4/F5/F6 respond 协议各建 wrapper，属可选增强，Batch6 边界内以
   seam + 默认 Fake 验证 changed-only 语义。real 路径（挂 F8 handler 计费）留给调用方注入
   或 Batch8 校准（如实记录为未验证项，不 pad PASS）。
2. **F456 段让步点**：Plan §15 场景 3 的 controller 级测试以 conflict spy 判定已进入
   changed-claim 段后发起 cancel（段内为同步 DB/LLM 工作，取消经每 claim 让步点投递）；
   断言 CANCELLED + finalize==0 + spy>0，覆盖矩阵 #3 语义。
3. **graph runner 默认**：真实默认 = lazy import `main_agent` 的 astream wrapper（D1 Option
   A）；自动化 Gate 全部用 scripted/fake runner 驱动编排 seam（真实 agent E2E 受凭据限制，
   与 Batch1–5 同一纪律，见 §10）。
4. **create_run=False / create 失败分支**：orchestrator 在“不新建 run / run 已存在”时从
   provenance 读 root sub-question 并 set research ctx（Plan §4 允许的复用分支），实现后补充。
5. **ARCHITECTURE §3 回填**：按 Plan §3 提议 + C1 措辞，仅红线段追加一行限定（+9 行），
   未做含糊"临时例外"。

## 9. AC → Evidence 汇总（重要验收）

- 单 execute / 无第二 run / F7-once：controller seam 全部场景断言 TaskRecord 终态
  （COMPLETED/CANCELLED/BUDGET_EXCEEDED/TIMED_OUT）+ finalize_sink 计数（normal==1、
  governance terminal==0）+ research_runs count==1。
- round0 exactly-once：flow 断言 `stages.count("round0")==1` 且 fallback 后直接
  final_synthesis（无重放）。
- changed-only F4→F5→F6：D4 spy 单测 + controller #3 spy。
- 矩阵 #1–#8：§6 表逐项，全部 controller-level（#7/#8 另加 flow 级 no-replay 断言）。

## 10. Limitations

1. 真实 provider E2E（real graph 执行 / real verifier / real detector·reviewer）受凭据环境
   限制（OPENAI_API_KEY 未解析、TAVILY_API_KEY 机器级未进本会话 env、VERIFY_REAL_LLM 未
   启用）→ 测试全部用 fake/scripted models·tools；D-A/D4 协议与计费 seam 已测，真网络 E2E
   需凭据环境（与 Batch1–5 同一纪律，不伪造证据）。
2. `_govadapt.py`（real-LLM F4/F5/F6 governed adapter）deferred（Plan 可选）；real 计费
   路径未在本批自动化 Gate 内验证。
3. D3 round state 为 in-memory：跨 execution/crash 恢复能力未做（future additive Gate）。
4. RESEARCH_POLICY_DEFAULTS（max_research_rounds=3 / max_no_progress_rounds=1 /
   repeated_gap_threshold=2）为临时默认，Batch8 Eval 校准前非冻结产品参数。
5. monitor 事件仅 emit_monitor=True 时做轻量埋点（task_started/assistant/result），与
   run_deep_agent 完整 monitor 事件面非逐字对齐（Plan §19 记录 Known Risk，本阶段不改
   main_agent）。
6. 测试 runner 每段 `await asyncio.sleep(0)` 为语义中性的可取消让步；真实 graph 天然让步。

## 11. Scope Audit

- 新增：`app/f9/orchestrator.py`、`tests/test_f9_orchestrator.py`、本报告。
- 修改：`ARCHITECTURE.md`（仅 §3 红线段例外 +9 行，属本批 L3 范围回填）。
- 零改动：`main_agent.py`、`run_deep_agent`、F8、F1–F7、Batch1–5 文件、`tavily_tool.py`、
  migration、依赖、既有 tests、Spec/Plan/Harness（AGENTS/PROCESS/PROJECT_CONTEXT 本批未改；
  AGENTS.md/PROCESS.md 的未提交改动系此前 harness-governance 批产物，非本批）。
- 代码路径审计：无 F7 调用除 normal 收尾 `finalize_run`（或注入 sink）；无第二
  Controller/execute/task/Runtime/ResearchRun；无 Redis/Kafka/Neo4j/vector/scheduler。

## 12. HARNESS REVIEW checklist（ARCHITECTURE.md 变更）

- [x] 变更面：ARCHITECTURE.md §3 红线段 +1 例外（仅 F9 governed orchestrator 内部每次 graph
      invocation 的 glue/context 注入等价要求）；非 F9 编排路径语义不变（产品外部入口仍走
      `run_deep_agent`）。
- [x] 依据：Batch6 Implementation Plan §3（L3，Review Closures C1–C3 applied）＋用户裁决
      D1（Option A）批准 ARCHITECTURE §3 最小回填。
- [x] 无含糊"临时例外"：例外范围收紧为"只绕过 run_deep_agent 生命周期 + F7 wrapper，不允许
      裸 main_agent.astream()"；注入清单与等价性要求显式列出。
- [x] 未触碰 AGENTS/PROCESS/PROJECT_CONTEXT/TESTING/DECISION 与其它 ARCHITECTURE 章节。
- [x] 用户 Review（2026-09-30）确认 ARCHITECTURE §3 例外回填通过，随 Batch6 一并 Freeze。

## 13. Final Decision

```text
F9-P0 Batch 6 — Round Orchestrator
STATUS: PASS / FROZEN（2026-09-30 用户正式 Freeze）
```

- 验收项（用户 Review 确认）：D1 Single Controller Execution / D2 Round0 Exactly-Once /
  D3 In-Memory Round State / D4 Changed-Claim F4→F5→F6 / D5 Research Policy·Stopping /
  D6 Final Synthesis·F7 Exactly-Once / F7-once adversarial matrix / Identity·Context·
  Persistence / SQLite 711 passed·88 skipped / PG 81 passed / Batch6 tests 17 passed /
  ruff / compile / Scope Audit —— 全部 **PASS**。
- `_govadapt.py` real-provider governed adapter E2E 保持 **DEFERRED / environment-limited**
  （不因"补齐"修改 Batch6 frozen contract，不虚报 PASS）。
- Batch6 glue duplication 继续作为 **Known Risk** 记录，不作为当前 defect 修复。
- 未宣布 F9-P0 IMPLEMENTATION COMPLETE；下一阶段 = F9-P0 Batch 7（Readiness Review 先行，
  仅 Readiness；不自动进入 Implementation），等待用户显式指令。
