# F8 Runtime Final Architecture Audit / Freeze Review

> 状态：**只读架构审计（未修改任何 production/frontend/test/schema）**。
> 范围：F8 Step 1–3 + Step 4 Batch 1/2/F1/Batch3 作为一个完整 Runtime 系统审查。
> 判定准则：不因“尚未实现”判 bug；区分 current-F8 defect vs future capability。

## 1. Executive Summary

F8 Runtime Foundation 已形成**内部一致、边界清晰、可冻结**的架构：
- **单一 control plane**：GovernanceController + 冻结 Terminal Funnel 是唯一有权决定 Task terminal
  state 的写入口（代码证据：TaskRecord 的 status/version 变更仅发生在
  `store.terminal_update`（funnel 内 CAS）+ 新行 insert；`start_task/update_run_id` 只写 started_at/
  run_id 列，不触碰 status/version）。
- **四平面职责不混**：TaskRecord（lifecycle truth）／governance_events（durable observation）／
  checkpoint（agent execution state）／research_*（research data）独立。
- **三类 identity/seq 分离**已冻结：`(task_id, governance_seq)` durable；`monitor_seq` live；
  `event_id` 幂等；F1 保证 governed run_id 全链单源。
- 审计发现 0 个 lifecycle/control/durability **correctness** 问题（ACTION REQUIRED 级不存在）；
  记录若干**非阻塞 Finding/边界**（见 §14），全部可归入 future capability 或已文档化的有界不一致。

**Verdict：PASS / FROZEN**（细节见 §16/§17）。

## 2. F8 Scope Reviewed
Step 1（Task Lifecycle/TaskRecord/funnel/CAS）· Step 2（governance 表族/事件/replay cursor）·
Step 3（Budget/Step/LLM/Tool/Search limits/Deadline/Cancel/Watchdog/Controller）·
Step 4 Batch1（service→controller governed submit/cancel/tasks/events）·
Batch2（replay/cursor/WS since_seq）· F1（run_id backfill/identity 全链）·
Batch3（monitor task_id additive / governance_terminal bridge / event_id 对齐 / observation fail-open）·
跨批 regression：sqlite 142 passed；PG 59 passed（最终回归记录）。

## 3. Final Runtime Architecture
```text
/api/task ──service.submit──▶ TaskRecord(running, run_id 必绑 F1) + durable task_started
   └─▶ GovernanceController.execute(policy)
         ├─ GovernanceExecution(ctx) ──▶ run_deep_agent（monitor 帧带 run_id(+task_id additive)）
         ├─ callbacks/BudgetCounter（compare-before-execute）· watchdog（monotonic deadline）
         ├─ funnel（唯一 terminal 写；CAS/version/pending/flush 冻结）
         ├─ finalize_with_event → durable lifecycle event（单写者，(task_id,seq)）
         └─ live_sink → governance_terminal bridge（observation；event_id==durable 对齐）
查询/恢复：GET /api/tasks·/api/tasks/{id}·/api/threads/{thread}/events?task_id&since_seq（只读）
WS：monitor_event（live）+ governance_replay 握手回放 + governance_terminal
```
Source of truth 分层：TaskRecord=lifecycle/terminal truth；governance_events=durable lifecycle
history/audit；checkpoint=agent execution state（续跑）；research_* = research 产物/溯源；live=
即时通知（无 truth 权）。

## 4. Lifecycle Integrity Audit

| 路径 | 实现 | 代码证据 |
|---|---|---|
| submit→running | service.submit：insert running（TaskRecord）+ start_task(started_at)（best-effort） | store.insert_task/start_task |
| completed/failed/cancelled/timed_out/budget_exceeded | governed execute 映射 / watchdog / cancel_governed / c2 → funnel | controller._execute_governed/_watchdog_loop/_converge_control_failure |
| superseded | funnel 支持（验证 + superseded_by 校验）；**服务层 resubmit 暂以 cancel_governed 收敛**（非 superseded） | controller.terminalize 校验；server.run_task |
| aborted | funnel 支持；startup reconciliation 语义经 Phase F PG 取证（新实例 funnel aborted） | tests（PG final F3） |
| orphan_reclaimed | funnel/枚举/表支持；**sweeper 产品未建**（future） | models + 无 sweeper 模块 |

- bypass funnel？**无**：所有 production 状态写经 funnel/store 单点；server 不直接写 TaskRecord；
  `controller.cancel`（冻结面）与 `cancel_governed` 都经 funnel；legacy cancel 分支只处理
  非 governed 历史任务（不产生 TaskRecord）。
- 无法 terminalize 的路径？进程崩溃中途 running → 无自动收敛（**future**：startup hook/sweeper，
  Phase F 已证明收敛语义可用）。其余路径均有收敛出口。
