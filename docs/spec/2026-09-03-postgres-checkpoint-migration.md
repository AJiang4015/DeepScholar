# Spec: PostgreSQL Checkpoint Migration（checkpoint backend 抽象 + AsyncSaver + Replay 验证）

> 状态：**Approved（用户裁决 G1–G8 通过）→ 实施完成（代码 + 自动测试全绿）**；
> PG 真实验证协议 P1–P7 待用户运行环境执行（本沙箱无 Docker/PG）。
> 实施记录见 §13；评审文档：`ARCHITECTURE_CHANGE_REVIEW.md`；决策：`DECISION.md D010`。

---

## 0. 需求覆盖映射（用户 24 项 ↔ 本 Spec 章节）

| # | 用户要求 | 章节 |
|---|---|---|
| 1 | PostgreSQL checkpoint backend abstraction | §4.1–4.2 |
| 2 | AsyncPostgresSaver 创建方式 | §4.4 |
| 3 | AsyncConnectionPool 生命周期 | §4.4 |
| 4 | FastAPI lifespan 集成 | §4.5 |
| 5 | main_agent lazy assembly | §4.6 |
| 6 | SQLite fallback → 官方 AsyncSqliteSaver | §4.3 |
| 7 | AsyncBridgeSqliteSaver 删除方案 | §4.7 |
| 8 | AGENT_CHECKPOINT_BACKEND / DSN 配置 | §4.1 |
| 9 | checkpoint setup() 初始化 | §4.8 |
| 10 | full saver 的 checkpoint history | §4.9 |
| 11 | get_state / history / resume / replay 验证方案 | §7.3–7.4 |
| 12 | 旧 SQLite checkpoint 处理策略 | §9.1 |
| 13 | run_deep_agent backward compatibility | §4.6.3 / §5 |
| 14 | Task Runtime / Event / Harness 影响面 | §5.2 / §10 |
| 15 | migration / rollback | §9 |
| 16 | connection pool failure / shutdown / restart | §4.4.4 / §9.3 |
| 17 | sync / async recovery test | §7.2 |
| 18 | PostgreSQL integration test | §7.5 |
| 19 | 文件修改清单 | §5.1 |
| 20 | 新增文件清单 | §5.2 |
| 21 | 依赖变更 | §6 |
| 22 | 测试矩阵 | §7.6 |
| 23 | acceptance criteria | §8 |
| 24 | Architecture / Decision / Testing 文档同步 | §10 |

---

## 1. Problem（为什么要做这次迁移）

1. 当前唯一 checkpointer 是 `app/runtime/checkpoint.py` 的 `AsyncBridgeSqliteSaver`（自定义 async 桥接）。
2. 已确认（源码实证）：
   - 官方同步 `SqliteSaver` 的 a* 方法抛 `NotImplementedError`（`langgraph-checkpoint-sqlite==3.0.3` `__init__.py:496-535`）；
   - 同版本**自带官方 `AsyncSqliteSaver`**（`aio.py:31`），且 `aiosqlite` 已存在 uv.lock → 自定义 Bridge 无继续存在理由（G3 选项 A）；
   - 官方 `AsyncPostgresSaver`（`langgraph-checkpoint-postgres==3.0.5`）原生实现全部 a*，适配仓库锁定 langgraph 1.1.10 / checkpoint 4.0.3；
   - 生产多进程/多实例共享 checkpoint 需要 PostgreSQL（SQLite 单写者上限）。
3. 目标分层（G5）：Execution Replay 归 checkpoint（LangGraph 原生），Research Reconstruction 归 Artifact（下一阶段）；**本阶段不开放 resume 产品入口**，但必须把 replay 数据面与验证方案做实。

---

## 2. Goal & Non-Goals

### Goal
- 建立 checkpoint backend 抽象：`sqlite`（local/test 默认，官方 AsyncSqliteSaver）与 `postgres`（生产，官方 AsyncPostgresSaver + AsyncConnectionPool）双后端，env 可切换，fail-fast。
- 删除 `AsyncBridgeSqliteSaver`。
- `main_agent` 改为 loop 内 lazy 组装；FastAPI lifespan 管理 PG pool。
- 在 SQLite-async 与 PostgreSQL 上分别验证：写入 → 多 checkpoint 历史 → get_state/get_state_history → resume / replay（时间旅行数据面）→ parent chain。
- 保持 `run_deep_agent` 对外契约与 R1/R2/R3 行为不回归。

### Non-Goals（本 Spec 不做）
- ❌ Research Artifact store（F1，下一阶段）。
- ❌ resume / replay 的**产品入口**（API/UI/续跑入口）——只做执行层验证与数据面。
- ❌ 工具副作用幂等机制的**实现**——只记录设计契约（§4.10），产品化前必须存在。
- ❌ 旧 SQLite checkpoint 自动迁移（G6）。
- ❌ 升级 langgraph/langchain/deepagents（G2：版本冻结）。
- ❌ Redis/Neo4j/Vector/Kafka。
- ❌ shallow saver（G5：Replay 需要 full history，禁止 shallow）。

---

## 3. Current Architecture（基线快照，源码实证）

