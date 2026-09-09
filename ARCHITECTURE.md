# ARCHITECTURE.md — Architecture Contract（架构契约）

> 本文件定义 Agent 修改代码时必须尊重的结构性约束：系统边界、模块职责、依赖方向、
> 数据流、安全边界、扩展点、兼容规则、禁止改动。它不描述每个类，只回答：
> **哪些结构可以改，哪些结构不能随便改。**

## 1. 系统边界

| 子系统 | 位置 | 职责 |
|--------|------|------|
| 后端 | `app/` | FastAPI 接口层 + DeepAgents 主/子智能体 + 工具层 + WebSocket 推送 |
| 前端 | `frontend/` | React + Vite 对话式研搜界面（仅经 HTTP/WS 与后端通信） |
| 本地数据 | `docker/` | MySQL 8.4 教学库（药品/库存/销售模拟数据，compose + init SQL） |
| 外部集成 | 环境变量 | OpenAI 兼容 LLM、Tavily、RAGFlow（凭据在 `.env`，不入库） |
| 教程示例 | `examples/` | DeepAgents 章节示例脚本（独立于 app/ 运行，非产品代码） |
| 知识库样例 | `docs/knowledge_base/` | RAGFlow 导入用示例 PDF（非文档，勿改动） |

后端运行时目录（gitignore 已忽略）：`app/output/session_{id}`（会话产物）、`app/updated/session_{id}`（上传暂存）、
`app/runtime/checkpoints.sqlite*`（R2 Agent 执行状态 checkpoint DB，含 WAL 伴生文件；跨 session 的运行时状态，见 §2 app/runtime）。
注意：server.py 与 main_agent.py 都把项目根解析为 **`app/`**（不是仓库根）。

## 2. 模块职责（app/）

