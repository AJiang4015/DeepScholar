# Multi-Session Backend Capability — Architecture Analysis & Implementation Plan（架构分析与实施计划）

> 阶段：**Architecture Analysis + Implementation Planning only**（用户指令：本阶段不修改任何代码）。
> Feature 等级：**L3**（涉及 DB schema / migration、公共 HTTP API、identity 相邻语义、并发与隔离——按
> PROCESS.md §11.1 判级；**Highest applicable level wins**）。本文档 = Readiness / Analysis + Plan 产物，
> 按 PROCESS.md §12.1 允许停留在 `main`（文档提交，不代表 Implementation 开始）。
> Implementation 阶段必须：创建 dedicated feature branch `feature/multi-session-backend` → Readiness
> Review（契约闭合）→ Decision Closure → Spec / Plan Review → 实现 → 验证 → User Freeze → 逐步批准
> Commit/Push/Merge（AGENTS.md §10 / PROCESS.md §12）。
> 本文档所有结论均基于对当前仓库 `main`（`113b6e8`，D018 semantic-module-layout merged 后）的实际代码
> 阅读，引用到文件与行号。未做任何代码修改。

---

## 0. Executive Summary

当前仓库**没有 Session 领域对象**。「session」一词今天指两件不同的事：

1. **thread_id / session_id**：前端会话键（`app/utils/session_id.py` 净化 `[A-Za-z0-9_-]{1,128}`），
   同时承担 **WS 路由键、checkpoint 线程键、output/updated 目录键、TaskRecord.thread_id、
   research_runs.thread_id、monitor 定向推送键** —— 是贯穿全链路的"会话/对话"身份；
2. F8 TaskRecord 上的 `parent_session` 预留列：**从未被写入**，F8 Plan 明言"无独立 Session Registry，
   P0-4 deferred"（`docs/plan/2026-09-14-f8-implementation-plan.md` §2 line 69/76）。

分析结论（详细论证见 §3）：

- **Session 应落在 Task 之上、API 之下，作为"持久化的对话容器"业务域**：它组织多个 Task
  （一个会话内的连续研究任务）与 Research Runs，不拥有 Runtime lifecycle 权威（后者仍只属 F8
  Controller）。
- **不需要新增与现有架构冲突的 SessionManager**：当前"任务管理"由 `app/api/server.py`（薄调度层：
  `active_tasks`/`_task_registry` 内存登记）+ `app/runtime/governance/service.py`（submit/list/cancel 助手）
  + `GovernanceController`（durable 权威）三者协同表达。Session 之上需要的只是一组**新的薄服务函数 +
  元数据持久化 + 只读聚合**，复用 governance store 连接与 controller。
- **Session 与 Task 的关联机制应复用 `thread_id`，而不是新增重复 ID 字段**：TaskRecord 与
  ResearchRun 都已按 thread_id 归属。把 Session 设计为"服务端签发的会话容器，其 `session_id`
  即该会话使用的 thread_id（1 会话 : 1 线程身份）"，则 Session → Task → Run 三层归属天然成立，
  隔离（A 不能读 B 的 Task）由 thread 作用域既有查询天然保证。
- **Session 元数据需要持久化**：新增 `sessions` 表（ACTIVE/ARCHIVED 两态），落在 governance
  迁移族（additive `0002_sessions.{sqlite,postgres}.sql`），复用 governance store 的连接/后端选择
  与 migration runner —— 无新 DB、无新 env、无新依赖。
- **Runtime 不需要任何核心改动**：Task 创建仍走 `gov_service.submit_task`（thread_id = session_id）；
  取消/并发/终态语义原样继承；Resume 产品能力当前不存在（F9 Batch9 deferred），本 Task 不新增。

最终交付（Implementation 阶段）：`MULTI_SESSION_API_SPEC.md`（API Contract，Frontend 后续会话唯一依据）
+ `MULTI_SESSION_IMPLEMENTATION_REPORT.md`（实现报告）。

---

## 1. INTAKE（问题陈述 / 验收标准 / 判级）

### 1.1 一句话问题陈述

在"单会话单研究流"现状之上，引入 **Session 作为 Task 上层的业务上下文边界**（可持久化、可归档、
可隔离、可并发的多会话容器），并形成稳定的 Backend API Contract，供后续独立 Frontend DSH 会话消费；
Frontend 不在本 Task 修改范围内。

### 1.2 验收标准（来自用户 Task §24，映射后保留）

