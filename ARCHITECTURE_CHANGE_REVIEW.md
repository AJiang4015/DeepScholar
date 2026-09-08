# ARCHITECTURE_CHANGE_REVIEW — SQLite → PostgreSQL：Checkpoint Persistence / Replay 重审 + Research Artifact Store 分层

> 状态：**Review 完成，待用户确认 Decision Gate（见 §11）**
> 范围：本文件只做架构评审与设计，**未修改任何产品代码**。
> 证据原则：所有框架能力结论均基于「当前仓库锁定版本对应的真实包源码」逐行核查，不基于记忆与猜测。
> 证据物目录：`.deepagents-fc/pgcheck/`（本评审下载解包的 wheel 源码）、`.deepagents-fc/src/deepagents-0.5.7/` 等（先前核查留存）。目录未入 git，复核后可清理。

---

## 0. 执行摘要（TL;DR）

1. **版本事实**：仓库锁定 `langgraph==1.1.10` / `langgraph-checkpoint==4.0.3` / `langgraph-checkpoint-sqlite==3.0.3`。与 checkpoint 4.0.3 匹配的官方 PostgreSQL checkpointer 是 **`langgraph-checkpoint-postgres==3.0.5`**（PyPI 元数据：requires `langgraph-checkpoint>=2.1.2,<5.0.0`；最新的 3.1.2 要求 `>=4.1.0`，与仓库锁 4.0.3 **不兼容**，只适配系统环境的 4.1.1）。
2. **「Bridge Replay」正名**：仓库里的 `AsyncBridgeSqliteSaver` 不是 Replay 逻辑，而是 **async 桥**——官方同步 `SqliteSaver` 的 a* 方法抛 `NotImplementedError`，与 `astream` 不兼容，故桥接到 `asyncio.to_thread`。真正的 resume/replay/time-travel 由 LangGraph 图运行时经 saver 的 `get_tuple/list/put/put_writes` 完成，仓库生产路径从未使用 resume。
3. **关键发现（可消除 Bridge）**：同版本 `langgraph-checkpoint-sqlite==3.0.3` **自带官方 `AsyncSqliteSaver`**（`langgraph/checkpoint/sqlite/aio.py`，aiosqlite 实现，原生 async）。D009 当年弃用它的两条理由（需 running-loop 构造、新增 aiosqlite 依赖）中，**依赖理由已不成立**——`aiosqlite`/`sqlite-vec`/`orjson` 均已作为传递依赖存在于 uv.lock。因此：SQLite fallback 也可放弃自定义 Bridge，改用官方 AsyncSqliteSaver。
4. **PostgreSQL 路径**：官方 `AsyncPostgresSaver` 原生实现全部 a* 方法（`setup/alist/aget_tuple/aput/aput_writes/adelete_thread`），支持 `AsyncConnectionPool`，**生产路径完全不需要 Bridge**。它同时保留 sync 方法，可支撑测试里 sync 调用的恢复语义。
5. **必须接受的应用层改造（最小但必要）**：`AsyncPostgresSaver.__init__` 需要 `asyncio.get_running_loop()`（aio.py:52），因此 **checkpointer 不能在模块 import 期构造**——`main_agent` 的模块级组装（main_agent.py:44-50）必须改为在 loop 内的 lazy/异步组装。这是与 R2 不同的结构性改动，已列入 Gate。
6. **Replay 能力边界（不要误判）**：PostgreSQL 迁移**不会**自动获得「新的 replay 能力」——full saver（非 shallow）本就保存完整 checkpoint 链，replay/time-travel 是**图运行时能力**（`get_state_history` + 带 `checkpoint_id` 的 resume / `Command(resume=...)`），两种后端语义一致。迁移真正买来的是：**原生 async（去 Bridge）、多进程/多实例并发共享、可扩展的长期存储**；同时解锁的是应用层此前缺失的 **Execution Replay 与 Research Reconstruction 分层**（§7）。
7. **建议裁决**：**APPROVE WITH CONDITIONS**（§11 条件 + 8 个 Gate 问题，逐个等你确认后才进入下一步设计）。

---

## 1. Current Architecture（当前实际关系）

### 1.1 工作树事实核查（E:\CodeField\DeepScholar @ 1406b9c）

| 层 | 本工作树实际文件 | 状态 |
|---|---|---|
| R1 文件安全 | `app/utils/path_utils.py`、`upload_guard.py`、`session_id.py` + tests | ✅ 存在 |
| R2 Checkpoint | `app/runtime/checkpoint.py`（AsyncBridgeSqliteSaver 工厂）+ `tests/test_checkpoint_recovery.py` + spec | ✅ 存在 |
| R3 Event Model | `app/api/monitor.py`（envelope/run_id/seq/per-thread queue）+ tests + spec | ✅ 存在 |
| Task Runtime（R4 规划内容：`task_manager.py`、`test_task_api.py` 等） | **本工作树不存在** | ⚠️ 缺失（README/导学文档引用过，属另一工作副本或后续分支） |
| Event Persistence（R5 规划内容：`event_store.py`） | 本工作树不存在 | ⚠️ 缺失 |
| Harness 契约 | AGENTS/PROCESS/ARCHITECTURE/DECISION/PROBLEM/TESTING.md + docs/problem + docs/spec | ✅ 存在 |

> 诚实声明：本评审按**当前实际文件**为基线；「Task Runtime」在设计中作为**待接入层**处理（§7 只依赖其公开语义：TaskStatus/RunOutcome 事件化终态），不依赖其内部实现。若目标分支确有 R4/R5 文件，请合并后补一次 diff 复核，设计接口保持不变。

### 1.2 当前持久化与执行链路（现状图）

