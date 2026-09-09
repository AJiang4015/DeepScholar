# MULTI_SESSION_IMPLEMENTATION_REPORT.md — Multi-Session Backend Capability 实现报告

> 日期：2026-10-03 ｜ Branch：`feature/multi-session-backend`（未提交；等待用户 Review Verification /
> Scope Audit 后再 Commit —— 按用户指令不 Commit/Push/Merge）。
> 前置文档：`docs/plan/2026-10-03-multi-session-backend-architecture-analysis-and-plan.md`
> （Analysis & Plan，用户已 Review 通过）+ 用户 Decision Closure（设计点全部闭合）。
> API Contract：`MULTI_SESSION_API_SPEC.md`（本 Feature 最终交付物之一，文档与代码一致）。

---

## 1. Summary

在既有「thread = 会话身份」架构之上引入 **Multi-Session Backend Capability**：

- 新增持久化 **Session 容器**（sessions 表，governance 迁移族 additive 0002，sqlite+PG 双方言）；
- 新增 **app/session 业务域**（models / store / service，函数式 repository，复用 governance store）；
- 新增 **8 个 /api/sessions 端点**（全部 additive，既有端点与旧客户端零改动、无 Breaking Change）；
- 实现 **Session ↔ Task 隔离与并发安全**（thread 作用域结构性隔离 + 归档/建任务并发守卫）；
- 全程 **未修改任何 Frontend / frozen 模块 / Agent Runtime / Research Pipeline / checkpoint**。

验收：sqlite 全量 **775 passed / 91 skipped / 0 failed**（baseline 731 + 新增 44，另 3 个 PG 门控用例
无 DSN 如实 skip）；ruff / format / compileall 全绿。

## 2. Architecture Changes（Session 在现有架构中的位置）

```text
API Layer（server.py 新增 /api/sessions…；复用既有错误/治理接线）
   ↓
app/session/service.py（Session 容器：元数据 / 状态机 / 归属校验 / 聚合）
   ↓
app/session/store.py（sessions 表 CRUD + governance_tasks 只读聚合）
   ↓                        ↕ 复用 governance store 连接 / backend / migration runner
app/runtime/governance（TaskRecord / Controller / service —— 未改动，Task/Runtime 权威不变）
   ↓
Agent Runtime / Research Domain / checkpoint —— 全部未改动
```

- **Identity**：`Session.session_id == thread_id == governance_tasks.thread_id == research_runs.thread_id`
  （1:1；`TaskRecord.parent_session` 保持 NULL/unused，未复用 —— 遵守 Decision #1）。
- **Session 不拥有 Runtime lifecycle**：未新增 Session cancel/budget/controller/执行状态机；
  创建/取消任务完全复用 `gov_service.submit_task` / `cancel_task` / `list_tasks`（Decision #10）。

## 3. Changed Files（完整清单；git 未提交）

### 新增
| 文件 | 说明 |
|---|---|
| `db/governance_migrations/0002_sessions.sqlite.sql` | sessions 表 DDL（sqlite 方言；additive，0001 零改动） |
| `db/governance_migrations/0002_sessions.postgres.sql` | 同上（postgres 方言；TIMESTAMPTZ/JSON 语义对齐 governance 0001） |
| `app/session/__init__.py` | 域包说明 |
| `app/session/models.py` | `Session` dataclass + `SessionStatus(ACTIVE/ARCHIVED)`；字段唯一权威 |
| `app/session/store.py` | sessions CRUD、list（archived 过滤/分页/排序）、`task_summary`（task_count/running_tasks/latest_task）、`archive_if_no_running`（单事务原子归档守卫） |
| `app/session/service.py` | 域逻辑：create/list/detail/archive/unarchive/assert_active/validate_task_in_session/list_session_tasks；域错误（SessionNotFound/State/Validation、TaskNotFound/NotInSession）；query enrich（research 面 fail-open） |
| `tests/test_sessions_store.py` | store/migration/聚合/归档守卫（sqlite） |
| `tests/test_sessions_service.py` | CRUD/状态机/隔离/归档规则（service 层） |
| `tests/test_sessions_isolation.py` | 双 Session 并发运行 + Cancel 隔离（controller 运行路径） |
| `tests/test_sessions_postgres.py` | PG 门控镜像（AGENT_CHECKPOINT_DSN_TEST） |
| `MULTI_SESSION_API_SPEC.md` | API Contract（最终交付物） |
| `docs/plan/2026-10-03-multi-session-backend-architecture-analysis-and-plan.md` | Analysis & Plan（用户 Review 通过） |
| `MULTI_SESSION_IMPLEMENTATION_REPORT.md` | 本文件 |