| 事实 | 证据 |
|---|---|
| 同步 `SqliteSaver` a* 抛 NotImplementedError | `.deepagents-fc/pgcheck/langgraph-checkpoint-sqlite-3.0.3/.../sqlite/__init__.py:496-535` |
| 官方 `AsyncSqliteSaver`：ctor 绑定 running loop（`self.loop=asyncio.get_running_loop()`）；sync 方法**仅限异线程**（同 loop 抛 `InvalidStateError`，异线程走 `run_coroutine_threadsafe`） | `sqlite/aio.py:111-138, 140-273` |
| 官方 `AsyncPostgresSaver`：ctor 绑定 loop（`aio.py:52`），conn 可为 `AsyncConnectionPool`；原生 async setup/alist/aget_tuple/aput/aput_writes/adelete_thread；sync 包装同「异线程」规则（`aio.py:436-579`） | `.deepagents-fc/pgcheck/langgraph-checkpoint-postgres-3.0.5/.../postgres/aio.py` |
| PG checkpoint 表族与版本迁移：`setup()`/`await setup()` 顺序执行内置 `MIGRATIONS[]` 并维护 `checkpoint_migrations(v)` | `postgres/base.py:37-85`、`__init__.py:77-102`、`aio.py:82-109` |
| full vs shallow：full 按 `(thread_id,ns,checkpoint_id)` upsert（保留全历史）；shallow 按 `(thread_id,ns)` upsert（只留最新） | `postgres/base.py:131-138` vs `shallow.py:123-126` |
| 仓库当前实现 | `app/runtime/checkpoint.py`（AsyncBridgeSqliteSaver + get_sqlite_checkpointer 单例）；`app/agent/main_agent.py:44-50`（import 期 create_deep_agent(checkpointer=...)）；`main_agent.py:129`（astream 调用点） |
| 系统环境运行探针（langgraph 1.2.7 + sqlite-saver 3.1.1 + aiosqlite 0.21）：AsyncSqliteSaver 上 interrupt→history 3 层（parent 链完整、newest-first、metadata.step=-1/0/1）→ Command(resume) 成功（counter=1 不重跑）→ 按 checkpoint_id 取历史成功 → 同 loop sync 调用抛 InvalidStateError | `.deepagents-fc/probes/replay_probe.py`（运行输出见 §附 B） |

---

## 4. Design

### 4.1 配置：backend 抽象与 env（对应 8）

`app/runtime/checkpoint.py` 顶部新增纯函数配置层（可单测，不 import 三方驱动）：

```python
# env 契约
#   AGENT_CHECKPOINT_BACKEND: "sqlite"(缺省) | "postgres"
#   AGENT_CHECKPOINT_DB:      sqlite 路径（沿用 R2，缺省 app/runtime/checkpoints.sqlite）
#   AGENT_CHECKPOINT_DSN:     postgres 连接串（backend=postgres 时必填）

@dataclass(frozen=True)
class CheckpointConfig:
    backend: str            # "sqlite" | "postgres"
    db_path: Path | None    # sqlite 用
    dsn: str | None         # postgres 用

def parse_checkpoint_config(environ: Mapping[str,str]) -> CheckpointConfig: ...
```

规则（fail-fast，均抛 `ValueError`/`RuntimeError`，不静默回退）：
- backend ∉ {sqlite, postgres} → 报错并列允许值；
- backend=postgres 且 dsn 为空 → 报错（提示设置 `AGENT_CHECKPOINT_DSN`）；
- backend=sqlite：沿用 `AGENT_CHECKPOINT_DB`（相对路径按进程 CWD，如 R2）或默认路径；
- `AGENT_CHECKPOINT_DB` 已设置但未显式设 backend → 语义 = sqlite（向后兼容）。
- 凭据只读 env（ARCHITECTURE §5），`.env.example` 同步（§6）。

### 4.2 checkpointer 抽象与进程级管理器（对应 1）

```python
# 类型：任何满足 LangGraph async checkpoint 接口的 saver
# 实现约束：必须在 running event loop 内创建（官方 async saver 均绑定 loop）

async def get_checkpointer() -> BaseCheckpointSaver:   # 统一入口（按配置分发）
async def close_checkpointer() -> None:                # 关闭 sqlite conn / 归还 pool（进程退出/lifespan shutdown）
```

- 进程内按 backend 缓存（`asyncio.Lock` 保护的模块级单例）；
- **loop 亲和（实现口径）**：进程级单例按 (backend, 资源键) 缓存并绑定创建时的 loop。
  同一 loop 内复用；跨 loop 访问时**自动重建新实例并尽力关闭旧实例**（sqlite / 自建
  pool 场景，兼容测试多 loop 与 uvicorn reload）；若 postgres pool 由应用层注入且绑定在
  其它 loop → 硬报错（FastAPI 场景不允许跨 loop 复用应用池）。
- sqlite 分支的 aiosqlite 连接与 postgres 分支的 pool 均**由本模块持有或经 `bind_postgres_pool()` 注入**（见 4.4）；
- 生命周期：`get_checkpointer()` 只负责取用（幂等）；`close_checkpointer()` 只允许在**创建它的 loop** 内调用。

### 4.3 SQLite fallback → 官方 AsyncSqliteSaver（对应 6）

```python
async def _open_sqlite_saver(cfg: CheckpointConfig) -> AsyncSqliteSaver:
    import aiosqlite  # 直接依赖（§6）
    path = str(cfg.db_path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(path)          # loop 内创建
    saver = AsyncSqliteSaver(conn)                # 绑定当前 loop
    await saver.setup()                           # 建表（幂等）+ 后续 PRAGMA 由官方 setup 处理
    return saver
```

