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

## D010 — PostgreSQL Checkpoint Migration：backend 抽象 + 官方 AsyncSaver（取代 D009 的 Bridge）

- **Status**: Accepted
- **Context**: 项目进入 Evidence-Grounded Deep Research 演进，多任务并发 / 事件持久化 /
  Research Artifact（后续 F1）/ 多实例部署对持久化底座提出新要求；SQLite 单写者 + 自定义
  `AsyncBridgeSqliteSaver` 不满足长期目标。用户批准 `ARCHITECTURE_CHANGE_REVIEW.md`
  （G1–G8 全通过）与 `docs/spec/2026-09-03-postgres-checkpoint-migration.md`。
  属"改变数据持久化方案 + 新增核心依赖"（ARCHITECTURE.md §8 Gate）。
- **Decision**:
  - 生产统一 PostgreSQL，SQLite 仅 local/test fallback；不引入 Redis/Neo4j/Vector/Kafka。
  - `AGENT_CHECKPOINT_BACKEND`=sqlite(缺省)/postgres；sqlite 用官方 `AsyncSqliteSaver`
    （aiosqlite），postgres 用官方 `AsyncPostgresSaver` + `AsyncConnectionPool`
    （FastAPI lifespan 持有/注入）；**删除 `AsyncBridgeSqliteSaver`**（同版本官方
    AsyncSaver 已存在且 aiosqlite 已在锁内，原 workaround 无必要）。
  - 版本配对：`langgraph-checkpoint-postgres==3.0.5`（requires checkpoint>=2.1.2,<5，
    兼容锁定 4.0.3；3.1.x 需 >=4.1.0 不可用）+ `psycopg[binary]>=3.2` +
    `psycopg-pool>=3.2` + `aiosqlite>=0.20`（转直接依赖）；**冻结** langgraph 1.1.10 /
    checkpoint 4.0.3 / sqlite-saver 3.0.3 / deepagents 0.5.7 / langchain 1.2.17。
  - `main_agent` 改为 loop 内 lazy 组装（`get_main_agent()`，官方 AsyncSaver 构造绑定
    loop）；`run_deep_agent` 对外契约不变。
  - Replay 边界：Execution Replay（checkpoint 全历史 + parent 链 + LangGraph graph runtime
    resume/time-travel）与 Research Reconstruction（artifact store，F1）逻辑解耦；本阶段
    只做实 Execution Replay 数据面，**不开放 resume 产品入口**，不实现副作用幂等
    （契约记录于 migration spec §4.10）。使用 full saver，禁止 shallow。
  - 旧 SQLite checkpoint 不自动迁移、不删除；切换后旧 thread 不跨 backend 续跑（文档化）。
- **Alternatives**: 升级 LangGraph 到支持更高版本/换库——冻结约束下不成立；自研通用
  Bridge 包 sync PostgresSaver——浪费 PG 原生 async/pool（ACR 选项 C，拒绝）；
  保留 SQLite 单机方案——不满足多实例共享与长期底座（拒绝）。
- **Rejected Alternatives**: 引入 Alembic（research_* 表族迁移暂用零依赖版本化 SQL +
  轻量 runner；checkpoint 表族归 saver.setup()）；事件/artifact 同层混存。
- **Consequences**: checkpoint_* 表族由官方 setup() 自管；research_* 表族（F1）另行
  迁移体系；fail-fast 语义保持（从模块 import 期移至 lifespan prewarm/首次 run，已文档化）；
  AsyncSaver 的 sync 方法仅异线程可用 → 恢复测试 runner 改 async 写法（语义判据不变）。
- **Constraints Created**: `app/runtime/checkpoint.py` 保持无 app 内依赖；`AGENT_CHECKPOINT_*`
  env 只读、`.env.example` 同步；跨 loop 复用应用注入 pool 报错；禁止修改 checkpoint 表族；
  PG 测试仅允许独立测试库（`AGENT_CHECKPOINT_DSN_TEST`）。
- **Related Problems**: 无新增
- **Related Architecture**: ARCHITECTURE.md §2（app/runtime）、§3（依赖方向）、§5（checkpoint 持久化）、§8（持久化 Gate）
- **验证（本环境，锁定版本隔离环境）**：锁定环境（langgraph 1.1.10 / checkpoint 4.0.3 /
  sqlite-saver 3.0.3 / langchain-core 1.3.3）全仓 pytest **215 passed, 6 skipped**
  （6 skipped = 5 PG 门控 + 1 MySQL 集成，环境无对应服务）；replay probe 复跑通过
  （history=3 / parent 链闭合 / resume 不重跑 / 按 checkpoint_id 取历史）。PG 实测 P1–P7
  待用户运行环境执行（见 migration spec §7.4/§10）。

## D011 — F1 Research Artifact Foundation：Research Data Plane 落地

- **Status**: Accepted
- **Context**: Evidence-Grounded 演进需要回答"Agent 获取过什么 / 来自哪里 / 哪段可作为证据"；
  Phase 0（checkpoint 双后端）提供 execution persistence，本决策建立独立 Research Data Plane。
  用户批准 `docs/spec/2026-09-07-research-artifact-foundation.md`（G-F1-1~6 全通过）。
- **Decision**:
  - 新增 `app/research/`（config/ids/normalize/schemas/context/store/migrations/registry/provenance），
    **不依赖 app.agent**、零新增依赖；
  - 实体：ResearchRun → SubQuestion(root-only, F9 树化) → SearchQuery → Source → Evidence；
    run_id 与执行 run_id 同源（唯一关联键），但 ResearchRun ≠ checkpoint（不共表/迁移/事务）；
  - 双后端 DDL（sqlite TEXT json / postgres JSONB）由 `db/migrations/0001_*.{sqlite,postgres}.sql`
    + 轻量 runner（schema_migrations，幂等）管理；checkpoint 表族不可触碰；
  - Source 注册确定性（canonical URL 归一化、系统生成 id、runtime 时间戳），**禁 LLM 生成 id/URL**；
  - 运行期 artifact 写入 **fail-open**（`artifacts_guard`，store 故障仅告警，工具返回原值；
    RESEARCH_STORE=disabled 可整体关闭）；run 创建/终态由 run_deep_agent 两处轻量钩子驱动；
  - Evidence = candidate evidence / groundable content；Claim-Citation 绑定留 F2（只留扩展边界）；
    恢复/重放产生重复 query/evidence 为 append 语义（本阶段允许，task_call_id 幂等留后续）。
- **Alternatives**: 把 artifact 塞进 LangGraph state / checkpoint——污染执行态且不可独立查询（拒绝）；
  引入 ORM/Alembic/图库——零依赖纪律拒绝；先做 HTTP provenance API——G8 裁决后置 P1 末尾。
- **Rejected Alternatives**: 增加 Planner/Verification/Citation Agent（不新增 Agent）；
  side-effect idempotency 实现（只留 metadata 字段位）。
- **Consequences**: 主链路工具返回与模型可见上下文不变；Agent Message 不含 artifact；
  Research 故障不阻断 Agent；工具/主智能体 import research 模块（无环：research 不 import agent）。
- **Constraints Created**: `app/research` 不得 import `app/agent`；工具签名/docstring 不可因注册而变；
  research_* 表族只经 app/research/migrations 演进；新实体 = schemas + registry 函数 + NNNN migration；
  PG 测试仅允许 `RESEARCH_DSN_TEST` 独立测试库。
- **Related Problems**: 无新增
- **Related Architecture**: ARCHITECTURE.md §2/§3/§5/§7（app/research、依赖方向、research 配置、扩展点）
- **验证（本环境，锁定版本隔离环境）**：F1 新增 43 项测试全绿（normalize/registry/provenance/
  fail-open/store+migration/PG 门控 2 skip）；全仓 **258 passed, 8 skipped**（=Phase0 215/6 + F1 43/2）；
  ruff/compileall 全绿；PG RESEARCH_DSN_TEST 实测待用户环境（沿用门控纪律）。

## D012 — F2 Claim / Citation Binding & Validation（rev2 contract 实施）

- **Status**: Accepted
- **Context**: Evidence-Grounded 主线需把 F1 candidate evidence 提升为可校验引用
  （Claim ← Evidence → Citation）；用户批准 `docs/spec/2026-09-08-claim-citation-binding.md` rev2
  （G1–G6 + rev2 四项修正全通过）。本阶段冻结 Phase 0 / F1 baseline。