### 修改
| 文件 | 变更 |
|---|---|
| `app/api/server.py` | +8 session 端点；抽取共享 `_start_governed_task` / `_cancel_governed_task`（`POST /api/task` 与 session-scoped 共用，语义与旧实现逐行一致）；新增 session 域错误 → HTTPException 映射。**既有端点/契约零改动** |
| `tests/test_runtime_governance.py` | 迁移清单断言 0001 → 0001+0002（additive 引起的期望变更；frozen 语义零改动） |
| `tests/test_runtime_governance_postgres.py` | 同上（PG 镜像） |
| `ARCHITECTURE.md` | §2 app/session 模块行；§3 依赖方向；§6 Multi-Session 端点集合（additive 契约） |
| `DECISION.md` | 新增 D019（决策记录，闭合决策备案） |

### 未改（明确）
`frontend/` 全部；`app/runtime/governance/{controller,models,store,events,migrations}.py`（既有语义）；
`app/runtime/checkpoint.py`；`app/research/*`；`app/utils/*`；依赖文件；`.env*`；WS schema。
用户个人文件（简历 md / pdf / png / html）未修改、未 add（本报告 §13 git 状态验证）。

## 4. Session Model（最终）

| 字段 | 类型 | 说明 |
|---|---|---|
| session_id | TEXT PK | 服务端 uuid4().hex 或提供的既有 thread_id（`[A-Za-z0-9_-]{1,128}`） |
| title | TEXT NOT NULL | 默认 `未命名会话`；≤200 |
| description | TEXT NULL | ≤2000 |
| status | TEXT NOT NULL | `active` / `archived` |
| created_at / updated_at | TEXT(sqlite) / TIMESTAMPTZ(PG) | UTC ISO |

派生视图：`task_count`、`running_tasks`、`latest_task`（governance_tasks thread 作用域聚合，不落库）。

## 5. Lifecycle（最终状态机）

```text
ACTIVE ──archive(DELETE)──▶ ARCHIVED ──unarchive(PATCH status=active)──▶ ACTIVE
```

规则：archived 禁建任务（409）；running Task 存在时归档被拒（409，单事务守卫）；重复归档/恢复幂等；
Archive 永不物理删除（Session/Task/Run/报告/checkpoint 全保留）。无 CREATED/PAUSED/COMPLETED/FAILED。

## 6. API（新增/修改清单 —— 详细契约见 MULTI_SESSION_API_SPEC.md）

```text
POST   /api/sessions                                          新增
GET    /api/sessions?include_archived=&limit=&offset=         新增
GET    /api/sessions/{session_id}                             新增
DELETE /api/sessions/{session_id}                             新增（逻辑归档）
PATCH  /api/sessions/{session_id}                             新增（v1 仅 unarchive）
POST   /api/sessions/{session_id}/tasks                       新增（thread_id=session_id，复用 governed 链路）
GET    /api/sessions/{session_id}/tasks                       新增
POST   /api/sessions/{session_id}/tasks/{task_id}/cancel      新增（归属校验后 cancel）
```
错误模型：复用既有 `HTTPException + {"detail": …}`（404 not found / 409 invalid state / 400 / 503）。
修改：无（server.py 抽取共享 helper，行为与旧实现一致）。

## 7. Runtime Integration

- Session-scoped 建任务 = `gov_service.submit_task(thread_id=session_id)` → TaskRecord/run_id/ResearchRun
  三方同源自动成立，Session ID 贯穿 Task 生命周期（thread_id 既有机制，零 runtime 改动）。
