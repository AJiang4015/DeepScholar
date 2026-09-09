# MULTI_SESSION_API_SPEC.md — Multi-Session Backend API Contract（Backend ↔ Frontend 契约）

> 本文档是 **Multi-Session Backend Capability** 的最终 API Contract，是后续 **Frontend Multi-Session
> Adaptation（独立 DSH 会话）实现的主要依据**。Frontend 不应自行推断或创造 Backend API。
> 文档与代码保持一致（实现：`app/api/server.py` + `app/session/{models,store,service}.py` +
> `db/governance_migrations/0002_sessions.*.sql`）；如发现不一致，以代码为准并回报。
> 生成日期：2026-10-03。Branch：`feature/multi-session-backend`。

---

## 1. Identity Contract（先读）

```text
Session.session_id
        ==
TaskRecord.thread_id
        ==
ResearchRun.thread_id
        ==
WS thread_id
        ==
checkpoint thread_id
```

- Session 与 Thread 为 **1:1 身份关系**；不得出现 `session_id != thread_id`，不得新增第二套
  Session/Thread 关联 ID。
- `TaskRecord.parent_session` 保持 `NULL / unused`，**不得复用**。
- id 字符集：沿用既有安全约束 `[A-Za-z0-9_-]{1,128}`（P004；服务端生成时为 uuid4().hex）。
- 归属（派生关联）：一个 Task 属于 Session X **当且仅当** `task.thread_id == X.session_id`；
  Research Run 同理（`research_runs.thread_id`）。

## 2. Session State Machine

只存在两态（无 CREATED / PAUSED / COMPLETED / FAILED —— 它们没有 Session 容器层面的真实
生命周期语义；Task / Runtime 执行状态由 GovernanceController 负责，见 §8）：

```text
ACTIVE ────(archive: DELETE /api/sessions/{session_id})────▶ ARCHIVED
   ▲                                                           │
   └───────(unarchive: PATCH /api/sessions/{session_id}        │
             body {status:"active"}) ◀─────────────────────────┘
```

| 操作 | ACTIVE | ARCHIVED |
|---|---|---|
| 创建 Task（POST …/tasks） | ✅ | ❌ `409` |
| 读取 Detail / Task 列表 | ✅ | ✅（历史可追溯） |
| Archive（DELETE） | ✅ | ✅ 幂等 no-op（`already_archived=true`） |
| Unarchive（PATCH status=active） | ✅ 幂等 no-op（`already_active=true`） | ✅ |
| 有 running Task 时 Archive | ❌ `409`（先取消；禁止 ARCHIVED + RUNNING） | — |
| 物理删除 | ❌ 永不（软删除；历史 Task / Run / 报告 / checkpoint 全保留） | ❌ |

## 3. Endpoint Overview（全部 additive；既有端点零改动）

```text
POST   /api/sessions                                        create session
GET    /api/sessions?include_archived=&limit=&offset=       list sessions（含聚合）
GET    /api/sessions/{session_id}                           session detail
DELETE /api/sessions/{session_id}                           archive（逻辑归档，幂等）
PATCH  /api/sessions/{session_id}                           restore / unarchive（幂等）
POST   /api/sessions/{session_id}/tasks                     create research task in session
GET    /api/sessions/{session_id}/tasks                     list tasks of session
POST   /api/sessions/{session_id}/tasks/{task_id}/cancel    cancel task（归属校验后）
```

现有端点（兼容，不受影响）：`POST /api/task`、`POST /api/task/{thread_id}/cancel`、`GET /api/tasks`、
`GET /api/tasks/{task_id}`、`GET /api/threads/{thread_id}/events`、`POST /api/upload`、
`GET /api/files`、`GET /api/download`、`WS /ws/{thread_id}`。旧客户端**无需注册 Session** 即可继续
使用 `POST /api/task`（旧行为完全保留）。

---

## 4. Endpoints

### 4.1 POST /api/sessions — 创建 Session

**Request（application/json）**

| Field | Type | Required | Default | Validation |
|---|---|---|---|---|
| title | string | no | `"未命名会话"` | trim 后 ≤ 200 字符；空 → 默认值 |
| description | string | no | `null` | ≤ 2000 字符 |
| thread_id | string | no | 服务端生成 uuid4().hex | 提供时必须匹配 `[A-Za-z0-9_-]{1,128}`；该 thread 未注册（注册既有历史 Thread）；已注册 → `409` |

语义：`session_id = thread_id`（提供或生成）；初始状态 `active`；`created_at = updated_at`（UTC ISO）。
不可出现 `session_id != thread_id`。

**Response 201**