| # | 验收标准 | 实现阶段验证载体 |
|---|---|---|
| AC1 | Session 可创建 / 查询 / 详情 / 逻辑归档；生命周期有明确状态定义 | sessions service + store 测试；API Contract |
| AC2 | Task 可关联 Session；Session 可取 Task；Task 可定所属 Session | 关联机制（§5.3）测试 |
| AC3 | Session A 无法访问/操作 Session B 的 Task；Cancel 不跨 Session | isolation 测试 |
| AC4 | 多 Session 可同时存在、同时运行；Runtime state 独立 | concurrent 测试（submit × 2 + cancel 1） |
| AC5 | Session 可持久化；重启不丢；Archive 不物理删除历史 | sqlite store 持久化测试 + PG gate |
| AC6 | API Contract 完整；Request/Response/Error/State 明确 | MULTI_SESSION_API_SPEC.md |
| AC7 | Existing Backend Tests 全过 + Multi-Session Tests 全过；无 Frontend 修改 | 回归 sqlite 全量（731 passed baseline）+ 新增套件 |
| AC8 | 不破坏 Harness / Agent Runtime / Research Domain；无无关重构 | git diff scope audit + 架构红线检查 |

### 1.3 判级与工作流选择

- **L3**（PROCESS.md §11.1）：DB schema/migration（新表）、公共 HTTP API（新端点集合）、
  identity 相邻语义（session_id ↔ thread_id 设计决策）、并发/隔离语义。
- 本阶段（本文档）：Analysis / Plan（read-only，允许在 `main`）。
- 下阶段（另立 DSH 会话或用户批准后）：`feature/multi-session-backend` branch 上按
  L2/L3 全流程实施（Readiness → Decision → Spec → Implementation → Tests → Report → User Freeze）。

### 1.4 PROBLEM.md 命中情况

未命中需要处理的既有问题（P001–P005 均为 Mitigated/Won't Fix；P006 Open 为测试基础设施遗留，
本 Task 沿用既有 pytest 约定，不新增问题）。P004（thread_id 净化）直接约束 Session id 设计：
session_id 必须落在既有安全字符集内。

---

## 2. Current Architecture Analysis（现状分析，证据清单）

> 本阶段阅读的文件（证据清单）：AGENTS.md / PROCESS.md / PROJECT_CONTEXT.md / PROBLEM.md /
> ARCHITECTURE.md / TESTING.md / DECISION.md（D001–D018）、`app/api/server.py`、`app/api/context.py`、
> `app/api/monitor.py`（要点）、`app/agent/main_agent.py`、`app/runtime/governance/{controller,models,
> service,store,events,migrations}.py`、`db/governance_migrations/0001_*.sql`、
> `app/research/{config,schemas,store,migrations,registry}.py`、`db/migrations/0001_*.sql`、
> `app/utils/session_id.py`、`tests/conftest.py`、`tests/test_runtime_governance*.py`、
> `tests/test_research_postgres.py`、`frontend/src/lib/{api,thread}.ts`、
> `docs/plan/2026-09-14-f8-implementation-plan.md`（session 语义预留）、`docs/plan/2026-10-01-f9-p0-final-completion-report.md`。

### 2.1 分层现实（与 Task 描述的目标模型对照）

```text
API Layer        app/api/server.py（唯一入口聚合层；FastAPI + WS）
   |
Task 管理        无独立 TaskManager 类 —— 三机制协同：
                 server.active_tasks / _task_registry（内存、per-thread）
                 app/runtime/governance/service.py（submit/list/cancel 薄助手）
                 GovernanceController（durable 权威：terminal funnel / handles / watchdog）
   |
Runtime          app/runtime/governance/controller.py（唯一 lifecycle/budget/cancel/terminal 权威）
                 app/runtime/checkpoint.py（官方 saver 表族，禁止触碰）
                 app/agent/main_agent.py run_deep_agent(query, session_id)（执行入口）
   |
Research Domain  app/research/（Data/Evidence Plane F1–F7 + Intelligence/Execution + eval）
   |
Persistence      三个独立表族 / 三套 store（见 §2.4）
```

### 2.2 Task Model / 生命周期

- `TaskRecord`（`app/runtime/governance/models.py`）：`task_id / thread_id / run_id / status /
  terminal_reason / error_kind / error / policy_snapshot / effective_limits / counters_snapshot /
  superseded_by / parent_session(NULL 预留) / underlying_linger_observed / owner_instance / version /
  created_at / started_at / finished_at`。字段权威在 models.py；DDL 由它机械导出。
- `TaskStatus`：`running` + 8 终态（completed/failed/cancelled/timed_out/budget_exceeded/
  superseded/aborted/orphan_reclaimed）。**无 queued**。
- `GovernanceController`（`app/runtime/governance/controller.py`，1028 行）：
  - `create_task(thread_id, *, task_id?, run_id?, policy_snapshot?, effective_limits?, parent_session?)`
    —— 插入 running TaskRecord；`parent_session` 参数存在但**当前无任何调用方传值**（已 grep 证实）。
  - `execute(task_id, coroutine, policy)` → `_execute_governed`：绑定 BudgetCounter / GovernanceExecution
    ContextVar / watchdog（单调 deadline）/ run_id 前绑定回填；一切收敛走唯一 `terminalize()` funnel
    （内存裁决权威 + 乐观 CAS `WHERE status='running' AND version=?` + retry → pending_terminal）。
  - 进程级单例 `get_controller()`；store 不可用 → None（server 侧 `_require_governance()` 503 fail-closed）。