- **Decision**:
  - 0002 迁移仅新增 `claims / claim_evidences / citations` 三表 + 索引，**零 ALTER F1 五表**；
    checkpoint 表族不触碰；schema_migrations 追加 0002（runner 幂等）。
  - Registry：`create_claim`（(run_id, statement_sha) 内容级幂等；type/status 枚举受控）、
    `bind_claim_evidence`（M:N，(claim_id,evidence_id) 幂等，同 run 守卫）、
    `create_citation`（R2 写前守卫：必须已有 binding；(run_id,claim_id,evidence_id) 幂等；
    citation_id 稳定 artifact identity，**无呈现编号列**；quote≤500 / locator≤200 无控制符）；
  - Validator `validate_run -> ViolationReport` 纯函数只读，R1–R10（R3=unsupported、R10=coverage）；
  - `apply_validation_outcome` 为 claim.status **唯一写入口**（validator 零 side effect）；
  - Provenance：list_claims / list_citations / get_claim_chain / render_citations
    （排序 created_at ASC, citation_id ASC；[n] 渲染派生不落库）。
- **Alternatives**: 呈现编号落库（破坏 identity 稳定/replay 语义，rev2 拒绝）；binding/citation 合一
  （生命周期不同，G2 拆表）；LLM 参与校验（Verification 域，F2 不引入）。
- **Rejected Alternatives**: 新 Agent / Graph / Memory / idempotency 实现；Verification/Conflict 提前。
- **Consequences**: claim.status drafted→validated 语义明确（仅覆盖引用者 validated）；
  unsupported/referenced 检出不改写 artifact；R1/R4 的 FK-off 审计路径测试为 sqlite 专有，
  PG 走同规则代码路径。
- **Constraints Created**: validator 不得写库/不 import 写路径；呈现编号不落库；F2 只增不改 F1；
  `app/research/validate.py` 纯读；claim_type 枚举仅存证不消费（Verification 扩展点）。
- **Related Problems**: 无新增
- **Related Architecture**: ARCHITECTURE.md §2/§3/§7（app/research 扩展）、F2 spec
- **验证（本环境，锁定版本隔离环境 + 真实 PG）**：F2 sqlite 套件 38 passed；全仓 sqlite-only
  **296 passed, 15 skipped**；双 DSN（独立测试库）**310 passed, 1 skipped**（连跑稳定）；
  ruff/compileall/format（仅历史 3 drift 文件未改）全绿。详见 F2 Implementation Report。


## D013 — F3 Semantic Verification（verifications 独立表 + fake/optional-real verifier）

- **Status**: Accepted
- **Context**: F2 已建立结构与 deterministic 校验（Claim/Evidence/Citation/R1–R10）；Evidence-Grounded
  主线需回答"证据内容是否真的足以支持 Claim"（语义层）。用户批准 docs/spec/2026-09-09-semantic-verification.md
  rev2（Q1–Q3 定案 + Conditional Approve → Final Gate PASS）。
- **Decision**:
  - 0003 迁移仅新增 `verifications` 表（含 verifier_spec 快照 / fingerprint / verdict / confidence /
    status / error），零 ALTER F1/F2；checkpoint 表族不触碰。
  - SemanticVerdict 仅 5 种（SUPPORTS/INSUFFICIENT/CONTRADICTS/UNVERIFIABLE/ABSTAIN）；VerifyStatus 仅
    pending/succeeded/failed；**单条无 partial**（partial 只属 batch/run ExecutionSummary）。
  - F2 validation errors = 结构 Gate（非空即拒绝）；R3/R10 warning 不阻止单条验证；citation coverage 仅
    默认候选优先级（budget 策略）；allowed_evidence_ids 显式覆盖。
  - Evidence.content/quote = factual basis；source metadata 仅 provenance（不是证据内容）。
  - confidence = 辅助信号（NULL 合法禁伪造）；threshold = deterministic policy（low-confidence→ABSTAIN）。
  - Verifier 默认 FakeVerifier；real-LLM adapter 可选且须 VERIFY_REAL_LLM=1（controlled，不进自动化 Gate，
    复用 OpenAI-compatible 栈）；timeout/provider/malformed 确定性重试 1 次；failed→verdict NULL。
  - 幂等键 UNIQUE(run_id, claim_id, evidence_id, verifier_fingerprint)；spec 升级=新行；不引入 task_call_id。
  - claim 级只读聚合 supported/contested/partially_supported/unverified；SUPPORTS+CONTRADICTS→contested
    （只标记不裁决，Conflict Resolution 属后续阶段）。
- **Alternatives**: 把语义验证并进 Agent（拒：入 Agent 回路/成本）；verdict 含 ERROR（rev2 拆分拒绝）；
  coverage 当验证（rev2 拒绝）；不持久化（无法溯源 eval，拒）。
- **Rejected Alternatives**: Conflict Resolution/Source Reliability/Replan/Graph/Memory 提前；新 LLM
  framework/基础设施；task_call_id。
- **Consequences**: 语义层与 Agent/报告主链路解耦（verifier 不可用仅产 unverified）；每次 verdict 可溯源到
  spec/时间；模型升级 = fingerprint 新行可对照。
- **Constraints Created**: verify.py 纯 research 域（不 import agent）；Gate 失败抛 VerificationGateError；
  单条状态机禁 partial；claim.status 不写（F2 finalizer 唯一入口不变）；confidence NULL 合法。
- **Related Problems**: 无新增
- **Related Architecture**: ARCHITECTURE.md §2/§3（app/research 扩展）、F3 spec rev2
- **验证（锁定隔离环境 + 真实 PG 独立库）**：F3 sqlite 套件 31 passed（+ validator 等回归 44 passed）；
  全仓 sqlite-only **327 passed, 18 skipped**；双 DSN **344 passed, 1 skipped**（连跑稳定）；
  ruff/compileall 绿（format 仅历史 3 drift 文件未改）。详见 F3 Implementation Report。


## D014 — F4 Conflict Detection（conflicts 独立 artifact + 受控 detector；不含 Resolution）

- **Status**: Accepted
- **Context**: F3 只输出 claim 级 `contested`，丢失 pair 级信息且不区分真矛盾与语境差异；
  用户批准 docs/spec/2026-09-10-conflict-detection.md rev2（Conditional → Final Gate PASS）。
- **Decision**:
  - 0004 仅新增 `conflicts`（identity=(run_id,claim_id,evidence_a,evidence_b,detector_fingerprint)，
    a<b 规范化）；F1/F2/F3 零 ALTER。
  - 候选默认域 = 同 claim F3 SUPPORTS×CONTRADICTS；explicit_pairs 仅绕过 verdict 候选生成，
    不绕过 structural/integrity；不引入 confidence / same-verdict / cross-claim。
  - ConflictStatus 4 态（candidate/confirmed/rejected/failed，无 partial）；genuine/conflict_type
    invariant 由唯一写路径+测试锁定（§10c）；detector SHALL NOT re-evaluate support。
  - verification_a/b FK ON DELETE SET NULL + F3 signal 快照存 metadata（防 provenance 丢失）。
  - 完整性 invariant（同 run/同 claim/双 binding/a<b/≠/verification 对齐）由写前守卫强制
    （DDL 可移植，不用跨表 CHECK/trigger）。
  - FakeDetector 默认；real-LLM 可选且须 VERIFY_REAL_LLM=1（不进自动化 Gate）。
- **Alternatives**: 仅保留 contested 聚合（丢 pair/不可溯源，拒）；verdict 作 identity（冲突天然两证据，拒）；
  复用 F3 verifier 通道（语义域不同，独立 detector）。
- **Rejected Alternatives**: Conflict Resolution / Source Reliability / Winner / 跨 claim / same-verdict
  候选；新 Agent/infra；ALTER Frozen 表。
- **Consequences**: contested 背后的 pair 事实可查询、可溯源、幂等；为后续 Resolution 提供只读派生视图。
- **Constraints Created**: conflicts 写入仅经 conflict 模块唯一写路径；genuine/type invariant 与
  integrity invariants 全项测试锁定；不写 claims/verifications。
- **Related Problems**: 无新增
- **Related Architecture**: ARCHITECTURE.md §2/§3（app/research 扩展）、F4 spec rev2
- **验证（锁定隔离环境 + 真实 PG）**：F4 sqlite 29 passed；全仓 sqlite-only **356 passed, 20 skipped**；
  双 DSN **375 passed, 1 skipped**（连跑稳定）；ruff/compileall 绿（format 仅历史 3 drift 未改）。