```json
{
  "session_id": "7f9c…hex",
  "title": "机器人行业调研",
  "description": null,
  "status": "active",
  "created_at": "2026-10-03T00:00:00+00:00",
  "updated_at": "2026-10-03T00:00:00+00:00"
}
```

**Errors**：400（thread_id 字符集非法 / title·description 超长）；409（thread 已注册）；503（governance store 不可用）。

### 4.2 GET /api/sessions — Session 列表

**Query**

| Field | Type | Default | Description |
|---|---|---|---|
| include_archived | bool | `false` | `true` → 含 archived（Sidebar 默认只看 active） |
| limit | int | `100` | 1..500 |
| offset | int | `0` | ≥ 0 |

**Response 200**：排序 `updated_at DESC, session_id DESC`。

```json
{
  "sessions": [
    {
      "session_id": "7f9c…",
      "title": "机器人行业调研",
      "description": null,
      "status": "active",
      "created_at": "…",
      "updated_at": "…",
      "task_count": 2,
      "running_tasks": 1,
      "latest_task": {
        "task_id": "…",
        "run_id": "…",
        "status": "running",
        "terminal_reason": null,
        "created_at": "…",
        "started_at": "…",
        "finished_at": null
      }
    }
  ],
  "total": 1
}
```

> Sidebar 可直接消费 `sessions[]`（title / status / updated_at / running_tasks 徽标）。`latest_task`
> 缺省为最新一条 governance task（created_at DESC, task_id DESC）；无 task 时为 `null`。

### 4.3 GET /api/sessions/{session_id} — Session Detail

**Response 200** = Session 元数据 + Session Tasks + 最新执行信息（archived 仍可读）。

```json
{
  "session": {
    "session_id": "7f9c…",
    "title": "机器人行业调研",
    "description": null,
    "status": "active",
    "created_at": "…",
    "updated_at": "…"
  },
  "tasks": [
    {
      "task_id": "…",
      "thread_id": "7f9c…",
      "run_id": "…",
      "status": "completed",
      "terminal_reason": "completed",
      "error_kind": null,
      "error": null,
      "counters_snapshot": {},
      "policy_snapshot": {},
      "effective_limits": {},
      "created_at": "…",
      "started_at": "…",
      "finished_at": "…",
      "query": "机器人行业调研" 
    }
  ],
  "task_count": 1,
  "running_tasks": 0,
  "latest_task": { "task_id": "…", "run_id": "…", "status": "completed", "…": "…" }
}
```

- `tasks`：该 Session（thread）下 governance task 摘要，created_at 降序（上限 50，见 §4.5 limit）。
- `query`：**可选 enrich**（见 §7 兼容/局限）。字段存在；Research Store 不可用 / 无对应 run → `null`。
  Frontend 不得依赖 `query` 非空（fallback 可用自身输入历史）。
- **Errors**：404（session 不存在）。

### 4.4 DELETE /api/sessions/{session_id} — 逻辑归档

**Response 200**

```json
{ "session_id": "7f9c…", "status": "archived", "already_archived": false, "updated_at": "…" }
```

重复归档：`already_archived: true`（幂等，200）。**Errors**：404；409（会话存在 running Task ——
先 `POST …/tasks/{task_id}/cancel` 或等待完成；不允许 ARCHIVED + RUNNING 未定义态）。

### 4.5 PATCH /api/sessions/{session_id} — 恢复（Unarchive）

**Request**：`{"status": "active"}`（v1 仅支持 unarchive；其他值 → 400）。

**Response 200**

```json
{ "session_id": "7f9c…", "status": "active", "already_active": false, "updated_at": "…" }
```

重复恢复：`already_active: true`（幂等）。**Errors**：400；404。

### 4.6 POST /api/sessions/{session_id}/tasks — 在 Session 内创建研究任务

**Request**

| Field | Type | Required | Description |
|---|---|---|---|
| query | string | yes | 研究问题 |
| policy | object | no | governance policy（同 POST /api/task，见既有契约） |

语义：前置校验 Session 存在 + `active`（archived → 409）；内部 `thread_id = session_id`，
复用既有 governed 运行链路（TaskRecord + Controller.execute）；同 thread 单活跃收敛与
`POST /api/task` 一致（先取消该 thread 旧任务再启动新任务）。

**Response 201**（与 POST /api/task 相同结构）

```json
{ "status": "started", "thread_id": "7f9c…", "task_id": "…", "run_id": "…" }
```

**Errors**：404（session 不存在）；409（archived）；503。

### 4.7 GET /api/sessions/{session_id}/tasks — Session 任务列表

**Response 200**

```json
{
  "session_id": "7f9c…",
  "tasks": [ { "task_id": "…", "thread_id": "7f9c…", "run_id": "…", "status": "completed", "…": "…", "query": null } ]
}
```