- **每 thread 单一活跃任务**语义（server.py `run_task`）：同 thread 新任务先 cancel 旧任务
  （governed 走 `cancel_governed` funnel），避免并发写同一会话目录。（正式 superseded 语义 = future，未接。）

### 2.3 Runtime / Execution 链路（一次任务）

```text
POST /api/task {query, thread_id?, policy?}
  → safe_thread_id_or_new(thread_id)            # P004 净化，缺失换新 uuid
  → (旧任务收敛) old_task.cancel() + ctl.cancel_governed(old_task_id)
  → gov_service.submit_task(ctl, thread_id, query, policy)
      = controller.create_task(thread_id, run_id=new)     # TaskRecord running
      + lifecycle_event(task_started)                     # durable observation, fail-open
      + asyncio.create_task(controller.execute(task_id, run_deep_agent(query, thread_id), policy))
  → server 登记 active_tasks[thread_id] / _task_registry[thread_id] = task_id
```

`run_deep_agent(task_query, session_id)`（main_agent.py）：
- 建 `app/output/session_{session_id}` 目录；复制 `updated/session_{id}` 上传文件；
- set ContextVar（session_dir / thread_id / run_id / task_id）；
- `research_reg.create_run_and_root(session_id, question, run_id)` → ResearchRun（fail-open）；
- checkpoint config `{"configurable": {"thread_id": session_id}}` → **同一 session_id 复用同一条
  LangGraph 执行上下文（记忆累积）**；
- governance-active 时注入 callback + recursion_limit；
- astream 正常结束 → F7 `finalize_run` + `task_result`；取消/异常路径不 finalize；
- finally reset ContextVar。

**关键结论**：thread_id 是贯穿 WS / checkpoint / 目录 / TaskRecord / ResearchRun / monitor 的
"会话身份"。一次 research 执行（task）恰对应一个 ResearchRun（run_id 三方同源：
TaskRecord.run_id == ResearchRun.run_id == monitor run_id）。

### 2.4 Persistence / Repository 现实（三平面纪律）

| 平面 | 表族 | 迁移体系 | DB 配置 | 权威语义 |
|---|---|---|---|---|
| checkpoint | checkpoints/… | 官方 saver.setup()（禁止触碰） | `AGENT_CHECKPOINT_BACKEND`/`_DB`/`_DSN` | 图执行状态 |
| governance | governance_tasks / governance_events | `db/governance_migrations/NNNN_*.{sqlite,postgres}.sql` + `governance_schema_migrations` 版本表（`app/runtime/governance/migrations.py` 幂等自动 apply，**已 apply 跳过；新版本文件 = 自动发现**） | `GOVERNANCE_BACKEND`(sqlite 缺省) / `GOVERNANCE_DB` / `GOVERNANCE_DSN`（兜底 `AGENT_CHECKPOINT_DSN`）；sqlite 默认 `app/runtime/governance.sqlite` | TaskRecord = terminal truth；event = durable observation |
| research | research_runs/sub_questions/search_queries/sources/evidences + claims/…(0002–0006) | `db/migrations/NNNN_*` + `schema_migrations`（`app/research/migrations.py`） | `RESEARCH_STORE`=sqlite/postgres/disabled；`RESEARCH_DB`/`RESEARCH_DSN` | run/artifact 数据面；fail-open |

- Repository 风格 = **函数式 store 模块**（`gov_store.list_tasks(store, thread_id=…)` 等纯函数接收
  store 连接句柄）+ 少量模块级单例工厂（`get_store()`）。SQL 统一 `%s` 参数化，sqlite 端转 `?`；
  JSON 列 sqlite=TEXT / postgres=JSONB（`json_param`）。写入一律 `with store.transaction() as tx`。
- `app/runtime/governance/events.py` 是先例：**同一 store 上追加只读/写函数，不新建 store 类**。

### 2.5 Research Domain（Run 粒度归属）

- `ResearchRun`（`app/research/schemas.py`）：`run_id / thread_id / question / status(running|
  finished|failed|cancelled|planned) / started_at / finished_at / plan / budget / metadata`。
- `research_runs` 有 `idx_research_runs_thread_id` —— **按 thread 查 run 是现成能力**。
- research store 可整体 disabled（fail-open）；**research 面与 governance 面分属不同 DB 文件/连接**，
  不能跨库 JOIN。

### 2.6 API Router / Schema（现状 + 兼容红线）

现有 HTTP 端点（`app/api/server.py`；ARCHITECTURE.md §6 兼容规则）：

