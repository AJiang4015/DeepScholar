# DECISION.md — Engineering Decision Record（工程决策记录）

> 只记录具有长期价值的工程决策及其**理由（Reasoning）**，不记录流水账（History）。
> 不记录：每次代码修改、每次测试、普通实现细节、开发时间线。
> 应记录：架构选择、技术选型、安全策略、API 兼容策略、数据模型选择、模块边界变化、重大 Trade-off、被明确拒绝的方案。
>
> 本文件包含两类决策：仓库既有决策（D001–D003，从实现与 README 还原）与 Harness 建立决策（D004–D006）。

---

## D001 — SQL 只读约束：设计意图为提示词层，但约束文本从未落地（实际零约束）

- **Status**: Accepted
- **Context**: 教学项目需要演示模型自主编写复杂只读 SQL（JOIN / 聚合 / 子查询）；在工具层强校验会增加教学前置知识。代码注释（db_tools.py:193）记录的设计意图是把只读约束放在提示词层。
- **Decision**: `execute_sql_query`（app/tools/db_tools.py）不校验只读性。**意图**是提示词层约束模型生成只读查询，但该约束文本从未写入 prompts.yml 或任何工具 docstring——实际状态为**零约束**（全仓库「只读」仅出现在 db_tools.py:193 注释）。
- **Alternatives**: 工具层白名单校验；数据库只读账号。
- **Rejected Alternatives**: 工具层校验（教学顺序考虑，推迟到生产化阶段）；只读账号（部署复杂度，超出教学范围）。
- **Consequences**: 教学链路直观；代价是模型可控输入可执行任意写 SQL（P001），且注释与实现不一致造成虚假安全感。
- **Constraints Created**: 任何修复（在提示词补充只读指令，或工具层强制校验）必须走 Architecture Change Gate（ARCHITECTURE.md §8）并新增测试；禁止在无 Decision 时删除 db_tools.py:193 的意图注释（它记录了设计理由）。
- **Related Problems**: P001
- **Related Architecture**: ARCHITECTURE.md §5（安全边界）

## D002 — 会话隔离采用 ContextVar + output/session_{id} 目录

- **Status**: Accepted
- **Context**: 多会话并发需要隔离工作目录与工具上下文。
- **Decision**: thread_id/session_id 标识会话；ContextVar（app/api/context.py）传递 session_dir 与 thread_id；文件工具统一经 `resolve_path` 解析路径。
- **Alternatives**: 显式传参贯穿工具链；请求对象传递。
- **Rejected Alternatives**: 显式传参会改动全部工具签名（教学复杂度）。
- **Consequences**: 工具无需感知调用链；代价是 ContextVar 必须正确 reset（main_agent.py finally），且路径隔离完全依赖 resolve_path 的正确性（P002）。
- **Constraints Created**: 文件类工具必须经由 resolve_path；新增涉及路径的工具必须复用 context 机制。
- **Related Problems**: P002, P004
- **Related Architecture**: ARCHITECTURE.md §2（模块职责）、§5（安全边界）

## D003 — 教学边界：不覆盖生产治理能力

- **Status**: Accepted
- **Context**: README「能力边界」声明不覆盖：登录/权限/多租户、上传安全扫描、任务队列、事件持久化、评测、监控等。
- **Decision**: 保持无认证、无任务队列、无事件持久化的现状。
- **Alternatives**: 引入认证与权限体系。
- **Rejected Alternatives**: 引入认证会破坏教程章节与 git 分支（02–14）对照，且超出项目定位。
- **Consequences**: 攻击面明确（P005）；公网部署须由外部网关/反向代理承担鉴权。
- **Constraints Created**: 未经用户明确要求，不得引入认证/权限系统；CORS 与无鉴权接口为既定边界。
- **Related Problems**: P005
- **Related Architecture**: ARCHITECTURE.md §5（安全边界）、§9（Forbidden Changes）

## D004 — 建立 Agent Runtime Harness（六个核心契约文件 + 独立 Problem Detail 文件）

- **Status**: Accepted
- **Context**: 需要约束、引导、验证、审查 Coding Agent 的行为，使其可控、可验证、可审查、可追踪、架构感知、范围受限、最小修改、证据完成。
- **Decision**: 采用六个核心契约文件（AGENTS.md 宪法 / PROCESS.md 执行状态机 / PROBLEM.md 索引 / TESTING.md 验证契约 / DECISION.md 决策 / ARCHITECTURE.md 架构契约）+ `docs/problem/` 下独立 Problem Detail 文件的结构；PREFLIGHT 与 COMPLETION 两个 Gate；完成须凭证据，Harness 可以拒绝完成。
- **Alternatives**: 单一巨型 AGENTS.md；每类规则散落各处。
- **Rejected Alternatives**: 单一文件无法承载六类职责且违反职责唯一性；散落各处无法审查。
- **Consequences**: 规则归属唯一、可审查、可演进；代价是 Agent 需按触发条件读多个文档（AGENTS.md §3 定义了触发条件）。
- **Constraints Created**: 新规则必须按 AGENTS.md §9 归属判断放入正确文件；不得在 AGENTS.md 堆规则。
- **Related Problems**: 无
- **Related Architecture**: 全文件