- 保留的 R2 行为契约：默认文件路径、`AGENT_CHECKPOINT_DB` 覆盖、fail-fast（建表/连接失败抛 `RuntimeError`）、thread_id 语义、文件不被 output/ 下载可达（路径不变）。
- 不再需要自定义 Bridge：`AsyncSqliteSaver` 原生 a* 支撑 `astream`；sync 方法只用于**异线程**（测试策略见 §7.2——恢复测试 runner 改为 async 写法）。
- 并发语义如旧：单进程单连接（aiosqlite 内部调度）；多进程各自独立连接；local 环境可接受。

### 4.4 PostgreSQL：AsyncPostgresSaver + AsyncConnectionPool（对应 2/3/16）

原则（G4）：**pool 归应用层（FastAPI lifespan）所有；saver 是薄封装；禁止 pool 泄漏与跨 loop 使用。**

```python
# app/runtime/checkpoint.py（新）
_held_pool: AsyncConnectionPool | None = None        # 仅 postgres backend
_held_pool_loop: AbstractEventLoop | None = None

async def bind_postgres_pool(pool: AsyncConnectionPool) -> None:
    """FastAPI lifespan 调用：登记应用持有的 pool（禁止重复绑定；loop 记录）。"""

async def _get_postgres_saver(cfg) -> AsyncPostgresSaver:
    from psycopg_pool import AsyncConnectionPool   # 惰性 import（§4.7 例外说明）
    ...
    if _held_pool is None:
        # 脚本/测试场景：自建 pool（随后必须显式 close）
        _held_pool = AsyncConnectionPool(conninfo=cfg.dsn, open=False, ...)
    await _held_pool.open()                        # open 幂等
    saver = AsyncPostgresSaver(conn=_held_pool)    # 绑定 loop + pool
    await saver.setup()                            # 官方 MIGRATIONS（§4.8）
    return saver
```

- **pool 生命周期（FastAPI）**：
  - `lifespan` 启动：`pool = AsyncConnectionPool(conninfo=dsn, open=False, min_size=1, max_size=10, kwargs={"autocommit": True})` → `await pool.open()` → `await bind_postgres_pool(pool)`（`asyncio.get_running_loop()` 记录）→ 可选 prewarm `await get_main_agent()`（§4.6，fail-fast 提前到启动期）。
  - `lifespan` 关闭：`await close_checkpointer()`（归还持有引用）→ `await pool.close()`（幂等）→ 清理注册。
- **脚本/测试场景（无 FastAPI）**：提供 `async with managed_postgres_checkpointer(cfg)` 上下文管理器（自建 pool、yield saver、`await pool.close()`）；同步子进程测试用 `asyncio.run` 包 async 调用。
- **pool failure / restart（16）**：
  - PG 不可达（首次 `open`/`setup` 失败）→ `RuntimeError`（fail-fast，含 DSN host 脱敏提示），与 R2 fail-fast 语义一致；
  - 运行中连接丢失：psycopg_pool 在 `pool.check()`/下一次借用时重连（配置 `max_waiting`、`timeout`、`reconnect_timeout`），**上层不吞错**——异常照常冒泡到 run 级 error 终态（R3 语义不变）；
  - 服务重启 = 新进程新 pool：saver 不跨进程；**不允许跨 event loop 使用**（loop 守卫 §4.2 拦截）。
- **并发**：saver 内部 `asyncio.Lock` 串行化单实例游标使用；多 run/多线程经 pool 连接复用；**多实例部署共享同一 PG**（对比 SQLite 单写者）——这是迁移的生产价值。

### 4.5 FastAPI lifespan 集成（对应 4）

`app/api/server.py` lifespan 改造（最小化）：

```python
@asynccontextmanager
async def lifespan(_app: FastAPI):
    loop = asyncio.get_running_loop()
    manager.set_loop(loop)
    await init_checkpointer_lifespan()      # backend=postgres: 建 pool + bind + setup + prewarm；
                                            # backend=sqlite: 可选 prewarm（保持启动期 fail-fast）
    yield
    await shutdown_checkpointer_lifespan()  # close_checkpointer() + pool.close()（幂等）
```

- `init/shutdown` helper 放 `app/runtime/checkpoint.py`（server 只薄调），维持 runtime 无 app 内依赖。
- **行为变更点（文档化）**：R2 的"模块 import 即建 DB/启动 fail-fast"改为"首次 run 或 lifespan prewarm 时 fail-fast"。服务场景用 lifespan prewarm 保持等价的启动期校验；脚本场景在首个 `run_deep_agent` 触发。

### 4.6 main_agent lazy assembly（对应 5/13）

现状：`main_agent.py` 模块 import 期 `create_deep_agent(checkpointer=get_sqlite_checkpointer(), ...)`（模块级全局）。

改为：

```python
# app/agent/main_agent.py
_main_agent_lock = asyncio.Lock()
_main_agent = None

async def get_main_agent():
    """loop 内 lazy 组装：首个 await 创建 AsyncSaver（绑定当前 loop）并编译 agent；幂等缓存。"""
    global _main_agent
    if _main_agent is None:
        async with _main_agent_lock:
            if _main_agent is None:
                checkpointer = await get_checkpointer()      # 由 backend 决定（§4.2）
                _main_agent = create_deep_agent(
                    model=model,
                    system_prompt=main_agent_content["system_prompt"],
                    tools=[generate_markdown, convert_md_to_pdf, read_file_content],
                    checkpointer=checkpointer,
                    subagents=[database_query_agent, network_search_agent, knowledge_base_agent],
                )
    return _main_agent
```