- 隔离：`task.thread_id == session_id` 校验 + thread 作用域查询（结构性）；跨 Session cancel/读取被拒。
- 并发：不同 Session 可同时 running（per-thread handle/状态）；Cancel 只收敛目标 task（funnel）。
- Resume：产品当前无 Resume 入口（F8 checkpoint + durable replay 数据面存在；runtime recovery 属
  F9 Batch9 deferred）——本 Task 未新增 Resume，Contract 明示现状。
- 并发窗口（归档 vs 建任务）：`archive_if_no_running` 单事务条件更新 + 建任务 submit 前
  precondition 二次校验，杜绝 ARCHIVED+RUNNING 与竞态误归档。

## 8. Persistence

`db/governance_migrations/0002_sessions.{sqlite,postgres}.sql`，由既有 governance migration runner
（`governance_schema_migrations` 版本表，幂等自动 apply）管理；复用 governance store（连接/后端选择/
双方言 JSON/时间语义）。无新 DB / env / 单例 / 依赖。Store 层 = 函数式 repository（API 不直接触库）。

## 9. Tests（新增 47 项 = 44 passed + 3 PG 门控 skip）

| 文件 | 数量 | 范围 |
|---|---|---|
| test_sessions_store.py | 27（sqlite，全部 pass） | migration fresh/upgrade(0001→0002)/幂等；CRUD；list 过滤/排序/分页；task_summary 聚合与 thread 隔离；归档守卫（running 阻塞 / terminal 放行 / 已归档 / 幂等） |
| test_sessions_service.py | ~17（全部 pass） | create（缺省 uuid/thread 注册/重复 409/字符集/长度）；list/detail（archived 过滤/聚合/query 键）；状态机（archive/unarchive 幂等、running 归档 409、archived 禁建）；归属（跨 Session → TaskNotInSession、未知 → NotFound） |
| test_sessions_isolation.py | 2（pass） | 双 Session 同时 running + Cancel A 不影响 B（B 继续 running→completed）；独立完成互不耦合 |
| test_sessions_postgres.py | 3（skip，无 `AGENT_CHECKPOINT_DSN_TEST` 如实 skip） | PG 镜像：migration/CRUD/task_summary |

执行命令（.venv python 3.12，Windows）：
`python -m pytest tests/test_sessions_store.py tests/test_sessions_service.py tests/test_sessions_isolation.py tests/test_sessions_postgres.py -q` → **40 passed, 3 skipped**（早前计数，未含后来新增归档守卫 4 项）；完整见下。

## 10. Regression（现有测试结果）

- 命令：`python -m pytest tests/ -q -p no:cacheprovider --ignore=tests/test_db_tools_mysql_integration.py`
- 结果：**775 passed, 91 skipped, 0 failed**（58.5s）
  - baseline（731 passed / 88 skipped，Batch8/D018 口径）+ 新增 session 44 passed + 3 PG skip。
  - 91 skipped = PG/MySQL 门控用例（本环境无 DSN/服务，按纪律如实 skip，不伪造）。
- 测试期望调整（必要、非弱化）：`test_runtime_governance*.py` 的 governance 迁移清单断言由 `["0001"]`
  更新为 `["0001","0002"]` —— additive 迁移引入的合法清单变更；frozen 表结构与语义零改动。
- ruff check / ruff format / compileall：全绿。

## 11. Known Limitations（如实报告）

1. query enrich 依赖 Research Store（独立 store，可 disabled）——不可用/无对应 run 时 task.query = null；
   不做跨库 JOIN（Decision #9 允许的逃逸路径已最小采用）。
2. Session 列表/详情的 task 摘要来自 governance；Research 面历史（claims/verifications…）不并入本
   端点（未来 Research History API 另行设计，不在本 Task 范围）。
3. 单实例语义延续 F8（无 lease/heartbeat/multi-instance）；Session 一致性假设与 Task 相同。
4. HTTP 级 E2E 未执行：真实端到端需 LLM/Tavily 等凭据（既有验证缺口，同历史 Feature；服务层 +
   controller 运行路径已覆盖核心语义）。
5. `_start_governed_task` / `_cancel_governed_task` 抽取：语义与旧 run_task/cancel_task governed 分支
   逐行一致（git diff 可查）；governance 不可用时新建任务 fail-closed（503）顺序微调（先拒绝后收敛旧
   任务），属安全方向变化（不启动新工作）。