## D005 — Harness 与代码文档使用中文

- **Status**: Accepted
- **Context**: 仓库注释、提示词、README 均为中文，教程面向中文读者。
- **Decision**: 六个核心文件与 docs/problem/ 使用中文编写；规范关键词（MUST / MUST NOT / REQUIRED / FORBIDDEN）保留英文以维持机器可校验性。
- **Alternatives**: 全英文。
- **Rejected Alternatives**: 与仓库语言不一致，增加 Agent 理解摩擦。
- **Consequences**: 中文读者低摩擦；英文规范关键词保持规范性。
- **Constraints Created**: 后续新增 Harness 内容沿用中文 + 英文规范关键词。
- **Related Problems**: 无

## D006 — 测试策略：不虚构测试框架；安全改动必须引入 pytest

- **Status**: Accepted
- **Context**: 仓库无任何自动化测试（P006）；TESTING.md 需要一个可落地的验证契约。
- **Decision**: 现状下验证以 ruff / compileall / pre-commit / 手动 E2E 为准（TESTING.md §1）；任何安全敏感改动（P001–P004 相关）必须引入 pytest 自动化测试；引入 pytest 为核心依赖变更，须经 Architecture Change Gate 与用户确认。
- **Alternatives**: 声明"无测试也可完成"；一次性补全全量测试。
- **Rejected Alternatives**: 无测试声明违反证据契约；全量补测超出最小修改原则。
- **Consequences**: 安全回归有防线；测试基础设施随第一个安全改动落地。用户拒绝引入 pytest 时，安全改动的测试要求不消失——相应验收标准保持未满足（NOT COMPLETE，见 TESTING.md §5）。
- **Constraints Created**: 新增测试不得依赖外部服务在线（LLM/Tavily/RAGFlow 需 mock 或 skip）；MySQL 测试用 docker compose 本地库；tests/ 建立后 TESTING.md §1 验证命令需更新。
- **Related Problems**: P006
- **Related Architecture**: ARCHITECTURE.md §8（Architecture Change Gate）

## D007 — P001 修复：工具层强制只读 + 表名白名单 + 参数绑定 + 审计日志

- **Status**: Accepted
- **Context**: P001 登记 `execute_sql_query` 实际零约束（可执行任意写 SQL，autocommit=True）。
  用户任务（2026 SQL 安全加固）要求小范围、可验证、可回滚的工具层改造，并明确要求 pytest
  与 tests/ 目录——即 D006 Architecture Change Gate（引入 pytest 需用户确认）的用户授权。
- **Decision**:
  - 新增纯函数校验层 `app/utils/sql_security.py`（stdlib-only）：`validate_read_only_sql`
    （语句类型 + 单语句 + 注释/大小写/多语句绕过处理 + 拒绝 SELECT ... INTO OUTFILE/DUMPFILE）、
    `extract_table_names`（FROM/JOIN/DESC/逗号表列表/WHERE 子查询；派生表、CTE、表函数 fail-closed）、
    `validate_allowed_tables`（表名白名单）、`normalize_plain_table_name`、`validate_params`、
    `prepare_read_only_query`（组合校验，SQL 与 params 原样返回，值绝不拼进 SQL 字符串）。
  - `execute_sql_query(query, params=None)`：改为「校验 → 白名单 → 参数绑定执行 → 审计」链路；
    `get_table_data` 表名规范化 + 白名单；`list_sql_tables` 语义不变并作为白名单数据源
    （information_schema 动态发现与 SHOW TABLES 同集合，拿不到即拒绝）。
  - 新增最小应用级审计日志（stdlib logging，记录类型/语句/结果/耗时/thread_id，不记录参数值）。
  - db 子智能体提示词（prompts.yml）补充两行只读约束（最小修改）。
  - 新增 tests/：test_db_tools.py（纯函数安全测试）+ test_db_tools_mysql_integration.py
    （真实 MySQL 集成测试，无环境自动 skip）。
  - 未修改 pyproject.toml / uv.lock：本环境 uv 缓存不可写、pytest 以系统 Python 运行；
    pytest 作为 dev dependency 的声明留待后续（P006 残余）。
- **Alternatives**: 引入完整 SQL parser（sqlparse/sqlglot）——超出最小修改，用户明确不要求；
  数据库只读账号——部署层改动，未做（作为残余风险记录）。