- `run_deep_agent` 内 `async for chunk in main_agent.astream(...)` → `agent = await get_main_agent()` 后 `agent.astream(...)`；**其余代码零改动**。
- 并发首个请求由 `_main_agent_lock` 串行化；失败不缓存（下次重试）。
- 脚本 `__main__`/examples 走 `asyncio.run(run_deep_agent(...))`，天然在 loop 内 → 无需改动。
- **Backward compatibility（13）**：
  - `run_deep_agent(query, session_id)` 签名、`POST /api/task` 语义、thread 替换/取消、R3 终态事件、monitor/ContextVar 全部不变；
  - `ARCHITECTURE.md`「不得绕过 run_deep_agent 直接调用 main_agent.astream」红线不变（get_main_agent 仅内部使用）；
  - 对外的 `main_agent` 模块级符号删除 → 需 grep 全仓确认无外部引用（预期仅 `main_agent.py` 内部与 `__main__`，实施时以 grep 验证为 AC）。

### 4.7 AsyncBridgeSqliteSaver 删除方案（对应 7）

- 删除：`app/runtime/checkpoint.py` 中 `AsyncBridgeSqliteSaver` 类、`get_sqlite_checkpointer()`、`_build_sqlite_saver`（桥接版）及其模块 docstring 的 Bridge 说明。
- 保留并泛化：fail-fast 工厂模式、进程内单例、路径/DSN env 解析、测试用的 `get_checkpoint_db_path()` 等价物（sqlite 路径断言）。
- 文档同步：R2 spec 与 `DECISION.md D009` 的「Bridge 必要性」表述更新为「D009 已由 D0xx 取代（官方 AsyncSqliteSaver + AsyncPostgresSaver）」；`ARCHITECTURE.md` §2 app/runtime 描述更新。
- 不保留 Bridge 作为长期 fallback（G3）；不提供兼容 shim（仓库内无外部使用者，先 grep 验证再删，AC-7）。

### 4.8 setup() 与表族归属（对应 9）

- checkpoint 表族（`checkpoint_migrations/checkpoints/checkpoint_blobs/checkpoint_writes`）**只由官方 saver 的 `setup()`/`await setup()` 管理**（`base.py MIGRATIONS[]` 版本化执行，`CONCURRENTLY` 建索引、`task_path` 演进列均内置）。
- 仓库侧**禁止**以任何迁移工具修改 checkpoint 表族（G7）；外部迁移体系（`schema_migrations`/版本化 SQL）属 F1 research_* 表族，**本阶段不创建**。
- 初始化时机：sqlite/postgres 工厂首次创建时执行；幂等（IF NOT EXISTS + 版本表）。

### 4.9 full saver 与 checkpoint history（对应 10）

- 统一使用 **full `AsyncPostgresSaver` / `AsyncSqliteSaver`**（非 shallow）：
  - checkpoints 行按 `(thread_id, checkpoint_ns, checkpoint_id)` upsert 保留全部历史；
  - `parent_checkpoint_id` 构成父链 → `get_state_history` 可回溯全部中间状态；
  - 运行探针实证：interrupt 场景产生 3 层历史（metadata.step = -1/0/1），newest-first 排序、父链完整。
- 存储治理（本阶段只记录，不实施）：按 thread 的保留/归档策略（`adelete_thread`/导出）留给后续；**禁止用 shallow 化换取存储**（G5）。

### 4.10 副作用幂等契约（记录设计，不实现）

> 必须文档化：checkpoint recovery 从**未执行完的中间 checkpoint** 恢复时，LangGraph 会重放该 checkpoint 之后的 pending 工具调用（task/搜索/DB/RAG 等外部副作用会**再次执行**）。

产品化 resume 入口前必须实现的机制（本阶段只确定契约）：

```text
task_call_id      每个工具/子智能体调用在父 run 内的稳定 id（可复用 LangGraph task id 或自生成）
idempotency_key   = hash(agent, tool, canonical_args)（参数归一化后）
side-effect registry（位置候选：app/runtime/side_effects 或 app/research 之上；F1 时定案）
policy: BEFORE 执行 → 查询 registry（pending/done/failed）；恢复重放时 done → 返回已记录结果，pending/failed → 重新执行
```

约束：不得在消息/LLM 上下文里承载该账本（防污染）；写账本与执行结果在**同一事务/原子边界**（artifact 层事务，F1）。

---

## 5. 文件改动清单（对应 19/20/13/14）

### 5.1 修改文件（Modify）

