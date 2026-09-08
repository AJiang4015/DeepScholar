"""Agent 执行状态持久化 Checkpointer（R2 演进：backend 抽象 + 官方 AsyncSaver）。

历史：R2 使用自定义 ``AsyncBridgeSqliteSaver``（把同步 SqliteSaver 的 a* 桥接到
asyncio.to_thread）。经源码核查（2026-09-03，见 ARCHITECTURE_CHANGE_REVIEW.md 与
docs/spec/2026-09-03-postgres-checkpoint-migration.md）：

- ``langgraph-checkpoint-sqlite==3.0.3`` 自带官方 ``AsyncSqliteSaver``（aiosqlite，
  a* 方法原生实现）；同步 ``SqliteSaver`` 的 a* 依旧抛 NotImplementedError；
- ``langgraph-checkpoint-postgres==3.0.5`` 的 ``AsyncPostgresSaver`` 同样原生 async
  并支持 ``psycopg_pool.AsyncConnectionPool``（多进程/多实例共享需要 PostgreSQL）。

本模块据此重构为 **checkpoint backend 抽象**：

- ``AGENT_CHECKPOINT_BACKEND`` = ``sqlite``（缺省，local/test fallback）| ``postgres``（生产）；
- sqlite 后端 → 官方 ``AsyncSqliteSaver``（不再需要任何自定义 async 桥接）；
- postgres 后端 → 官方 ``AsyncPostgresSaver``：连接池在 FastAPI 场景由应用层
  （lifespan）创建并经 ``bind_postgres_pool`` 注入；脚本/测试场景可用
  ``managed_postgres_checkpointer`` 自建并释放 pool；
- 统一入口 ``get_checkpointer()``（必须在 running event loop 内调用——官方 AsyncSaver
  构造时绑定 loop）。进程级单例按 ``(backend, 资源键)`` 缓存，并绑定创建时的 loop：
  同一 loop 内复用；跨 loop 访问时**自动重建新实例并尽力关闭旧实例**（避免测试
  多 loop 场景互相污染）；若 postgres pool 由应用层注入且绑定在其它 loop → 硬报错
  （FastAPI 场景不允许跨 loop 复用应用池）；
- ``close_checkpointer()`` 幂等关闭本模块持有的 sqlite 连接 / 自建 pool；
- fail-fast 纪律不变：DB 不可用/建表失败抛 ``RuntimeError``，绝不静默回退内存。

持久化边界（沿用 R2，勿夸大）：
- 持久化 LangGraph 图状态快照（messages/节点进度/pending writes）至所选后端；
- 不持久化/不自动重放外部副作用（搜索/DB/RAGFlow/文件产物）——恢复可能重放未落
  checkpoint 的工具调用，幂等机制属后续阶段（见 migration spec §4.10）；
- 本模块是 Checkpoint 层，不是 BaseStore / Research Artifact（artifact 属 app/research，另一机制）。

执行状态（checkpoint_* 表族）由官方 saver 的 ``setup()`` 自行管理（版本化迁移），
本模块及任何仓库迁移工具都不得修改 checkpoint 表族。

平台注记（真实运行验证 2026-09-07）：psycopg 的 async 路径（AsyncConnectionPool /
AsyncPostgresSaver）不能在 Windows 默认 ProactorEventLoop 上运行；Windows 上跑
postgres 后端需 ``asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())``
（测试已在 tests/conftest.py 统一设置）。Linux/生产默认 SelectorEventLoop，无需处理。
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Mapping, Optional

# ---------------------------------------------------------------------------
# 环境变量契约（非凭据或凭据，均只读 env；与 ARCHITECTURE.md §5/§9 对齐）
# ---------------------------------------------------------------------------
CHECKPOINT_BACKEND_ENV = "AGENT_CHECKPOINT_BACKEND"
CHECKPOINT_DB_ENV = "AGENT_CHECKPOINT_DB"
CHECKPOINT_DSN_ENV = "AGENT_CHECKPOINT_DSN"

DEFAULT_BACKEND = "sqlite"
#: 默认 DB 路径：app/runtime/checkpoints.sqlite（相对本文件定位，gitignore 已忽略）
DEFAULT_CHECKPOINT_DB_PATH = Path(__file__).resolve().parent / "checkpoints.sqlite"

ALLOWED_BACKENDS = ("sqlite", "postgres")


# ---------------------------------------------------------------------------
# 配置解析（纯函数，可单测）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CheckpointConfig:
    """一次进程内生效的 checkpoint 配置。"""

    backend: str
    db_path: Optional[Path] = None  # sqlite 后端
    dsn: Optional[str] = None  # postgres 后端


def parse_checkpoint_config(
    environ: Optional[Mapping[str, str]] = None,
) -> CheckpointConfig:
    """从 env 解析 checkpoint 配置（fail-fast，绝不静默回退）。

    规则：
    - backend ∉ {sqlite, postgres} → ValueError（列允许值）；
    - backend=postgres 且 AGENT_CHECKPOINT_DSN 为空 → ValueError；
    - backend=sqlite：AGENT_CHECKPOINT_DB 覆盖（相对路径按进程 CWD resolve，
      与 R2 语义一致），否则默认路径 app/runtime/checkpoints.sqlite；
    - 只设置 AGENT_CHECKPOINT_DB 而未设置 backend → 语义 = sqlite（向后兼容）。
    """
    env = dict(os.environ if environ is None else environ)
    raw_backend = env.get(CHECKPOINT_BACKEND_ENV)
    backend = (raw_backend or DEFAULT_BACKEND).strip().lower()
    if backend not in ALLOWED_BACKENDS:
        raise ValueError(
            f"AGENT_CHECKPOINT_BACKEND 取值非法：{raw_backend!r}；允许值："
            f"{', '.join(ALLOWED_BACKENDS)}"
        )
    if backend == "postgres":
        dsn = (env.get(CHECKPOINT_DSN_ENV) or "").strip()
        if not dsn:
            raise ValueError(
                f"AGENT_CHECKPOINT_BACKEND=postgres 时必须设置 {CHECKPOINT_DSN_ENV}"
                f"（PostgreSQL 连接串）。拒绝回退到 sqlite。"
            )
        return CheckpointConfig(backend="postgres", dsn=dsn)
    raw_db = env.get(CHECKPOINT_DB_ENV)
    db_path = (
        DEFAULT_CHECKPOINT_DB_PATH
        if raw_db is None or not raw_db.strip()
        else Path(raw_db).expanduser().resolve()
    )
    return CheckpointConfig(backend="sqlite", db_path=db_path)


def get_checkpoint_db_path(environ: Optional[Mapping[str, str]] = None) -> str:
    """返回 sqlite 后端当前生效的 DB 文件绝对路径（供测试/报告断言）。"""
    cfg = parse_checkpoint_config(environ)
    if cfg.backend != "sqlite" or cfg.db_path is None:
        raise RuntimeError("get_checkpoint_db_path 仅适用于 sqlite 后端")
    return str(cfg.db_path)


# ---------------------------------------------------------------------------
# 进程级资源持有（按 loop 重建，绑定创建时的 loop）
# ---------------------------------------------------------------------------
class _HeldSaver:
    """一个被本模块持有的 checkpointer 及其绑定资源。"""

    __slots__ = ("saver", "loop", "conn", "pool", "backend", "key")

    def __init__(
        self,
        saver: Any,
        loop: asyncio.AbstractEventLoop,
        backend: str,
        key: str,
    ) -> None:
        self.saver = saver
        self.loop = loop
        self.backend = backend
        self.key = key
        self.conn: Any = None  # aiosqlite.Connection（sqlite 后端）
        self.pool: Any = None  # psycopg_pool.AsyncConnectionPool（自建时持有）


_holder: Optional[_HeldSaver] = None
_holder_lock = asyncio.Lock()
#: 由应用层 lifespan 注入的 postgres pool（FastAPI 场景；未注入时后端自建 pool）
_injected_pool: Any = None
_injected_pool_loop: Optional[asyncio.AbstractEventLoop] = None


def _current_loop() -> asyncio.AbstractEventLoop:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        raise RuntimeError(
            "get_checkpointer/close_checkpointer 必须在 running event loop 内调用"
            "（官方 AsyncSaver 构造时绑定 loop；FastAPI 由 lifespan 初始化）。"
        ) from None


# ---------------------------------------------------------------------------
# sqlite 后端：官方 AsyncSqliteSaver
# ---------------------------------------------------------------------------
async def _open_sqlite_saver(
    db_path: Path, loop: asyncio.AbstractEventLoop
) -> _HeldSaver:
    import aiosqlite  # 惰性导入：驱动缺失时给出可操作提示

    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(str(db_path))
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        saver = AsyncSqliteSaver(conn)
        # setup() 幂等建表（官方实现）
        await saver.setup()
        held = _HeldSaver(saver, loop, "sqlite", str(db_path))
        held.conn = conn
        return held
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一转 fail-fast RuntimeError
        raise RuntimeError(
            f"无法初始化 SQLite checkpointer（{CHECKPOINT_DB_ENV} 或默认路径解析到 "
            f"{db_path}）：{exc}。拒绝静默退化为内存 checkpointer，请检查路径可写性与 "
            f"依赖（aiosqlite / langgraph-checkpoint-sqlite）。"
        ) from exc


# ---------------------------------------------------------------------------
# postgres 后端：官方 AsyncPostgresSaver + AsyncConnectionPool
# ---------------------------------------------------------------------------
async def _open_postgres_saver(dsn: str, loop: asyncio.AbstractEventLoop) -> _HeldSaver:
    try:
        from psycopg_pool import AsyncConnectionPool  # 惰性导入（可选驱动）
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    except ImportError as exc:  # pragma: no cover - 依赖缺失路径
        raise RuntimeError(
            "postgres 后端需要依赖：langgraph-checkpoint-postgres==3.0.5、"
            "psycopg[binary]>=3.2、psycopg-pool>=3.2。当前环境缺少："
            f"{exc.name}。"
        ) from exc

    pool: Any = None
    try:
        if _injected_pool is not None:
            if _injected_pool_loop is not loop:
                raise RuntimeError(
                    "注入的 postgres pool 绑定在其它 event loop 上；跨 loop 复用应用池被禁止"
                    "（FastAPI reload/重启会重建 lifespan）。"
                )
            pool = _injected_pool
        else:
            pool = AsyncConnectionPool(
                conninfo=dsn,
                open=False,
                min_size=1,
                max_size=5,
                kwargs={"autocommit": True},
                timeout=10,
            )
            await pool.open()
        saver = AsyncPostgresSaver(conn=pool)
        await saver.setup()  # 官方版本化 MIGRATIONS（checkpoint 表族自管）
    except Exception as exc:  # noqa: BLE001 - 统一转 fail-fast RuntimeError
        if pool is not None and _injected_pool is None:
            try:
                await pool.close()
            except Exception:  # noqa: BLE001
                pass
        raise RuntimeError(
            f"无法初始化 PostgreSQL checkpointer（{CHECKPOINT_DSN_ENV} 不可用）：{exc}。"
            "拒绝静默回退到 sqlite。"
        ) from exc
    held = _HeldSaver(saver, loop, "postgres", dsn)
    if _injected_pool is None:
        held.pool = pool
    return held


# ---------------------------------------------------------------------------
# 统一入口 / 生命周期
# ---------------------------------------------------------------------------
async def bind_postgres_pool(pool: Any) -> None:
    """FastAPI lifespan 调用：注入应用持有的 AsyncConnectionPool（只允许一次）。"""
    global _injected_pool, _injected_pool_loop
    loop = _current_loop()
    if _injected_pool is not None and _injected_pool is not pool:
        raise RuntimeError(
            "postgres pool 已绑定，禁止重复 bind（先 shutdown 再 bind）。"
        )
    _injected_pool = pool
    _injected_pool_loop = loop


async def _close_holder_resources(held: _HeldSaver) -> None:
    """关闭一个持有对象占用的资源（尽力而为、幂等、容忍跨 loop 清理）。"""
    # aiosqlite 经独立 worker 线程执行，close 不绑定调用方 loop
    if held.conn is not None:
        try:
            await held.conn.close()
        except Exception:  # noqa: BLE001 - 关闭失败不阻断后续
            pass
    if held.pool is not None:
        try:
            await held.pool.close()
        except Exception:  # noqa: BLE001 - psycopg pool 跨 loop close 失败可忽略（进程退出兜底）
            pass


def _resource_key(cfg: CheckpointConfig) -> Optional[str]:
    if cfg.backend == "sqlite":
        return str(cfg.db_path)  # type: ignore[arg-type]
    return cfg.dsn


async def get_checkpointer() -> Any:
    """返回当前后端对应的 checkpointer（loop 内创建；同 loop 复用，跨 loop 重建）。

    必须在 running event loop 内调用。生产/测试规则：
    - 同 loop + 同资源键 → 复用；
    - 同 loop + 不同资源键（测试切换 DB 路径）→ 关闭旧实例并重建；
    - 跨 loop → 重建新实例并尽力关闭旧实例（sqlite / 自建 pool 场景）；
      若 postgres pool 由应用层注入且绑定在其它 loop → RuntimeError。
    """
    global _holder
    loop = _current_loop()
    cfg = parse_checkpoint_config()
    key = _resource_key(cfg)

    async with _holder_lock:
        reuse = False
        if _holder is not None:
            if (
                _holder.loop is loop
                and _holder.backend == cfg.backend
                and _holder.key == key
            ):
                reuse = True
            else:
                # 跨 loop 或键变化：关闭旧实例（注入池跨 loop 由 _open_postgres_saver 拦截）
                await _close_holder_resources(_holder)
                _holder = None
        if reuse:
            return _holder.saver

        if cfg.backend == "sqlite":
            held = await _open_sqlite_saver(cfg.db_path, loop)  # type: ignore[arg-type]
        else:
            held = await _open_postgres_saver(cfg.dsn or "", loop)  # type: ignore[arg-type]
        _holder = held
        return held.saver


async def close_checkpointer() -> None:
    """关闭本模块持有的 sqlite 连接 / 自建 postgres pool（幂等）。

    设计上允许在任意 loop 调用（清理尽力而为）；FastAPI shutdown 在同一 loop
    内调用时行为确定。
    """
    global _holder
    async with _holder_lock:
        if _holder is not None:
            await _close_holder_resources(_holder)
            _holder = None


async def init_checkpoint_lifespan() -> None:
    """FastAPI lifespan 启动：postgres 后端时创建并注入 AsyncConnectionPool。

    sqlite 后端无需在此初始化（prewarm 时的首个 get_checkpointer 创建，保持启动期
    fail-fast——server 层 prewarm 见 app/api/server.py）。
    """
    cfg = parse_checkpoint_config()
    if cfg.backend == "postgres":
        try:
            from psycopg_pool import AsyncConnectionPool
        except ImportError as exc:  # pragma: no cover - 依赖缺失路径
            raise RuntimeError(
                "postgres 后端缺少依赖：psycopg-pool/psycopg[binary]。"
            ) from exc
        pool = AsyncConnectionPool(
            conninfo=cfg.dsn,
            open=False,
            min_size=1,
            max_size=5,
            kwargs={"autocommit": True},
            timeout=10,
        )
        await pool.open()
        await bind_postgres_pool(pool)


async def shutdown_checkpoint_lifespan() -> None:
    """FastAPI lifespan 关闭：释放本模块资源并关闭注入的 pool（幂等）。"""
    global _injected_pool, _injected_pool_loop
    try:
        await close_checkpointer()
    finally:
        pool = _injected_pool
        _injected_pool = None
        _injected_pool_loop = None
        if pool is not None:
            try:
                await pool.close()
            except Exception:  # noqa: BLE001 - 幂等关闭
                pass


@asynccontextmanager
async def managed_postgres_checkpointer(
    dsn: Optional[str] = None,
) -> AsyncIterator[Any]:
    """脚本/测试用上下文管理器：自建 pool + AsyncPostgresSaver，退出时释放。

    用法::

        async with managed_postgres_checkpointer(dsn) as saver:
            graph = build().compile(checkpointer=saver)
            await graph.ainvoke(...)

    独立于进程级单例（不经 get_checkpointer），适合需要精确控制 pool 生命周期的
    恢复/回放测试。
    """
    loop = _current_loop()
    cfg = parse_checkpoint_config()
    effective_dsn = dsn or (cfg.dsn if cfg.backend == "postgres" else None)
    if not effective_dsn:
        raise RuntimeError(
            "managed_postgres_checkpointer 需要 AGENT_CHECKPOINT_DSN 或 dsn 参数。"
        )
    held = await _open_postgres_saver(effective_dsn, loop)
    try:
        yield held.saver
    finally:
        await _close_holder_resources(held)