## D015 — F5 Independent Evidence Corroboration（corroborations 独立 artifact；只计量独立性，不裁决）

- **Status**: Accepted / **FROZEN**（F5 Final Gate PASS，2026-09-11；Spec 与 Implementation 不再修改）
- **Context**: F1–F4 已有证据链+校验+验证+冲突发现，但转载链 A→B→C→D 被误当独立来源、冲突两侧
  独立性未知（corroboration 高估）；用户批准 docs/spec/2026-09-11-f5-audit-and-independent-corroboration.md
  rev2（F5 Spec Final Gate PASS）。**核心原则：F5 measures independence; it does not score trust。**
- **Decision**:
  - 0005 仅新增 `corroborations`（identity=(run_id, claim_id, method_fingerprint)）；F1–F4 零 ALTER。
  - Claim 作用域 **global clustering**：同一 universe（SUPPORTS/CONTRADICTS verified evidence → sources）
    统一 union-find 聚类，support/contradict 只是同一聚类结果的侧视图（不分别聚类）。
  - deterministic v1 规则锁死：R-URL（canonical_key 相同）/ R-DOMAIN（同 domain 且 path 前 3 段相同
    或 path 为空）/ R-TITLE（归一化相等或字符集 Jaccard≥0.85）/ R-SHINGLE（5-gram Jaccard≥0.60），
    **OR 归并**；pairwise 按字典序严格一次；chaining（A≈B、B≈C、A≠C → 同簇）由 union-find 锁定；
    cluster_key = 簇内最小 canonical_key + `#<n>`（deterministic）。
  - source_count = distinct source_id；independent_count = distinct global cluster id；
    cross-side independence = F4 confirmed 冲突两侧 cluster id 交集为空（**仅计量，非 winner/可信**）。
  - source_profile 仅 descriptive 分布（type/domain/publisher/freshness/agent），无 score 字段。
  - optional LLM cluster review 默认 OFF（Fake reviewer 测试；real 须 VERIFY_REAL_LLM=1，不进 Gate）；
    Context Envelope 仅 identification metadata + evidence content（≤8000），无 Agent 历史/run plan/他 claim。
  - 失败语义：review 失败 → **complete + metadata.review_failed=true**（携带 reason）；
    deterministic 失败（gate/读取/聚类）→ **failed + error**；complete artifact 幂等复用，
    failed artifact 重算并 UPDATE 同行（同指纹单行收敛）。
  - 不引入 task_call_id；无 ranking/weighting/winner/resolution/reliability/authority/confidence。
- **Alternatives**: A=本方向（接受）；B=Conflict Resolution（依赖 A，暂缓）；C=Evidence-driven control
  （跨 Agent loop，后置）；D=Claim Graph（明确不做，"graph for graph" 风险）。
- **Rejected Alternatives**: 对支持/反驳侧分别聚类（漏判转载跨侧）；cluster review 输出信任/权威；
  引入任务系统/新 Agent/infra。
- **Consequences**: 转载膨胀与冲突两侧独立性可计量、可复现、幂等；输出供报告展示与未来 resolution/replan
  只读引用；仅计量不改变 run/claim/verification 语义。
- **Constraints Created**: corroborations 写入仅经 corroboration 模块唯一写路径；F5 不写 F1–F4 表；
  source_profile/cluster 输出字段受"无 score"测试锁定。
- **Related Problems**: 无新增
- **Related Architecture**: ARCHITECTURE.md §2/§3（app/research 扩展）、F5 spec rev2
- **验证（锁定隔离环境 + 真实 PG）**：F5 sqlite 24 passed；全仓 sqlite-only **380 passed, 20 skipped**；
  双 DSN **405 passed, 1 skipped**（连跑稳定）；ruff/compileall 绿（format 仅历史 3 drift 未改）。

## D016 — F6 Conflict Reconciliation（reconciliation + contested register；不裁决）

- **Status**: Accepted（F6 Spec rev2 Final Gate PASS → Implementation 完成，待 F6 Final Gate）
- **Context**: F4 判 pair、F5 计独立性，但 claim 名下"同源自我矛盾 vs 独立细节不一致 vs 独立真争点"
  从未被判定登记；F3 `contested` 无法表达该层。Spec rev2 定位：
  **F6 explains/registers conflict state; it does not adjudicate truth.**
- **Decision**:
  - 0006 仅新增 `reconciliations`（identity=(conflict_id, method_fingerprint)；conflict FK CASCADE）；
    F1–F5 零 ALTER；F6 只读消费 F5 corroboration（support/contradict.cluster_ids 与
    conflicts_independence[].side_a/side_b.cluster_ids、independent_between_sides）。
  - outcome 三态锁死：cluster intersection != empty → SAME_ORIGIN_CONTRADICTION；
    independent + F4 INCONSISTENCY → DETAIL_INCONSISTENCY（仅登记类别）；
    independent + F4 CONTRADICTION → GENUINE_CONTESTED；
    independence/cluster signal missing or malformed → **failed**（硬红线：Unknown ≠ Not Independent，
    绝不把 missing 当同源；不重新聚类/不重算 independence）。
  - claim register 派生（不落表）：precedence genuine > detail > same_origin > verified_consistent；
    无 SUPPORTS → unverified；任一 confirmed conflict 缺 complete reconciliation / corroboration /
    required input → incomplete_input（不静默降级）；verified_consistent 需 ≥1 SUPPORTS + 无更高冲突
    状态 + inputs 可解析（不得仅因"无 contradiction"）。
  - run_unresolved 只返回 register=genuine_contested 的 claims + 对应 confirmed conflicts；
    不含 DETAIL/SAME_ORIGIN。
  - optional LLM review 默认 OFF（Fake reviewer 测试；real 须 VERIFY_REAL_LLM=1，不进自动化 Gate）；
    reviewer 最多产出 rationale/detail/metadata，**不得改变 outcome 语义族**（rev1 的
    UNRESOLVABLE_SEMANTIC 已删除）；review 失败 → complete + metadata.review_failed。
  - 失败/幂等沿用 F5 纪律：deterministic 失败 → failed + error；complete 复用；failed 重算 UPDATE 同行。
- **Alternatives**: B=Evidence-driven Research Control（G2，需 claim 物化 + Runtime/Control 边界 → F7 候选）；
  C=Synthesis/Claim Graph（拒）；D=Quality（拒，authority 红线）。
- **Rejected Alternatives**: winner/truth/reliability/authority；UNRESOLVABLE_SEMANTIC（rev1→rev2 删除）；
  修改 F3 verdict / F4 conflict / claim 内容；新 Agent/Runtime/基础设施。
- **Consequences**: contested claim 的证据状态可解释、可审计、幂等；run unresolved 成为未来
  F7 report/control 的只读契约输入；F6 仍是分析面（Data Plane），零 Runtime/Agent 改动。
- **Constraints Created**: reconciliations 写入仅经 reconciliation 模块唯一写路径；F6 不写 F1–F5 表；
  review 不得改变 outcome；register/run_unresolved 派生只读。
- **Related Problems**: 无新增
- **Related Architecture**: ARCHITECTURE.md §2/§3（app/research 扩展）、F6 spec rev2
- **验证（锁定隔离环境 + 真实 PG，2026-09-12 Implementation 实测）**：F6 sqlite 21 passed + PG 4 passed；
  全仓 sqlite-only **401 passed, 30 skipped**；双 DSN **430 passed, 1 skipped**（连跑稳定）；ruff/compileall 绿
  （format 仅历史 3 drift 未改）。

## D017 — F7 Research State Bridge（Claim Materialization + F2–F6 orchestration；首个真实 run 消费点）

- **Status**: Accepted（F7 Spec rev2 Final Gate PASS → Implementation 完成，待 F7 Final Gate）
- **Context**: 审计确认 F1 runtime-consumed 但 F2–F6 在真实 run 中零调用、claims 零产出（producer 断流）；
  F2 spec §12.2 finalizer / §12.4 report integration 原为"设计契约不实现"。Spec rev2 定位：
  **F7 = orchestration layer（Research State Bridge），不重实现 F3/F4/F5/F6 算法；不做 F8 Control；不改 Agent-visible context。**