```text
FastAPI (app/api/server.py)
  │ POST /api/task → active_tasks[thread_id] = asyncio.create_task(run_deep_agent(query, session_id))
  ▼
run_deep_agent (app/agent/main_agent.py:56)          ← 唯一 run 边界（ARCHITECTURE §3 MUST NOT 绕过）
  ├─ 建 output/session_{id}；复制上传文件；ContextVar(thread/session/run)
  ├─ config = {"configurable": {"thread_id": session_id}}
  ├─ async for chunk in main_agent.astream({messages:[...]}, config)
  │     main_agent = create_deep_agent(checkpointer=get_sqlite_checkpointer(), ...)   ← 模块 import 期构造
  │         └─ LangGraph checkpoint 调用：aput/aput_writes/aget_tuple/alist
  │               └─ AsyncBridgeSqliteSaver（a* → asyncio.to_thread → 同步 SqliteSaver）
  │                     └─ sqlite3 conn → app/runtime/checkpoints.sqlite（默认）
  └─ terminal exactly-once 事件（R3 RunTerminalGuard）
```

### 1.3 现状中与持久化直接相关的代码点

| 关注点 | 现状 | 位置 |
|---|---|---|
| SQLite 直接依赖点 | **仅 1 处**：`app/runtime/checkpoint.py`（sqlite3 + langgraph-checkpoint-sqlite）。其余模块不直接 import sqlite | `checkpoint.py:154` |
| 自定义 Bridge 依赖点 | `AsyncBridgeSqliteSaver`（a* 桥接）+ `get_sqlite_checkpointer()` 单例工厂 | `checkpoint.py:63-112, 127-146` |
| checkpointer 注入点 | `main_agent = create_deep_agent(checkpointer=get_sqlite_checkpointer())`（模块 import 期，即写盘 fail-fast） | `main_agent.py:44-50` |
| thread_id/checkpoint_id 用法 | thread_id=session_id（净化后）；checkpoint_id 由 LangGraph 生成并写入 checkpoint 元数据；repo 代码从不显式读取 checkpoint_id | `main_agent.py:101-102` |
| resume/replay 使用 | 生产路径**从不** `Command(resume=...)`；只有测试用真实子进程验证 get_state/resume | `tests/test_checkpoint_recovery.py` |
| 运行时 DB 路径 | `AGENT_CHECKPOINT_DB` env 覆盖，默认 `app/runtime/checkpoints.sqlite`；gitignore | `checkpoint.py:49-52` |

### 1.4 「Bridge Replay」为什么存在（拆解：框架缺失 vs 项目缺失）

