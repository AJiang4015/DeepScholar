# F8 Step 4 Spec — Runtime History / Sequencing / Replay（Capability Audit + Spec-only）

> 状态：**AUDIT / SPEC-ONLY（未实现）**。基于 F8 Step 1–3 / Phase A–F 全部冻结语义的再审计。
> 本文件是决策输入，不是实现契约；仅当 Gate 通过后才进入 Implementation。

## 0. 审计方法（只读证据）

- 代码审计：`app/api/monitor.py`（R3 事件信封/seq/WS）、`app/api/server.py`（/api/task、cancel、
  `/ws/{thread_id}`、active_tasks）、`app/runtime/checkpoint.py`（checkpointer 配置）、
  `app/runtime/governance/*`（governance_tasks/governance_events、funnel）、`app/research`（ResearchRun）。
- 版本基线：deepagents 0.5.7 / langchain 1.2.17 / langgraph 1.1.10 / PG16（见 Step3 Final Gate Report）。

## 1. 当前 Runtime 还缺什么（实测）

| 能力 | 现状（证据） | 缺口 |
|---|---|---|
| TaskRecord（governance_tasks） | Step 1–3 冻结；CAS/pending/flush 全链 PG Gate 通过 | **未接入真实服务路径**：`server.py /api/task` 仍直接 `asyncio.create_task(run_deep_agent(...))` + `active_tasks`，无 TaskRecord/controller/execute(policy) |
| Durable Event History | `governance_events` 表 + `GovernanceEvent` 模型存在（Step 1），**零写入方**（全仓 grep：仅模型/注释）；funnel 明确把事件留给 sequencer Step | 无任何 durable 事件 |
| Live event（R3 monitor） | 进程内 `monitor_seq` + `event_id` + per-thread WS 串行推送；WS enqueue fail-open；**不落盘** | WS disconnect / backend restart 后事件不可恢复；无 since_seq / 回放 |
| WS reconnect catch-up | `/ws/{thread_id}` 连接即收新事件；无握手游标、无历史回放 | 断线重连丢增量 |
| 服务级 API（F8 语义） | 计划 §18（POST /api/task + cancel/tasks/events/close）**未实现** | submit 不接受 policy/limits；cancel 不走 funnel |
| sweeper / startup reconciliation（产品模块） | 语义已用冻结 funnel 组合在 PG 取证（Phase F F3）；产品模块未建 | crash 后遗留 running 的自动收敛 |
| Sequencer / Replay / Adapter | 全部未实现（计划内后续 Step） | 见 §5/§6 判定 |

## 2. 为什么缺（不归因于“没时间”）

- Step 1–3 被刻意收窄为 **control-plane 内核**：funnel 唯一写入口、事件写被明确推迟（Step 2 docstring、
  Step 3 spec §16 non-goals），以先证明 terminal 语义在 PG 上收敛。
- monitor 的职责至今只是 live WS（R3 语义）；任何 durable 化都会触碰 monitor payload/seq 契约，
  因此在 adapter 边界未定前不动。
- 服务接线（server/API）被排在 runtime 内核验证之后——避免在 funnel 未冻结时接线上层。
- 审计证据：governance 包内仅 `models`/`controller` docstring 提及 events；`monitor.py` 无持久化路径。

## 3. 用户/系统实际要解决的问题（需求驱动，不为“完整”而加功能）

1. **用户/运营要看到“这个任务最后怎么了”**：terminal reason、budget/timeout/cancel 结果、counters ——
   这由 TaskRecord 满足（已具备数据），缺的是服务级 tasks 查询与 /api/task 接线。
2. **断线/刷新后前端要能恢复进行中或刚结束的任务视图**：需要 durable 事件 + reconnect catch-up
   （since cursor），或退而求其次：仅恢复 TaskRecord 快照（状态/result），事件流增量可选。
3. **审计/排障要能回答“哪次 budget 触发的、何时、快照多少”**：需要 durable event history。
4. **崩溃后运维要能认知在途任务**：startup reconciliation（running→aborted）→ TaskRecord 已支持，
   需产品级 sweeper/startup hook。