| 文件 | 改动 | 影响面 |
|---|---|---|
| `app/runtime/checkpoint.py` | 重写：配置解析 + async 工厂 + sqlite/postgres 分支 + pool 登记/关闭 + loop 守卫 + 上下文管理器；删除 Bridge | 核心（§4.1–4.5、4.7） |
| `app/agent/main_agent.py` | 模块级 `main_agent` → `async def get_main_agent()` lazy 组装；`run_deep_agent` 改用 `await get_main_agent()` | Agent 组装（§4.6） |
| `app/api/server.py` | lifespan 增 `init/shutdown_checkpointer_lifespan()`（backend=postgres 时建 pool/prewarm/关闭） | 服务生命周期（§4.5） |
| `app/api/context.py` | **不改**（保持零依赖） | — |
| `app/api/monitor.py` | **不改** | — |
| `tests/test_checkpoint_recovery.py` | runner 从 sync invoke 改 async（`asyncio.run` + `ainvoke`）；工厂调用更新；语义断言不变（marker/重启恢复） | 测试（§7.2） |
| `.env.example` | 增 `AGENT_CHECKPOINT_BACKEND` / `AGENT_CHECKPOINT_DSN` 注释示例 | 配置文档 |
| `pyproject.toml` / `requirements.txt` | 依赖增补（§6） | 依赖 |

### 5.2 新增文件（Add）

| 文件 | 内容 |
|---|---|
| `tests/test_checkpoint_config.py` | env 解析纯函数：合法/非法/缺省/组合/向后兼容（AGENT_CHECKPOINT_DB 无 backend） |
| `tests/test_checkpoint_async_sqlite.py` | AsyncSqliteSaver：astream/interrupt/resume/history/parent chain/按 checkpoint_id 取历史/sync-trap/loop 守卫 |
| `tests/test_checkpoint_postgres.py` | PG 集成（skipif 无 `AGENT_CHECKPOINT_DSN_TEST`/无 psycopg）：同 async 套件 + pool 生命周期 + 并发多 thread + 重启(新 pool) + fail-fast |
| `tests/test_checkpoint_replay_verify.py` | Replay 验证协议（§7.4）：multi-checkpoint → history → 指定 checkpoint_id → resume/replay 断言（双后端参数化，PG 部分 skipif） |
| `docs/spec/2026-09-03-postgres-checkpoint-migration.md` | 本文件 |
| `docker/docker-compose.postgres.yaml` | 本地/CI 用 PG 服务（postgres:16，最小配置，env 化） |

### 5.3 删除文件（Delete）

| 文件 | 说明 |
|---|---|
| 无独立文件删除 | `AsyncBridgeSqliteSaver` 在 `app/runtime/checkpoint.py` 内删除（§4.7） |

---

## 6. 依赖变更（对应 21）

| 依赖 | 版本 | 说明 | 是否已在锁内 |
|---|---|---|---|
| `langgraph-checkpoint-postgres` | `==3.0.5` | PG full saver（requires checkpoint>=2.1.2,<5 → 兼容锁 4.0.3） | ❌ 新增（G2） |
| `psycopg[binary]` | `>=3.2` | PG 驱动 | ❌ 新增（G2） |
| `psycopg-pool` | `>=3.2` | AsyncConnectionPool | ❌ 新增（G2） |
| `aiosqlite` | `>=0.20` | app 直接 import（sqlite fallback 工厂）→ 需转为**直接依赖声明** | ✅ 已在锁（传递）→ 显式声明 |
| `orjson` | — | saver 内部依赖 | ✅ 已在锁，不声明 |
| 既有 langgraph 1.1.10 / checkpoint 4.0.3 / sqlite-saver 3.0.3 / deepagents 0.5.7 / langchain 1.2.17 | — | **冻结不升级** | — |

安装/锁定方式：`uv add langgraph-checkpoint-postgres==3.0.5 'psycopg[binary]>=3.2' 'psycopg-pool>=3.2' aiosqlite>=0.20`（若 uv 可用）；否则 pyproject/requirements 同步 + 用户环境安装（遵循 R2 先例：环境不可用时如实报告，不伪造）。实施环境注意 `pyproject.toml` `requires-python = >=3.12,<3.13` 与 psycopg binary wheel 兼容。

---

## 7. 测试计划与验证方案（对应 11/17/18/22）

### 7.1 原则

- 默认测试路径 = **SQLite-async**（无外部服务、快、隔离；`_testtmp` 临时 DB 模式沿用 R1/R2）。
- PG 测试 = **显式环境门控**：`AGENT_CHECKPOINT_DSN_TEST` 存在且可连且 psycopg 可导入才跑，否则 skip（TESTING.md §1 skipif 纪律）。
- 复用 R2 的「真实子进程跨进程恢复」模式，但 runner 改为 **async 执行**（官方 AsyncSaver 的 sync 方法仅限异线程——见 §4.3/证据）。

### 7.2 sync/async recovery test（17）

`tests/test_checkpoint_recovery.py` 改造：
- 子进程 runner 由 `graph.invoke(...)` 改为 `asyncio.run(main())` + `await graph.ainvoke(...)` / `Command(resume=...)`；
- 跨进程证据模式保留：随机 `marker_a`（进程 B 重跑 step1 则必然不同）+ counter==1 + log step1 仅一次；
- 工厂调用改为 `await get_checkpointer()`（backend=sqlite 时覆盖 `AGENT_CHECKPOINT_DB` 指向临时文件）；
- 断言不变：DB 文件存在、checkpoints 表有 thread_id 记录、进程 B `get_state` 找回、resume 不重跑 step1。
- **新增**：同一套件加 PG 参数化版本（skipif），子进程 runner 用 `managed_postgres_checkpointer` 上下文（自建 pool）。

### 7.3 get_state / get_state_history / resume（11a）