## 12. Frontend Integration Notes（给后续 Frontend DSH）

依据 `MULTI_SESSION_API_SPEC.md`（唯一契约，勿自行推断 API）：

- **获取 Session 列表**：`GET /api/sessions`（默认只含 active；`include_archived=true` 取全部）。Sidebar
  字段：`session_id / title / status / updated_at / running_tasks / task_count / latest_task`。
- **创建 Session**：`POST /api/sessions {title?, description?}` → 201 返回完整 session；用返回的
  `session_id` 作为会话 thread：`WS /ws/{session_id}`、`/api/upload`、`/api/files`（thread_id=session_id）。
- **切换 Session / Workspace**：`GET /api/sessions/{id}`（session 元数据 + tasks + latest_task）作为
  历史/详情展示；live 用 WS。
- **发起研究**：`POST /api/sessions/{id}/tasks {query, policy?}`（archived → 409；响应 thread_id=session_id）。
- **历史**：`GET /api/sessions/{id}/tasks` + `GET /api/threads/{id}/events?task_id=&since_seq=`（durable replay）。
- **归档/恢复**：`DELETE /api/sessions/{id}`（409 当 running） / `PATCH /api/sessions/{id} {status:"active"}`。
- **状态展示**：`active | archived` 两态；Sidebar 可区分 Active/Archived（archived 默认不进列表）。
- 注意：`task.query` 可能为 null（research 面 enrich，fail-open）；前端勿依赖非空。

## 13. Git Verification（用户指令要求）

### git status
```text
 M ARCHITECTURE.md / DECISION.md / app/api/server.py
 M tests/test_runtime_governance.py / test_runtime_governance_postgres.py
 M 简历项目经历-深度研搜.md                 ← 用户个人文件（先前已存在，非本 Feature）
?? MULTI_SESSION_API_SPEC.md / app/session/ / db/governance_migrations/0002_sessions.*
?? tests/test_sessions_*.py / docs/plan/2026-10-03-… / MULTI_SESSION_IMPLEMENTATION_REPORT.md
?? _qa_resume.pdf / _qa_resume_full.png / 简历-AI应用开发-Agent方向.html   ← 用户个人文件
```
（branch = feature/multi-session-backend，无 commit；`git diff --check` 无 whitespace errors。）

### git diff --stat（vs main，含未跟踪新文件无法入 diff 的内容，见上方清单）
（无 commit，故无 `git log -1` 增量；分支基于 `main @ 113b6e8`。）

### 个人文件确认
用户个人文件（简历 md/pdf/png/html）**未修改（除先前已存在状态外）、未 add、未 commit** ——
后续 Commit 将仅包含 Multi-Session 相关变更（Scope Audit 已确认）。

## 14. AC → Evidence 映射（要点）

| AC | 实现 | 测试 → 结果 |
|---|---|---|
| Session 创建/查询/详情/归档/生命周期 | service + store + server 端点 | test_sessions_store/service（pass）；状态机 ACTIVE⇄ARCHIVED |
| Task 关联 Session / 相互确定 | session_id==thread_id（派生关联） | detail/tasks 聚合 + membership 测试 |
| Session A 无法访问/操作 B 的 Task | thread 作用域 + validate_task_in_session | test_sessions_service::TestTaskMembershipIsolation |
| Cancel 不跨 Session；双 Session 并发 | controller 运行路径 | test_sessions_isolation（2 pass） |
| 持久化 / 重启不丢 / Archive 不删历史 | 0002 migration + 软删除语义 | migration fresh/upgrade/幂等 + 归档守卫测试 |
| API Contract 完整一致 | MULTI_SESSION_API_SPEC.md | 与代码逐端点核对 |
| Existing + New 全绿；无 Frontend 修改 | — | 775 passed / 91 skipped / 0 failed；git audit |

## 15. 下一步（等用户 Review）

Verification 与 Scope Audit 已在上文列出，**未 Commit**。请用户 Review 后决定：Commit（仅 Multi-Session
文件）→ Push → Merge main（每步单独批准，AGENTS.md §10 / PROCESS.md §12.4）。
