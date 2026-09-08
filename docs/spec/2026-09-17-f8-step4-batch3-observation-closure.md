# F8 Step 4 Batch 3 Spec — Runtime Observation Closure（Durable→Live + Live Identity）

> 状态：**SPEC / ARCHITECTURE REVIEW（未实现）**。仅分析 E2E 暴露的两个 Runtime Observation gaps
> 并给出可实施契约；不写实现代码。
> 冻结前提（引用，不重设计）：TaskRecord=durable lifecycle truth；task_id=lifecycle/durable/replay
> correlation；run_id=单次 execution correlation；governed 启动前 run_id 必绑（F1 已 PASS/FROZEN）；
> `(task_id, governance_seq)` durable cursor；Batch1/2 replay 语义；PG=production baseline；
> live WS envelope 现为 `{thread_id, run_id, …}`（无 task_id）。

## 1. Current Observation Architecture（代码事实）

- live 唯一来源：`run_deep_agent` 内部对 `monitor.report_*` 的调用（task_started 是首个 live 帧，
  发生在 try 内、research context 建立之后）；R3 envelope：`{type: monitor_event, event_id, run_id,
  thread_id, seq(monitor_seq), message, data}` → per-thread WS。
- durable：TaskRecord（funnel）+ governance_events（started/terminal；单写者 `(task_id, seq)`）；
  controller 终态由 `finalize_with_event` 发布 durable 事件；service.submit 发布 started。
- 两路互不相连：**没有任何代码把 governance 终态/durable 事件推送到 WS**；monitor 也不读
  governance（monitor 不写 DB）。
- reconnect/replay：WS 握手 `?task_id=&since_seq=` → durable 回放帧 → 转 live（Batch2）。

## 2. Exact gaps（含代码级证据）

- **Gap-1（B3-A 核心）**：governance terminal 先于 run 的首个 monitor 帧时，WS 零帧。
  证据路径：`service.submit`（写 durable started）→ `execute(policy)` 进入 ctx → 等待
  `run_deep_agent`。若在 run 的首个 `monitor.report_task_started` 之前发生——(a) research
  `create_run_and_root` 抛错（位于 try 之前）→ run_deep_agent 直接异常、无任何 live 帧；
  (b) 极短 `wall_clock_timeout` 下 watchdog 在首个 report 前收敛 → CancelledError 先于帧；
  (c) submit 后、coroutine 启动前 controller 侧 c2/校验失败 → 无 live 帧。TaskRecord/durable
  均正确，但前端（仅靠 live）看不到任何状态变化。
- **Gap-2（B3-B 核心）**：同 thread 多 task/run 交错 + live 无 task_id。旧 run 的迟到终态帧
  （旧 run_id）与新 run 的 task_started（新 run_id）可在同一 `/ws/{thread}` 交错；冷启动
  （刷新/重连、未持有 submit 响应）时客户端无法把 run_id 帧归属到具体 TaskRecord（run_id→task
  只存在于 TaskRecord 行，需额外 API 查询）；`task_started.run_id` anchor 依赖前置 submit 响应。

## 3. B3-A：Durable Lifecycle → Live Observation Closure（option 分析）

