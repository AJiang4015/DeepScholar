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
| app/runtime | Checkpoint 运行时层：SQLite Checkpointer 工厂（进程内单例复用、`AGENT_CHECKPOINT_DB` 路径配置、SqliteSaver 异步桥接、DB 不可写 fail-fast）；无业务语义，无 app 内依赖 | checkpoint.py |
| app/ragflow | RAGFlow 配置加载与调用示例 | rag_config.py、knowledge_demo.py |
| app/prompt | 提示词配置（主智能体 + 三个子智能体） | prompts.yml |

工具归属（不可混淆）：`read_file_content`、`generate_markdown`、`convert_md_to_pdf` 只归主智能体；
`internet_search` 只归网络搜索助手；`list_sql_tables` / `get_table_data` / `execute_sql_query` 只归数据库助手；
`get_assistant_list` / `create_ask_delete` 只归 RAGFlow 助手。

## 3. 依赖方向（MUST 遵守）

```text
app/api/server        → app/agent/main_agent
app/agent/main_agent  → agent/llm、agent/prompts、agent/subagents/*、tools/*、api/context、api/monitor、runtime/checkpoint
app/agent/subagents/* → tools/*、agent/prompts
app/tools/*           → api/context、api/monitor、utils/*、ragflow/rag_config
app/utils/*           → 不依赖任何 app 内模块（纯函数 + 三方库）
app/runtime/checkpoint → 无 app 内依赖（仅 stdlib + langgraph / langgraph-checkpoint-sqlite 三方库）
app/api/context       → 无（contextvars 标准库）
app/api/monitor       → api/context
```

MUST NOT：

- `app/tools/*`、`app/utils/*` 不得 import `app/agent/*`（依赖环风险）。
- 任何模块不得 import `app/api/server`（server 是入口聚合层）。
- 工具不得绕过 `app/api/context` 自行持有会话状态。
- 不得绕过 `run_deep_agent` 直接调用 `main_agent.astream`（会破坏会话目录初始化与 ContextVar 设置）。

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
- **checkpoint DB 隔离（R2）**：Agent 执行状态 DB 默认 `app/runtime/checkpoints.sqlite`（env `AGENT_CHECKPOINT_DB` 可覆盖，gitignore 已忽略）；位于 app 内而非 output/ 会话产物区，天然不在 `/api/files`、`/api/download` 的 output 包含性校验可达范围内；DB 不可写/建表失败时工厂 fail-fast 抛错，**不静默回退内存 Checkpointer**。属 Checkpoint 执行状态持久化，非 BaseStore / 长期记忆、非多实例共享方案。
- **凭据**：`.env` 不入库；新增凭据 MUST 只读环境变量并同步 `.env.example`，MUST NOT 硬编码。
- **已知开放边界**：无认证（P005）、上传内容安全扫描（MIME/魔数，Non-Goal）、
  CORS `allow_origins=["*"]` + `allow_credentials=True`（server.py 中间件配置）。

## 6. 兼容规则（对外契约，变更需 Decision）

- **HTTP 端点集合与语义**：`POST /api/task`、`POST /api/task/{id}/cancel`、`POST /api/upload`、`GET /api/files`、`GET /api/download`、`WS /ws/{thread_id}`。前端 `frontend/src/lib/api.ts`、`thread.ts`、`config.ts`（VITE_API_BASE_URL / VITE_WS_BASE_URL）、`hooks/useDeepAgentSession.ts` 依赖它们。
- **WS 事件 schema**：见 §4；`EventStream.tsx` 等组件依赖，MUST NOT 静默修改。
- **WS 心跳协议**：客户端发送任意文本心跳，服务端回复 `{"type":"pong","message":...}`（server.py websocket_endpoint；前端 useDeepAgentSession.ts 约每 25s 发送）。修改心跳协议必须同步前端。
- **prompts.yml 键结构**：`main_agent.system_prompt`；`sub_agents.{tavily,db,ragflow}.{name,description,system_prompt}`；`app/agent/prompts.py` 与三个子智能体按这些键注册。MUST NOT 改键名而不改代码。
- **LangChain 工具签名**：`@tool` 的函数签名与 docstring 暴露给模型，MUST NOT 随意改参数名/默认值（影响模型调用）。

## 7. 扩展点（如何加东西）

- **新子智能体**：`app/agent/subagents/x.py`（字典式注册）+ `prompts.yml` 新增 `sub_agents.x` + 加入 `main_agent.py` 的 `subagents` 列表。
- **新工具**：`app/tools/x.py`（`@tool` + `monitor.report_tool`）+ 挂到对应智能体 tools 列表。
- **提示词调优**：只改 `prompts.yml`，不硬编码在 py 文件。
- **新文件格式支持**：`upload_file_read_tool.py` 分支扩展 + `word_converter.py` 同模式。

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