- **Decision**:
  - 新增 `app/research/bridge.py`（finalize_run / build_run_research_state / get_run_research_state /
    run_finalization_history）+ `app/research/extractor.py`（BaseExtractor/FakeExtractor/RealLLMExtractor
    + candidates schema 校验；**无 verdict 键，verdict 仅 F3 可产生**）；F7 不新增 migration/表。
  - 唯一 Runtime 接线点：`main_agent.run_deep_agent` astream 正常结束后、task_result 前 fail-open 调
    `finalize_run`；取消/异常路径不执行。
  - materialization：statement/type 结构非法 → 该 candidate 不入库（唯一被拒通道）；anchor 缺失/非法 →
    claim 仍落库 + metadata.unanchored=true（不建 ClaimEvidence/Citation，F3 无该 claim verification）；
    quote 须为 content 连续子串（否则 anchor 无效，不静默改写）；cap：claims=12 / bindings=8。
  - orchestration：F2 structural（validate + apply_validation_outcome）必须执行；F3/F4/F5/F6 仅 enabled
    时调既有 public API，disabled → skipped_off、不伪造 artifact、相关计数 not_computed/null。
  - finalization identity：sha256({extractor_spec, stages:{f3..f6 spec}, final_content_hash,
    evidence_universe_hash})；同代复用；改 content / evidence universe → 新 generation（旧 artifacts 不删）；
    artifact 级幂等沿用 F2–F6 既有契约。
  - fail-open：extractor/stage/store 失败只记日志、不阻断 task_result；research_runs.metadata 只写
    `research_finalizations` 键（registry/F1 语义不变）。
- **Alternatives**: B=Evidence-driven Research Control（依赖 claims 物化 + Runtime 决策 → F8 候选）；
  C=State→Agent Context（无 state 可注入）；D=Control Boundary（纪律）。均因 audit 断流结论后置。
- **Rejected Alternatives**: 重新实现验证/冲突/聚类/reconciliation；为 F7 新增 Agent/工具/infra；改 F1–F6
  migration/公共 API 语义；把 skipped 当 verified/no-conflict/reconciled；Agent-visible context 注入。
- **Consequences**: 真实 run 首次物化 claims 并首次被 F2–F6 orchestrate；run-level research state 可查询
  （verified/contested/unresolved/claims 计数 + pipeline 状态），成为 F8 control 的只读输入契约；
  语义边界（extractor 不判支持、verdict 仅 F3）与 identity 幂等被测试锁定。
- **Constraints Created**: bridge 只调 F2–F6 公共 API（AC-Orch）；finalizations 只写 metadata 单键；
  不改 Agent-visible context / monitor / checkpoint / tool。
- **Related Problems**: 无新增
- **Related Architecture**: ARCHITECTURE.md §2/§3、F7 spec rev2
- **验证（锁定隔离环境 + 真实 PG，2026-09-12）**：F7 sqlite 19 passed + PG 4 passed；全仓 sqlite-only
  **420 passed, 34 skipped**；双 DSN **453 passed, 1 skipped**（连跑稳定）；ruff/compileall 绿；
  real-LLM extractor 受控 probe 执行（配置存在、endpoint 可达；.env key 401 → 部署环境需有效 key）。

## D018 — Semantic Module Ownership Relocation：app/f9 → app/research（代码结构归属调整；非 F9 行为重设计）

- **Status**: Accepted（用户 Phase 1/Readiness/Plan Review 通过并批准实施；Freeze 已批准）
- **Context**: `app/f9/` 属 Feature/阶段编号命名（"F9-P0"），而目录应表达稳定软件领域。
  本决策是**代码结构 ownership 调整**（semantic module ownership relocation），不是 F9 行为
  重设计。逐文件职责盘点确认：六模块 + eval 全部属 Research 领域（数据/知识/研究编排 + 质量验证），
  无 Agent 能力、无 Runtime governance、无 schema；生产入口零接线（仅 eval/测试消费）。
- **Decision**:
  - **只做 Module Relocation**：`app/f9/{projection,gaps,judge,plan,targeted,orchestrator}.py`
    → `app/research/`；`app/f9/eval/` → `app/research/eval/`；删除 `app/f9/__init__.py`
    （docstring 语义并入 `app/research/__init__.py`）。零行为/常量/schema/API 改动；
    symbol（`f9_orchestrator` 等）与内部值（`CONFIG_VERSION="f9-plan-config-v1"` 等）不变。
  - **归属裁决**：orchestrator = 业务研究编排（research/，不进 runtime/、agent/）；eval =
    Research Intelligence 行为质量验证基础设施（research/eval/，暂不建顶层 app/eval/）。
  - **依赖契约（Flag A）**：Research Data/Evidence Plane 核心模块维持"无 app 内依赖、不依赖
    app/agent"；Research Intelligence/Execution + eval 允许依赖 app/runtime/governance
    （受治理 LLM/上下文契约，F8 control 原样传播），默认 seam 仅 lazy import
    app.agent.main_agent / app.tools.tavily_tool / app.api.monitor；
    Runtime Controller 仍唯一拥有 lifecycle/budget/cancellation/timeout/terminal authority。
  - 测试语义化改名 `test_f9_*` → `test_research_*`、`_f9_helpers.py` → `_research_helpers.py`；
    历史 docs/plan、docs/spec、Batch 报告保持原状（Flag D，residual 附录列出）。
- **Alternatives**: 机械改名 `app/research_intelligence/`（拒：新顶层编号化/重复语义）；
  orchestrator → app/runtime/（拒：业务研究编排 ≠ Runtime governance，Principle B）；
  eval → 顶层 app/eval/（未来 Eval 扩展到非 Research 行为时再单独决策）。
- **Rejected Alternatives**: 修改 F9 算法/stopping/judging/planning/targeted 行为；symbol rename；
  新增生产接线；修改 DB/config/API/WS；顺带重构其它 research 模块；重写历史文档。
- **Consequences**: `app/` 目录表达稳定领域（Research = Data/Evidence + Intelligence/Execution +
  eval），Feature 编号（F1–F10…）只留在 docs/spec、docs/plan 与溯源文字；生产无行为/接线变化。
- **Constraints Created**: 未来 F10+ 代码落位必须进语义模块而非 Feature 编号目录；
  Research Intelligence 模块新增代码须遵守 §3 依赖契约（governance 受控依赖 + lazy seam）。
- **Related Problems**: 无新增
- **Related Architecture**: ARCHITECTURE.md §2/§3（app/research 双子域 + 依赖分层 + orchestrator 例外路径）
- **验证（2026-10-02，refactor/semantic-module-layout 分支）**：compileall PASS；ruff check
  PASS（format 仅既有历史 drift，沿用 D015–D017 "format 未改"先例）；sqlite 全量 **731 passed /
  88 skipped / 0 failed**；定向 Research/Eval/Calibration 175 passed；PG gate 无 DSN 如实 skip
  （User Environment Gate，不伪造 81 passed）。

## D019 — Multi-Session Backend：Session = thread 上层的对话容器域（session_id == thread_id）

- **Status**: Accepted（2026-10-03，用户 Decision Closure 正式批准进入 Implementation；Feature =
  Multi-Session Backend，本决策记录已闭合的设计点，不再重新讨论）
- **Context**: 系统长期围绕单研究流程运行，缺少可持久化、可归档、可隔离、可并发的多会话容器；
  现有 thread_id 已是贯穿 WS / checkpoint / 目录 / TaskRecord / ResearchRun / monitor 的会话身份，
  F8 TaskRecord.parent_session 为从未使用的预留列。Task §5–§20 要求分析后以最小必要改动落地。