```text
POST  /api/task                       # 创建任务（governed）；body {query, thread_id?, policy?}
POST  /api/task/{thread_id}/cancel    # 按 thread 取消当前活跃任务
GET   /api/tasks?thread_id=           # 任务列表（thread 可选过滤，created_at 降序）
GET   /api/tasks/{task_id}            # 任务详情
GET   /api/threads/{thread_id}/events # task-scoped durable event replay（cursor task_id+seq）
POST  /api/upload  GET /api/files  GET /api/download
WS    /ws/{thread_id}                 # monitor live + 握手 replay + 心跳 pong
```

- Schema：pydantic `BaseModel`（TaskRequest: query / thread_id / policy）。错误模型 = **FastAPI
  `HTTPException` + `{"detail": ...}`**（无独立错误码枚举；service 层复用 controller/store 错误，
  server 层映射 HTTP 状态）。P005 无鉴权 = 既定教学边界（不引入用户体系）。
- **WS 事件 schema / monitor event 类型 / 心跳协议 = frozen**（ARCHITECTURE.md §4/§9，勿动）。

### 2.7 Frontend 对 Backend 的调用方式（只读盘点，不改）

- `frontend/src/lib/thread.ts`：**单会话模型** —— localStorage `deepsearch.thread_id` 单键
  uuid，`getStoredThreadId()` 惰性创建。
- `frontend/src/lib/api.ts`：`startTask(query, threadId)` → POST /api/task；`cancelTask(threadId)`；
  `getTask(taskId)`；`uploadSessionFiles`；`listSessionFiles`；`getDownloadUrl`。
- `frontend/src/hooks/useDeepAgentSession.ts`：WS /ws/{thread_id} 生命周期（心跳 ~25s）等。
- 结论：前端**无 Session 列表/切换/归档概念**；单 thread 贯穿。未来 Frontend DSH 将消费
  MULTI_SESSION_API_SPEC.md 实现 Session Sidebar + Workspace（本 Task 不实施）。

### 2.8 现有测试基线（回归目标）

- sqlite 全量基线：**731 passed / 88 skipped / 0 failed**（Batch8 report 口径，排除
  `test_db_tools_mysql_integration.py` 进程内污染文件，见 PROJECT_CONTEXT §8/§9）。
- PG 门控：`AGENT_CHECKPOINT_DSN_TEST`（governance/checkpoint 共用独立测试库）、`RESEARCH_DSN_TEST`
  （research）。skipif 纪律；无 DSN 如实 skip，不伪造。
- 测试风格：conftest 提供 `research_tmp`/`research_sqlite`；governance 测试自带 `gov_tmp` fixture +
  monkeypatch `GOVERNANCE_DB` + `gov_store.reset_store()` / `reset_controller()`。

---

## 3. Design Questions（Task §5.1–§5.6 的逐项回答）

### Q5.1 Session 应位于当前架构的哪个层级？为什么？

**回答：Session 是位于 API 之下、Task（governance）之上的"对话容器"业务域 —— 新建轻量
`app/session/` 域模块（models/service/store 函数），不进入 app/runtime/governance 冻结包，不新建
架构层/控制器。**

理由：
1. 分层现实（§2.1）中 Task 管理已是"server 薄调度 + governance service + Controller"三机制；
   Session 容器只需在其上做元数据 CRUD + 归属校验 + 只读聚合，语义上**高于** task、**不触碰**
   lifecycle 权威 —— 与"F8 Controller 唯一拥有 lifecycle/budget/cancel/terminal 权威"红线一致
   （PROJECT_CONTEXT §4 / ARCHITECTURE.md §3）。
2. Session 不拥有任何 Runtime 行为：没有"Session 运行/取消/预算"这类状态 —— 它只有
   ACTIVE/ARCHIVED 两种组织状态（§5.2）。因此它不可能也不应该"持有" controller/execution，
   只**引用** task 归属。
3. 独立 `app/session/`（而非塞进 governance 包）：governance 包承载 F8 冻结语义（TaskRecord 字段
   权威、funnel、CAS…），PROJECT_CONTEXT §4 冻结纪律要求不扩大其语义面；新域代码放独立包可保持
   frozen 文件 byte-identical，diff 清晰可审计。
4. 复用 store 连接：`app/session/store.py` 的函数接收 governance store 句柄（`events.py` 先例），
   不重复造 store 单例。

### Q5.2 Session 与 TaskManager 的关系？是否需要 SessionManager？

**回答：不需要新增 `SessionManager` 类。** 当前没有叫 TaskManager 的类，任务管理由
server（调度/登记）→ gov_service（submit/query 助手）→ GovernanceController（权威）表达。Session
层需要的最小面是：

- `app/session/service.py`：`create/list/get/archive/unarchive` + 归属校验（
  `task_belongs_to_session`）+ 运行中守卫（archive 前置检查）。
- `app/session/store.py`：`sessions` 表 CRUD + 按 thread 聚合 task 摘要（复用 gov_store 查询函数）。

这些是**薄函数**而非有状态 manager（与 service.py / events.py 风格一致）。未来如出现多实例/租赁等
需求，才考虑独立管理器 —— 本 Task 明确不做。