- **Rejected Alternatives**: 维持提示词层约束（零约束/虚假安全感，fail-closed 不满足）；
  仅改注释或 docstring（无行为变化）。
- **Consequences**: 工具层 fail-closed；教学链路保留（合法 JOIN / 聚合 / WHERE 子查询可用）；
  派生表、WITH CTE、表函数被拒属已知限制（宁拒勿放）；提示词与实现不再互相矛盾。
- **Constraints Created**: 校验函数保持纯函数（不 import 驱动 / LangChain / FastAPI）以便无 DB 单测；
  执行顺序固定为「先校验后执行」；审计不得记录参数值；`query` 必填参数名不变（兼容模型调用），
  `params` 为新增可选参数。
- **Related Problems**: P001
- **Related Architecture**: ARCHITECTURE.md §5（安全边界）、§6（LangChain 工具签名）

## D008 — R1 文件安全治理：resolve_path fail-closed + 上传治理 + thread_id 净化

- **Status**: Accepted
- **Context**: P002（resolve_path 三处逃逸，High）、P003（上传路径穿越，High）、P004（thread_id
  未净化拼目录，Medium）均为 Open。用户以 Spec 流程批准 R1（docs/spec/2026-09-03-R1-file-security.md），
  属"改变安全边界/路径隔离策略"（ARCHITECTURE.md §8 Gate）。
- **Decision**:
  - `resolve_path`（app/utils/path_utils.py）重构为统一出口：所有分支最终 `resolve()` +
    `is_within_directory(session_dir)` 包含性校验（Windows normcase 大小写不敏感），越界抛
    `PathSafetyError`（独立于普通 IO/业务异常）；`updated/` 前缀映射回 session_dir basename，
    不再 resolve 到进程 CWD；清洗分支混入 `..` 显式拒绝。
  - 上传治理（app/utils/upload_guard.py 纯函数）：文件名净化（basename + 非法字符清洗，保留
    中文/空格/括号）、类型白名单（与 read_file_content 对齐）、大小上限（20MB 默认，env 覆盖）、
    **随机存储名 + manifest 原名映射**（用户批准方案）；manifest 只是名称映射，**不是文件访问
    授权边界**——无论 manifest 如何篡改，最终文件访问一律经 resolve_path 的 session 包含性校验。
    server.py `/api/upload` 重写（净化 → 白名单 → 流式限大小 → 随机名落盘 → manifest 原子写）；
    main_agent.py 复制段按 manifest 还原原名（无 manifest 回退旧行为），模型按原名读取语义不变。
  - thread_id 净化（app/utils/session_id.py）：安全字符集 `[A-Za-z0-9_-]{1,128}`；
    `/api/task` 非法/缺失换新 uuid（前端接受响应 id）、`/api/upload` 与 `WS` 非法拒绝/关连。
  - 三个文件工具单独捕获 `PathSafetyError` 返回"安全拒绝：..."，不被宽泛 except 吞并。
- **Alternatives**: 上传净化原名直接落盘（无 manifest）——被拒（用户明确批准随机名 + manifest 方案）；
  完整 MIME/魔数内容扫描——Non-Goal（保持轻量）。
- **Rejected Alternatives**: 维持"外部绝对路径原样返回"（伪安全，fail-closed 不满足）；
  仅拒绝不换新 thread_id（破坏前端兼容）。
- **Consequences**: P002/P003/P004 三个注册高危/中危问题修复（Problem 状态更新为 Mitigated）；
  工具越界路径现在返回显式中文安全拒绝；上传链路与 Agent 读取链路解耦（原名/存储名分离）。
- **Constraints Created**: manifest 永不作为授权边界；新上传治理逻辑保持纯函数可单测；
  PathSafetyError 必须由工具单独捕获，禁止宽泛 except 吞并安全异常；前端 upload 返回结构兼容
  （files 仍为原名列表）。
- **Related Problems**: P002、P003、P004
- **Related Architecture**: ARCHITECTURE.md §5（安全边界）、§6（LangChain 工具签名）、§9（红线）

## D009 — R2 会话执行状态持久化：InMemorySaver → SQLite Checkpointer

- **Status**: Accepted
- **Context**: Agent 会话执行状态只存在于进程内 `InMemorySaver`（main_agent.py 模块级 dict），
  服务重启（uvicorn reload / 崩溃 / 部署）后所有 thread 状态丢失，用户无法"中断后继续同一研究任务"。
  用户以 Spec 流程批准 R2（docs/spec/2026-09-03-R2-persistent-checkpoint.md）；属"改变数据持久化方案"
  （ARCHITECTURE.md §8 Gate），故记录本 Decision。