- terminal 后再次变更？CAS `WHERE status='running' AND version=?` + funnel no-op/adopt 保证单次
  transition；start_task/update_run_id 不触碰 status/version。
- controller/service/store 重复定义？无第二套 terminal 定义（枚举/表/模型单一权威）。

## 5. Control Plane Audit
1. 硬限制执行前生效：✅ on_llm_start/on_tool_start compare-before-execute（Provider B + D/E 实证）。
2. compare-before-execute 完整：✅（decision 回合 llm+step 原子；tool/search 双槽原子；无 overshoot）。
3. governance exception 穿透：✅ `GovernanceLimitExceeded(GraphBubbleUp)` 穿透 main/ToolNode/subagent（Probe B）。
4. timeout/cancel/budget race → 统一 funnel：✅（单一 terminalize 入口、funnel 仲裁）。
5. 分类保持：✅ agent_failure / governance_control_failure / framework_recursion_safety 分离映射。
6. controller 自身失败 → 永久 running？运行期 watchdog/c2 均收敛 failed(governance_control_failure)；
   仅**进程级崩溃**会遗留 running（future startup hook；非当前缺陷，已文档化）。
7. fail-closed/open 边界：✅（submit/store 不可用=c1 fail-closed；observation= fail-open）。
> Control Plane 是唯一决定 Task terminal state 的地方 —— ✅（无其它写入口）。

## 6. Durable Plane Audit
- truth 分层明确，无混源（§3）。
- replay cursor `(task_id, governance_seq)`：唯一 durable cursor；seq task-scoped（events 单写者
  事务内 MAX+1，UNIQUE(task_id,seq)）；查询限定 thread+task、seq>since ASC、只读（SELECT）；
  replay 不改 lifecycle。
- 跨 task 混序？不可能（task 维查询）；同 thread 多 task 各自独立 cursor。
- since_seq duplicate/omission：cursor=(task_id,governance_seq) 后同 task 无洞（单写者）；
  Batch2 已验证双 task 无重复/无遗漏；跨 task 无全序承诺（文档化）。
- Batch3 未破坏 Batch2：bridge 不落库、不进 governance_events/replay；写路径零改动。

## 7. Observation Plane Audit
- 三层明确：monitor=execution progress live；governance_terminal=lifecycle terminal notification；
  durable replay=reconnect/authoritative recovery。
- live fail-open：✅（monitor enqueue 异常不反向破坏执行）。
- live 不会成为第二 truth：✅ 无任何代码以 live 事件驱动 TaskRecord。
- bridge 不影响 terminal state、不进 durable ordering、不带 governance_seq：✅（代码事实）。
- bridge event_id 与 durable event_id 对齐（成功/幂等时相同）；durable 缺失（写失败）时 bridge 仍可
  以同一 attempted event_id 通知（fail-open，不反向改 truth）——文档化。
- reconnect：durable replay 独立恢复状态 ✅（无 live 依赖）；bridge 丢失不影响最终一致
  （TaskRecord/durable 为准，eventual convergence）。

## 8. Identity Contract Audit（F1 后）
全链同源（governed）：TaskRecord.run_id → GovernanceExecution.run_id → ctx.run_id →
ResearchRun.run_id → monitor/run_id → task_result/run_id → durable event/run_id（F1 测试 + Audit）。
分裂仅可能出现在非 governed/无 TaskRecord 执行（允许，无一致性约束）。
monitor.task_id / governance_terminal.task_id / TaskRecord.task_id 关联：governed 全部取
GovernanceExecution.task_id（monitor 经 run_deep_agent set_task_context）；bare → null。
same-thread multi-task / superseded old run / reconnect / cold start：task+run 各自唯一，event
task-scoped；冷启动以 tasks API + per-task replay（best-effort 合并视图未承诺）。
粒度契约（冻结）：thread=会话/UI；task=lifecycle/durable；run=execution；governance_seq=durable
ordering；monitor_seq=live ordering；event_id=idempotency identity。互不混用。

## 9. Failure / Durability Semantics Matrix

| Component | Semantics |
|---|---|
| Task terminal control | FAIL-CLOSED（memory authority 后 funnel；绝不停留在“无状态运行”） |
| TaskRecord CAS/version | FAIL-CLOSED（rowcount==1 证明恰一次） |
| run_id binding（F1） | FAIL-CLOSED（无法绑定 → 拒绝启动） |
| governance event persistence | FAIL-OPEN（durability_gap；不改 Task 状态） |
| monitor | FAIL-OPEN（enqueue 失败仅丢 live） |
| governance_terminal bridge | FAIL-OPEN（sink 异常/无连接 → 丢弃/日志） |
| replay | 只读；读失败 → API 500/握手失败可重试（不伪造 live） |
| WS connection | 断开可重连；握手 since_seq 回放（durable 兜底） |
| frontend observation | 无 polling/status-sync（记录为 future） |
> observation failure 不反向破坏 control/lifecycle truth —— ✅（无 live→TaskRecord 依赖路径）。