### Q5.3 Session / Task / Runtime 的边界？

| 层 | 负责 | 不负责 |
|---|---|---|
| Session（新） | 对话容器：title/description/ACTIVE·ARCHIVED、Task 组织与列表、归档/恢复规则、历史研究结果入口 | 任务调度、budget、cancel、terminal 权威、执行状态 |
| Task（TaskRecord + Controller） | 单次任务生命周期：status/terminal funnel/run_id 绑定/events | Session 组织语义 |
| Runtime（controller + checkpoint + run_deep_agent） | Agent 执行、watchdog/cancel/resume(不存在)/completion、记忆与产物目录 | Session 元数据 |

运行路径（目标形状，§11）：

```text
POST /api/sessions/{id}/tasks
  → Session service（校验 session 存在 + ACTIVE）
  → gov_service.submit_task(ctl, thread_id=session_id, query, policy)   # 与既有 POST /api/task 同源
  → Controller.execute(... run_deep_agent(query, session_id) ...)       # 链路零改动
```

### Q5.4 Session 状态是否需要持久化？ Q5.5 是否需要新增持久化结构？

**回答：需要持久化；新增一个 `sessions` 表（governance 迁移族 0002），设计见 §5.1。** 理由：
- Session 元数据（title/status/created/updated）是 UI 列表与历史恢复的读源，必须跨重启存活
  （重启后 Session 信息不丢失 = AC5）。
- 不加新表就无法表达"命名/归档/恢复" —— thread_id 本身只是 key，无元数据。
- **不加新 DB / 新 env / 新 store**：复用 governance store（连接、后端选择、sqlite/PG 双方言、
  migration runner）→ 最小 diff、与 Task 同库（同文件 sqlite 或同 DSN PG），未来可加索引 JOIN。

### Q5.6 当前 Task 是否已有表达 Session 上下文的字段/机制？能否复用？

**回答：有，且应复用 —— `TaskRecord.thread_id`（以及 research_runs.thread_id）。**

- 现状 thread_id 已经做到"每个 Task 按会话归属、按会话隔离、按会话取列表"（`gov_store.list_tasks(
  store, thread_id=…)`；replay 归属校验 `_resolve_replay_task`；research_runs 索引 thread_id）。
- `TaskRecord.parent_session`（F8 预留、从未写入）**不复用**：它的字面语义模糊（"parent"），且
  F8 Plan 原文声明 session 生命周期"无独立 Registry"；若用 parent_session 存 session_id 会与
  thread_id 形成**双关联键**（Task 同时有 thread_id 与 parent_session 两个"所属会话"），违反
  Task §10"不要创建重复的 Context / ID 字段"。保持 NULL 预留不变（frozen）。

---

## 4. Impact Analysis（影响清单）

### 4.1 Required Changes（实现 Multi-Session 所必需）

| 文件 | 改动 | 理由 |
|---|---|---|
| `db/governance_migrations/0002_sessions.sqlite.sql` / `.postgres.sql`（新增） | `sessions` 表 + 索引 | Session 元数据持久化；governance migration runner 自动发现并幂等 apply（**additive，0001 与既有表零改动**） |
| `app/session/__init__.py`（新增） | 包说明 | 语义域包 |
| `app/session/models.py`（新增） | Session dataclass + SessionStatus + from_row/to_dict | 字段权威（镜像 governance models.py 风格） |
| `app/session/store.py`（新增） | sessions CRUD + thread 聚合查询（函数式，接收 governance store 句柄） | Repository 边界：API 不直接触库 |
| `app/session/service.py`（新增） | create/list/get/archive/unarchive/task 归属校验/running 守卫/归档状态校验；SessionNotFound / InvalidSessionState 域错误 | Service / Domain 层 |
| `app/api/server.py`（修改，最小） | 新增 `/api/sessions…` 端点（§5.4），复用 `_require_governance()` / gov_service / 新 session service | API 接线 |
| `tests/test_sessions_store.py`（新增） | sessions store/migration 单测（sqlite） | 16.x 测试要求 |
| `tests/test_sessions_service.py`（新增） | CRUD + 状态规则 + 隔离（service 层） | 同上 |
| `tests/test_sessions_concurrency.py`（新增） | 双 Session 并发运行 + Cancel 隔离（controller/服务层，stub runner） | 16.3/16.4 |
| `tests/test_sessions_postgres.py`（新增） | PG 门控镜像（AGENT_CHECKPOINT_DSN_TEST） | 双后端纪律 |
| `MULTI_SESSION_API_SPEC.md`（最终交付） | API Contract | Task §15/22.2 |
| `MULTI_SESSION_IMPLEMENTATION_REPORT.md`（最终交付） | 实现报告 | Task §22.1 |
| `docs/…`（PROJECT_CONTEXT/ARCHITECTURE 状态同步） | 实施后状态/边界更新 | Harness 纪律 |

### 4.2 Optional Changes（可做可不做；默认不做或明确标 Optional）