- **Decision**:
  - **Identity**：`Session.session_id == thread_id`（1:1 身份复用，唯一关联键）；不得新增第二套
    Session/Thread 关联 ID；`parent_session` 保持 NULL/unused（不复用）。
  - **Persistence**：`sessions` 表 = governance 迁移族 **additive 0002**（sqlite + postgres 双方言，
    复用 governance store 连接 / backend 选择 / migration runner）；无 Session 独立 DB / env /
    Store 单例 / 新依赖。
  - **State**：仅 `ACTIVE ⇄ ARCHIVED`（archive = DELETE，幂等；unarchive = PATCH status=active，
    幂等）；不实现 CREATED/PAUSED/COMPLETED/FAILED（容器层无真实语义）；Task/Runtime 执行状态
    仍由 GovernanceController 全权负责。
  - **Archive 守卫**：不允许 ARCHIVED + RUNNING —— 归档为**单事务条件更新**
    （`archive_if_no_running`：active 且该 thread 无 running Task 才成功），并保留友好预读提示；
    建任务侧在收敛旧任务（可能 await）后、submit 前**二次校验 Session 仍 active**
    （server `_start_governed_task(precondition=…)`），关闭归档/建任务的并发窗口。
  - **Isolation**：所有 Session-scoped 操作先验证 session 存在 → 状态合法 → `task.thread_id ==
    session_id`，否则拒绝（404/409/400 映射到既有 HTTPException 错误模型）；不跨 Session 读取/取消。
  - **API（additive）**：`POST/GET /api/sessions`、`GET/DELETE/PATCH /api/sessions/{id}`、
    `POST/GET /api/sessions/{id}/tasks`、`POST /api/sessions/{id}/tasks/{task_id}/cancel`；
    既有端点（含 POST /api/task）与旧客户端语义**零改动**（旧 thread 无需注册 Session）。
    契约文档 = 仓库根 `MULTI_SESSION_API_SPEC.md`。
  - **Runtime 边界**：Session 不拥有 lifecycle；Task 创建/取消复用 governance service/controller；
    不修改 Harness / Agent Runtime / Research Pipeline / checkpoint / GovernanceController；
    Frontend 不在本 Task 修改范围。
  - **query enrich**：Session 任务摘要的 question 来自 research 面**独立只读 enrich**（fail-open；
    research store disabled → null）；不做跨库 JOIN，不影响 governance 核心可用性。
- **Alternatives**: TaskRecord 增独立 session_id 列（双关联键，重复建设，拒）；parent_session 复用作
  session 键（语义模糊 + 双键，拒）；独立 sessions DB/store/env（重复三平面体系，拒）；多状态
  Session（无生命周期语义，拒）；扩大 POST /api/task 强制注册 Session（破坏旧客户端，拒）。
- **Consequences**: 对话/会话从"隐式 thread 键"升级为"可命名、可列表、可归档、可恢复的持久化
  容器"；隔离由 thread 作用域结构性保证；并发多 Session 运行无需任何 runtime 改动。
- **Constraints Created**: session_id 必须落在 `[A-Za-z0-9_-]{1,128}`；sessions 表只经
  governance migration runner 演进（新版本 = `db/governance_migrations/NNNN_*.{sqlite,postgres}.sql`）；
  session 域代码（app/session/）可依赖 governance service/store、不可反向依赖；不得新增 Session
  Runtime 权威；Frontend 后续实现以 MULTI_SESSION_API_SPEC.md 为准。
- **Related Problems**: 无新增（P004 净化规则沿用）
- **Related Architecture**: ARCHITECTURE.md §2/§3/§6（app/session、依赖方向、端点契约）
- **验证（2026-10-03，feature/multi-session-backend 分支）**：session 新套件 44 passed + PG 门控 3
  skip（无 DSN 如实 skip）；sqlite 全量回归（见 Implementation Report §10）；ruff/format/compileall
  全绿；governance 迁移清单断言更新为 0001+0002（additive 引起的期望变更）。

## D-Phase2-P2-1-001 — 默认 Runtime Policy 强制化：policy=None ⇒ governed（Phase-2 P2-1）

- **Status**: Accepted（2026-10，用户 Review Decision 批准 P2-1 Spec / Plan 并授权 Implementation；
  本决策在 `feature/runtime-p2-1-policy-enforcement` 分支登记）
- **Context**: Runtime 审计（`RUNTIME_OBSERVABILITY_AUDIT_REPORT.md`）确认：生产提交路径
  （server → `gov_service.submit_task`）在调用方未传 policy（`TaskRequest.policy` 缺省 None、
  前端从不发送 policy）时把 None 原样透传给 `controller.execute` → 落入 `_execute_bare`
  （无 watchdog / 无 deadline / 无 budget / 无 terminal lifecycle event）；执行（LLM/tool/
  checkpoint）静默挂起即永久 RUNNING（现场：任务停留「研搜中」>30 分钟、无终态无日志）。
  `TaskRequest` 注释声称「缺省由 governance 服务默认（M-Spec §7.4）」但代码未实现（文档-代码漂移）。
- **Decision**:
  - **提交层默认注入**：`gov_service.submit_task` 对 `policy=None` / `{}` 注入
    `DEFAULT_GOVERNED_POLICY`（M-Spec §7.4 冻结默认：`wall_clock_timeout=600s`、
    `max_agent_steps=200`、`max_llm_calls=120`、`max_tool_calls=300`、`max_search_calls=40`），
    使生产任务恒走 `controller._execute_governed`（watchdog / budget / 异常 re-raise /
    terminal lifecycle event 常开）；`policy_snapshot` / `effective_limits` 落默认表。
  - **bare 保留**：`controller.execute(policy=None)` 的 Step2 bare 语义不变，仅限 unit test /
    local debugging / internal development；生产禁止 bare 由提交层（唯一 normalize 收敛点）保证。
  - **fail-closed 校验**：显式 policy 中 `max_*` 必须 ≥0 int、`wall_clock_timeout` 必须 >0
    （禁止传 0 关闭 watchdog 逃逸治理），非法 → `PolicyValidationError`（HTTP 400，任务不启动）。
  - **默认值不调参**：P2-1 不改默认 policy 语义；env 覆盖（`RUNTIME_WALL_CLOCK_TIMEOUT` 等）归
    P2-2 决策 `D-Phase2-P2-2-002`。
- **Alternatives**: `controller.execute` 内做防御性默认（改变冻结 execute 语义、直调也被悄悄
  governed，拒）；server 各端点逐处注入（多入口漂移，拒）；不修复（永久 RUNNING 保留，拒）。
- **Consequences**: 生产任务不再可能「无任何外部干预而永久 running」；agent 异常不再被吞成
  completed（governance-active re-raise → `failed(agent_failure)`）；TaskRecord
  `policy_snapshot/effective_limits` 从空变为默认表；terminal lifecycle event 对全部生产任务
  可见；超过默认 600s 的合法长任务将被 `timed_out` 终态化（显式 policy 可覆盖；调参归后续
  Runtime Policy Tuning，非本批）。
- **Constraints Created**: 生产提交必须经 `gov_service.submit_task`（唯一注入点）；默认表单一
  来源（`app/runtime/governance/policy.py`，与 controller/counters 冻结常量同源并由测试锁定）；
  不新增 schema/migration；不动 TaskStatus / terminal funnel / CAS / event replay 冻结语义；
  不修改 `app/agent/**`、`app/research/**`、frontend。
- **Related Problems**: 无新增（审计发现，未登记 PROBLEM.md）
- **Related Architecture**: ARCHITECTURE.md（Runtime lifecycle / governance controller /
  server 提交接线）；`P2-1_RUNTIME_POLICY_ENFORCEMENT_SPEC.md` / `P2-1_IMPLEMENTATION_PLAN.md`

## D-Phase2-P2-2-001 — Runtime Health Plane：heartbeat = Runtime Health Telemetry（stale 为派生态）

- **Status**: Accepted（P2-2 Decision Closure Report §Decision Status 审核通过；随该报告等待用户 ratify）
- **Context**: 审计确认进程退出/执行器失联时 `running` 行无存活信号、无 stale 判定、无收敛路径与
  诊断数据（现场「研搜中」>30min）。F8 冻结 `governance_tasks` 为 Task Lifecycle 字段权威
  （`tests/test_runtime_governance.py` TASK_COLUMNS 注释明文禁止 Plan 外 lifecycle 字段）。
- **Decision**: 引入独立 **Runtime Health Plane**：heartbeat = **Runtime Health Telemetry**（存活
  遥测），stale（healthy / stale / expired）= **派生只读态**，均**不进入** `TaskStatus` /
  `governance_tasks.status`；reclaim 收敛到既有终态，不新增 TaskStatus 枚举值。
- **Conflict Resolution（AGENTS §1 显式裁决，不静默）**: F8 §5.3 将「execution owner / lease /
  heartbeat 机制」排除在 v1 之外、`PROJECT_CONTEXT` §4 亦禁止 `lease-heartbeat` —— 该禁令针对
  **多实例 ownership/互斥（lease/fencing）**；本决策的 heartbeat **仅**为单实例健康遥测，
  **不提供** lease/fencing/所有权仲裁，**不改变** F8 §5.3 single-instance 假设，**不新增**
  第二 Runtime 控制面（F8 controller 仍是唯一 lifecycle 权威）。