5. 四类“replay”的真实需求不同（见 §4）——不能混成一个 Event Sourcing 大系统。

## 4. 边界：Event / Task / Checkpoint / ResearchRun（四平面）

| 平面 | 载体 | 生命周期 | 用途 | 恢复语义 |
|---|---|---|---|---|
| Agent Execution State | LangGraph checkpoint（官方 saver，sqlite/PG，按 thread_id） | 对话/执行延续 | agent 自身状态续跑 | checkpoint replay = 对话/步骤续跑 |
| Task Lifecycle State | `governance_tasks` | submit→terminal（controller/funnel） | 生命周期/审计/预算结果 | 崩溃后认知 + startup aborted |
| Event History | `governance_events`（append-only，计划） | 观测事件 | live 去重、durable 审计、replay | 断线 catch-up、重启后审计 |
| Research Artifacts | `research_*`（F1–F7，run_id 关联） | 研究产物 | claims/sources/结果 | research 面独立查询 |

- TaskRecord terminal ≠ checkpoint 已完成 ≠ 底层已停（Step2/3 已证）；ResearchRun 状态由 run_deep_agent
  各自维护（finished/cancelled/failed）。
- 关联键：execution `run_id`（ResearchRun.run_id == monitor run_id）；TaskRecord.task_id 需在服务接线时
  与 run_id 绑定（controller create_task(run_id=...)），目前 server 路径两者都缺。
- **禁止**：把四者并表 / 共事务 / 用 checkpoint 存 governance 事件 / 用事件表驱动 agent 状态。

## 5. Durable Event History：是否需要？

- **需要**（限 lifecycle/观测审计子集）：task_status_change / budget_exceeded / timed_out / cancelled /
  task_started / task_result 等；目的 = crash 后可审计 + WS reconnect catch-up + tasks 详情的时间线。
- 不需要：research 全量过程事件（F3–F6 已有自己的产物数据）、token 明细流（telemetry 可降级）。
- 当前 monitor live 事件**不是** durable：断线即丢；不可当历史。

## 6. Sequencer / Single Writer：是否需要独立组件？

- **需要单一 durable 写者**（governance 内部 `events` 模块职责，非新 infra）：分配 `(task_id, seq)`
  （全序、唯一），持久化成功后才进 live（durable=true）；失败 → live durable=false 预览 + durability_gap；
  `event_id` publish 时生成、live/durable/replay 同一身份（幂等去重键）。
- monitor 不持有第二套持久 seq（其 monitor_seq 仅供 live 排序）；`adapter` 只在“提交”边界把
  monitor/controller 事件转换为 canonical envelope —— 不复制 monitor._emit 逻辑。
- 判定：controller/monitor 现有架构**不足以**直接承担 durable 单写者（controller 是 control 语义、
  funnel 不做事件写；monitor 无事务/无 (task_id,seq)）；需要独立 events 写者，但它是小模块，不是
  “事件源/消息系统”。

## 7. Replay：是否需要（按 §4 四类分开判定）

| 类型 | 价值 | 判定 |
|---|---|---|
| LangGraph checkpoint replay | 对话续跑 | 已存在（官方 saver）；**不在本 Step** |
| Runtime event replay（durable 审计/时间线） | 审计/详情 | **有条件需要**（与 Durable Event History 同批，API 只读） |
| Frontend WS reconnect catch-up | 刷新/断线恢复视图 | **需要**（最轻量 = 握手 `?since_seq=` + 任务快照兜底） |
| Task state recovery | 崩溃后认知 | **需要**（= tasks 查询 + startup reconciliation；非事件回放） |

产品价值排序：tasks 查询 + reconnect 快照 > durable lifecycle 事件审计 > 全量事件 replay UI。

## 8. Failure semantics / Idempotency / PG transactions / crash-restart

- 事件写 = observation：失败 → live 预览 durable=false + durability_gap + degraded 标记；**不改 Task 状态、
  不禁用 control**（沿用 Step3 F1/F6 矩阵）。
- 幂等：`event_id` 唯一（重发不重复）；`(task_id, seq)` 唯一；terminal 事件与 TaskRecord terminal
  同批次或紧随、以 TaskRecord 为真（不二义）。
