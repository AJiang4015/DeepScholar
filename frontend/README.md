# DeepSearch Agents Frontend

React + Vite + Tailwind CSS + Ant Design frontend for the DeepSearch Agents FastAPI backend.

## Run

```bash
pnpm install
pnpm dev
```

By default the app talks to `http://localhost:8000` and `ws://localhost:8000`.
Override with `.env.local`:

```bash
VITE_API_BASE_URL=http://localhost:8000
VITE_WS_BASE_URL=ws://localhost:8000
```

## Multi-Session Architecture

前端是 **Multi-Session UI**：一个页面内可管理多个 Session（`session_id == thread_id` 1:1）。

```text
App（会话集合层）
 ├─ sessions[] / currentSessionId / turnsBySession（browser-lifetime 内存缓存）
 ├─ create / switch / archive / restore session
 └─ SessionWorkspace key={currentSessionId}（runtime boundary）
      ├─ useDeepAgentSession(sessionId)   ← WebSocket /ws/{sessionId}、task、files、runtime state
      ├─ ConversationThread               ← 当前 Session 对话流（视觉不变）
      ├─ ChatComposer                     ← 提交/取消/上传全部 session-scoped
      └─ TaskHistory                      ← durable task 摘要（历史视图）
```

职责边界：

- **App**：session 集合 / currentSessionId / turnsBySession 内存缓存 / 会话级操作；**不持有** task/WS/runtime state。
- **SessionWorkspace**：以 `key={currentSessionId}` 重挂载 —— 切换 Session 即卸载旧 workspace（关 WS、
  清 timer/polling），A 的 runtime state 不泄漏到 B。
- **useDeepAgentSession(sessionId)**：单 Session runtime；任务创建/取消走
  `POST /api/sessions/{session_id}/tasks` 与 session-scoped cancel；WS 心跳/重连/run_id 过滤保留。

## Important State Semantics

- **turnsBySession 只是内存 cache**：`sessionId → ChatTurn[]`，仅用于同一页面内切换回来还原对话。
  它**不是** durable transcript —— 服务端当前不持久化完整对话消息。
- 页面刷新后可以恢复：`currentSessionId`（localStorage `deepsearch.thread_id`）、Session metadata、
  Task history（durable）、Task lifecycle 状态、缓存过的产物路径（`deepsearch.session_path.<sessionId>`）。
- **不要**声称刷新后能恢复完整聊天 transcript / 历史 WebSocket event / 历史 final answer 文本
  （Backend 未持久化；实现遵循 `docs/frontend/BACKEND_FRONTEND_ADAPTATION_REPORT.md` 的既定边界）。
- 刷新后若存在 running task：恢复 durable lifecycle 状态（可取消），并接收之后的新 WS live events；
  不伪造 run_id、不做 transcript replay。
- archived Session：可查看历史 Task 与产物；Composer 禁用；不能创建 Task（Backend 409 兜底）。

## Backend Contract

- `POST /api/sessions` / `GET /api/sessions` / `GET /api/sessions/{id}`
- `DELETE /api/sessions/{id}`（归档）/ `PATCH /api/sessions/{id}`（恢复）
- `POST /api/sessions/{id}/tasks` / `GET /api/sessions/{id}/tasks`
- `POST /api/sessions/{id}/tasks/{task_id}/cancel`
- `POST /api/upload`、`GET /api/files`、`GET /api/download`、`WebSocket /ws/{thread_id}`
  （thread_id = session_id）

契约唯一事实来源：仓库根 `MULTI_SESSION_API_SPEC.md`。