- **Alternatives**: 无持久化心跳（无法判定执行器失联，拒）；把 stale 写入 TaskStatus（改冻结枚举，拒）；
  引入 lease/heartbeat 作为多实例机制（超出 P2-2 范围且违反 F8 §5.3，拒）。
- **Consequences**: 遗留 RUNNING 可被确定性识别与收敛；诊断面具备 `last_heartbeat` 语义；
  代价是新增健康平面存储与扫描组件。
- **Constraints Created**: LA-1–LA-4（heartbeat 只写 health plane；reclaim 只经
  `finalize_with_event`；health 不写 status；health 行清理不属 controller）。
- **Related Problems**: P2-2 Closure 建议在实现阶段评估登记（「无健康平面导致遗留 RUNNING 不可诊断」）。
- **Related Architecture**: `P2-2_SPEC_v2.md` §4.0/§4.6/§9；F8 §5.3/§6.1。

## D-Phase2-P2-2-002 — P2-2 阈值与配置默认值（沿用 M-Spec §7.4，env 可覆盖）

- **Status**: Accepted（用户 2026-10 P2-2 启动指令已裁决；本 Closure 确认）
- **Context**: P2-2 需要 wall_clock 与 stale grace 的默认值基准，且不得改变 P2-1 冻结的默认
  policy 语义。
- **Decision**: 默认 `wall_clock_timeout = 600s`（M-Spec §7.4）、`stale grace = 30s`、
  `stale_threshold = wall_clock + grace`；上述参数**必须支持环境变量覆盖**
  （`RUNTIME_WALL_CLOCK_TIMEOUT` / `RUNTIME_STALE_GRACE_PERIOD`），**默认值不变**；
  P2-2 不做调参（Runtime Policy Tuning 属后续独立批次）；不针对单个 Agent 调整 timeout。
- **Alternatives**: 全局固定常量（长任务误回收，拒）；本批内改默认值（越界，拒）。
- **Consequences**: 观测到的阈值可运维调整而不改代码；默认行为与 P2-1 基线一致。
- **Constraints Created**: 逐任务 `effective_limits.wall_clock_timeout` 为 stale 判定主基准，
  全局 env 仅作 legacy 回退（`P2-2_SPEC_v2.md` §5.1）。
- **Related Problems**: 无新增
- **Related Architecture**: `P2-2_SPEC_v2.md` §5/§13。

## D-Phase2-P2-2-003 — reclaim 终态映射：restart → aborted；stale/registry → orphan_reclaimed（Gate D5）

- **Status**: Accepted（P2-2 Decision Closure；Gate D5 审核通过；随该报告等待用户 ratify）
- **Context**: F8 §6.1/§10.2 冻结区分「进程 shutdown/restart 收敛 = `aborted`」与「台账与实际
  执行者脱节 = `orphan_reclaimed`」；P2-1 Spec 语境曾出现「统一复用 `orphan_reclaimed`」的表述，
  构成文字冲突（AGENTS §1 要求显式裁决）。
- **Decision**: 采纳 F8 拆分：**startup sweep（上一世代/实例失联）→ `aborted`**；
  **heartbeat stale / registry 脱节 → `orphan_reclaimed`**；两者均为既有终态，不新增状态。
- **Alternatives**: 统一 `orphan_reclaimed`（偏离 F8 冻结文字、混淆「重启」与「脱节」语义，拒）。
- **Consequences**: 终态语义可审计、与 F8 一致；前端文案缺口（aborted/orphan_reclaimed）记为后续项。
- **Constraints Created**: reclaim 唯一入口 = `controller.finalize_with_event(...)`；
  终态选择必须按触发源映射（不得由调用方自由选择）。
- **Related Problems**: 无新增
- **Related Architecture**: P2-2 Spec v2 §6.1/§6.2；F8 §6.1/§10.2。

## D-Phase2-P2-2-004 — 心跳首拍时机 = `_execute_governed` 执行起点（Gate D13）

- **Status**: Accepted（P2-2 Decision Closure；Gate D13 审核通过；随该报告等待用户 ratify）
- **Context**: 首拍存在两处候选：`submit_task` 后 vs `_execute_governed` 起点。`submit_task` 只
  表示「受理」，其后才 `create_task`/`ensure_future`/注册 handle；`execute()` 早期失败
  （store 读失败等）会留下无执行的 running 行。
- **Decision**: 首拍**必须**在 `_execute_governed` 执行起点、**handle 注册成功之后**写入；
  `submit_task` 路径**MUST NOT** 写心跳。
- **Alternatives**: submit 路径写首拍（会把「未开始执行」伪装成「有心跳」，并前移 stale 基准，拒）。
- **Consequences**: heartbeat 的存在性成为「曾进入 governed 执行」的证据（与 Gate D18 证据条件互补）。
- **Constraints Created**: 首拍写失败为 fail-open（不阻断执行），由 stale/registry 路径兜底。
- **Related Problems**: 无新增
- **Related Architecture**: P2-2 Spec v2 §4.3(1)/§5.5；`controller._execute_governed` 顺序。

## D-Phase2-P2-2-005 — RuntimeHealthScanner 组件边界（Gate D14）

- **Status**: Accepted（P2-2 Decision Closure；Gate D14 审核通过；随该报告等待用户 ratify）
- **Context**: startup sweep + periodic scan + 分类 + 收敛 + 清理若写入 `server.py` 会使 API 层
  承担 Runtime 逻辑并不可测。
- **Decision**: 抽象独立 runtime 组件 `RuntimeHealthScanner`（startup sweep / periodic scan /
  owner 分类 / reclaim 协调 / health 只读视图 / 健康行清理）；`server.lifespan` **仅**负责生命周期
  接线（构造、启动、shutdown cancel）；scanner **MUST** 复用进程级 `get_controller()` 单例
  （`SC-1`–`SC-4`），**MUST NOT** 新建 controller（否则 handle registry 为空 → 全量误回收）。
- **Alternatives**: 扫描逻辑内联 server.py（膨胀 + 不可测，拒）；scanner 自建 controller（高危，拒）。
- **Consequences**: 可测（可注入 clock/触发）、可复用（P2-4 后续读取 health 视图）、server 保持薄层。
- **Constraints Created**: scanner 只读调用 controller（`get_task`/`has_active_handle`/
  `terminal_decision`）与 `finalize_with_event`；controller 不依赖 scanner；**文件/模块命名为
  Planning 决策，不在本次 Closure 冻结**。
- **Related Problems**: 无新增
- **Related Architecture**: P2-2 Spec v2 §7.0/§7.1。

## D-Phase2-P2-2-006 — registry 脱节收敛证据条件（Gate D18）

- **Status**: Accepted（P2-2 Decision Closure；Gate D18 审核通过；随该报告等待用户 ratify）
- **Context**: `submit_task` 建行后才注册 handle；仅凭「无 handle + age」会在事件循环阻塞时
  误回收「已受理但尚未开始」的任务；`start_task()` 失败非致命，`started_at` 可能为空。
- **Decision**: registry 脱节路径收敛**必须**同时满足：`age > RUNTIME_RECLAIM_MIN_AGE`
  **且**（健康行存在 **或** `started_at` 非空）**且**
  `now - max(last_heartbeat_at, started_at) > max(3 × RUNTIME_HEARTBEAT_INTERVAL, 15s)`；
  不满足则仅记 stale/diagnostic，不收敛。
- **Alternatives**: 仅 `age > MIN_AGE`（误回收窗口，拒）；仅看 `started_at`（失败场景漏判，拒）。
- **Consequences**: 关闭 submit→handle 注册窗口与「心跳写长期失败」误判。
- **Constraints Created**: 该阈值（3× 心跳间隔且 ≥15s）同时作为「liveness 证据」基准。
- **Related Problems**: 无新增
- **Related Architecture**: P2-2 Spec v2 §5.4 P10/§6.1。

## D-Phase2-P2-2-007 — 健康行生命周期与清理责任方 = scanner（Gate D17）

- **Status**: Accepted（P2-2 Decision Closure；Gate D17 审核通过；随该报告等待用户 ratify）
- **Context**: 若在 controller/funnel 内删除健康行，lifecycle 权威模块将承担 health 平面写操作。
- **Decision**: 健康行：首拍/周期拍由 controller 持有的 HeartbeatWriter 写；**清理（终态任务行 +
  孤儿行）由 scanner 负责**；controller/funnel **MUST NOT** 删除健康行。`RUNTIME_RECLAIM_MODE=off`
  ⇒ 心跳写亦停（避免无清理者的行增长）。