| 维度 | A：governance terminal live bridge | B：frontend status-sync/polling | C：live 加 task_id | D：A+C |
|---|---|---|---|---|
| lifecycle correctness | TaskRecord 仍唯一 truth；bridge 只做“告知” | 依赖前端轮询 TaskRecord（后端仍 truth） | 不改 truth；只增强关联 | 最强（truth 不变 + 主动告知 + 可关联） |
| reconnect | bridge 帧无持久语义；replay(Batch2) 仍权威 | 轮询天然覆盖重连 | 配合 replay 关联 | 同 A+Batch2 |
| replay | 不替代 durable replay（bridge=提示） | 无关 | 增强 replay/live 统一身份 | 一致身份 |
| duplicate events | bridge 需 event_id 幂等（可重复投递） | 轮询天然可重 | 无新增重复源 | bridge 按 event_id 幂等 |
| ordering | 不承诺与 live 全序（只保证“终态可达”） | 与 live 独立 | 无关 | 客户端以 durable seq/event_id 收敛 |
| run/task identity | bridge 带 task_id/run_id | 以 TaskRecord/API 为准 | task_id 进帧 | 统一 thread+task+run |
| fail-open/fail-closed | bridge 失败=observation fail-open（不阻断 control） | 前端轮询失败独立 | 无控制面影响 | 全部 fail-open |
| frontend complexity | 低（加一个事件处理） | 高（轮询/状态机/去抖） | 低（读字段/过滤） | 低-中 |
| future multi-instance | 未来 WS 归属需演进（bridge 事件源变化） | 最稳（后端 API 单源） | 不受影响 | 需同步演进 |

职责边界（source of truth）：**terminal visibility 的权威层 = TaskRecord + durable event（后端，
已有）；bridge 只解决“连接中的客户端何时被通知”，不改变 truth；frontend status-sync 是“通知
丢失后的兜底”，属可选增强**。推荐主路径 A（+C 身份），B 保留为 Batch3 之后的 fallback feature。

## 4. B3-B：Live Event Identity Strengthening

1. 仅靠 run_id 无法正确关联的场景：客户端未持有 submit 响应（刷新后冷启动）下的 in-flight live；
   同 thread 新旧 run 迟到帧归属；多 tab 各自锚定不同 run。
2. task_id 应作为 live event 的 **additive correlation field**（可选、可为空）。
3. 若加 task_id：
   - R3 破坏？**不破坏**：只新增可选键（additive），旧客户端忽略；
   - 兼容旧 client：缺省/空 task_id 时行为同现在；
   - monitor 如何取得：run_deep_agent 从 governance ctx 读取 `task_id`（F1 已保证 ctx 存在且
     与 TaskRecord 同源），bare 执行无 ctx → task_id=null；
   - replay/live 统一 contract：两侧都以 `thread_id+task_id+run_id` 描述事件；durable 侧重
     `(task_id, governance_seq)`，live 侧重即时性。
4. 正式粒度契约：
   - `thread_id`：会话/UI 分组（可承载多 task 历史）；
   - `task_id`：生命周期/durable 主键（一次“提交-终态”的唯一对象）；
   - `run_id`：单次执行（research/monitor 相关性；governed 下 == TaskRecord.run_id，F1）。
   约束：task 唯一绑定一个 thread 与一个 run_id；run_id 跨 task 唯一（submit 每次生成）。

## 5. Recommended architecture

- **Source of truth 不变**：TaskRecord/durable event 权威；live 仅是通知通道。
- **D = A + C（最小）**：
  - C：monitor 事件信封 additive 加可选 `task_id`（来自 governance ctx；bare→null），
    run_deep_agent 的 monitor 调用前设置 task context；
  - A：governed 终态（funnel 后，即 `finalize_with_event` 成功/内存裁决后）发布一条
    **terminal live bridge 帧**（type=governance_terminal；含 thread_id/task_id/run_id/
    status/terminal_reason + event_id），经现有 manager 按 thread 推送（fail-open）；
    该帧只读通知，不替代 durable event/replay。
- **不做**：B polling/status-sync（Batch3 之后候选）；任何 generic bus/multi-instance/heartbeat。
- bridge 实现归属：controller 侧事件发布（与 durable event 同点，观察层）→ 经一个可注入的
  live sink（server 注册 manager）→ 不复制 monitor seq、不加 governance_seq 到 R3 envelope
  （bridge 用独立 type，客户端以 event_id/task_id 收敛）。

## 6. Exact API / WS contract changes（草案，待 Gate）