- 事件写与 TaskRecord terminal 写不共事务（两个 plane；以 TaskRecord CAS 为终态权威，事件允许滞后/间隙，
  用审计注记对齐——沿用 Step2 R2 有界不一致策略）。
- crash/restart：内存 live 丢失正常；durable 事件重启后可回放；在途 running → startup reconciliation
  → aborted（产品 sweeper/startup hook，Phase F 已取证语义）。

## 9. 与 F8 Step 3 的关系

Step 3 = control-plane（budget/deadline/cancel/terminal，已 PASS/FROZEN）。
Step 4 候选 = **observation-plane + 服务接线**：events(durable)/sequencer/adapter + server/API
（submit→controller.execute(policy)、cancel→funnel、tasks 查询、events 查询、close）+ sweeper/startup hook。
**不修改 Step 1–3 语义**；Step 4 只是把已冻结能力接入真实服务路径并补观测层。

## 10. Non-goals（本 Step 明确不做）

Redis/Kafka/VectorDB/Event Sourcing；frontend 实现（仅定 API/WS 契约）；multi-instance ownership/
lease/heartbeat；research 语义/F3–F6；token hard budget；scheduler；新 Agent；全量 process 事件存储。

## 11. 应继续推迟的能力

- 全量 event replay UI 与“任意时刻现场重建”（无真实需求，成本高）；
- 事件驱动的 research 增量物化（F7 terminal-only 保持）；
- checkpoint 之上的多平面统一恢复编排（先让 TaskRecord/API 落地再谈）；
- 进程级 crash 的真实自动 startup hook（可在 sweeper Step 内做，非本步前置）。

## 12. Recommended Scope（若 Gate 通过）

1. `server.py`（+governance）：/api/task → controller.create_task + execute(policy)；cancel → controller
   cancel；GET /api/tasks[?thread]、GET /api/tasks/{id}（TaskRecord 详情）；governance store fail-closed。
2. governance `events`（单写者）：terminal/lifecycle 事件 durable 写 `governance_events`（sequencer 分配
   (task_id,seq)、event_id 幂等、live durable=false 预览、durability_gap）；controller funnel 成功后经
   hook 发布（不改 funnel 语义）。
3. adapter（最小）：monitor live 信封保持不变；仅新增“提交”把 canonical 事件送入 events 单写者。
4. WS 握手 `?since_seq=` + GET /api/threads/{id}/events?since_seq 回放（durable 升序）。
5. sweeper/startup：startup reconciliation（running→aborted，single-instance）+ 周期 pending flush
   reconciler（15s）复用现有 flush_pending。
6. 关联：TaskRecord.run_id = execution run_id（服务接线时写入）。

## 13. Gate Criteria（Implementation Gate）

- Step 1–3 + Phase A–F 回归全绿（sqlite 108 + PG 33）；
- 新增服务/事件/sequencer 测试：sqlite 逻辑 + 真实 PG（sequencer 并发串行化、(task_id,seq) 全序、
  event_id 幂等、replay catch-up、durability_gap、crash 后回放）；
- server 接线集成测试：正常/budget/timeout/cancel → TaskRecord + 事件 + WS 可恢复；
- ruff/format/compileall；scope audit 无越界；real-provider 仍可选（不伪造）。

## 14. Decision（审计建议）

- 四平面边界与 §0 审计证据下：**Step 1–3 内核 + PG Gate 已完整，值得继续的是"把内核接入真实服务路径
  并补 observation-plane"，而非追加 control 能力**。
- 建议：**CONDITIONAL —— 收敛范围继续**（§12 的 1–3 为第一实现批：server 接线 + durable lifecycle
  events + tasks API；4–5 紧随；6 关联键随接线落地）。事件 replay 全量 UI / 统一恢复编排明确推迟。
- 若产品优先级判定"仅快照即可"，最小可行替代：只做 §12.1（服务接线 + tasks 查询）并推迟 events，
  此时本文件降级为参考。
- **本阶段未实现任何代码；等待用户 Gate 裁决后再进入 Implementation。**