- **Alternatives**: controller 内终态即删（污染 lifecycle 边界，拒）；TTL 保留（无界增长风险，拒）。
- **Consequences**: 清理滞后 ≤ 1 个扫描周期；lifecycle 模块保持单一职责。
- **Constraints Created**: LA-4；`off`/`detect`/`enforce` 行为矩阵（Spec v2 §13.1）为唯一模式语义。
- **Related Problems**: 无新增
- **Related Architecture**: P2-2 Spec v2 §4.6/§12.2/§13.1。

## D-Phase2-P2-2-008 — reclaim 诊断载体 = 仅 `error` 字符串（Gate D16，非阻塞）

- **Status**: Accepted（P2-2 Decision Closure；Gate D16 审核通过；随该报告等待用户 ratify）
- **Context**: reclaim 需要可诊断信息（trigger/owner_class/last_heartbeat/threshold/age），但
  `events.lifecycle_event` 属冻结 observation 模块。
- **Decision**: P2-2 只把诊断写入终态 `error` 字符串（≤800 字符，沿用 `_clip_error` 约束），
  **MUST NOT** 修改 `events.lifecycle_event` 的 payload 构造；结构化 payload 扩展留 P2-4 另行决策。
- **Alternatives**: 直接扩展 payload（触碰冻结 observation 模块，拒）。
- **Consequences**: 最小 diff、不破坏 replay 契约；诊断可读性略低于结构化字段。
- **Constraints Created**: error 字符串不得包含凭据、prompt 正文或工具参数。
- **Related Problems**: 无新增
- **Related Architecture**: P2-2 Spec v2 §10。

## D-Phase2-P2-2-009 — 跨进程重复 terminal event = 已知限制，本批不硬化（Gate D19，非阻塞）

- **Status**: Accepted（P2-2 Decision Closure；Gate D19 审核通过；随该报告等待用户 ratify）
- **Context**: 单实例内 `terminalize` 经内存裁决短路/DB 已终态采纳，保证 ≤1 terminal event；
  但跨进程 CAS 败者在 retry 采纳 DB 事实时仍返回 `winner=True`，`finalize_with_event` 可能重复发布
  （`controller.py` L316→L319-326）。
- **Decision**: P2-2 的 exactly-once event 保证**限定于单实例 supported envelope**（F8 §5.3）；
  跨进程重复事件记录为**已知限制**，**本批不做**硬化（硬化需改动冻结 controller 语义）。
- **Alternatives**: 立即硬化（标记 `adopted_from_db` 并跳过发布——属冻结模块改动，本批拒，留待
  多实例议题重启时评估）。
- **Consequences**: 不引入冻结面改动；Spec/Report 必须显式声明该限制（不得宣称全局 exactly-once）。
- **Constraints Created**: 若未来启用多实例，必须先解决该重复事件语义（与 owner/lease 议题同批）。
- **Related Problems**: 无新增
- **Related Architecture**: P2-2 Spec v2 §6.3；F8 §5.3。

## D-Phase2-P2-2-010 — owner 三分法收敛条件：SAME_HOST_PREV 必须叠加 liveness 证据（Gate D15 修订）

- **Status**: Accepted（用户 2026-10 ratify P2-2 Decision Closure 并批准执行 D15 Spec 修订；登记为
  append-only 追加）
- **Context**: v2 设计对 `SAME_HOST_PREV`（同主机、异 PID ⇒ 上一世代）允许「立即收敛」，仅对
  `CURRENT` 叠加心跳陈旧条件。该设计在 **rolling / blue-green 部署重叠窗口**下不安全：旧进程仍在
  运行且持续写心跳（owner 为旧 PID、同主机），新进程 startup sweep 会立即把它标记为上一世代并
  `aborted` → **误杀在途任务**。
- **Decision**: `SAME_HOST_PREV` **不得**因 owner generation 不同而立即 reclaim；收敛前必须满足
  **liveness 证据**：
  `now - last_heartbeat_at > max(3 × RUNTIME_HEARTBEAT_INTERVAL, 15s)`；
  若无 heartbeat health row（legacy）→ 回退 `now - max(started_at, created_at) > 同阈值`。
  `CURRENT` 维持既有规则（`deadline_evidence` = `wall_clock_timeout + stale grace`）；
  `FOREIGN` 继续只观测、**禁止 reclaim**。三分法分类本身（CURRENT / SAME_HOST_PREV / FOREIGN）保持不变。
- **核心安全不变量**:
  - 活跃的旧进程在 rolling / blue-green overlap 期间**不得**被新进程 startup sweep 误杀；
  - 真正停止心跳的 `SAME_HOST_PREV` 任务仍可在 **~15s 级**进入 reclaim；
  - 不新增 TaskStatus；不改变 F8 lifecycle authority；
  - heartbeat 仍然只是 Runtime Health Telemetry，**不是 lease / fencing**。
- **Alternatives**: 维持「立即收敛」（重叠部署误杀，拒）；统一要求 `deadline_evidence`
  （真死进程需等 ~630s 才收敛，恢复过慢，拒）；引入 lease/心跳互斥（超出 P2-2 范围且违反
  F8 §5.3，拒）。
- **Consequences**: restart 场景的遗留任务收敛延迟从「立即」变为「最后一次心跳 + ~15s」；
  换来重叠部署期间不误杀在途任务。`liveness` 阈值复用 D18 已有参数，**不新增配置项**。
- **Constraints Created**: `SAME_HOST_PREV` 的 startup/periodic 收敛均须先判定 `liveness_evidence`；
  该证据同时受 P3 最小年龄守卫约束；`FOREIGN` 永远只统计。
- **Related Problems**: 无新增
- **Related Architecture**: `P2-2_SPEC_v2.md` **Rev 2.1** §5.5 / §6.1 / §6.2 / §7.3 / §14 F11；
  F8 §5.3/§10.2 e。
- **Ratification Note**: 用户已于 2026-10 ratify `D-Phase2-P2-2-001`–`009`（append-only：不改写既有
  条目状态字段）；`D3/D6/D7/D8/D9/D11/D12` 仍 awaiting explicit confirmation，未登记。

## D-Phase2-P2-2-011 — 双阶段确认次数 = 2（Gate D3）

- **Status**: Accepted（用户 2026-10 批准 P2-2 Pending Decision Proposal；append-only 追加）
- **Context**: 周期扫描需要决定「连续 N 次判定 stale 才允许 reclaim」，以在误回收防护与收敛延迟之间
  取舍；启动期 sweep 为单遍执行，无法套用确认次数，须由证据谓词（`liveness_evidence` /
  `deadline_evidence`）承担同等职责。
- **Decision**: `RUNTIME_STALE_CONFIRMATIONS` 默认 **2**；配置下限 1，取 1 须显式 opt-in 并在日志标注
  `aggressive`；确认计数为进程内状态、重启清零（更保守）；**启动 sweep 不适用确认次数**。
- **Alternatives**: 1 次（低延迟、防护弱）；3 次（更保守但延迟高）；自适应分裂阈值（复杂度高、
  收益不明确）——均未采纳。
- **Consequences**: 收敛延迟增加 1 个扫描周期；误回收概率进一步下降；启动/周期严格度不对称须在
  Spec 中保持明示。
- **Constraints Created**: 确认次数只作用于 periodic 路径；startup 路径的误判防护只能依赖证据谓词 +
  owner 分类 + 最小年龄。
- **Related Problems**: 无新增
- **Related Architecture**: `P2-2_SPEC_v2.md` Rev 2.2 §5.2/§8；F8 §10.2。

## D-Phase2-P2-2-012 — `flush_pending` 本批不接线（Gate D6）

- **Status**: Accepted（用户 2026-10 批准；append-only 追加）
- **Context**: `controller.flush_pending()`（F8 既有方法）在生产代码中无调用方；需要决定 P2-2 是否接线，
  以及如何处理「内存已裁决、DB 写失败」的 pending 裁决。
- **Decision**: **本批不接线**（startup flush 在重启后恒为空操作；周期 reconciler 属 F8 文档化 future，
  越出本批范围）；`shutdown flush` 登记为后续批次候选（需独立批准，且须硬超时 + fail-open）。
- **Alternatives**: startup flush（新进程内存为空 → 无效，拒）；周期 reconciler（scope 外扩，拒）；
  本次一并接线 shutdown flush（未获批准，留候选）。