| 模块 | 职责 | 关键文件 |
|------|------|----------|
| app/api | HTTP/WS 接口层：任务/取消/上传/文件列表/下载/WS；ContextVar 会话上下文；monitor 事件推送 | server.py、context.py、monitor.py |
| app/agent | 智能体组装：模型初始化、主智能体、三个子智能体、提示词加载、run_deep_agent 执行入口 | main_agent.py、llm.py、prompts.py、subagents/ |
| app/tools | LangChain 工具：网络搜索/DB 查询/RAGFlow 问答/文件读取/文档生成 | tavily_tool.py、db_tools.py、ragflow_tools.py、upload_file_read_tool.py、markdown_tools.py、pdf_tools.py |
| app/utils | 无业务语义的通用工具：路径解析、Markdown→PDF 底层转换 | path_utils.py、word_converter.py |
| app/runtime | Checkpoint 运行时层：backend 抽象工厂（`AGENT_CHECKPOINT_BACKEND`=sqlite(缺省,官方 AsyncSqliteSaver)/postgres(官方 AsyncPostgresSaver + AsyncConnectionPool，pool 由 server lifespan 注入)；进程级单例 + loop 亲和、官方 setup() 自管 checkpoint 表族、DB 不可用 fail-fast）；无业务语义，无 app 内依赖 | checkpoint.py |
| app/session | Multi-Session 对话容器域（F-MultiSession；不拥有 Runtime lifecycle 权威）：Session 元数据 + 生命周期（仅 ACTIVE/ARCHIVED）+ Session↔Task 归属与隔离（session_id == thread_id，TaskRecord.thread_id 派生关联）；sessions 表族 = governance 迁移族 additive 0002；只读聚合 governance_tasks（task_count/running_tasks/latest_task）；复用 governance store/service/controller；query enrich 可选 lazy 读 research（fail-open） | app/session/models.py、store.py、service.py |
| app/research | Research Data Plane（F1+F2）：ResearchRun/SubQuestion/SearchQuery/Source/Evidence（F1）+ Claim/ClaimEvidence/Citation 与 validator R1–R10（F2，0002）；F3 semantic verification（0003 verifications）；F4 conflict detection（0004 conflicts，pair 粒度 + genuine/type invariant）；F5 corroboration（0005 corroborations，claim 作用域 global clustering + 独立性计量，只计量不裁决）；F6 reconciliation（0006 reconciliations，conflict fact + independence signal → claim-level conflict state / contested register，解释/登记不裁决）；F7 research bridge（app/research/bridge.py + extractor.py：真实 run terminal finalization 物化 candidate claims、orchestrate F2–F6、run-level research state；main_agent 唯一接线点，fail-open；不改 Agent-visible context）；RESEARCH_STORE=sqlite(缺省)/postgres/disabled；research_* 表族版本化迁移（0001–0006，F7 无新迁移）；validator 纯只读；verification/detector/review 均 fail-open 或受控（real-LLM 须显式启用，不进自动化 Gate）；呈现编号 [n] 不落库；**不依赖 app/agent**，与 checkpoint 逻辑解耦 | research/*、db/migrations/ |
| app/research（② Research Intelligence / Execution + eval；原 F9-P0） | 与上一条目同一目录内的**智能执行层**（非数据面）：projection（只读投影）/ gaps（确定性缺口信号）/ judge（LLM 语义判断）/ plan（validated follow-up plan + dedup）/ targeted（定向研究执行 + 增量 F3 验证）/ orchestrator（**业务研究编排**：单 F8 governed execution 内编排 round0 → Projection → Gap → Judge → Plan → Targeted → Stopping → Final Synthesis → F7 once；**不拥有 lifecycle / budget / cancellation / timeout / terminal authority** —— F8 Controller 仍唯一权威；不新建第二 Runtime/Controller/task；research_round 是编排计数）；eval/（Research Intelligence 确定性行为质量验证与校准：world/agents/harness/rubric/scenarios/calibration）。依赖边界见 §3：智能层允许依赖 app/runtime/governance（受治理 LLM/上下文契约）；默认 seam 仅 lazy import app.agent.main_agent / app.tools.tavily_tool / app.api.monitor。 | projection.py、gaps.py、judge.py、plan.py、targeted.py、orchestrator.py、eval/ |
| app/ragflow | RAGFlow 配置加载与调用示例 | rag_config.py、knowledge_demo.py |
| app/prompt | 提示词配置（主智能体 + 三个子智能体） | prompts.yml |

工具归属（不可混淆）：`read_file_content`、`generate_markdown`、`convert_md_to_pdf` 只归主智能体；
`internet_search` 只归网络搜索助手；`list_sql_tables` / `get_table_data` / `execute_sql_query` 只归数据库助手；
`get_assistant_list` / `create_ask_delete` 只归 RAGFlow 助手。

## 3. 依赖方向（MUST 遵守）

```text
app/api/server        → app/agent/main_agent
app/api/server        → app/session/service（Multi-Session 端点接线；session 域无 server 依赖）
app/agent/main_agent  → agent/llm、agent/prompts、agent/subagents/*、tools/*、api/context、api/monitor、runtime/checkpoint、research/*
app/agent/subagents/* → tools/*、agent/prompts
app/tools/*           → api/context、api/monitor、utils/*、ragflow/rag_config、research/*
app/research（Data/Evidence Plane 核心模块）→ 无 app 内依赖（stdlib + pydantic；psycopg 惰性 import）；不依赖 app/agent
app/research（Research Intelligence/Execution + eval）→ 允许依赖 app/runtime/governance（GovernanceExecution · make_handler · counters；F8 control 信号原样传播，不吞）；默认 seam 仅 lazy import app.agent.main_agent / app.tools.tavily_tool / app.api.monitor；不拥有 Runtime governance 权威
app/session/*         → app/runtime/governance（复用 governance service/store/controller：Task 提交/取消/列表）、app/utils/session_id（P004 字符集校验）；query enrich 仅 lazy import app.research.store（fail-open，独立只读）。app/session 不被 governance / research / agent import；不拥有任何 Runtime lifecycle 权威
app/utils/*           → 不依赖任何 app 内模块（纯函数 + 三方库）
app/runtime/checkpoint → 无 app 内依赖（stdlib + langgraph / langgraph-checkpoint / langgraph-checkpoint-sqlite / langgraph-checkpoint-postgres；aiosqlite/psycopg/psycopg-pool 惰性 import）
app/api/context       → 无（contextvars 标准库）
app/api/monitor       → api/context
```

MUST NOT：

- `app/tools/*`、`app/utils/*` 不得 import `app/agent/*`（依赖环风险）。
- 任何模块不得 import `app/api/server`（server 是入口聚合层）。
- 工具不得绕过 `app/api/context` 自行持有会话状态。
- 不得绕过 `run_deep_agent` 直接调用 `main_agent.astream`（会破坏会话目录初始化与 ContextVar 设置）。
- **F9 governed orchestrator 例外（2026-09-29，Batch6 Decision/Plan；范围收紧）**：F9-scoped 例外**只**
  允许绕过 `run_deep_agent` 的"生命周期 + F7 wrapper"，**不允许变成裸 `main_agent.astream()`**——
  F9 orchestrator（`app/research/orchestrator.py`）在同一 F8 `Controller.execute` 内每次 graph invocation
  必须完成与既有 governed execution 等价的 glue/context 注入：governance callback、recursion
  limit、research context、session/thread context、run_id、task_id、monitor context、
  cancellation/timeout 语义与必要 agent config；且保持同一 Controller.execute /
  GovernanceExecution / BudgetCounter / deadline·cancel / ResearchRun（不新建）、不触发 F7、
  不新建 Runtime/Controller/task。产品外部入口仍必须走 `run_deep_agent`（本条红线对非 F9 编排
  路径语义不变）。
  语义澄清（2026-10-02 Semantic Module Layout）：该 orchestrator 属**业务研究编排**——本例外不授予
  任何 lifecycle / budget / cancellation / timeout / terminal 权威；它们仍只属 F8 Controller。

## 4. 数据流（一次任务）

1. 前端 `POST /api/task`（body: query, thread_id?）→ server.py 创建 asyncio 后台任务。
2. `run_deep_agent(query, session_id)`：创建 `app/output/session_{id}`，把 `app/updated/session_{id}` 的上传文件 copy2 进会话目录，set ContextVar，注入工作目录指令。
3. `main_agent.astream` 执行：主智能体调度子智能体（task 工具调用）→ 子智能体调用 tools（Tavily / MySQL / RAGFlow）。
4. 工具经 `monitor.report_*` → ConnectionManager → WS `/ws/{thread_id}` 推送前端。
5. 产物写入 session_dir；前端 `GET /api/files` + `GET /api/download` 获取。
6. `finally` 中 reset ContextVar。

monitor 事件类型（前端依赖，勿改）：`tool_start`、`assistant_call`、`task_result`、`task_cancelled`、`session_created`、`error`，
payload 统一为 `{"type":"monitor_event","event":...,"message":...,"data":...,"timestamp":...}`。
事件 MUST 经公共 `report_*` API 发出；`_emit` 是私有方法（main_agent.py:150 现有 `error` 事件直接调用 `_emit`，属既有债务），不得新增跨模块 `_emit` 调用。

## 5. 安全边界（当前实现）

- **文件下载/列表**：`resolve()` + `is_relative_to(output_dir)` 校验（server.py 下载/列表接口）—— MUST NOT 移除，这是现有 fail-closed 防护。
- **会话目录隔离**：`resolve_path` 全分支统一 `resolve()` + `is_within_directory(session_dir)` 包含性校验，越界抛 `PathSafetyError`（app/utils/path_utils.py，P002 已 Mitigated，R1）；工具层单独捕获并返回"安全拒绝"。
- **上传边界**：文件名净化（basename + 非法字符清洗）+ 类型白名单 + 大小上限 + 随机存储名 + manifest 原名映射（app/utils/upload_guard.py，P003 已 Mitigated，R1）。manifest 只是名称映射，**不是授权边界**。
- **thread_id 净化**：`[A-Za-z0-9_-]{1,128}`，非法换新/拒绝（app/utils/session_id.py，P004 已 Mitigated，R1）。
- **SQL 只读**：工具层强制——只读语句类型校验 + 表名白名单 + 参数绑定 + 审计日志
  （`app/utils/sql_security.py` 纯函数层 + `app/tools/db_tools.py` 编排；P001 已 Mitigated，见 D007）。
  部署层"数据库只读账号"未做，属残余风险；白名单数据源为 information_schema 动态发现
  （与 list_sql_tables 的 SHOW TABLES 同集合，拿不到即拒绝，fail-closed）。
- **checkpoint 执行状态持久化（R2 演进：backend 抽象）**：Agent 执行状态由
  `AGENT_CHECKPOINT_BACKEND` 选择后端——`sqlite`（缺省，local/test fallback；官方
  `AsyncSqliteSaver`，默认 `app/runtime/checkpoints.sqlite`，env `AGENT_CHECKPOINT_DB`
  可覆盖，gitignore 已忽略）；`postgres`（生产；官方 `AsyncPostgresSaver` +
  `AsyncConnectionPool`，DSN 来自 env `AGENT_CHECKPOINT_DSN`，pool 由 FastAPI
  lifespan 创建/关闭并注入 runtime）。checkpoint 表族由官方 saver 的 `setup()` 版本化
  自管（checkpoint_migrations/checkpoints/checkpoint_blobs/checkpoint_writes），仓库
  迁移体系禁止触碰。两种后端均位于 output/ 会话产物可达范围之外；初始化/建表失败时
  fail-fast 抛错，**不静默回退内存或切换后端**。属 Checkpoint 执行状态持久化，
  非 BaseStore / Research Artifact（research_* 表族归 app/research，另一机制、
  另一迁移体系）。
- **Research Artifact Store 配置（F1）**：`RESEARCH_STORE`=sqlite（缺省，本地/测试
  fallback，`RESEARCH_DB` 覆盖，默认 `app/runtime/research.sqlite`）| postgres（生产，
  `RESEARCH_DSN` 必填）| disabled；research_* 表族由 `app/research/migrations` 版本化
  自管（`schema_migrations`），与 checkpoint 表族互不触碰；配置非法/运行期故障 →
  research plane disabled（fail-open，不阻断 Agent 主链路；工具返回原值）。
- **凭据**：`.env` 不入库；新增凭据 MUST 只读环境变量并同步 `.env.example`，MUST NOT 硬编码。
- **凭据**：`.env` 不入库；新增凭据 MUST 只读环境变量并同步 `.env.example`，MUST NOT 硬编码。
- **已知开放边界**：无认证（P005）、上传内容安全扫描（MIME/魔数，Non-Goal）、
  CORS `allow_origins=["*"]` + `allow_credentials=True`（server.py 中间件配置）。

## 6. 兼容规则（对外契约，变更需 Decision）

- **HTTP 端点集合与语义**：`POST /api/task`、`POST /api/task/{id}/cancel`、`POST /api/upload`、`GET /api/files`、`GET /api/download`、`WS /ws/{thread_id}`。前端 `frontend/src/lib/api.ts`、`thread.ts`、`config.ts`（VITE_API_BASE_URL / VITE_WS_BASE_URL）、`hooks/useDeepAgentSession.ts` 依赖它们。
- **Multi-Session 端点（additive；2026-10-03，契约 = 仓库根 MULTI_SESSION_API_SPEC.md）**：
  `POST /api/sessions`、`GET /api/sessions`、`GET /api/sessions/{session_id}`、`DELETE /api/sessions/{session_id}`（逻辑归档）、
  `PATCH /api/sessions/{session_id}`（v1 仅 unarchive）、`POST /api/sessions/{session_id}/tasks`、
  `GET /api/sessions/{session_id}/tasks`、`POST /api/sessions/{session_id}/tasks/{task_id}/cancel`。
  身份契约：session_id == thread_id（1:1）；TaskRecord.parent_session 保持 NULL/unused；旧端点与旧客户端语义不变。
- **WS 事件 schema**：见 §4；`EventStream.tsx` 等组件依赖，MUST NOT 静默修改。
- **WS 心跳协议**：客户端发送任意文本心跳，服务端回复 `{"type":"pong","message":...}`（server.py websocket_endpoint；前端 useDeepAgentSession.ts 约每 25s 发送）。修改心跳协议必须同步前端。
- **prompts.yml 键结构**：`main_agent.system_prompt`；`sub_agents.{tavily,db,ragflow}.{name,description,system_prompt}`；`app/agent/prompts.py` 与三个子智能体按这些键注册。MUST NOT 改键名而不改代码。
- **LangChain 工具签名**：`@tool` 的函数签名与 docstring 暴露给模型，MUST NOT 随意改参数名/默认值（影响模型调用）。

## 7. 扩展点（如何加东西）

- **新子智能体**：`app/agent/subagents/x.py`（字典式注册）+ `prompts.yml` 新增 `sub_agents.x` + 加入 `main_agent.py` 的 `subagents` 列表。
- **新工具**：`app/tools/x.py`（`@tool` + `monitor.report_tool`）+ 挂到对应智能体 tools 列表。
- **提示词调优**：只改 `prompts.yml`，不硬编码在 py 文件。
- **新文件格式支持**：`upload_file_read_tool.py` 分支扩展 + `word_converter.py` 同模式。
- **新 Research 实体/字段（F1 起）**：`app/research/schemas.py`（模型）+ `app/research/registry.py`
  （写入函数）+ `db/migrations/NNNN_*.{sqlite,postgres}.sql`（runner 幂等执行，版本递增）。

## 8. Architecture Change Gate（以下改动 MUST 先记录 Decision）

- 新增核心依赖（如 pytest，见 D006）
- 更换基础设施（DB / 搜索 / 知识库服务）
- 修改公共 API（HTTP 端点、WS schema）
- 修改核心抽象（ContextVar 机制、resolve_path 语义、智能体组装方式）
- 移动模块职责
- 改变安全边界（认证、SQL 强制只读、路径隔离策略）
- 改变数据持久化方案（InMemorySaver 更换）
- 引入新的架构层
- 删除现有架构约束

MUST NOT 因"重构更优雅"而直接实施上述改动。

## 9. Forbidden Changes（红线）

- MUST NOT 移除 `/api/download` 与 `/api/files` 的 output 包含性检查。
- MUST NOT 移除 `resolve_path` 的会话目录包含性校验（P002 已修复，R1）。
- MUST NOT 静默修改 WS 事件 schema 或 monitor event 类型。
- MUST NOT 在未记录 Decision 时引入认证/权限（P005 边界）。
- MUST NOT 在 `app/tools` 或 `app/utils` 中 import `app/agent`。
- MUST NOT 硬编码 `.env` 凭据，或新增环境变量而不更新 `.env.example`。
- MUST NOT 绕过 `run_deep_agent` 直接调用 `main_agent.astream`。
- MUST NOT 修改 `examples/` 或 `docs/knowledge_base/` 来"让测试通过"（它们是教程资源，与测试无关）。
- MUST NOT 在未走 Architecture Change Gate 时引入新的核心依赖。
- MUST NOT 以工具 `__main__` 调试块（markdown_tools.py / upload_file_read_tool.py / pdf_tools.py 底部）的输出作为验证证据——它们硬编码 `./examples/test_docs` 绕过会话目录机制，仅限本地调试。

## 10. 当前已知问题

见 PROBLEM.md 索引与 docs/problem/ 记录（P001–P006）；修改相关代码前 MUST 阅读对应问题文件。