| 现象 | 归属 | 证据 |
|---|---|---|
| 官方同步 `SqliteSaver` 的 a*（`aget_tuple/alist/aput/aput_writes`）抛 `NotImplementedError` | **框架能力缺口（sqlite 后端）** | `langgraph-checkpoint-sqlite==3.0.3` `__init__.py:496-535`；checkpoint 4.0.3 `base/__init__.py:316-408`（基类默认抛） |
| `astream`（async Pregel）要求 checkpointer 有 a* 实现 | 框架正常约束（InMemorySaver 因实现 a* 而可用） | D009 记录 |
| 因此需要「同一实例同时服务 sync（测试/恢复脚本）与 async（生产 astream）」 | **项目层决策**（D009），实现为 AsyncBridge | `checkpoint.py` docstring |
| resume/replay/time-travel 语义 | **LangGraph 图运行时能力**（非 saver 层专有）：saver 只需保存完整历史（parent 链）并提供 get_tuple/list；图用 `Command(resume=...)`/`checkpoint_id` 驱动 | `tests/test_checkpoint_recovery.py`（真实子进程恢复）；[LangGraph time-travel 文档](https://docs.langchain.com/oss/python/langgraph/use-time-travel) |
| 生产链路从不 resume / 从不 read history | **项目层缺失**（没有暴露续跑入口，也没有 artifact reconstruction） | `main_agent.py` 全量检索 |

结论：**不存在「自定义 replay 算法」需要迁移**。Bridge 只是 sqlite 后端的 async 补丁；迁移 PostgreSQL 的收益之一是官方 AsyncPostgresSaver 原生 async，补丁本身可以退役。

---

## 2. Dependency / Version Verification（全部基于锁定版本真实源码，非记忆）

### 2.1 锁定 vs 实装矩阵

| 包 | pyproject/uv.lock（生产基线） | 系统 Python 实装（本机） | 备注 |
|---|---|---|---|
| deepagents | **0.5.7** | ❌ 未安装 | 主/子智能体工厂 |
| langchain | **1.2.17** | 1.3.11 | deepagents 0.5.7 约束 `>=1.2.17,<2` |
| langchain-core | 1.3.3（lock） | 1.4.8 | deepagents 约束 `>=1.3.2,<2` |
| langgraph | **1.1.10**（lock） | 1.2.7 | checkpoint 主键体系一致 |
| langgraph-checkpoint | **4.0.3**（lock） | 4.1.1 | saver 基类来源 |
| langgraph-checkpoint-sqlite | **3.0.3**（lock） | 3.1.1 | 官方 aio 存在 |
| langgraph-prebuilt | 1.0.13（lock） | 1.1.0 | 仅工具节点相关 |
| sqlalchemy | 2.0.49（lock，传递） | 2.0.51 | 与 Agent 持久化无关（勿误用为 ORM 选型依据） |
| **aiosqlite / sqlite-vec / orjson** | ✅ 已在 uv.lock（传递自 sqlite-saver） | — | 见 §2.3 |
| **psycopg / psycopg-pool** | ❌ 不在 uv.lock | ❌ | 迁移需新增（Gate） |

验证来源：`pyproject.toml`、`uv.lock`（行号见下）、PyPI JSON API（版本要求元数据）、wheel 解包源码（`.deepagents-fc/pgcheck/`）。
uv.lock 关键行：langchain 1.2.17（L621-622）、langgraph 1.1.10（L763-764）、langgraph-checkpoint 4.0.3（L780-781）、langgraph-checkpoint-sqlite 3.0.3（L793-794）、langgraph-prebuilt 1.0.13（L807-808）、aiosqlite（L76）、sqlite-vec（L1388）、orjson（L999）。

### 2.2 PostgreSQL checkpointer 版本配对（PyPI 元数据实证）

| langgraph-checkpoint-postgres | requires langgraph-checkpoint | 与仓库锁 4.0.3 兼容？ | 适配场景 |
|---|---|---|---|
| **3.0.5**（选用） | `>=2.1.2,<5.0.0` | ✅ | 仓库生产基线（langgraph 1.1.10 / checkpoint 4.0.3） |
| 3.1.2（最新） | `>=4.1.0,<5.0.0` | ❌ | 仅适配系统 4.1.1（langgraph 1.2.7），**勿锁入仓库** |
| 2.0.x | `<3.0.0,>=2.1.2` | ❌ | 旧 checkpoint API |

其余要求：`psycopg>=3.2.0`、`psycopg-pool>=3.2.0`、`orjson>=3.11.5`、`python>=3.10`（orjson 已在锁内）。**结论：生产基线应锁定 `langgraph-checkpoint-postgres==3.0.5`。**

### 2.3 官方 Async 能力存在性核查（源码实证）

| 后端 | 官方 async saver | 证据（解包源码行号） | 结论 |
|---|---|---|---|
| SQLite | ✅ `AsyncSqliteSaver`（aiosqlite） | `langgraph-checkpoint-sqlite-3.0.3/langgraph/checkpoint/sqlite/aio.py:31`（class）、`:275`（async setup）、`:316/400/479/531/572`（aget_tuple/alist/aput/aput_writes/adelete_thread） | **同版本官方 async 存在**；同步 `SqliteSaver` a* 抛 NotImplementedError（`__init__.py:496-535`） |
| PostgreSQL | ✅ `AsyncPostgresSaver`（psycopg async） | `langgraph-checkpoint-postgres-3.0.5/langgraph/checkpoint/postgres/aio.py:32`（class）、`:82`（async setup）、`:111/173/224/296/329`（alist/aget_tuple/aput/aput_writes/adelete_thread）；`:52`（`self.loop = asyncio.get_running_loop()`） | **原生 async，生产路径无需 Bridge**；构造需 running loop（影响 §4.10 组装方式） |

### 2.4 本机验证边界（如实声明）

- 本机系统 Python **无 deepagents**（无法 import `app.agent.main_agent`），无 mysql.connector，**无 Docker、无本地 PostgreSQL**。
- 因此本评审的框架结论是「**源码级验证**」；真实 PG 运行验证（setup/并发/resume/time-travel）需按 §10 的验证协议在你的运行环境执行——与 R2 阶段「用户本机验证」的模式一致。

---

## 3. SQLite → PostgreSQL Migration Design（总体）

### 3.1 选型结论

- 生产：**PostgreSQL（统一底座）**，两个逻辑层共存于同一物理库，但**表族、连接、迁移、事务边界互相独立**（§7）。
- 本地开发 / 测试 fallback：**SQLite**，且推荐升级为官方 `AsyncSqliteSaver` 以**消除自定义 Bridge**（零新增依赖，见 §2.3/§5.3）。
- 不引入：Redis / Neo4j / Vector DB / Kafka（维持你的红线）。

### 3.2 配置抽象（env 驱动，fail-fast 保持）

```text
# 后端选择（新增，默认 local 保证现有行为不破）
AGENT_CHECKPOINT_BACKEND=postgres|sqlite        # 缺省=sqlite（向后兼容 dev/test）
# sqlite（沿用 R2 语义）
AGENT_CHECKPOINT_DB=<path>                       # 默认 app/runtime/checkpoints.sqlite
# postgres（新增）
AGENT_CHECKPOINT_DSN=postgresql://user:pass@host:5432/dbname
RESEARCH_DSN=postgresql://user:pass@host:5432/dbname   # 可与 checkpoint 同库不同逻辑层
```

原则（继承 R2/D009 纪律）：DB 不可用 → fail-fast 抛错，**绝不静默回退内存或换后端**；单例/生命周期与进程绑定关系按 §4.10 重构。

### 3.3 兼容与回退语义

- `AGENT_CHECKPOINT_BACKEND` 缺省 `sqlite` → **现有行为、现有 DB 文件、现有测试全部不变**（无破坏性默认值变更）。
- 切到 `postgres` 后：新 run 全部走 PG；SQLite 文件保留不删（可继续读/或导出，§9）。

---

## 4. PostgreSQL Checkpoint 能力：逐项回答（对应你的 B 节 12 问）

| # | 问题 | 结论 | 证据 |
|---|---|---|---|
| 1 | 官方/当前版本是否支持 PG Checkpointer | ✅ `langgraph-checkpoint-postgres==3.0.5`（sync `PostgresSaver` + async `AsyncPostgresSaver` + `ShallowPostgresSaver`/`AsyncShallowPostgresSaver`） | PyPI 元数据 + 包源码结构 |
| 2 | 是否支持 async | ✅ 原生 async（全部 a* 方法为 `async def`，psycopg `AsyncConnection`/`AsyncPipeline`/`AsyncConnectionPool`） | aio.py:20-53, 82-329 |
| 3 | checkpoint persistence | ✅ `checkpoints`(JSONB) / `checkpoint_blobs`(BYTEA) / `checkpoint_writes`(BYTEA) 三表 upsert 落盘（`ON CONFLICT ... DO UPDATE/DO NOTHING`） | base.py:41-85, 125-153；同步 `put`/`put_writes` __init__.py:255-368 |
| 4 | resume | ✅ saver 层 `get_tuple`（by thread_id，或 thread_id+checkpoint_ns+checkpoint_id）；resume 语义由 LangGraph `Command(resume=...)` 驱动（现有 R2 测试已证明该语义在 sqlite full saver 上成立；PG full saver 接口一致） | aio.py:173+；tests/test_checkpoint_recovery.py |
| 5 | 从历史 checkpoint 获取 state | ✅ `get_tuple(list)` 保留全部历史（含 `parent_checkpoint_id` 链、pending writes、metadata）；`list(filter/before/limit)` 支持 `get_state_history` 依赖的遍历 | base.py SELECT_SQL（87-112）；aio.py:111-172 |
| 6 | replay / time-travel | ⚠️ **数据面支持，语义面在图运行时**：full saver 保存全历史 → 图层 `get_state_history` + 带 `checkpoint_id` 的 resume / `update_state(as_node=...)` 即可 time-travel。**注意**：`ShallowPostgresSaver`（按 thread 只留最新 checkpoint）**不支持** replay，本设计必须用 full `AsyncPostgresSaver` | base.py 与 shallow.py 的 UPSERT 差异（full: ON CONFLICT(thread_id,ns,checkpoint_id)；shallow: ON CONFLICT(thread_id,ns)）；[time-travel 文档](https://docs.langchain.com/oss/python/langgraph/use-time-travel) |
| 7 | migration / setup | ✅ **包自管**：`setup()`/`await setup()` 执行内置 `MIGRATIONS[]` 并维护 `checkpoint_migrations` 版本表（幂等、版本化、顺序执行）；含 `CREATE INDEX CONCURRENTLY`（自动提交语义，官方已处理）与 `ADD COLUMN task_path` 等演进迁移。**外部迁移工具不得触碰 checkpoint 表族**（checkpoints/checkpoint_blobs/checkpoint_writes/checkpoint_migrations 归 saver.setup() 管） | base.py:37-85；__init__.py:77-102；aio.py:82-109 |
| 8 | 并发 thread | ✅ 多 thread/多 run 并发写安全：saver 内部 `asyncio.Lock`（或 sync `threading.Lock`）串行化单实例游标使用；`AsyncConnectionPool` 提供连接复用；**多进程/多实例共享同一 PG 可写**（对比 SQLite 单写者限制） | aio.py:35,51,363；_internal.py:10-20（Conn = Connection \| ConnectionPool） |
| 9 | connection pool 管理 | 推荐 `psycopg_pool.AsyncConnectionPool`（应用生命周期持有，open/close 一次）；saver 直接持有 pool 对象（`Conn` 联合类型支持）。极高吞吐才考虑单 `AsyncConnection`+Pipeline。**saver 与 pool 分离**：pool 由 app 层建，saver 为薄封装 | aio.py:23,37-53；_internal.py |
| 10 | FastAPI + async runtime 接入 | ① server `lifespan` 创建/关闭 `AsyncConnectionPool`；② 在 loop 内 `await get_async_checkpointer()`（因 aio.py:52 构造需 running loop）；③ **`main_agent` 组装从模块 import 期改为 lazy（首次 run 时在 loop 内构造并缓存）**；④ monitor/ContextVar 语义不变。此点属应用层必要改造（Gate） | aio.py:52；main_agent.py:44-50,129 |
| 11 | 当前 Bridge 哪些可删除 | **生产 PG 路径**：`AsyncBridgeSqliteSaver` 整体不需要（AsyncPostgresSaver 原生 a*）。若 SQLite fallback 也切官方 AsyncSqliteSaver（推荐），则 Bridge **全仓库退役** | §5.3 |
| 12 | 哪些 Bridge/能力仍需保留 | 需保留的**不是 Bridge 类**，而是：fail-fast 初始化（R2 纪律）、进程内单例/生命周期管理、backend 抽象（env 选择）、「sync 与 async 语义等价可测」的测试契约（sync 恢复脚本仍可用 `get_state`/`invoke` 直测）。若选择「最小改动」保留 sqlite=同步+Bridge 分支，则 Bridge 仅存在于该分支内 | §5.3 选项 A/B |

> 反向提示（采纳你的原话）：PostgreSQL 不会「自动解决 replay」——它提供的是**与 full sqlite saver 等价的完整历史存储 + 原生 async + 多实例共享**。要获得可用的 Resume/Replay 产品能力，应用层还必须补：resume 入口（现在 run_deep_agent 从不 resume）、以及「恢复后重放外部副作用」的幂等策略（§8/§10）。

---

## 5. Checkpoint Replay 设计（保留 / 删除 / 重构）

### 5.1 三层归属（你的 §九 框架落地）

| 能力 | 归属 | 本设计动作 |
|---|---|---|
| `get_state` / `get_state_history` / resume / replay（checkpoint_id 定位） | **Framework capability**（langgraph 图运行时 + saver 数据面） | 保留，改用 PG full saver 承接；不重写 |
| Execution Replay（run 恢复后从断点继续、重放未完成工具调用） | Framework capability + **应用策略**（幂等/重放边界） | 保留 LangGraph 语义；补「工具副作用幂等键」策略（R2 spec 已声明不自动重放外部副作用） |
| Research Reconstruction（由 Artifact 重建 run 的研究过程/结论，而非回放消息） | **Application capability（新）** | 本设计引入：artifact store 是 reconstruction 的唯一事实源（§7） |
| Task cancellation / lifecycle / RunOutcome | Application capability（Task Runtime 层） | 不依赖 checkpoint 内部；保持解耦（§7.3） |

### 5.2 设计决策

- **D-CKPT-1**：采用 **full `AsyncPostgresSaver`**（非 shallow）——为 replay/time-travel 保留完整历史。存储膨胀治理交给**保留策略**（按 thread 定期 `adelete_thread` / 归档），而不是 shallow 化牺牲 replay。
- **D-CKPT-2**：checkpoint 表族迁移由 **saver.setup()** 全权管理；仓库侧不引入外部迁移工具触碰它们（§6/§9 只管理 research_* 表族）。
- **D-CKPT-3**：`Execution Replay`（checkpoint）与 `Research Reconstruction`（artifact）**显式分离为两个产品能力**：前者给「同一任务续跑」，后者给「查看/复用该 run 的研究过程与 provenance」。二者通过 `thread_id`/`run_id` 关联，但**互不依赖**（artifact 不依赖消息历史存在；checkpoint 不依赖 artifact 表）。

### 5.3 Bridge 处置选项（等你 Gate）

- **选项 A（推荐）**：生产 PG = 官方 AsyncPostgresSaver；本地/测试 SQLite = **官方 AsyncSqliteSaver**（aiosqlite 已在 uv.lock，零新增依赖）。→ **`AsyncBridgeSqliteSaver` 全仓库退役**；checkpoint.py 重构为「backend 抽象 + async 工厂」。改动面：checkpoint.py、main_agent 组装（lazy）、R2 测试适配 async factory（断言语义不变）。
- **选项 B（最小改动）**：SQLite 分支原样保留 Bridge；PG 分支新增。→ Bridge 代码保留但仅服务 fallback；生产语义仍清晰。代价：仓库里长期留两套 async 方案，测试矩阵多一条。
- **选项 C（不推荐）**：对 sync `PostgresSaver` 复用「通用 Bridge」模式（a*→to_thread）。浪费 PG 原生 async 与 pool 优势，等于把 sqlite 的缺陷搬到 PG。

---

## 6. PostgreSQL Schema 设计（两个表族，逻辑解耦）

### 6.1 表族一：LangGraph Checkpoint（saver.setup() 自管，只读引用）

`checkpoint_migrations` / `checkpoints` / `checkpoint_blobs` / `checkpoint_writes`（DDL 见 `base.py:37-85` 实证）。本仓库**不做 schema 迁移、不写触发器、不加索引之外的东西**；如需扩展索引，应作为**独立迁移在 saver.setup 之后执行**（避免与官方 MIGRATIONS 版本表冲突）。

### 6.2 表族二：Research Artifact（仓库自管，新迁移体系）

实体关系（对应你的 F1 实体清单）：

```text
research_runs (run_id PK)
  ├── sub_questions (sub_question_id PK, run_id FK→research_runs, parent_id FK self)
  │     └── search_queries (query_id PK, run_id FK, sub_question_id FK)
  │           └── sources (source_id PK, run_id FK, query_id FK)
  ├── evidences (evidence_id PK, run_id FK, source_id FK, sub_question_id FK)
  │     └── claims.evidence_ids 由 join 表承载
  ├── claims (claim_id PK, run_id FK, sub_question_id FK)
  │     ├── claim_evidences (claim_id FK, evidence_id FK)        ← M:N，避免 JSONB 存外键
  │     └── claim_conflicts (claim_id FK, other_claim_id FK, type, status, rationale)
  └── citations (citation_id PK, run_id FK, claim_id FK, evidence_id FK, source_id FK, locator)
```

设计要点逐项分析：

| 维度 | 决策 | 理由 |
|---|---|---|
| PK | 全部业务 id 用 **UUIDv7/雪花类**（服务端生成，禁 LLM 生成——确定性 Source 注册契约 §6.3）；checkpoint 表 PK 保持官方定义 | 分布式可生成、不可枚举；与 LLM 输入完全隔离 |
| FK | research_runs 为根：**所有子表 FK → research_runs**（不依赖中间链路可达性），同时保留逐级 FK（sub_questions→runs 等）做语义约束 | 满足「run_id ↓ 全 Artifact 可追溯」；直连根 FK 使按 run 删除/归档是单点操作 |
| UNIQUE | `sources(run_id, canonical_url, query_id)` 唯一（同 run 内去重 + 确定性）；`search_queries(run_id, sub_question_id, seq)` 唯一；`claims(run_id, statement_hash)` 唯一（防重复写入） | 幂等写入的落点（§6.4） |
| INDEX | 命中查询模式：`sources(canonical_url)`、`sources(cluster_id)`（F6 同源簇）、`evidences(source_id)`、`claims(run_id,status)`、`citations(claim_id)`、`sub_questions(run_id)`、全部表 `run_id` 首列；metadata 用 GIN 若需过滤 | 支持 provenance traversal 与统计（按 run/source/claim） |
| JSONB 边界 | 只用于：`research_runs.plan`、`budget`、`metadata`（不透明扩展）；`sources.reliability`（F6 打分明细，非关系数据）；**禁止**把 evidence_ids/conflicts 这类**关系**存 JSONB | 关系必须可 JOIN/可约束；不透明载荷才用 JSONB（你 §六 红线） |
| 事务边界 | 一次工具/Agent 产出 = 一个事务（artifact 写入短事务）；research_runs 状态推进（started→finished）单独事务；**不跨 checkpoint 与 artifact 做分布式事务** | 两类写来源不同（LangGraph checkpoint 自管 vs repository），一致性靠「run 状态机 + 幂等键」而非 2PC |
| Cascade/Restrict | `run_id` 级联删除仅在显式 `delete_run` 使用；常规写入不删；`citations→claims→evidences→sources` 引用一律 **RESTRICT**（provenance 不可悬空），显式归档时先删 citation 再逐级上删 | 保证引用完整性可审计 |
| timestamp | 一律 `timestamptz`；`fetched_at` 由 **runtime/工具层** 记录（时钟以服务端为准，不信任 LLM/前端） | §7 Source 契约 |
| 乐观并发 | artifact 写入**不需要**（append-mostly + 幂等 upsert）；`claims.status` 的推进（Verification 后更新）用 `WHERE status IN (期望前值)` 做**条件更新**防覆盖 | 避免引入 version 列的全局复杂度 |
| 幂等 | 所有 insert 带确定性唯一键 + `ON CONFLICT DO NOTHING/UPDATE`（见 UNIQUE）；LLM 触发的事件（如 claim 状态）带 `event_idempotency_key` | 恢复重放（§8）与 agent 重试不产生重复 artifact |
| artifact version | `evidence_versions`/`claim_versions` 只对**可变实体**（claim.status、reliability）启用：主表 + 轻量历史表（append），不做全实体快照 | 演进成本与回溯收益平衡（P1 可先只记录 updated_at + 审计，P2 再补版本表） |
| run isolation | 一切写入经 repository 层强制注入 `run_id`（来自 ContextVar/参数，禁用 LLM 输入）；查询 API 一律带 run 作用域 | 防止跨 run 污染与越权读取（对齐现有 thread/session 隔离纪律） |

### 6.3 Source 注册契约（确定性优先，拒绝 LLM 自由发挥）

```text
Tool 执行（internet_search / SQL / RAGFlow / upload read）
  → 统一 SourceRegistry 入口（代码层，非 LLM）
  → normalize（canonical_url 归一化、来源类型判定、时间戳由 runtime 注入、agent/run/query 由上下文注入）
  → 幂等 upsert（按 §6.2 UNIQUE）→ 返回 source_id
```

硬性规则（写入 ARCHITECTURE 级约束，随 F1 落地）：
1. Source/SearchQuery/run 等 id **一律服务端生成**（UUIDv7），LLM 只引用已返回的 id；
2. `canonical_url` 由代码归一化（去 tracking/片段、域名规范化），不允许模型提供；
3. `fetched_at`、`agent`、`run_id`、`query_id` 由 runtime/tool 层注入，**不从模型文本解析**；
4. DB（无 URL）用 `source_type='db'` + `locator=表/查询标识`、RAGFlow 用 `source_type='kb'` + assistant/文档标识、Upload 用 `source_type='upload'` + 文件路径——**统一抽象到同一张 sources 表**，Evidence 只引用真实 source_id；
5. 证据抽取（F3）若需 LLM，只允许它**选择已有 source_id + 引用文本片段**，禁止生成新 Source。

### 6.4 事务与一致性示例（写入时序）

```text
工具调用完成 → (1) upsert search_query (2) upsert sources（同事务 T1）
子智能体返回 → (3) register evidence（引用 T1 的 source_id，事务 T2，幂等键=task_call_id+source_id+span_hash）
finalize    → (4) claim 写入 + claim_evidences 关联（T3，statement_hash 幂等）
报告生成    → (5) citation 校验/写入（T4，F2 validator 前置，T4 失败→CITATION_MISSING recovery）
```

---

## 7. Runtime Integration（边界与职责，防止反向侵入）

```text
Task Runtime（R4 层，本树未含，按公开语义对待）
   │  submit/cancel/status（TaskStatus/RunOutcome）—— 只编排 run 生命周期，不感知 checkpoint/artifact 内部
   ▼
run_deep_agent（run 边界，唯一入口）
   ├─ 创建 ResearchRun 记录（research_runs, status=started）        ← Artifact 层
   ├─ config = {thread_id} → main_agent.astream(...)               ← Checkpoint 层（PG full saver）
   │     每步 checkpoint 落库（saver 自管表）
   │     工具层经 SourceRegistry 写 search_queries/sources          ← Artifact 层（工具内，非消息）
   ├─ run 终态（RunTerminalGuard exactly-once，R3 保持）
   │     → ResearchRun.status=finished/error；产物登记 citations     ← Artifact 层
   ▼
Research Reconstruction（新 API 语义，非消息回放）
   GET /api/research/{thread_id}/{run_id}             → run + subquestions + queries 摘要
   GET /api/research/{thread_id}/{run_id}/provenance/{claim_id}
                                                      → claim → evidences → sources（含 URL/时间/可靠度）
```

| 组件 | 拥有的事实 | 不许做的事 |
|---|---|---|
| Task Runtime | 任务生命周期（run 维度） | 不写 artifact 业务表、不读 checkpoint 内部格式 |
| Checkpoint（PG） | 执行状态（messages/节点/pending writes） | 不承载研究知识；不因 artifact 需求扩大 state |
| Research Artifact（PG） | 研究知识/provenance | 不反向驱动 task 状态机；不作为 Agent 消息通道 |
| Monitor/Event（R3） | 可观测（调用元数据） | 事件只做展示与审计；**artifact 事实以 repository 为准**（事件不持久化阶段尤其如此） |
| 约束（继承 ARCHITECTURE §2/§3） | `app/tools` 不得 import `app/agent`；新增 `app/research` 不得 import `app/agent`；`app/runtime` 保持无 app 内依赖（checkpoint 工厂泛化后仍遵守） | — |

目录草案（贴合现有仓库，不机械照抄）：

```text
app/
├── runtime/checkpoint.py        # 重构：backend 抽象 + async 工厂（sqlite|postgres）＋（可选）官方 AsyncSaver
├── research/                    # 新增：无 app/agent 依赖
│   ├── schemas.py               # pydantic 实体（ResearchRun/SubQuestion/SearchQuery/Source/Evidence/Claim/Citation）
│   ├── registry.py              # SourceRegistry（确定性归一化/幂等 upsert）
│   ├── repository.py            # run 作用域 CRUD + 幂等 + 事务封装
│   ├── provenance.py            # claim→evidence→source 回溯查询
│   └── db.py                    # 连接/迁移执行（仅 research_* 表族）
├── tools/…（各工具末尾接 registry 调用；或经 research service 侧 hook）
└── api/server.py                # lifespan 管理 async pool；新只读 provenance 端点（走 Gate）
```

---

## 8. Backward Compatibility 影响面（逐个显式列出）

| 既有成果 | 受影响？ | 说明 |
|---|---|---|
| R1 文件安全 | ✅ 不受影响 | 纯函数 + server.py 边界；与持久化零交集 |
| R2 checkpoint（SQLite + Bridge） | ⚠️ 重构 | 工厂抽象化；默认 backend=sqlite 时**行为不变**（文件、路径 env、fail-fast、单例语义保持）；切官方 AsyncSqliteSaver（选项 A）时 a* 不再走 to_thread，测试需回归但语义等价 |
| R3 event model | ✅ 不受影响 | monitor/信封/终态守卫不触碰；新增 artifact_id 进事件属 additive 增量（如采纳） |
| WS/HTTP schema | ✅ 不变（除新增只读 provenance 端点需 Gate） | ARCHITECTURE §6 兼容规则保持 |
| `run_deep_agent` 契约 | ⚠️ 内部改造 | 对外（/api/task 语义、thread 替换、终态事件）不变；内部：main_agent 组装 lazy + checkpointer 按 backend 选择；**MUST NOT 绕过 run_deep_agent 的红线保持** |
| 现有会话（sqlite 旧 thread） | ⚠️ 按策略处理 | 见 §9（不自动迁移；可选导出；切换后旧 thread 不再续跑） |
| 测试 | ⚠️ 增补 | R2 recovery 测试保持 sqlite 默认可跑；PG 集成测试 skipif（无 env 不跑）；现有 180+ 项不回归 |
| Harness（AGENTS/PROCESS/ARCHITECTURE/DECISION/TESTING） | ✅ 需同步 | 本评审通过后：新增 Decision（D0xx）；ARCHITECTURE §2/§5/§7 增补 research 层与 PG 边界；TESTING 增补 PG 验证契约 |
| 框架依赖升级 | ❌ 不升级 | 仅**新增** langgraph-checkpoint-postgres/psycopg/psycopg-pool（版本配对 §2.2）；不升级 langgraph/langchain/deepagents |

---

## 9. Migration Strategy（旧 SQLite 数据怎么处理）

| # | 问题 | 决策建议（待 Gate） |
|---|---|---|
| 1 | 现有 checkpoint 是否需要迁移 | **建议不迁移**：checkpoint = 执行状态（与具体代码 schema、模型版本绑定），研究价值低；跨后端序列化格式虽同为 JSONB/BYTEA，但迁移收益 < 风险 |
| 2 | 不迁移时新 run 是否直接用 PG | ✅ 是（backend=postgres 即全量新 run 走 PG） |
| 3 | 旧 thread 是否继续读 SQLite | 过渡期支持：`backend=sqlite` 可继续跑旧线程（行为不变）；切 postgres 后旧线程不再续跑（文档化行为变更，需要你在 Gate 确认可接受） |
| 4 | one-time migration | 不提供 checkpoint 迁移；提供**可选导出脚本**（sqlite → JSON 归档，`AGENT_CHECKPOINT_DB` 只读导出），供审计留档 |
| 5 | migration 幂等 | 对 research_* 表族：版本化 SQL 文件（`db/migrations/V0NN__*.sql`）+ 简单迁移表，重跑安全（IF NOT EXISTS / 版本跳过） |
| 6 | 本地开发 fallback | `AGENT_CHECKPOINT_BACKEND` 缺省 sqlite；无 PG 时可完整开发/测试 |
| 7 | test 如何运行 | 默认 SQLite（快速、隔离）；PG 集成测试：`RESEARCH_DSN`/`AGENT_CHECKPOINT_DSN` 存在才跑（skipif 纪律沿用 R2 模式） |
| 8 | CI/container | 本机无 Docker；提供 `docker/docker-compose.postgres.yaml`（供你的运行环境/CI 起 PG 服务），CI 按需 `services: postgres:` 段；本环境不做强依赖 |
| 9 | 避免测试强依赖线上 PG | 测试一律指向临时 schema/库（每测试 `CREATE SCHEMA test_xxx` 或独立 DB）；生产 DSN 与测试 DSN 分离 |
| 10 | schema migration 工具选型 | 推荐**仓库零新增依赖方案**：版本化 `.sql` 文件 + 轻量 runner（`app/research/db.py` 执行 + `schema_migrations` 表），**只管理 research_* 表族**；checkpoint 表族交给 saver.setup()。若后续 artifact 表增多再评估 alembic（新增依赖需 Gate） |

---

## 10. Risk Register

| 风险 | 等级 | 缓解 |
|---|---|---|
| 版本配对错误（3.1.x 要求 checkpoint≥4.1.0 与锁 4.0.3 冲突） | 高 | 锁定 **3.0.5**（PyPI requires 实证）；uv sync 后立即跑 `checkpoint-postgres` 导入探针 |
| `AsyncPostgresSaver` 需 running-loop 构造（aio.py:52）与现有模块级组装冲突 | 高 | main_agent lazy 组装 + lifespan 建 pool（§4.10）；必须作为 Gate 条件接受 |
| 恢复/重放语义误判：PG 不自动获得 replay 产品能力 | 高 | 评审 §4-6 明示边界；提供 §10 验证协议在真实 PG 上验收 resume/time-travel |
| 恢复时工具重放（未落 checkpoint 的 task 调用被重跑，搜索/DB 副作用重复） | 高 | checkpoint 只存图状态（R2 已声明）；应用层补「副作用幂等键」注册（search query hash / task_call_id 已执行集合）后再开放 resume 入口 |
| 并发写：LangGraph 每步 checkpoint + artifact 高频追加并发 | 中 | PG 多写者天然支持；saver 内部锁 + AsyncConnectionPool；artifact 短事务 + 幂等 upsert |
| async 连接生命周期（loop 亲和、pool 泄漏） | 中 | pool 归 lifespan 管理；saver 归 run/进程单例；测试覆盖「重启后 pool 重建」 |
| 事务一致性跨 checkpoint/artifact | 中 | 不做跨层分布式事务；run 状态机 + 幂等键收敛（§6.4） |
| 迁移期间行为漂移（旧会话、事件、R1/R2/R3 回归） | 中 | backend 缺省 sqlite 零破坏；全套既有测试先绿再切 |
| 测试环境（无 Docker/PG、无 deepagents） | 中 | 遵循 R2 模式：本环境源码级验证 + 用户本机/CI 运行验证协议 |
| 部署配置（DSN、凭据、网络） | 中 | env 全量 + `.env.example` 同步；凭据只读 env（ARCHITECTURE §5）；fail-fast |
| research 表族迁移工具零依赖方案的长期演进性 | 低-中 | 版本化 SQL 足够支撑 P1；P2 需要时评估 alembic（Gate） |

---

## 11. Architecture Decision（建议裁决 + 待确认 Gate）

### 建议裁决：**APPROVE WITH CONDITIONS**

条件：① 锁定 `langgraph-checkpoint-postgres==3.0.5`（+psycopg/psycopg-pool）；② 接受 main_agent/checkpointer 组装方式的 lazy/异步改造（选项 A 或 B 二选一）；③ checkpoint 旧数据不自动迁移、切换后旧 thread 不续跑（文档化行为变更）；④ checkpoint 表族归 saver.setup() 自管、外部迁移只碰 research_*；⑤ 全部阶段遵循你 §十九 的逐段确认流程，每段完成停下汇报；⑥ R1/R2/R3 测试在任一 backend 切换前保持全绿；⑦ PostgreSQL 真实能力（setup/resume/time-travel/并发/pool）先按 §10 验证协议在你的运行环境通过，再进入 F1。

### 需要你逐项确认的 Gate（请回复编号与选择）

- **G1（后端范围）**：确认生产=PostgreSQL、SQLite=local/test fallback 的方向，且不引入 Redis/Neo4j/Vector/Kafka。
- **G2（版本锁定）**：确认新增依赖 `langgraph-checkpoint-postgres==3.0.5`、`psycopg[binary]>=3.2`、`psycopg-pool>=3.2`（不升级既有 langgraph/langchain/deepagents）。orjson 已在锁内。
- **G3（Bridge 处置）**：选项 A（推荐，SQLite fallback 切官方 AsyncSqliteSaver，Bridge 全退役）／选项 B（SQLite 分支保留 Bridge 最小改动）／选项 C（不推荐）。
- **G4（组装方式）**：确认 main_agent/checkpointer 由「模块 import 期构造」改为「loop 内 lazy/异步组装 + lifespan 管理 pool」（这是 PG 原生 async 的必要代价）。
- **G5（replay 边界）**：确认「Execution Replay=checkpoint（LangGraph 原生）」「Research Reconstruction=artifact store（应用层）」分层，且 P1 只做 Reconstruction 的查询底座，resume 产品入口留到后续阶段。
- **G6（旧数据）**：确认 checkpoint 旧 SQLite 数据不自动迁移（可选导出脚本留档）；切 postgres 后旧 thread 不续跑为文档化行为变更。
- **G7（迁移工具）**：确认 research_* 表族采用「零新增依赖的版本化 SQL + 轻量 runner」；checkpoint 表族归 saver.setup()。
- **G8（目录/契约）**：确认新增 `app/research/`（registry/repository/provenance/db）与 checkpoint 工厂重构的文件边界；新增 provenance 只读端点时机（P1 末还是随 F1）。

---

## 12. 下一步（你确认后）

1. 你确认 G1–G8（可部分确认/带修改）→ 输出 **PostgreSQL Checkpoint Migration Design**（`docs/spec/` 规范文档：工厂抽象、lazy 组装、pool 生命周期、迁移 SQL、测试矩阵、验证协议）。
2. 你确认 → 实施 Checkpoint Migration / Replay Verification（默认 sqlite 回归全绿 → PG 在你的运行环境按验证协议跑通 resume/time-travel）。
3. 你确认 → F1 Research Artifact（schemas/repository/registry + research_* DDL + run 生命周期挂钩）。
4. 此后 F2→F3→… 每段停下汇报。

---

### 附：本评审使用的验证证据清单（均可复核）

- 仓库代码：`app/runtime/checkpoint.py`、`app/agent/main_agent.py`、`tests/test_checkpoint_recovery.py`、`DECISION.md D009`、`uv.lock`、`ARCHITECTURE.md`、`ROADMAP.md`
- 包源码（解包 wheel）：`.deepagents-fc/pgcheck/langgraph-checkpoint-postgres-3.0.5/`、`langgraph-checkpoint-sqlite-3.0.3/`、`langgraph-checkpoint-4.0.3/`
- 版本要求：PyPI JSON API（langgraph-checkpoint-postgres 3.0.5 / 3.1.2 / 2.0.25 的 requires_dist）
- 文档：LangGraph [time-travel](https://docs.langchain.com/oss/python/langgraph/use-time-travel)