- `POST /api/task` 请求体加可选 `session_id` 字段（默认不加：走新增 session-scoped 端点即可，
  旧 API 完全不动）。
- `PATCH /api/sessions/{id}` 支持改 title/description（本阶段**不做** rename；如需仅在 Spec 阶段
  按用户裁决补充）。
- `GET /api/sessions` 分页 cursor（默认 limit/offset 简单分页足够）。

### 4.3 Must Not Change（红线 / 冻结）

- ❌ `frontend/` 任何文件（Task §2；如发现适配需求 → 记录到报告，不实施）。
- ❌ `app/runtime/governance/controller.py` / `models.py` / `store.py` / `events.py` 的**既有语义**
  （frozen：TaskStatus、funnel、CAS、event replay）。本设计不需要改它们（只新增独立包 + server
  接线 + governance_migrations additive 文件）。
- ❌ checkpoint 官方表族 / `app/runtime/checkpoint.py`。
- ❌ `app/research/` 领域代码与 migration（只读消费现有查询能力）。
- ❌ `parent_session` 语义、`thread_id` 净化规则（`app/utils/session_id.py`）。
- ❌ WS 事件 schema / monitor event 类型 / 心跳协议。
- ❌ 引入认证/用户体系（P005 边界）；引入 Memory 系统、复杂 Resume、新 Framework/依赖（Task §19）。
- ❌ 用户 working tree 中的个人文件（简历 md / 两张简历图）：不属于本 Feature，禁止纳入任何 commit。

---

## 5. Design（设计）

### 5.1 Session 数据模型（持久化）

```sql
-- db/governance_migrations/0002_sessions.{sqlite,postgres}.sql（additive；0001 不改）
CREATE TABLE IF NOT EXISTS sessions (
    session_id  TEXT PRIMARY KEY,          -- 服务端签发：uuid4().hex（P004 安全字符集内）
    title       TEXT NOT NULL,             -- create 缺省 → 默认标题（见 5.3）
    description TEXT,                      -- 可空
    status      TEXT NOT NULL,             -- 'active' | 'archived'
    created_at  TEXT NOT NULL,             -- sqlite TEXT ISO / PG TIMESTAMPTZ（双方言沿用 governance 风格）
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status, updated_at);
```

- 字段权威 = `app/session/models.py`（dataclass `Session`），DDL 与 from_row/to_dict 从它导出
  （镜像 governance models.py 纪律）。
- 不带 FK 引用 governance_tasks（task 可无 session = 既有遗留 thread 行为保留）；归属是
  **派生关联**：`sessions.session_id == governance_tasks.thread_id`。

### 5.2 Session 状态机（真实语义，非"为完整而造"）

```text
ACTIVE ──(archive: DELETE /api/sessions/{id})──▶ ARCHIVED
  ▲                                                 │
  └──────(unarchive: PATCH {status:"active"})───────┘
```

- **只有 ACTIVE / ARCHIVED 两态**。明确排除：
  - `CREATED`：无独立语义（创建即 ACTIVE，与 Task 无 queued 同构）；
  - `PAUSED`：Session 容器无暂停对象（没有进程/资源挂在 Session 上）——Task 的暂停=取消/收敛，属 Task；
  - `COMPLETED / FAILED`：容器不会"完成/失败"（失败只属于 Task/ResearchRun）；
  - 避免 Task §8 警告的"无生命周期语义的空状态"。
- 规则（写进 Contract，不留未定义行为）：
  | 操作 | ACTIVE | ARCHIVED |
  |---|---|---|
  | 创建 Task | ✅ | ❌ 409 invalid_session_state |
  | 读 Detail/List Task | ✅ | ✅（历史可追溯） |
  | Archive（DELETE） | ✅ | ✅ 幂等 no-op（200，`already_archived=true`） |
  | Unarchive（PATCH status=active） | ✅ 幂等 no-op | ✅ |
  | 有 running Task 时 Archive | ❌ 409（先取消；防"归档后任务仍在跑"的未定义态） | — |
  | 物理删除 | ❌ 永不（软删除语义；历史 research 数据完整保留） | ❌ |

### 5.3 Session 与 Task 的关联（核心）

**决策：`session_id == thread_id`（1 Session : 1 线程身份），归属 = `governance_tasks.thread_id`。**

- 创建 Session 时服务端签发 `session_id`（uuid4().hex，天然满足 P004 字符集）。该 id 在会话存续期
  内即该会话的 thread_id：未来 Frontend 用 `session_id` 连接 `WS /ws/{session_id}`、调用
  upload/files（以 session_id 为 thread_id）、发起研究任务（session-scoped 端点内部 thread_id=
  session_id）。
- Task → Session：`task.thread_id ∈ sessions.session_id`；Session → Task：
  `gov_store.list_tasks(store, thread_id=session_id)`；Run → Session：`research_runs.thread_id`。