- **Decision**:
  - `main_agent` 的 checkpointer 由 `InMemorySaver()` 换为 `app/runtime/checkpoint.get_sqlite_checkpointer()`
    （文件型 SQLite，langgraph-checkpoint-sqlite）；DB 路径由 env `AGENT_CHECKPOINT_DB` 覆盖，
    默认 `app/runtime/checkpoints.sqlite`（gitignore 已忽略）；进程内单例复用同一连接；
    DB 不可写/建表失败时 fail-fast 抛 RuntimeError，**不静默回退内存**。
  - **AsyncBridgeSqliteSaver**：真实探测（两套锁定版本环境）发现官方**同步** `SqliteSaver` 的
    a* 方法（aget_tuple/aput/aput_writes/alist）一律抛 `NotImplementedError`，与 main_agent 的
    async `astream` **不兼容**（InMemorySaver 能用是因为实现了异步方法）。故新增
    `AsyncBridgeSqliteSaver(SqliteSaver)`，把异步方法桥接到同步实现（asyncio.to_thread 默认线程池），
    同步方法原样继承 → 同一实例同时支撑 sync invoke（重启恢复测试）与 async astream（生产路径）。
  - **版本兼容选型**：仓库锁定 langgraph==1.1.10 / langgraph-checkpoint==4.0.3 →
    选用 `langgraph-checkpoint-sqlite==3.0.3`（requires checkpoint>=3,<5.0.0）；最新 3.1.1
    要求 checkpoint>=4.1.0，与锁定 4.0.3 不兼容，仅用于本机独立验证（3.1.1 + 系统 4.1.1），
    **不改 uv.lock / pyproject.toml**（安装由用户在运行环境执行，Spec §10）。
  - **SQLite 定位**：单实例轻量方案（单进程 asyncio 调度 + SqliteSaver 内部 Lock + WAL）；
    **多实例共享 checkpoint 需 Postgres，不属于 R2**（单独决策）。
- **Alternatives**: 官方 `AsyncSqliteSaver`（aiosqlite）——构造需 running-loop（`asyncio.get_running_loop()`）
  且引入 aiosqlite，模块导入期构造不可行、偏离 Spec 的 SqliteSaver 字面契约，未选；
  保持 InMemorySaver——R2 目标（跨重启恢复）未达成，拒绝；改 astream 为同步驱动——改动执行链路与
  monitor 异步语义，偏离最小修改，拒绝。
- **Rejected Alternatives**: BaseStore / 长期记忆 / 跨会话知识库（Store 层另一机制，Non-Goal）；
  Postgres / Redis 等新依赖（Non-Goal）；升级/降级既有 LangGraph 锁定依赖（Non-Goal）。
- **Consequences**: thread_id 从"进程内临时会话键"升级为"跨重启的执行身份"；
  产物（output/session_{id} 文件）与执行状态（SQLite checkpoint）双持久化互补；
  checkpoint 只覆盖图状态/消息/pending writes，**不自动重放外部副作用**（搜索/DB/RAGFlow/文件），
  幂等性需上层设计；main_agent 模块导入即建 DB 文件，运行时目录不可写则启动 fail-fast。
- **Constraints Created**: DB 路径只能来自 `AGENT_CHECKPOINT_DB` 或默认路径；checkpoint DB 属
  跨 session 运行时状态，禁止放入 output/session_{id} 会话语义区；app/runtime 保持无 app 内依赖；
  新增异步/持久化能力不得改变 thread_id 语义、WS/HTTP schema、monitor 事件与前端契约。
- **Related Problems**: 无新增（P006 测试基础设施残余仍 Open；R2 测试沿用系统 Python + tests/ 约定）
- **Related Architecture**: ARCHITECTURE.md §2（app/runtime 模块）、§3（依赖方向）、§5（checkpoint DB 隔离）、§8（数据持久化 Gate）

**Update（2026-09-02，用户授权——依赖正式补录）**：`langgraph-checkpoint-sqlite==3.0.3`
已**正式补入** `pyproject.toml` / `requirements.txt` / `uv.lock`，成为 R2 的**正式运行时依赖**
（app/runtime/checkpoint.py 实际 import `langgraph.checkpoint.sqlite`，此前未声明，`uv sync`
会将其清理导致 R2 启动失败）。版本固定 **3.0.3**（与锁定 langgraph==1.1.10 /
langcheckpoint==4.0.3 匹配；最新 3.1.1 要求 checkpoint>=4.1.0 不兼容），**未升级任何其他
LangGraph/LangChain 依赖**。本更新推翻了原决策中"不改 uv.lock / pyproject.toml"的安排。
验证：`uv sync --frozen` 通过；`tests/test_checkpoint_recovery.py`（R2 restart recovery）全绿；
完整 pytest / ruff / compileall 通过（详见 R2 spec §12）。