- **Consequences**: pending 裁决在进程死亡后由 startup/periodic sweep 收敛，可能产生**终态语义漂移**
  （原裁决 → `aborted`/`orphan_reclaimed`）；方向保守、可审计，但丢失原裁决原因（记为已知限制）。
- **Constraints Created**: 任何未来的 flush 调用点必须 fail-open 且不得阻断 control（F8 §7.2）；
  pending 的内存裁决权威/retry 阶梯/degraded 标记语义保持不变。
- **Related Problems**: 无新增
- **Related Architecture**: `P2-2_SPEC_v2.md` Rev 2.2 §6.5；F8 §7.2。

## D-Phase2-P2-2-013 — startup sweep 时序 = `yield` 前有界 sweep（Gate D7）

- **Status**: Accepted（用户 2026-10 批准；append-only 追加）
- **Context**: 启动期收敛遗留 `running` 行应发生在 `lifespan` `yield` 之前（阻塞 readiness）还是之后
  （后台执行）——决定 readiness 语义、F8 restart 收敛的确定性、以及首请求是否可能看到遗留假 running。
- **Decision**: 采**方案 A**：`store/migration init → startup sweep（batch ≤200、预算 ≤2s、fail-open）→
  yield`；store 不可用或超预算 → 跳过 + 日志，**不 fail-fast**（显式取舍，见下）。
- **Alternatives**: 方案 B（yield 后后台）——窗口内旧行可见，且与新 submit 竞争可能把旧行收敛为
  `cancelled` 而非 F8 语义的 `aborted`，并触发 session archive 守卫 409 → 拒；方案 C（混合快速通道）
  ——当前遗留行数 ≈ 崩溃时在途任务数（通常 <10），复杂度不划算 → 留作规模化演进选项。
- **Consequences**: 首请求前账本干净、收敛确定；代价是有界启动延迟（受 batch/预算约束）。
- **Constraints Created**: **显式记录对 F8 §7.3 (c3) 的取舍**：本批选择「startup governance store 不可用 →
  跳过 sweep、服务继续（提交侧由 c1 返回 503）」，而非启动 fail-fast；FOREIGN owner 行不参与 sweep。
- **Related Problems**: 无新增
- **Related Architecture**: `P2-2_SPEC_v2.md` Rev 2.2 §7.2/§7.3；F8 §10.2 e、§7.3 c3。

## D-Phase2-P2-2-014 — scanner interval = 15s + ±20% 抖动（Gate D8）

- **Status**: Accepted（用户 2026-10 批准；append-only 追加；相对 Spec v2 默认值 30s 的变更）
- **Context**: 周期扫描间隔决定检出/收敛延迟、扫描开销与健康行清理滞后；Spec v2 默认 30s。
- **Decision**: `RUNTIME_SCAN_INTERVAL` 默认 **15s**，抖动 **±20%**（进程随机相位），
  **单飞**且**空闲跳过**；30s 亦为可接受配置（env 覆盖）。
- **Alternatives**: 30s（v2 默认，开销更低但延迟翻倍）；60s（延迟过高）；自适应退避（复杂度换收益不明确）
  ——均未采纳。
- **Consequences**: registry 脱节路径最坏检出延迟 ≈ min_age + 2×15s；与确认次数（2）相乘后总延迟 30s
  （优于 30s×2=60s）；扫描开销可忽略（1 次索引查询 + N 行 Python 判定）。
- **Constraints Created**: 抖动必须按进程随机相位；验收标准需写明最坏检出延迟公式；
  Spec 默认值需在 Rev 2.2 同步（§13/§8）。
- **Related Problems**: 无新增
- **Related Architecture**: `P2-2_SPEC_v2.md` Rev 2.2 §8/§13；F8 §10.2（示例值「如 15s」）。

## D-Phase2-P2-2-015 — heartbeat PostgreSQL 写约束（Gate D9）

- **Status**: Accepted（用户 2026-10 批准；append-only 追加）
- **Context**: PG 后端 governance store 每次调用开**短连接**且**同步执行于事件循环内**；心跳（5s/任务）
  会随在途任务数放大连接与 loop 抖动，而同一 loop 承载 watchdog 与 agent 调度。
- **Decision**: 逐拍 UPSERT + 逐任务节流（默认 5s）+ 抖动相位；**不引入连接池、不新增依赖**；
  心跳写**必须**为单语句短事务（禁止在同一调用内追加查询/锁/重试风暴）；批量 writer 列为**应急候选**
  （需独立批准）。
- **Alternatives**: 连接池（新增依赖/基础设施，违反 AGENTS §4，拒）；仅内存心跳（破坏 D15
  `liveness_evidence`，会使活跃旧进程被误判，拒）；批量 writer（保留为实测后的应急路径）。
- **Consequences**: 保留 D15 安全证据可判定性；控制路径不受心跳写开销侵蚀（F8 §7.2）。
- **Constraints Created**: 心跳写 fail-open；不得借此引入 lease/fencing（F8 §5.3 不变）。
- **Related Problems**: 无新增
- **Related Architecture**: `P2-2_SPEC_v2.md` Rev 2.2 §4.5/§11；F8 §7.2、§5.3。

## D-Phase2-P2-2-016 — Problem Registry 登记时机（Gate D11）

- **Status**: Accepted（用户 2026-10 批准；append-only 追加）
- **Context**: 「Runtime 无健康平面 ⇒ 进程退出/执行器失联后任务永久 RUNNING 且不可诊断」具备长期价值，
  但 P2-2 仍处 Spec/Decision Closure 阶段（AGENTS §11.3 的登记义务发生在任务完成前）。
- **Decision**: 本轮**不登记**；在 **P2-2 Implementation 起点**（或 Readiness 通过时）登记正式 Problem
  （`docs/problem/P0NN-*.md` + `PROBLEM.md` 索引行，登记动作需单独批准）。
- **拟登记内容**: 题名「Runtime 缺少健康平面：进程退出/执行器失联后任务永久 RUNNING 且不可诊断」；
  Type = Runtime behavior / Architecture constraint；Severity = High；预期状态 Open → 由 P2-2 实现关闭；
  关联 `RUNTIME_OBSERVABILITY_AUDIT_REPORT.md`、`P2-2_SPEC_v2.md` Rev 2.2、`D-Phase2-P2-2-001`。
- **Alternatives**: 现在登记（与实现进度脱节）；登记 Candidate（轻量但检索性弱）；不登记（丢失稳定 ID，拒）。
- **Consequences**: 知识沉淀时机与实现对齐；本轮 `PROBLEM.md` 零改动。
- **Constraints Created**: 登记必须引用本批 Spec/Decision，避免与 Phase-2 路线重复登记。
- **Related Problems**: 拟新增（P0NN，待登记）
- **Related Architecture**: Harness 层（AGENTS §11 / PROBLEM.md）；与 F8/F9 无关。

## D-Phase2-P2-2-017 — frontend terminal 文案策略 = 本批零改动（Gate D12）

- **Status**: Accepted（用户 2026-10 批准；append-only 追加）
- **Context**: P2-2 首次让 `aborted` / `orphan_reclaimed` 成为生产可见终态。原 Spec 表述「文案缺口」经
  证据核验**不成立**：`frontend/src/lib/taskStatus.ts:13-14` 已有「任务中止」「已回收」；
  `frontend/src/components/ConversationThread.tsx:323-326` 已有对应断线提示文案。
- **Decision**: **本批零 frontend 改动**；真实缺口仅为「`aborted`/`orphan_reclaimed` 不展示 `error`
  原因」（仅 `failed` 分支展示，L311-314），记为后续项（P2-3 或独立小批，需显式 scope 例外批准）。
  Spec §9/§21 的表述在 Rev 2.2 更正。
- **Alternatives**: 同批 ≤5 行补 `error` 展示（打破本批「零 frontend」边界，未采纳）；永不改动（信息缺失，拒）。
- **Consequences**: 批次边界清晰；reclaim 原因需查 `GET /api/tasks/{id}` 或事件/日志。
- **Constraints Created**: 未来若做 reason 展示，须遵守 `D-Phase2-P2-2-008`（`error` 不得含凭据/prompt 正文），
  且不得改动 WS/HTTP 契约（AGENTS §4）。
- **Related Problems**: 无新增
- **Related Architecture**: `P2-2_SPEC_v2.md` Rev 2.2 §9/§21；前端呈现层。