## 10. Concurrency / Race Audit（代码级，winner/loser/durable/live）
funnel 单一仲裁（memory→CAS→exactly-one→loser adopt）对所有组合成立；winner=先入 funnel 者；
loser=already_terminal no-op；durable=单 terminal、version+1；live：monitor 帧与 bridge 可交错，
**最终一致性 = observation eventual convergence**（durable 为准），非 lifecycle inconsistency。
- completed×cancel/timeout/budget、budget×timeout、supersede×cancel、duplicate terminalization：
  funnel 层已证（sqlite+PG）；Phase D/E integration 复证。
- concurrent run_id backfill：UPDATE WHERE run_id IS NULL 条件化 + 并发采纳 fresh（controller 逻辑）。
- same-thread multi-task：task 独立记录/事件；registry done-callback 守卫防旧任务误删新登记。
- WS disconnect×terminal：重连 replay；bridge 丢失无影响。
- replay×live bridge：分离（bridge 不进 replay；replay 只读 durable）。

## 11. Backend / Frontend Boundary
Backend authoritative：TaskRecord / governance_events / GET /api/tasks/{id} /
GET /api/threads/{thread}/events。Backend live：monitor_event / governance_terminal。
Frontend responsibility：live progress、live filter、reconnect、durable replay、terminal UI 收敛。
`task_started.run_id anchor`：介于正式与 workaround —— 在 governed 路径 run_id 是稳定单源（可作
分组键），但**非完整 contract**：正式推荐以 submit 返回 {task_id,run_id} + durable replay 收敛；
live 仅增量（Batch3 已给 task_id additive，改善该情况）。frontend 保持现状（不审改）。

## 12. Architectural Duplication Check
| location | duplication | risk | recommendation |
|---|---|---|---|
| server `active_tasks` + `_task_registry` | 双 map（legacy monitor 兼容 + governed registry） | 低（守卫清理） | 保留；未来 API Step 收敛 |
| server legacy cancel 分支（非 governed 历史任务） | 与 governed cancel 并存 | 低（仅历史任务路径） | 文档化；随旧任务退役移除 |
| monitor seq 分配 vs events seq | 无重叠（live vs durable 分开） | 无 | 保持 |
| checkpoint vs TaskRecord vs events vs research | 无职责重叠（§3 分层） | 无 | 保持 |
> 无“两套 runner/terminal/event writer/identity/cancel/timeout/replay/sequencer”。

## 13. Production Readiness Boundary
**已成立**：single-instance runtime；PG durable lifecycle（CAS/terminal/事件/replay）；runtime
budget/deadline/cancel；run identity（F1）；live observation bridge（Batch3）。
**未成立（future capability，非缺陷）**：multi-instance ownership/lease/heartbeat；
crash/process 级自动 startup hook（语义已 PG 取证，产品模块未建）；polling/status-sync；
real-provider E2E（需凭据）；horizontal scaling；durable live streaming 基础设施。

## 14. Known Limitations（记录不修复）
- superseded 服务层语义未接线（resubmit 用 cancelled 收敛）——AC2 完整 supersede 留后续 API Step。
- orphan/sweeper、startup 自动收敛、durability_gap 表/预览通道、跨 task 合并时间线、polling 兜底：
  全部为 future capability。
- live 帧仍无任务级全量历史恢复承诺（冷启动以 durable replay 兜底）。
- 真实凭据环境的 WS E2E 未在本环境运行（bridge/replay 语义已受控覆盖）。

## 15. Open Questions
1. supersede 服务层语义何时落地（API Step）？
2. startup 自动收敛（sweeper）归属下一阶段范围？
3. frontend 是否切到正式 anchor（{task_id, run_id} + replay），何时做？

## 16. Required Closures
**无 lifecycle/control/durability correctness 级 closure 要求。**
可选（非阻塞，future）：服务层 superseded 接线、startup hook 产品化、frontend 正式 anchor 迁移。

## 17. Final Architecture Verdict
**PASS / FROZEN** —— F8 Runtime Foundation 内部语义一致、边界清晰、可冻结；
审计未发现需要 ACTION REQUIRED 的 correctness 问题；观察到的差异均为 observation eventual
convergence 或明确 future capability，不构成当前缺陷。
