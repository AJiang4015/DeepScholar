# ROADMAP.md — 工程化完善路线图（R1~R6）

> 状态：**进行中**（R1 待启动）。
> 本文件是 deepsearch-agents 从教学型 Demo 演进为"具备较完整后端工程能力的 Agent Runtime"的
> 工程化路线图，供每个阶段（R1~R6）的 INSPECTION / 验收 / REPORT 对照引用。

## 0. 命名规则（用户裁决，禁止违反）

- **Pxxx 只代表 PROBLEM.md 问题注册表编号**（P001~P006），不得用于工程化路线，禁止重新定义/复用/覆盖。
- **工程化路线统一使用 R1、R2、R3……**，按执行顺序编号。
- **设计文档统一以 Rxx 开头**，存放于 `docs/spec/`（如 `docs/spec/R01-file-security.md`）。
- 问题注册表状态更新仍走 `PROBLEM.md` + `docs/problem/`（如 R1 修复 P002/P003/P004 后同步状态）。

## 1. 最终目标（能力矩阵）

完成后形成：Architecture / Runtime / Security / Persistence / Concurrency / Observability /
Testing / Evaluation / Frontend 九个维度的能力矩阵，并按"Java 后端简历 / AI Agent 简历 /
仅面试展开"分档（见 §6）。

## 2. 已确认实施顺序（含用户对 Checkpoint 位置的裁决）

| 阶段 | 名称 | 关联问题注册表 | 新增依赖 | Architecture Gate | 本环境可验证 |
|---|---|---|---|---|---|
| **R1** | 文件安全治理 | P002 + P003 + P004（部分） | 无 | 否 | ✅ 纯函数全量单测 |
| **R2** | 会话执行状态持久化（Checkpoint） | 无对应 Open（P006 部分） | langgraph-checkpoint-sqlite | **是**（用户已批准提前到第 2 位） | ⚠️ 需用户本机装依赖验证"重启恢复" |
| **R3** | Agent Task Runtime | 无 | 无（asyncio） | 否 | ✅ async 单测 |
| **R4** | Event Persistence（事件运行时） | 无 | 无（sqlite3 stdlib） | 否 | ✅ sqlite 内存单测 |
| **R5** | Agent Evaluation | P006（测试基建延伸） | 无（pyyaml 已有） | 否 | ✅ 离线 executor + 10+ case |
| **R6** | Observability（OpenTelemetry） | 无 | opentelemetry-api/sdk（sdk 部分已可用） | **是** | ⚠️ sdk + InMemorySpanExporter 可测；完整链路受限 |

顺序说明：R2 按用户裁决提前（用户本机可安装依赖，由其完成"重启恢复"运行验证；
本环境缺 deepagents/mysql.connector，无法 import `app.agent.main_agent`，运行验证受限，
届时提供装包命令与验证清单）。R1 先行的理由：零依赖、修复 3 个已登记高危问题、是后续安全地基。

## 3. 当前架构能力地图（现状快照，2026 基线）