只返回 `thread_id == session_id` 的 task（结构性隔离）。archived 仍可读。**Errors**：404。

### 4.8 POST /api/sessions/{session_id}/tasks/{task_id}/cancel — Session-scoped 取消

前置：Session 存在；`task.thread_id == session_id`（归属校验）。通过后走既有 governance 冻结
funnel（TaskRecord → cancelled + lifecycle event），并收敛 server 登记。

**Response 200**

```json
{ "status": "cancelled", "thread_id": "7f9c…", "task_id": "…", "already_terminal": false }
```

- 已终态任务：`already_terminal: true`（no-op）。
- **Errors**：404（session 不存在 / task 不存在 / **task 不属于该 session** —— 拒绝跨 Session 操作，不泄露存在性）；503。

### 4.9 WS / 上传 / 文件（复用，零改动）

- 前端连接 `WS /ws/{session_id}`（session_id 即合法 thread_id）；握手 `?task_id=&since_seq=` replay
  语义与既有契约一致。
- `POST /api/upload`、`GET /api/files`、`GET /api/download`：以 `session_id` 作为 `thread_id` 参数
  使用；P004 净化与 output 包含性校验不变。

---

## 5. Error Model（复用项目既有错误体系）

本项目无独立错误码枚举 —— 错误形态 = FastAPI `HTTPException` + `{"detail": "<message>"}`。

| 语义 | HTTP | detail 示例 |
|---|---|---|
| Session Not Found | 404 | `会话不存在: <id>` |
| Task Not Found / Task Not Belonging To Session | 404 | `任务不存在: <id>` / `任务不属于该会话: <id>（session=…）` |
| Invalid Session State（archived 建任务 / running 归档 / 重复注册） | 409 | `会话已归档（archived）…` / `会话存在运行中的任务…` / `thread 已注册为 Session…` |
| Invalid Request（charset / 长度 / PATCH status 非法 / 分页越界） | 400 | … |
| Persistence / Governance Unavailable | 503 | `governance store 不可用，拒绝受理（fail-closed）`（复用既有 `_require_governance`） |

## 6. Isolation Guarantees（Frontend 可依赖）

1. Session A 的 list/detail/tasks/cancel 端点**永远只返回/操作** `thread_id == session_id(A)` 的数据。
2. Session A 无法 cancel / 读取 Session B 的 Task（跨 Session 操作 → 404，B 不受影响）。
3. 并发：不同 Session 可同时 running（per-thread 独立 handle / 状态）；Cancel 只收敛目标 task。
4. Archive 不删除任何历史（Session 行、Task、Research Run、报告、checkpoint 全保留）。

## 7. Backward Compatibility & Known Limitations

- 既有端点（§3）语义、WS schema、monitor 事件、心跳协议全部不变 —— **无 Breaking Change**。
- `POST /api/task` **不要求** thread 已注册 Session（遗留客户端不受影响）；需要进 Sidebar 的会话，
  由前端先 `POST /api/sessions {thread_id: 既有thread}` 注册（幂等限制：重复注册 → 409）。
- **Resume**：当前产品无 Resume 入口（F8 checkpoint 数据面 + durable replay 存在，但 runtime
  recovery = F9 Batch9 deferred）。若未来实现 Resume，必须 task-scoped + session-scoped 校验。
- `query` enrich：依赖 Research Store（独立 store / 可 disabled）。不可用或跨 store 无对应 run →
  `null`。**不做跨库 JOIN**，不改变 Governance 核心可用性。
- 无认证/用户体系（P005 教学边界）—— 未来引入用户体系时，Session 查询需与用户上下文关联。

## 8. Future Frontend Consumption Map

| Frontend 需求 | 使用 |
|---|---|
| Session Sidebar / Session List | `GET /api/sessions`（`sessions[]`；title/status/running_tasks/updated_at） |
| Create Session | `POST /api/sessions {title?, description?}`；拿到 `session_id` 后作为 thread 使用 |
| Switch Session / Main Workspace | `GET /api/sessions/{id}`（detail → tasks + latest_task）；WS `/ws/{id}` |
| 在 Session 内发起研究 | `POST /api/sessions/{id}/tasks {query, policy?}`（响应 thread_id = session_id） |
| Session History | `GET /api/sessions/{id}/tasks` + `GET /api/threads/{id}/events`（durable replay） |
| Archive / Restore | `DELETE /api/sessions/{id}` / `PATCH /api/sessions/{id} {"status":"active"}` |
| Active vs Archived | `status: "active" | "archived"`；列表 `include_archived=true` 才含 archived |
| 上传 / 文件 | `/api/upload`、`/api/files`、`/api/download`（thread_id = session_id） |