- R3 envelope additive：`task_id?: str|null`（保留 thread_id/run_id/event_id/…；monitor_seq 不动）。
- 新 WS 帧（controller→manager→thread）：`{"type":"governance_terminal","event_id",
  "task_id","run_id","thread_id","status","terminal_reason","error_kind"?,"message"}`。
- replay/API：不变（Batch2 冻结）；bridge 帧不回放（durable 回放仍走 /events 与握手 replay）。
- 兼容：旧客户端忽略新键/新 type；新客户端以此触发状态刷新并可回源 /api/tasks/{task_id} 校对。

## 7. Event ordering / idempotency semantics

- durable ordering = `(task_id, governance_seq)`（冻结）；bridge/live 不参与 durable 顺序；
- duplicate：bridge 帧可重（网络/断线重连以握手 replay + event_id 去重收敛）；
  客户端以 event_id 去重；TaskRecord 终态不可重复（funnel 冻结）。

## 8. Reconnect / replay semantics

- 断线：重连握手 since_seq（Batch2）回放 durable；bridge 帧丢失无妨（durable 为准）；
- 冷启动：tasks API 列出任务 → 每 task replay 或当前活跃 task 握手；
- terminal visibility 兜底链：live/bridge 提示 → 必要时 `GET /api/tasks/{task_id}`（真相校对）。

## 9. Failure & fail-open

- bridge 推送失败：log + 不回滚（observation fail-open）；TaskRecord 终态不受影响；
- 无 active 连接：bridge 直接丢弃（可观测性降级，不重试/不落库——durable 已落）；
- monitor/manager 故障：同现状（不影响 control）。

## 10. Backward compatibility

- envelope additive（task_id 可选）与新增独立 type 帧均向后兼容；旧前端零改动可运行；
- bare（非 governed）执行不产生 bridge 帧、task_id=null，语义同现状。

## 11. Test / PG Gate plan

- SQLite：bridge 帧生成条件（governed 终态五态+bare 不产生）；task_id 注入（ctx 有/无）；
  envelope additive 字段；event_id 幂等去重；fail-open（sink 抛错不阻断）。
- PG Gate：governed run_deep_agent 终态后 bridge 帧存在性（service 层替身）+ durable event 一致；
  冷启动 tasks→per-task replay 与 bridge 结果可对齐；F1 全链 run_id 不回退。
- E2E（凭据环境）：pre-monitor 早期失败场景（research 抛错/极短 timeout）仍能收到 bridge 终态帧。

## 12. Explicit non-goals（Batch 3）

Redis/Kafka/generic event bus；multi-instance/lease/heartbeat；Neo4j/vector DB；research 智能；
大规模 frontend 重构；polling/status-sync 产品化（B 仅记录为后续候选）；新 Agent；F9/F10 类能力。

## 13. Open Questions

1. bridge 帧是否需要带 counters_snapshot/error 全文？（建议只带轻量 status+terminal_reason+error_kind，
   详情回源 /api/tasks/{task_id}）
2. task_id additive 由 monitor 新 context（set_task_context）注入 vs run_deep_agent 直接传参——
   倾向 ctx 注入（bare 兼容、F1 同源）；待实现层裁定。
3. bridge 是否也用于「task_started/运行中」治理事件（如预算预警）？本 Batch 仅终态，避免扩大。

## 14. Final recommendation

**CONDITIONAL（PASS 方向明确，待 Gate 后按 D=A+C 最小实现）**
- B3-A 推荐 **A（governance terminal live bridge，fail-open、独立 type、不回放）**；
  B（polling/status-sync）不纳入本批，仅记录为后端 truth 之上的可选兜底；
- B3-B 推荐 **C（live envelope additive `task_id`）** 并冻结粒度契约
  `thread_id(会话/UI) + task_id(lifecycle/durable) + run_id(execution, F1 必绑一致)`；
- 任何 live/bridge 帧不改变 TaskRecord truth、不参与 durable ordering、不复制 monitor_seq。

Spec Review 完成；未实现任何代码；等待 Gate 后进入 Batch 3 Implementation。