`tests/test_checkpoint_async_sqlite.py`（PG 变体同构）断言：
1. interrupt 后 `aget_state`: `next == ("step2",)`、counter==1；
2. `aget_state_history`: ≥3 条、newest-first、每条 parent_config 指向下一条（父链闭合）、`metadata.step` 单调；
3. latest resume（`Command(resume=...)`）：counter==1（step1 未重跑）、marker_b 注入成功；
4. 按历史 checkpoint_id 直取 `aget_tuple` 成功（数据面支持任意时间点定位）。

### 7.4 Replay / time-travel 验证协议（11b，真实验证链）

验证对象 = **checkpoint 数据面 + LangGraph 图运行时的 replay 语义**（明确三层归属，防误判）：

| 能力 | 归属 | 本阶段动作 |
|---|---|---|
| 写入多 checkpoint + parent 链 + 任意 checkpoint_id 可取 | Framework（saver） | ✅ 自动测试（§7.3）+ 本地探针已证 |
| `get_state_history` 遍历/定位历史态 | Framework（graph runtime × saver） | ✅ 自动测试 |
| resume（Command(resume=...) 从最新中断点续跑） | Framework | ✅ 自动测试（R2 模式） |
| **time-travel**：从**历史 checkpoint** 分支/重放（update_state as_node / 带 checkpoint_id 的 invoke；LangGraph 文档 API，langgraph 1.1.10 需真实验证细节） | Framework（graph runtime） | ⚠️ 写入 PG 验证协议（下述 P2），实施阶段在仓库锁定环境复跑 |
| resume 产品入口（API/UI/续跑命令） | Application/Product | ❌ 本阶段不实现（G5） |
| 恢复后未落 checkpoint 的副作用重放与幂等 | Application | ❌ 只记录契约（§4.10） |

**PG 运行验证协议（在你的 PG 环境执行，本沙箱无 PG/Docker）**：
```text
P1 环境准备：docker compose -f docker/docker-compose.postgres.yaml up -d
            export AGENT_CHECKPOINT_DSN_TEST=postgresql://... AGENT_CHECKPOINT_BACKEND=postgres
P2 自动套件：pytest tests/test_checkpoint_postgres.py tests/test_checkpoint_replay_verify.py -q -k postgres
P3 手工 replay 演示（脚本，复用 §7.3 图）：
    run → interrupt（记录：history N 条、各自 checkpoint_id/parent）
    get_state_history 选择 step=1 的历史态
    按官方 time-travel 方法（update_state + as_node 或带 checkpoint_id invoke——以锁定 langgraph 1.1.10
    实测行为为准，先跑探针再固化断言）从该历史态分支 → 断言：历史之前的状态未被重跑（marker 保留）、
    分支产生新 checkpoint 且 parent 指向所选历史态
P4 并发：多 thread 并发 ainvoke 多 run → 互不干扰、history 各自独立
P5 重启：进程 A 中断落库 → 退出 → 进程 B 新 pool 同 thread get_state + resume（等价 R2 跨进程语义）
P6 fail-fast：错误 DSN → 启动/首次 run 抛 RuntimeError（host 脱敏）
P7 pool 生命周期：lifespan 启停不泄漏（close 幂等）；运行中 kill PG → 下一次操作异常冒泡（R3 error 终态）
```

### 7.5 PostgreSQL integration test（18）

`tests/test_checkpoint_postgres.py`（skipif 门控）：
- async suite 同构（§7.3/7.4 的 PG 参数化）；
- pool 生命周期：`managed_postgres_checkpointer` 建/用/关；二次 close 幂等；
- 跨「进程重启」模拟：两次独立 pool+saver 实例读写同一 DSN（临时 schema 隔离，避免污染共享库：每测试 `CREATE SCHEMA test_<uuid>` 并在 DSN 显式 search_path 或独立 DB——推荐独立 DB/账号，实施时定案其一）；
- fail-fast：错误 DSN；
- 并发多 thread 测试（见 P4）。

### 7.6 测试矩阵（22）

| 测试 | 后端 | 环境 | 断言要点 |
|---|---|---|---|
| test_checkpoint_config.py | — | 任意 | env 解析/非法组合/缺省/向后兼容 |
| test_checkpoint_recovery.py（改造） | sqlite-async | 任意（_testtmp） | 跨进程 restart recovery（AC-3/AC-4 语义保留） |
| test_checkpoint_async_sqlite.py | sqlite-async | 任意 | §7.3 + sync-trap + loop 守卫 |
| test_checkpoint_replay_verify.py (sqlite) | sqlite-async | 任意 | replay 数据面 + time-travel 探针化断言 |
| test_checkpoint_postgres.py | postgres | DSN_TEST | §7.5 |
| test_checkpoint_replay_verify.py (postgres) | postgres | DSN_TEST | 同左 PG 版 |
| 回归 R1/R3 全量 | — | 任意 | 既有 180+ 项不回归（backend 缺省 sqlite） |

---

## 8. Acceptance Criteria（对应 23）