| 能力 | 现状 | 缺口 → 阶段 |
|---|---|---|
| Multi-Agent 编排（一主三从） | ✅ 完整（main_agent.py + subagents/* + prompts.yml） | — |
| 会话隔离（ContextVar + thread_id + session_dir） | ✅ 完整 | thread_id 未净化拼目录 → R1 |
| API 层（任务/取消/上传/文件/下载/WS） | ✅ 可用 | 上传无治理 → R1 |
| 事件推送（monitor → WS） | ⚠️ 内存态、无 event id/schema/历史 | → R4 |
| 任务调度（active_tasks dict） | ⚠️ 无并发上限/超时/重试/状态机 | → R3 |
| 检查点（InMemorySaver） | ⚠️ 重启即失 | → R2 |
| SQL 安全（P001，已完成勿重复） | ✅ sql_security.py + db_tools.py + 86 测试 | — |
| 文件安全 | ⚠️ 下载/列表有边界；上传裸存、resolve_path 逃逸 | → R1 |
| 测试 | ⚠️ 安全层 86 项；无 API/Agent/Runtime 测试 | R1~R6 逐项补 |
| 可观测 | ❌ 无 trace/span | → R6 |
| 评估 | ❌ 无 eval | → R5 |
| 前端 | ✅（本轮不动） | — |
| 工程契约（Harness） | ✅（P001 已同步 D007） | R 完成时同步 |

## 4. 每阶段范围与验收要点

### R1 文件安全治理（File Security）
- **文件**：新增 `app/utils/upload_guard.py`（类型白名单/大小上限/文件名清洗/随机存储名）、
  `app/utils/session_id.py`（thread_id 净化）；修改 `app/utils/path_utils.py`（P002 逃逸修复，
  绝对路径/`..`/`updated/` 分支补最终包含性检查，fail-closed）；修改 `app/api/server.py`
  （upload 接入 guard + 元数据登记 原名↔随机名、thread_id 入参校验）；修改
  `app/agent/main_agent.py`（复制上传文件时按元数据还原原名到 session_dir，保持
  `read_file_content` 按原名读取的接口不变）；`.gitignore` 追加 `pytest-cache-files-*/`。
- **测试**：`tests/test_upload_guard.py`、`tests/test_session_id.py`、`tests/test_path_utils.py`
  （含 P002 逃逸负路径矩阵：`../`、绝对路径、`updated/` 前缀、包含性绕过、重复 session 名）。
- **验收**：上传超限/类型不符/穿越文件名被拒；随机落盘且元数据可还原；resolve_path 无法逃出
  session_dir；thread_id 非法字符被清洗/拒绝；现有下载/列表 is_relative_to 边界保持。
- **简历**：对标 Java 文件上传安全与路径边界治理。

### R2 会话执行状态持久化（Persistent Checkpoint）
- **文件**：`app/agent/main_agent.py`（`InMemorySaver()` → `SqliteSaver`）；依赖声明
  `langgraph-checkpoint-sqlite`（Gate：Decision + 用户批准，pyproject/uv.lock 或用户本机 pip）；
  `.env.example` 视需要补 DB 路径变量。
- **验证**：checkpointer 层 sqlite 写/读/恢复单测（装包后）；**用户本机 E2E**：启动 →
  执行任务 → 重启服务 → 同一 thread_id 续跑恢复。装包命令与验证清单由 R2 阶段提供。
- **措辞纪律**：只写"会话执行状态持久化 / 重启后可恢复 thread 执行状态"，**不写"长期记忆"**。
- **简历**：AI Agent 方向亮点（会话可恢复）。

### R3 Agent Task Runtime
- **文件**：新增 `app/runtime/` 目录 + `app/runtime/task_manager.py`（TaskStatus 枚举
  pending/running/cancelling/succeeded/failed/timed_out/cancelled、TaskRecord、
  TaskManager：并发信号量上限、asyncio.wait_for 超时、可配重试+退避、cancel 语义、
  done 清理、状态变更回调供 R4 消费）；修改 `app/api/server.py`（active_tasks dict →
  TaskManager；/api/task 语义保持：同 thread 新任务替换旧任务）。
- **测试**：`tests/test_task_manager.py`（happy/failure/timeout/retry/cancel/并发上限，pytest-asyncio）。
- **约束**：不引入 Redis/Celery/Kafka（原则 4）；单进程 asyncio 语义如实描述。
- **简历**：对标 Java 线程池/CompletableFuture 的任务生命周期治理，Java 后端面试价值最高。

### R4 Event Persistence（事件运行时）
- **文件**：新增 `app/runtime/event_store.py`（sqlite3 stdlib + WAL；表 agent_events：
  event_id/thread_id/event_type/created_at/payload_json；append/query(thread_id, since_id, limit)/count）；
  修改 `app/api/monitor.py`（事件 schema 规范化含 event_id + 同步写 store，store 故障不阻断
  Agent 主链路，保留 WS 推送）；修改 `app/api/server.py`
  （`GET /api/threads/{thread_id}/events?since_id=&limit=` 历史查询；WS 可选最近 N 条重放，
  前端向后兼容）。
- **测试**：`tests/test_event_store.py`（内存 sqlite append/query/分页/边界/非法输入）+ monitor 写路径单测。
- **简历**：事件驱动 + 可回溯审计（实时订阅 + 历史查询）。

### R5 Agent Evaluation
- **文件**：`eval/cases/*.yaml`（schema：id/task/expected.agents/expected.tools/output_contains/
  output_excludes/tags，**≥10 个真实 case**：Web/SQL/RAG/文件分析/多 Agent 综合/错误输入）；
  `eval/runner.py`（加载 cases → 注入 executor → 断言 pass/fail + reason → console + JSON report）；
  `eval/executors/offline.py`（离线执行器，对 SQL 安全层/路径工具直接断言）。
- **纪律**：报告只列 pass/fail 与原因，**不伪造准确率**。
- **测试**：runner 单测（fake case/executor）+ case 文件 schema 校验测试。

### R6 Observability（OpenTelemetry）
- **文件**：新增 `app/runtime/tracing.py`（tracer 初始化：console/OTLP/none，env 控制；
  span helper 记录 trace_id/thread_id/agent/tool/duration/status/error；import 可选防护，
  缺依赖不炸主链路）；插桩点：server 请求、run_deep_agent、monitor 内 subagent/tool
  （R4 事件上关联 trace_id）。
- **测试**：`tests/test_tracing.py`（InMemorySpanExporter 断言 span 树与属性）。
- **简历**：trace_id 贯穿 request→agent→tool（Request→Agent→SubAgent→Tool→external）。

## 5. 验证总策略（环境约束，全阶段适用）

- 可 import 实测的模块：`app/utils/*`、`app/api/context`、`app/api/monitor`（fastapi 可用）、
  新增 `app/runtime/*`（stdlib）→ 逻辑全量单测。
- **不可 import**：`app.api.server`（→ main_agent → deepagents 缺失）、`app.agent.*`、
  `app.tools.db_tools`（mysql.connector 缺失）→ 这些模块的改动遵守
  **"逻辑下沉纯函数模块 + 薄层语法/静态验证 + 用户本机集成验证"** 策略（P001 已验证此模式）。
- 依赖变更（R2/R6）走 Architecture Change Gate：新增 Decision + 用户确认；本环境不可
  uv sync 时如实报告，不伪造命令输出。
- 残留 `pytest-cache-files-*` 目录为本环境沙箱产物（无法删除），R1 阶段以 .gitignore 消除 git 噪音。

## 6. 简历分档（完成后更新为最终能力矩阵）

- **Java 后端简历主推**：R3 Task Runtime、R1 文件安全治理、R4 Event Persistence。
- **AI Agent 简历亮点**：R2 Checkpoint（会话状态持久化）、R5 Evaluation、R6 Observability。
- **仅面试展开**：词法级 SQL 校验内部实现、SSE 解析细节、ReportLab 中文 PDF、tokenizer 白名单策略。

## 7. 每 R 完成报告模板（A~G）

Problem → Design → Changes → Tests → Risks → Resume → Interview（每 R 完成后按此模板 REPORT，
并在本文件"状态追踪"更新）。

## 8. 状态追踪

| 阶段 | 状态 | 完成日期 | 关键产出 | 验收证据 |
|---|---|---|---|---|
| R1 | ✅ 完成 | 2026-09-03 | resolve_path fail-closed（PathSafetyError）、upload_guard（净化/白名单/大小/随机存储名+manifest）、session_id（thread_id 净化）、三个工具安全拒绝、P002/P003/P004 → Mitigated、D008 | tests/test_path_utils.py + test_upload_guard.py + test_session_id.py 共 87 项新增；173 passed, 1 skipped；ruff 全绿；P002/P003/P004 负路径矩阵全过 |
| PostgreSQL Checkpoint Migration（ACR 后续，非 R 序号阶段） | ✅ 完成（代码+测试） | 2026-09-03 | checkpoint backend 抽象（sqlite=AsyncSqliteSaver / postgres=AsyncPostgresSaver）、AsyncConnectionPool lifespan、main_agent lazy 组装、AsyncBridgeSqliteSaver 退役、D010；版本配对 postgres-saver 3.0.5 + psycopg3 | 锁定版本隔离环境（1.1.10/4.0.3/3.0.3）全仓 215 passed, 6 skipped；PG P1–P7 实测待用户环境（见 docs/spec/2026-09-03-postgres-checkpoint-migration.md） |
| R2 | ✅ 完成（2026-09-03 由本阶段演进部分取代） | 2026-09-03 | SqliteSaver → backend 抽象（官方 AsyncSqliteSaver/AsyncPostgresSaver）、AsyncBridgeSqliteSaver 退役（D010）、全仓测试 215 passed | docs/spec/2026-09-03-postgres-checkpoint-migration.md |
| F1 Research Artifact Foundation（非 R 序号阶段） | ✅ 完成（代码+测试+PG 实测） | 2026-09-07 | app/research/（ResearchRun→SubQuestion→SearchQuery→Source→Evidence）、双后端 DDL + 零依赖 migration runner、Source 确定性注册（canonical URL）、fail-open、run 钩子；D011 | F1 新增 43 passed；PG 实测通过（RESEARCH_DSN_TEST 独立库）；全仓双 DSN 270 passed / 1 skipped（见 2026-09-08 验证） |
| F2 Claim/Citation Binding & Validation（非 R 序号阶段） | ✅ 完成（代码+测试+PG 实测） | 2026-09-08 | claims/claim_evidences/citations（0002，零 ALTER F1）、内容级幂等、validator R1–R10（纯函数）、apply_validation_outcome 单写入口、render_citations（[n] 不落库）；D012 | F2 sqlite 38 passed；全仓 sqlite-only 296 passed/15 skipped；双 DSN 310 passed/1 skipped（连跑稳定）；ruff/compileall 绿（docs/spec/2026-09-08-claim-citation-binding.md） |
| F3 Semantic Verification（非 R 序号阶段） | ✅ 完成（代码+测试+PG 实测） | 2026-09-09 | verifications 表（0003，零 ALTER F1/F2）、SemanticVerdict(5)/VerifyStatus(3 无 partial)、结构 Gate+coverage 默认优先级+allowed 覆盖、FakeVerifier 默认/real-LLM controlled、confidence policy、fingerprint 幂等、aggregation（contested 只标记）、provenance；D013 | F3 sqlite 31 passed；全仓 sqlite-only 327 passed/18 skipped；双 DSN 344 passed/1 skipped（连跑稳定）；ruff/compileall 绿（docs/spec/2026-09-09-semantic-verification.md rev2） |
| F4 Conflict Detection（非 R 序号阶段） | ✅ 完成（代码+测试+PG 实测） | 2026-09-10 | conflicts 表（0004，零 ALTER F1–F3）、SUPPORTS×CONTRADICTS 默认候选 + explicit_pairs controlled、ConflictStatus 4 态 + genuine/type invariant、integrity invariants（写前守卫）、verification FK SET NULL + signal 快照、FakeDetector 默认/real-LLM controlled、聚合 contested→pair 事实；D014 | F4 sqlite 29 passed；全仓 sqlite-only 356 passed/20 skipped；双 DSN 375 passed/1 skipped（连跑稳定）；ruff/compileall 绿（docs/spec/2026-09-10-conflict-detection.md rev2） |
| F5 Independent Evidence Corroboration（非 R 序号阶段） | ✅ **FROZEN**（IMPLEMENTATION COMPLETE / VERIFIED / FROZEN，F5 Final Gate PASS 2026-09-11） | 2026-09-11 | corroborations 表（0005，零 ALTER F1–F4）、claim 作用域 global clustering（union-find v1：R-URL/R-DOMAIN/R-TITLE/R-SHINGLE OR 归并 + chaining 锁死）、source_count/independent_count、cross-side independence（cluster 交集，仅计量）、descriptive source_profile（无 score 字段）、optional LLM review 默认 OFF、review 失败→complete+review_failed、deterministic 失败→failed、fingerprint 幂等；D015 | F5 sqlite 24 passed；全仓 sqlite-only 380 passed/20 skipped；双 DSN 405 passed/1 skipped（连跑稳定）；ruff/compileall 绿；scope audit clean；F1–F4 未污染；F5 Final Gate **PASS** → F5 **FROZEN**（docs/spec/2026-09-11-f5-audit-and-independent-corroboration.md rev2；Spec/Implementation 不再修改，limitations 保留于 Spec §Self Review） |
| F6 Conflict Reconciliation（非 R 序号阶段） | 🚧 实施完成，待 F6 Final Gate | 2026-09-12 | reconciliations 表（0006，零 ALTER F1–F5）、outcome 三态（SAME_ORIGIN_CONTRADICTION/DETAIL_INCONSISTENCY/GENUINE_CONTESTED）、Unknown≠NotIndependent 硬红线（缺 corroboration/未覆盖/malformed signal → failed）、claim register（precedence/unverified/incomplete_input/verified_consistent）、run_unresolved 只含 genuine_contested、optional LLM review 默认 OFF（不改 outcome）、fingerprint 幂等、F4/F5 signal 快照 provenance；D016 | F6 sqlite 21 passed + PG 4 passed；全仓 sqlite-only 401 passed/30 skipped；双 DSN 430 passed/1 skipped（连跑稳定）；ruff/compileall 绿（docs/spec/2026-09-12-f6-conflict-reconciliation.md rev2）；待 F6 Final Gate |
| F7 Research State Bridge（非 R 序号阶段） | 🚧 实施完成，待 F7 Final Gate | 2026-09-13 | app/research/bridge.py + extractor.py（Fake/Real gated）、main_agent 唯一 terminal finalization 接线点（fail-open）、Claim materialization（anchored/unanchored 语义）、F2–F6 orchestration（enabled/skipped_off）、finalization identity（content+universe+methods）、research_runs.metadata.finalizations 单键写入；**不新增 migration/表、不改 Agent-visible context**；D017 | F7 sqlite 19 passed + PG 4 passed；全仓 sqlite-only 420 passed/34 skipped；双 DSN 453 passed/1 skipped（连跑稳定）；ruff/compileall 绿（docs/spec/2026-09-13-f7-research-state-bridge.md rev2）；待 F7 Final Gate |
| R3 | 待启动 | — | — | — |
| R4 | 待启动 | — | — | — |
| R5 | 待启动 | — | — | — |
| R6 | 待启动 | — | — | — |
