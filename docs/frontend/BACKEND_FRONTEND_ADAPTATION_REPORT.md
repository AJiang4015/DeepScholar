# Backend → Frontend Adaptation Report（DeepScholar）

> 依据仓库实际代码完成的一次性「后端能力演进 → 前端适配」闭环记录。
> 方法：先全量盘点后端（`app/api` / `app/runtime/governance` / `app/runtime/checkpoint` /
> `app/research` / `app/tools` / `app/agent`，辅以测试与 spec 对照），再盘点前端激活链路
> （`App.tsx` → `ConversationThread` / `ChatComposer` / `useDeepAgentSession` / `lib`），
> 产出能力映射表后**只改前端 5 个源文件**；后端零改动。
> 视觉约束：以 `DESIGN_SYSTEM.md` / `UI_PATTERNS.md` / `FRONTEND_CONSISTENCY_RULES.md`
> 与当前激活页为唯一基准，**未引入任何新颜色/字体/圆角/阴影/框架，未改 CSS，未启用 legacy console**。

---

## 1. Backend 实际发现的能力（以代码为准）

### 1.1 HTTP API 面（`app/api/server.py`）

| 端点 | 说明 | 证据 |
| --- | --- | --- |
| `POST /api/task` | 受 governance 接管：创建 TaskRecord + Controller.execute；响应现含 `task_id/run_id`；请求体可带 `policy` | server.py:152-191, 124-131 |
| `POST /api/task/{thread_id}/cancel` | 治理取消（冻结 funnel），响应含 `task_id/already_terminal`；旧路径保留 | server.py:194-243 |
| `GET /api/tasks` | 任务列表（created_at 降序，可选 thread 过滤） | server.py:246-251 |
| `GET /api/tasks/{task_id}` | 任务详情：`status/terminal_reason/error_kind/error/counters_snapshot/policy_snapshot/effective_limits/时间` | server.py:254-261；service.py:90-109 |
| `GET /api/threads/{thread_id}/events` | durable 事件回放：cursor=(task_id, governance_seq)，`since_seq` 升序 + `next_seq + gap` | server.py:286-306；governance/events.py:178-211 |
| `POST /api/upload` | 白名单 6 类扩展名 + 20MB；净化文件名 + 随机存储名 + manifest | server.py:309+；upload_guard.py:30-36,51-76,114-167 |
| `GET /api/files` / `GET /api/download` | 仅服务 `output/` 目录；错误以 HTTP 200+`{"error":...}` 返回 | server.py:381-459, 107-108 |
| `WS /ws/{thread_id}` | 握手可选 `task_id`/`since_seq` → 先发 `governance_replay`/`governance_replay_end`/`governance_error` 帧再转 live；心跳为文本 ping/pong | server.py:467-551 |

### 1.2 WebSocket / 事件面（`app/api/monitor.py`）

- **live monitor_event 信封（R3）**：`{type, event, event_id, run_id, thread_id, seq, timestamp, message, data}`；
  seq 为进程内单调分配序，per-thread 队列保证 WS 到达顺序 = enqueue 顺序（monitor.py:136-146, 226-329）。
- **live 事件 7 种**：`task_started` / `session_created` / `assistant_call` / `tool_start` /
  `task_result` / `task_cancelled` / `error`；终态 `{task_result, task_cancelled, error}` 每 run 至多一次
  （RunTerminalGuard，monitor.py:74, 79-90）；发出点 main_agent.py:193-260；tool_start 由 8 个业务工具自埋。
- **事件不持久化**：断线期间 enqueue 直接丢弃（monitor.py:289-291），**绝不补发**。

### 1.3 Governance durable 面（`app/runtime/governance/*`）

- **TaskStatus 9 态**：running + completed/failed/cancelled/timed_out/budget_exceeded/superseded/aborted/orphan_reclaimed
  （models.py:15-40）；`TaskRecord` 全字段 models.py:75-147。