- 关联在 Task 生命周期中不丢失：thread_id 是 TaskRecord NOT NULL 列，Task 一旦创建即绑定。
- **隔离保证（结构性）**：所有 session-scoped 查询/操作先解析 session（404 if missing），再以
  thread_id=session_id 过滤 → Session A 无法命中 Session B 的任何 task/run。
- 可选支持：`POST /api/sessions {thread_id?: str}` 允许把**既有遗留 thread 注册成 Session**
  （校验安全字符集；缺省服务端生成）→ 无缝"恢复历史会话"，同时保持旧 /api/task 流程不破坏。

### 5.4 API 设计（草案；最终以 MULTI_SESSION_API_SPEC.md 为准）

新增端点（全部 additive，既有端点零改动）：

```text
POST   /api/sessions
       body: {title?: str(≤200), description?: str(≤2000), thread_id?: str}
       201 → {session_id, title, description, status:"active", created_at, updated_at}
       · thread_id 提供则校验 [A-Za-z0-9_-]{1,128}（非法 400）；缺省服务端生成
       · title 缺省 "未命名会话"（或取 thread 既有最新 run question 前缀，Spec 阶段定）

GET    /api/sessions?include_archived=false&limit=50&offset=0
       200 → {sessions: [{session_id, title, description, status, created_at, updated_at,
                          task_count, running_tasks, latest_task:{task_id,status,created_at,finished_at}}],
               total}

GET    /api/sessions/{session_id}
       200 → {session:{...}, tasks:[{task_id,status,terminal_reason,run_id,created_at,
                                     started_at,finished_at, query?:str|null}], latest_run?: {...}}
       404 → session not found

DELETE /api/sessions/{session_id}                       # 逻辑归档（软删除；幂等）
       200 → {session_id, status:"archived", already_archived: bool}
       404 → session not found；409 → 有 running task

PATCH  /api/sessions/{session_id}  body: {status:"active"}   # unarchive（幂等）
       200 → {session_id, status:"active", already_active: bool}

POST   /api/sessions/{session_id}/tasks   body: {query, policy?}
       201 → 同 POST /api/task 返回（status/thread_id=session_id/task_id/run_id）
       404 session；409 archived session

GET    /api/sessions/{session_id}/tasks
       200 → {tasks:[...]}（thread 作用域 task 摘要，created_at 降序）

POST   /api/sessions/{session_id}/tasks/{task_id}/cancel
       200 → cancel 结果（含 already_terminal）；404 任务不存在/不属于该 session
```

- task 摘要中的 `query`：governance 面不存 query（TaskRecord 无 query 列）。可选 enrich：
  research store 可达时按 run_id 读 `research_runs.question`（fail-open；disabled → null）。
  **不跨库 JOIN**。
- 错误模型（复用 FastAPI HTTPException + detail）：404 not_found / 409 invalid_session_state /
  400 invalid_request / 422 validation / 503 governance store 不可用（沿用 `_require_governance`）。
  内部域错误 `SessionNotFoundError`/`SessionStateError` 由 service 抛、server 映射 HTTP。
- WS / 上传 / 文件 / 下载：以 session_id 作 thread_id 直接复用，**零改动**。

### 5.5 Runtime Integration（Session ID 贯穿 + 隔离）

- Session-scoped 创建 Task = `gov_service.submit_task(ctl, thread_id=session_id, query, policy)`
  —— TaskRecord.thread_id=session_id、run_id 绑定、ResearchRun.thread_id=session_id 自动成立；
  Run A1 belongs to Session A 由 thread_id 回答（无需任何 runtime 改动）。
- Runtime **不允许跨 Session 操作**：session-scoped cancel 先做归属校验
  （`task.thread_id == session_id`，否则 404/403 语义）；全局旧端点 /api/task/{thread}/cancel 保持
  原 thread 语义（不新增跨 session 能力）。
- Cancel/并发隔离：cancel = `controller.cancel_governed(task_id)`（funnel，task-scoped）；
  不同 Session 的任务是不同 task_id/thread_id → 天然互不影响（测试锁定）。
- Resume：**当前产品无 Resume 入口**（F8 有 checkpoint 数据面与 durable replay，但 Batch9
  runtime recovery deferred）。本 Task 不新增 Resume；Contract 明示现状，后续若做 Resume 必须
  task-scoped + session-scoped 校验。
- 并发：单进程内多 Session 同时 running 完全可行（asyncio + per-thread handle），无全局执行锁。

### 5.6 向后兼容（Task §18）

| 现状 API | 处理 |
|---|---|
| POST /api/task | **不变**。不要求 thread 必须注册为 Session；遗留 client 行为完全保留（新任务可落在无 session 记录的 thread 上） |
| GET/POST /api/tasks…、replay、upload/files/download、WS | 全部不变 |
| governance/research/checkpoint 表族 | 不变（新增 sessions 表为 additive） |
| 前端单 thread 模型 | 不受影响（未消费新端点；本 Task 也不改前端） |