| AC | 内容 | 验证 |
|---|---|---|
| AC-1 | `parse_checkpoint_config` 全分支可单测：缺省 sqlite、非法 backend、postgres 缺 DSN、AGENT_CHECKPOINT_DB 兼容 | test_checkpoint_config.py |
| AC-2 | backend=sqlite（缺省）时 `run_deep_agent` 全链路行为与 R2 等价：DB 路径/文件/thread_id/fail-fast | 回归 + recovery 测试 |
| AC-3 | `AsyncBridgeSqliteSaver` 及其引用全部删除；全仓 grep `AsyncBridge|get_sqlite_checkpointer` = 0 | ruff + grep AC |
| AC-4 | `get_main_agent()` lazy 组装：首个调用在 loop 内创建 saver 并成功；并发首个请求只创建一次；run_deep_agent 对外契约不变 | async 单测 + E2E（用户环境） |
| AC-5 | 官方 AsyncSaver（双后端）通过 §7.3 全部断言（history/parent/resume/historical-id） | 自动测试 |
| AC-6 | PG：setup 幂等建表 + checkpoint_migrations 版本记录；错误 DSN fail-fast；pool 启停幂等；并发多 thread 隔离 | test_checkpoint_postgres.py（用户 PG 环境） |
| AC-7 | 恢复测试语义不回归：marker_a 跨进程一致、step1 只跑一次（sync runner→async runner 迁移后） | test_checkpoint_recovery.py |
| AC-8 | loop 亲和：同 loop 复用；跨 loop 自动重建（不抛错，旧实例尽力关闭）；应用注入 pool 跨 loop → RuntimeError；同 loop sync 调用 async saver → InvalidStateError（官方行为，文档化） | 测试 |
| AC-9 | 既有 R1/R3 测试全绿；ruff / compileall 通过 | 回归 |
| AC-10 | 文档同步完成（§10）：ARCHITECTURE / DECISION（新 D 记录）/ ROADMAP / TESTING / .env.example / R2 spec 标注 | diff review |
| AC-11 | Replay 验证协议 P1–P7 在用户 PG 环境执行并留档（本沙箱无 PG，如实标注未执行部分） | 验证记录 |

---

## 9. Migration / Rollback / 旧数据处理（对应 12/15）

### 9.1 旧 SQLite checkpoint（12，G6）
- 不自动迁移、不跨 backend 续跑、不静默删除（`app/runtime/checkpoints.sqlite*` 保留不动）；
- 提供**可选只读导出脚本**（后续小工具：sqlite → JSON 归档，仅读，不动原文件）——本阶段若时间不足可延后，属可选交付；
- `AGENT_CHECKPOINT_BACKEND=postgres` 的行为变更（新 run 走 PG、旧 thread 不续跑）写入部署文档（§10 ROADMAP/README/部署注记）。

### 9.2 代码迁移路径
1. 先合入「依赖 + 配置 + 工厂（sqlite 分支切官方 AsyncSaver）+ main_agent lazy」——**默认 backend=sqlite，行为等价**；
2. 同一改动内完成 PG 分支（新依赖、pool、lifespan）；
3. 删除 Bridge（§4.7）；
4. 全量回归 → PG 验证协议 → 文档同步。

### 9.3 Rollback
- 代码：git revert 至上一 commit（改动集中在 3 个产品文件 + 测试 + 依赖，边界清晰）；
- 运行时：`AGENT_CHECKPOINT_BACKEND` 缺省即 sqlite，**不设置即回退**（无需改代码）；
- 数据：本阶段无数据迁移，无回滚期数据风险；PG 中产生的 checkpoint 属执行态，删除/保留均无业务影响；
- 依赖：psycopg/psycopg-pool/checkpoint-postgres 从 pyproject/uv.lock 移除即可（无历史依赖）。

---

## 10. 文档同步（对应 24）

| 文档 | 同步内容 |
|---|---|
| `ARCHITECTURE.md` | §2 app/runtime 描述更新（backend 抽象、pool 生命周期）；§5 新增 `AGENT_CHECKPOINT_BACKEND/DSN` env 与凭据纪律；§3 依赖方向（runtime 无 app 内依赖保持）；§8 无新增 Gate 项（本变更已过 ACR Gate） |
| `DECISION.md` | 新增 **D0xx — PostgreSQL checkpoint migration**（记录 G1–G8 裁决、版本配对、Bridge 退役、官方 AsyncSaver、lazy 组装、pool 归属、replay 边界、旧数据策略、副作用幂等契约） |
| `docs/spec/2026-09-03-R2-persistent-checkpoint.md` | 顶部加「已被本 Spec（2026-09-03-postgres-checkpoint-migration）部分取代」标注：Bridge 部分退役、AsyncSqliteSaver 采用 |
| `ROADMAP.md` | 状态/阶段表追加本阶段行（PostgreSQL Checkpoint Migration + Replay Verification + F1 为下一阶段） |
| `TESTING.md` | §1 环境门控更新：新增 PG 测试的 env/依赖条件（AGENT_CHECKPOINT_DSN_TEST、psycopg、docker compose PG）与 skipif 纪律说明 |
| `.env.example` | `AGENT_CHECKPOINT_BACKEND`、`AGENT_CHECKPOINT_DSN`（注释：生产 PostgreSQL 示例、sslmode） |
| 部署注记（README 或 docs） | backend 切换行为（§9.1）、PG 启动/迁移/清理说明、`docker-compose.postgres.yaml` 用法 |

---

## 11. Risks / Limitations