- **durable 事件**：只写 lifecycle 标记 `task_started/task_completed/task_failed/task_cancelled/task_timed_out/
  task_budget_exceeded(+schema 预留)`，payload = `{status, terminal_reason?, error_kind?, error?(≤800),
  counters_snapshot?, finished_at?}`（events.py:29-39, 69-81）；event_id 跨平面唯一，可用于去重。
- **关键现象**：timeout/budget/abort/superseded 的**真实原因只在 durable 面**；live 面 run 只见
  CancelledError/Exception，可能发 `task_cancelled`/`error` → 前端若只看 live 会把「超时/预算」误显示成「已取消」。

### 1.4 Research / 内部面（`app/research/*`）

- claims/citations/verification/conflict/corroboration/reconciliation 全部落
  `app/runtime/research.sqlite`，**零 HTTP/WS 暴露**（app/api 中无引用）；运行期 F2–F6 实际为空转
  （finalize_run 缺省 extractor=None、stage 全 disabled，bridge.py:303-320, main_agent.py:241-243）。
- checkpoint / research / governance 内部计数与审计列为纯内部实现。

## 2. Frontend 当前支持情况（改动前）

| 面 | 状态 |
| --- | --- |
| `POST /api/task`、cancel、upload、files、download | ✅ 已接（lib/api.ts:26-69） |
| WS `monitor_event` + `pong` | ✅ 已消费（hook） |
| live 事件 → timeline → 状态机（running/success/error/cancel） | ✅ 已具备（ConversationThread/App） |
| 信封字段 `event_id/run_id/thread_id/seq` | ❌ types 未声明 |
| `task_started` 事件 | ❌ 无专属类型/图标语义 |
| 同 thread 多 run 隔离 | ❌ 无（可能混入旧 run 迟到事件） |
| `/api/tasks/{task_id}`、`/api/tasks`、`/events`、WS `since_seq` 回放帧 | ❌ 零调用/零解析 |
| 治理终态（timed_out/budget_exceeded/aborted/superseded…）呈现 | ❌ 无（断线窗口内结束 → UI 卡「研搜中」或误判「已取消」） |
| legacy console 组件 | 确认未被 `App.tsx` import（仅自引用），本轮未启用 |

## 3. Gap Analysis（确认为需要补齐）