唯一"语义扩展"（非 breaking）：同 id 现在既是 thread 也是 session；两者规则兼容（session 是
thread 的命名注册超集）。无 Breaking Change。

---

## 6. What Remains Unchanged（明确不动）

- Agent Runtime（controller/checkpoint/run_deep_agent/ContextVar/monitor）；Research Pipeline
  （F1–F7、orchestrator/eval）；governance 冻结契约；thread_id 净化与目录隔离；WS schema；
  依赖集合；`.env*`；`frontend/`；`parent_session`。

---

## 7. Implementation Order（实施顺序，Implementation 阶段执行）

```text
Step 0  Branch：feature/multi-session-backend（git 写操作，需用户批准；main 上有用户个人文件，
        创建分支后仍不触碰、不提交它们）
Step 1  Readiness Review（契约闭合）→ 用户裁决 §8 决策点 → Decision 记录（DECISION.md 或 docs/plan）
Step 2  Spec：docs/spec/2026-10-xx-multi-session-backend.md（最终 API Contract 定稿源）
Step 3  0002_sessions.{sqlite,postgres}.sql + app/session/models.py（字段权威）
Step 4  app/session/store.py（CRUD + 聚合查询）
Step 5  app/session/service.py（状态机/归属/守卫）
Step 6  server.py 端点接线（session-scoped tasks 复用 gov_service）
Step 7  tests：store → service → concurrency/isolation → PG gate
Step 8  VERIFICATION（compileall / ruff / 定向 pytest / sqlite 全量回归 731+ / PG gate）
Step 9  MULTI_SESSION_API_SPEC.md + MULTI_SESSION_IMPLEMENTATION_REPORT.md + 状态文档同步
Step 10 User Review → Freeze → Scope Audit → 逐步批准 Commit / Push / Merge
```

## 8. Test Strategy（测试策略，对应 Task §16）

| 组 | 文件（建议） | 覆盖 |
|---|---|---|
| Session CRUD | test_sessions_store.py / test_sessions_service.py | create(默认值/thread_id 提供/非法)/list(含 archived 过滤/排序)/get/archive(幂等)/unarchive/404/409 |
| Store + Migration | test_sessions_store.py | fresh DB apply 0002、upgrade 0001→0002、幂等 ensure、双后端归一（sqlite 必跑；PG 镜像 test_sessions_postgres.py 用 AGENT_CHECKPOINT_DSN_TEST） |
| 隔离 | test_sessions_service.py / api 层 | Session A/B 各自 task 列表互不可见；A 名下 cancel B 的 task → 404；A 详情不含 B task |
| Runtime 隔离 / 并发 | test_sessions_concurrency.py | submit_task ×2（thread A/B，stub runner）→ 双 running、状态独立；cancel A → B 仍 running/completed 不受影响 |
| 状态机 | service 层 | archived 创建 task 409；running 时 archive 409；unarchive 后可创建；幂等重复 archive/restore |
| 回归 | 全量 | sqlite `uv run pytest tests/ -q`（排除 mysql 污染文件；期望 731+新增全绿）；PG gate 有 DSN 则跑 |

断言纪律：TaskRecord 终态唯一（funnel once）；cancel 不影响他 thread；不弱化既有 fail-closed。

## 9. Open Decisions（需要用户裁决后进入 Implementation）

1. **关联机制**：Session id == thread id（1:1 身份复用，推荐）vs TaskRecord 增 `session_id` 独立列
   （重复键，不推荐）。
2. **sessions 表归属**：governance 迁移族 additive 0002（推荐）vs 独立表族（新 store/新 env，重复建设）。
3. **Session 状态集**：仅 ACTIVE/ARCHIVED（推荐）vs 扩充（无真实语义，不推荐）。
4. **任务创建路径**：新增 POST /api/sessions/{id}/tasks（推荐）vs 扩展 POST /api/task 加 session_id。
5. **title 默认值与 task 摘要 query enrich**：缺省标题文案；research 面 enrich 是否纳入（fail-open）。
6. **archive 前置规则**：有 running task 拒绝归档（推荐）vs 允许归档并继续运行。

## 10. Known Risks / Limitations（本阶段识别）

- 多实例/跨进程 Session 一致性（F8 单实例假设延续；不引入 lease/heartbeat —— Task §19 禁止提前实现）。
- session→thread 1:1 约束在"一个 Session 含多个相互独立、不共享记忆的子对话"需求出现时需演进
  （记录为 future，不在本 Task 扩大）。
- task 摘要 query 的 enrich 依赖 research store 可达性（可能为 null）。
- Frontend 适配全部留待独立 DSH 会话，以 MULTI_SESSION_API_SPEC.md 为准。

## 11. Next Action

本阶段产出即本文档；**未修改任何产品代码**。等待用户 Review：
① 确认 §9 决策点（或直接采用推荐默认）；② 批准后在独立/后续会话进入 Implementation（建 branch）。