| 风险 | 缓解 |
|---|---|
| 锁定 langgraph 1.1.10 上 AsyncSaver/replay 细节与系统 1.2.7 探针环境有出入 | 实施阶段先在**仓库锁定环境**跑 §7.4 探针再固化断言（P3 明确标注） |
| AsyncSaver sync 方法仅异线程可用 → 旧 sync 测试写法失效 | runner 统一 async（§7.2）；sync 接口只在「异线程 + loop 存活」场景保留（文档化） |
| 模块 import 期 fail-fast 变为 run/lifespan 期 fail-fast | lifespan prewarm（§4.5）保持服务启动即校验；行为变更文档化（AC-2 配套） |
| pool 生命周期/loop 亲和（FastAPI reload 会重建 loop） | loop 守卫 + lifespan 幂等启停；uvicorn reload 场景 pool 随 lifespan 重建 |
| 多进程/多实例 PG 共享后的并发写与长事务 | saver 内锁 + pool；单 run 单线程写 checkpoint（LangGraph 保证）；不做跨层分布式事务（F1 阶段再定 artifact 事务边界） |
| sqlite-async 多进程并发（本地 fallback） | WAL 单写者语义如旧；生产多实例一律走 PG |
| 旧 R2 恢复测试改为 async 后语义漂移 | marker/counter/log 断言保留原判据（AC-7） |
| 本沙箱无法验证 PG（无 Docker/服务） | 源码级验证完成（§3 证据）+ 用户环境协议 P1–P7 留档（AC-11），延续 R2「用户本机验证」模式 |

---

## 12. 附

### A. 依赖增补汇总（§6）一句话
`uv add langgraph-checkpoint-postgres==3.0.5 "psycopg[binary]>=3.2" "psycopg-pool>=3.2" "aiosqlite>=0.20"`，其余锁定不动。

### B. 运行探针输出（两份环境均通过；锁定版本复跑见 §13）
```text
SNAP next= ('step2',) counter= 1
HISTORY count= 3
HISTORY newest-first order ok= True
HISTORY parent-chain ok= True
  ckpt … parent= … next= ['step2'] step= 1     （interrupt 前）
  ckpt … parent= … next= ['step1'] step= 0
  ckpt … parent= None next= ['__start__'] step= -1
RESUME-latest counter= 1 marker_b= resumed-by-latest
HISTORICAL fetch by id ok= True next= ['step2']
SYNC-TRAP raised: InvalidStateError
```
探针脚本：`.deepagents-fc/probes/replay_probe.py`（可复核；未入 git）。

### C. Agent / Interview 价值（简要）
- 面试主线：「checkpoint 三态」（执行状态/历史/恢复）与「framework vs application vs product capability」分层是 deep agent runtime 的高频深挖点；
- 简历可写：多后端 checkpoint 抽象（sqlite-async/PG）、AsyncConnectionPool 生命周期、Bridge 退役决策（以官方实现替代 workaround）、跨进程 resume/replay 验证（marker 反证法）；
- 禁忌措辞：不得声称"迁移 PG 即获得 replay"（§7.4 归属表已隔离口径）。

---

## 13. 实施记录（2026-09-03，用户批准后执行）

| 项 | 结果 |
|---|---|
| 代码改动 | `app/runtime/checkpoint.py`（backend 抽象 + AsyncSaver 工厂 + pool 生命周期/注入）；`app/agent/main_agent.py`（lazy `get_main_agent()`）；`app/api/server.py`（lifespan init/shutdown + prewarm）；`AsyncBridgeSqliteSaver` 删除（grep 全仓无残留引用） |
| 依赖变更 | pyproject/requirements 增 `langgraph-checkpoint-postgres==3.0.5`、`psycopg[binary]>=3.2`、`psycopg-pool>=3.2`、`aiosqlite>=0.20`；既有版本全部冻结 |
| 配置/部署 | `.env.example` 增 AGENT_CHECKPOINT_BACKEND/DB/DSN/DSN_TEST；新增 `docker/docker-compose.postgres.yaml`（dev/test PG） |
| 测试 | 重写 `tests/test_checkpoint_recovery.py`（async runner，语义判据不变）；新增 `test_checkpoint_config.py` / `test_checkpoint_async_sqlite.py` / `test_checkpoint_replay_verify.py` / `test_checkpoint_postgres.py`（PG skipif 门控）/ `tests/_checkpoint_replay_harness.py` |
| 自动测试（锁定版本隔离环境：langgraph 1.1.10 / checkpoint 4.0.3 / sqlite-saver 3.0.3 / langchain-core 1.3.3） | checkpoint 相关 **25 passed, 5 skipped**（skip = PG 门控）；**全仓 215 passed, 6 skipped**（另含 1 MySQL 集成 skip）；compileall 通过 |
| 锁定环境 replay probe 复跑 | ✅ 与 §12B 输出一致（history=3 / 链闭合 / resume 不重跑 / checkpoint_id 取历史 / sync-trap） |
| PG 实测 P1–P7 | ⏳ 未执行（本沙箱无 Docker/PG）；按 §7.4 协议在用户运行环境执行并留档（含 P3 time-travel 分支细节以锁定版本实测为准） |
| 文档同步 | ARCHITECTURE.md §2/§3/§5；DECISION.md D010；ROADMAP.md 状态行；TESTING.md §1（PG 门控 + 独立测试库纪律）；R2 spec 顶部部分取代标注 |
| 范围核对 | 未触碰 F1/F2/F3、resume 产品入口、副作用幂等实现、Agent Orchestration 与 R1/R3 模块 |