| Backend Capability | Backend Source | Existing FE | Missing UI/Interaction | Priority | 处置 |
| --- | --- | --- | --- | --- | --- |
| governed task 身份 `task_id/run_id` | server.py:186-191 | ✗ 丢弃 | hook 需要用于终态校正与 run 隔离 | 高 | 实施 |
| durable 终态语义（区分完成/失败/取消/超时/预算/取代/中止） | events.py:29-39、models.py:15-40 | ✗ 无 | 断线后任务若在窗口内结束：UI 需真实终态并停止“研搜中” | 高 | 实施（重连后查 `/api/tasks/{task_id}` 校正） |
| live 信封（seq/run_id/event_id） | monitor.py:136-146 | ✗ 未解析 | run 级隔离防旧事件污染；event_id 预留去重 | 中 | 实施（run_id 过滤） |
| `task_started` 生命周期起点 | monitor.py:193-197 | ✗ | timeline 专属图标语义 | 低 | 实施（图标映射） |
| cancel 响应扩展字段 | server.py:220-225 | ✗ | 类型一致性 | 低 | 实施（类型） |
| `/api/tasks` 列表 / durable replay REST | server.py:246-306 | ✗ | 单线程对话页无任务列表形态；monitor 轨迹本身不持久化，回放仅 lifecycle | 中 | 暂不做（Remaining Gaps） |
| WS `since_seq` 握手回放帧 | server.py:492-531 | ✗ | 同上；且用 REST 校正即可满足用户状态 | 低 | 暂不做 |
| counters / 预算明细 | service.py:90-109 | ✗ | 属成本/运维信号；结束原因文案已覆盖用户需要 | 低 | 暂不做 |
| research（claims/校验/冲突/调和） | app/research/* | ✗ | 生产链路空转 + 无 API 暴露；需后端先行 | 高（未来） | 暂不做（Remaining Gaps） |

## 4. Implemented（实际修改，均只读前端 5 个源文件）

| 文件 | 改动 | 为什么 |
| --- | --- | --- |
| `frontend/src/types.ts` | ① `MonitorEventName` 增加 `task_started`；② `MonitorMessage` 增加信封字段 `event_id/run_id/thread_id/seq`；③ `TaskResponse` 增加 `task_id/run_id`；④ `CancelTaskResponse` 增加 `task_id/already_terminal`；⑤ 新增 `TaskInfo` 类型 + `TERMINAL_TASK_STATUSES`/`isTerminalTaskStatus` | 对齐后端 schema（monitor.py:136-146、server.py:186-191/220-225、service.py:90-109），为 hook 状态机提供类型基础 |
| `frontend/src/lib/api.ts` | 新增 `getTask(taskId)` → `GET /api/tasks/{task_id}` | 重连后取治理终态的唯一通道（server.py:254-261） |
| `frontend/src/hooks/useDeepAgentSession.ts` | ① 记录 `task_id`（taskId 状态 + ref）与 `run_id`；② WS 消息按 run_id 过滤同 thread 旧 run 迟到事件；③ 断线重连（reconnecting→connected）后若 run 未收尾，调 `getTask` 校正：终态 → 停 running/cancelling、写 `taskStatus`，failed 且带 error → 走既有 `lastError` Alert 通道；④ reset/submit 时清空相关状态 | 修复「断线窗口内任务结束 → UI 永久研搜中 / 超时误判已取消」；避免旧 run 事件污染新 run |
| `frontend/src/App.tsx` | `createTurn` 增加 `taskStatus: null`；session 同步 effect 把 `session.taskStatus`（status/error/terminalReason）随 last-turn 回填 | 让每条 turn 携带治理终态供渲染层展示 |
| `frontend/src/components/ConversationThread.tsx` | ① `ChatTurn` 增加可选 `taskStatus`（`TurnTaskStatus`）；② `EventIcon` 映射 `task_started → PlayCircleOutlined`；③ AssistantMessage 在「无 result 且非运行且 taskStatus 终态」时：meta 右侧显示真实结束标签（如 `预算超限 · 用时 00:51`），消息区内用 `endedNoticeText` 给出人话说明（区分完成/失败/取消/超时/预算/取代/中止） | 把 durable 语义转成用户可读的状态文案，完全复用现有气泡/时间线结构 |

## 5. UI Mapping（后端能力 → 前端 UI）

| 后端能力 | 映射到前端 |
| --- | --- |
| `task_started` | Thinking timeline 条目（PlayCircle 图标 + 「任务开始执行」，默认 cyan 图标井样式） |
| live 事件信封（seq/run_id） | 内部：run 级过滤（hook）；无直接 UI 暴露 |
| governed task `task_id` | 内部：重连校正的查询键（hook）；不发散到 UI 文案 |
| 治理终态（正常完成） | 既有 live `task_result` → markdown 答案 + artifact（不变） |
| 治理终态（用户取消） | 既有 cancel 流程（不变） |
| 治理终态（**断线窗口内** 完成/失败/超时/预算/取代/中止/回收） | 消息 meta 右标签 + 无结果区的结束说明文案；failed 同时走顶部 error Alert |
| artifact 文件 | 既有轮询 `/api/files` + ArtifactShelf（不变，已闭环） |

## 6. WebSocket / Event Flow（改动后）

```
Backend (governed run)
  ├─ live: monitor_event{event_id,run_id,thread_id,seq,...}  → WS /ws/{thread_id}
  │     hook.onmessage: run_id 与当前 run 不符 → 丢弃（旧 run 隔离）
  │                     匹配 → events 追加(截断 120) → task_started/session_created/
  │                     assistant_call/tool_start 推进 timeline；task_result/task_cancelled/error → 终态
  └─ durable: governance_events(task 级) + GET /api/tasks/{task_id}
        ↑ 断线窗口内终态丢失（live 不持久化）时：
  hook: WS close → reconnecting → 重连成功(connected)
        → 若 run 仍为未收尾(无 live 终态) → getTask(task_id)
        → 终态 → setTaskStatus + 停 running/cancelling（failed 另走 lastError）
        → App 同步到 last turn → ConversationThread 显示结束标签/说明文案
```

顺序/一致性结论：live 平面按 seq/enqueue 有序、终态至多一次（服务端守卫）；durable 平面同 task 内无洞
（单写者 + UNIQUE(task_id,seq)），event_id 跨平面唯一可去重；monitor 轨迹**不持久化** → 断线窗口内的
工具/助手明细无法补全（本轮以「任务级终态」作为可恢复真相，粒度明细不做）。

## 7. Validation

- `pnpm build`（= `tsc -b && vite build`）：**通过**（3260 modules，built in 28s；首跑 vite/esbuild 被沙箱
  EPERM 拦截，full-access 重跑通过）。
- API/WS 类型一致性：types.ts ↔ monitor.py/server.py/service.py 逐字段核对；`TaskInfo` 字段与
  service.py:90-109 的 task_dict 白名单一致。
- task lifecycle 状态覆盖：completed/failed/cancelled/timed_out/budget_exceeded/superseded/aborted/
  orphan_reclaimed 均有人话标签与说明文案；loading/running/success/error/cancelled 沿用既有链路。
- responsive / a11y：无布局改动、无新交互控件、无新颜色/动画/CSS；新增文案为纯文本（复用 pending 区与
  meta 行），不破坏 900px 列、fixed composer、980/560 断点。
- legacy console：未被引用/未启用（git diff 确认 App.tsx 未引入任何 console 组件）。
- 变更范围：git diff 仅 `frontend/src/{types,lib/api,hooks/useDeepAgentSession,App,components/ConversationThread}`
  5 文件（+243/-6）；无大规模 UI 重构；后端零改动。

## 8. Remaining Gaps（暂不做及原因）

| 能力 | 前端暂无体现 | 原因 |
| --- | --- | --- |
| 任务列表 / 历史任务恢复（`/api/tasks`、`/events`、WS since_seq 回放） | 无 | 当前产品是单线程对话页，无任务列表形态；页面刷新后 turn 为客户端内存态，后端无“按 thread 重建对话”契约。工具/助手轨迹不持久化，回放只能给 lifecycle 标记；如需跨会话历史，属产品级新功能（先立 Decision）。 |
| 预算/计数明细（counters_snapshot / effective_limits） | 无 | 属成本与运维信号；结束文案（如「预算超限」）已覆盖用户需要的“为什么停”。若后续要 live 预算进度，应在 governance callbacks 加 observation 事件 + 后端小改，再接入 UI。 |
| research/claims/校验/冲突/调和 | 无 | 生产链路 F2–F6 空转且零 HTTP 暴露（纯内部 SQLite）；先在 main_agent.finalize_run 接通受控 extractor 并开放只读 research API，前端才能展示“来源清单/断言校验”等。 |
| download/files 200+error 直链陷阱 | 保持现状 | `/api/files`、`/api/download` 错误以 200+`{"error"}` 返回；产物行仍用直链下载。低概率且无破坏性，属后续小优化。 |
| event 截断 MAX_EVENTS=120 | 保持现状 | 长轨迹只保留窗口内最近事件（含计数徽标总览），设计权衡；如需完整轨迹应由 durable 面提供。 |

---

**结论**：本轮优先接通了「后端已实现、前端缺失、且对用户有真实价值」的三件事：任务终态语义（含
断线/超时/预算等真实原因）、live 事件信封与 run 隔离、task 生命周期起点展示；全部落在现有 Chat
Workspace 内，未新增页面/区域/颜色/字体/动画，后端与 CSS 零改动。
