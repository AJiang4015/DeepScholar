"""F8 governance store（Plan Rev2 §4/§14；独立于 checkpoint/research 表族）。

- sqlite 缺省（GOVERNANCE_DB，默认 app/runtime/governance.sqlite）；
- postgres 复用 checkpoint DSN 的**独立 governance 表**（GOVERNANCE_DSN 优先，
  AGENT_CHECKPOINT_DSN 兜底；不触碰 checkpoint 官方表族）；
- 同一套 %s 参数化 SQL 跨后端复用；JSON 列：sqlite=TEXT，postgres=JSONB；
- 写入一律 `with store.transaction() as tx`；只读用 store.execute；
- 无 migration rollback；clear/drop 只允许作为测试 fixture/reset（由测试自行执行）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

logger = logging.getLogger("deepsearch.runtime.governance.store")

try:  # 可选驱动：postgres 后端才需要
    from psycopg.types.json import Jsonb

    _HAS_PSYCOPG = True
except Exception:  # pragma: no cover
    _HAS_PSYCOPG = False

#: 运行时（get_task 反序列化）需要 TaskRecord；models 不依赖 store，无环。
from app.runtime.governance.models import TaskRecord  # noqa: E402

GOVERNANCE_BACKEND_ENV = "GOVERNANCE_BACKEND"
GOVERNANCE_DB_ENV = "GOVERNANCE_DB"
GOVERNANCE_DSN_ENV = "GOVERNANCE_DSN"
CHECKPOINT_DSN_ENV = "AGENT_CHECKPOINT_DSN"

#: 默认 DB 路径：app/runtime/governance.sqlite（gitignore 已忽略 *.sqlite 族）
DEFAULT_GOVERNANCE_DB_PATH = Path(__file__).resolve().parent / "governance.sqlite"

ALLOWED_BACKENDS = ("sqlite", "postgres")


class GovernanceStoreError(RuntimeError):
    pass


def json_param(value: Any, dialect: str) -> Any:
    """JSON 列参数编码：sqlite 存 TEXT（json.dumps），postgres 用 Jsonb 适配。"""
    payload = value if value is not None else {}
    if dialect == "postgres":
        return Jsonb(payload)
    return json.dumps(payload, ensure_ascii=False)


def _sqlite_placeholder(sql: str) -> str:
    """统一 SQL 使用 psycopg 风格 %s；sqlite 驱动要求 ? ——sqlite 边界转换。"""
    return re.sub(r"%s", "?", sql)


def _rows_from_sqlite_cursor(cur: Any) -> list[dict]:
    if cur.description is None:
        return []
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


class _SqliteTx:
    def __init__(self, store: "_GovernanceSqliteStore") -> None:
        self._store = store

    def execute(self, sql: str, params: tuple = ()) -> list[dict]:
        cur = self._store._conn.execute(_sqlite_placeholder(sql), params)
        return _rows_from_sqlite_cursor(cur)

    def execute_rc(self, sql: str, params: tuple = ()) -> int:
        """执行写语句并返回受影响行数（terminal CAS 的 rowcount 证据）。"""
        cur = self._store._conn.execute(_sqlite_placeholder(sql), params)
        return cur.rowcount or 0


class _GovernanceSqliteStore:
    """sqlite 后端：进程级单连接 + RLock + WAL（镜像 research store 纪律）。"""

    dialect = "sqlite"

    def __init__(self, db_path: str) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        self._lock = threading.RLock()

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self._lock:
            try:
                yield _SqliteTx(self)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def execute(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(_sqlite_placeholder(sql), params)
            return _rows_from_sqlite_cursor(cur)

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
            self._conn = None


class _PgTx:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def execute(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            if cur.description is None:
                return []
            return list(cur.fetchall())

    def execute_rc(self, sql: str, params: tuple = ()) -> int:
        """执行写语句并返回受影响行数（psycopg cursor.rowcount）。"""
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount or 0


class _GovernancePostgresStore:
    """postgres 后端：每次 transaction/查询一个短连接（复用 checkpoint DSN 的独立表）。"""

    dialect = "postgres"

    def __init__(self, dsn: str) -> None:
        if not _HAS_PSYCOPG:
            raise GovernanceStoreError(
                "postgres governance store 需要 psycopg（AGENT_CHECKPOINT_DSN 场景）"
            )
        import psycopg  # noqa: PLC0415

        self._psycopg = psycopg
        self._dsn = dsn

    def _conn(self):
        from psycopg.rows import dict_row  # noqa: PLC0415

        return self._psycopg.connect(self._dsn, row_factory=dict_row)

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        conn = self._conn()
        try:
            yield _PgTx(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def execute(self, sql: str, params: tuple = ()) -> list[dict]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                if cur.description is None:
                    return []
                return list(cur.fetchall())
        finally:
            conn.close()

    def close(self) -> None:
        pass


def parse_governance_config(env: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """解析 governance store 配置（env 显式注入优先，缺省 os.environ）。

    - GOVERNANCE_BACKEND ∈ {sqlite, postgres}，缺省 sqlite；
    - postgres：GOVERNANCE_DSN 优先，AGENT_CHECKPOINT_DSN 兜底（独立 governance 表）；
      两者都缺 → 抛错（fail-closed，不静默回退）；
    - sqlite：GOVERNANCE_DB 覆盖，否则默认 app/runtime/governance.sqlite。
    """
    env = os.environ if env is None else env
    raw = (env.get(GOVERNANCE_BACKEND_ENV) or "sqlite").strip().lower()
    if raw not in ALLOWED_BACKENDS:
        raise GovernanceStoreError(
            f"{GOVERNANCE_BACKEND_ENV} 取值非法：{raw!r}；允许值：{list(ALLOWED_BACKENDS)}"
        )
    if raw == "postgres":
        dsn = (env.get(GOVERNANCE_DSN_ENV) or env.get(CHECKPOINT_DSN_ENV) or "").strip()
        if not dsn:
            raise GovernanceStoreError(
                f"governance backend=postgres 时必须设置 {GOVERNANCE_DSN_ENV} "
                f"或 {CHECKPOINT_DSN_ENV}"
            )
        return {"backend": "postgres", "dsn": dsn}
    db_path = (env.get(GOVERNANCE_DB_ENV) or "").strip()
    if not db_path:
        db_path = str(DEFAULT_GOVERNANCE_DB_PATH)
    return {"backend": "sqlite", "db_path": db_path}


def _open_store(cfg: dict[str, Any]) -> Any:
    from app.runtime.governance import migrations as gov_migrations  # noqa: PLC0415

    if cfg["backend"] == "postgres":
        store: Any = _GovernancePostgresStore(cfg["dsn"])
    else:
        store = _GovernanceSqliteStore(cfg["db_path"])
    gov_migrations.ensure_schema(store)
    return store


_instance: Any = None
_disabled_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# TaskRecord / terminal 最小 store API（Step 2；controller 经此完成持久化）
# ---------------------------------------------------------------------------
def insert_task(store: Any, task: "TaskRecord") -> None:
    """插入 running TaskRecord（created_at 必填；重复 task_id 抛错）。"""
    with store.transaction() as tx:
        tx.execute(
            "INSERT INTO governance_tasks (task_id, thread_id, run_id, status, "
            "terminal_reason, error_kind, error, policy_snapshot, effective_limits, "
            "counters_snapshot, superseded_by, parent_session, "
            "underlying_linger_observed, owner_instance, version, created_at, "
            "started_at, finished_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                task.task_id,
                task.thread_id,
                task.run_id,
                task.status,
                task.terminal_reason,
                task.error_kind,
                task.error,
                json_param(task.policy_snapshot, store.dialect),
                json_param(task.effective_limits, store.dialect),
                json_param(task.counters_snapshot, store.dialect)
                if task.counters_snapshot is not None
                else None,
                task.superseded_by,
                task.parent_session,
                task.underlying_linger_observed,
                task.owner_instance,
                task.version,
                task.created_at,
                task.started_at,
                task.finished_at,
            ),
        )


def get_task(store: Any, task_id: str) -> Optional["TaskRecord"]:
    rows = store.execute("SELECT * FROM governance_tasks WHERE task_id=%s", (task_id,))
    if not rows:
        return None
    return TaskRecord.from_row(rows[0])


def list_tasks(
    store: Any, thread_id: Optional[str] = None, limit: int = 50
) -> list["TaskRecord"]:
    """task 查询（created_at 降序；thread 可选过滤）。仅供只读查询。"""
    if thread_id:
        sql = (
            "SELECT * FROM governance_tasks WHERE thread_id=%s "
            "ORDER BY created_at DESC, task_id LIMIT %s"
        )
        rows = store.execute(sql, (thread_id, limit))
    else:
        sql = (
            "SELECT * FROM governance_tasks ORDER BY created_at DESC, task_id LIMIT %s"
        )
        rows = store.execute(sql, (limit,))
    return [TaskRecord.from_row(r) for r in rows]


def start_task(store: Any, task_id: str, started_at: str) -> bool:
    """running → 记 started_at（已非 running / 已有 started_at / 不存在 → False）。"""
    with store.transaction() as tx:
        rc = tx.execute_rc(
            "UPDATE governance_tasks SET started_at=%s "
            "WHERE task_id=%s AND status=%s AND started_at IS NULL",
            (started_at, task_id, "running"),
        )
    return rc == 1


def update_run_id(store: Any, task_id: str, run_id: str) -> bool:
    """F1：仅在 running 且 run_id 未绑定时回填 run_id（不触碰 version/terminal CAS 语义）。"""
    with store.transaction() as tx:
        rc = tx.execute_rc(
            "UPDATE governance_tasks SET run_id=%s "
            "WHERE task_id=%s AND status=%s AND run_id IS NULL",
            (run_id, task_id, "running"),
        )
    return rc == 1


def terminal_update(
    store: Any,
    task_id: str,
    *,
    expected_version: int,
    status: str,
    terminal_reason: Optional[str],
    error_kind: Optional[str],
    error: Optional[str],
    counters_snapshot: Optional[dict],
    superseded_by: Optional[str],
    underlying_linger_observed: Optional[bool],
    finished_at: str,
) -> bool:
    """Terminal 乐观写（唯一写入口经 controller 串行化后调用）。

    UPDATE … SET status=?, …, version=version+1
    WHERE task_id=? AND status='running' AND version=?
    → 返回 rowcount==1（客观 durable transition 证据；恰一次）。
    """
    with store.transaction() as tx:
        rc = tx.execute_rc(
            "UPDATE governance_tasks SET status=%s, terminal_reason=%s, "
            "error_kind=%s, error=%s, counters_snapshot=%s, superseded_by=%s, "
            "underlying_linger_observed=%s, finished_at=%s, version=version+1 "
            "WHERE task_id=%s AND status='running' AND version=%s",
            (
                status,
                terminal_reason,
                error_kind,
                error,
                json_param(counters_snapshot, store.dialect)
                if counters_snapshot is not None
                else None,
                superseded_by,
                underlying_linger_observed,
                finished_at,
                task_id,
                expected_version,
            ),
        )
    return rc == 1


def reset_store() -> None:
    """测试用：关闭并清空进程级 store 单例。"""
    global _instance, _disabled_reason
    if _instance is not None:
        try:
            _instance.close()
        except Exception:  # noqa: BLE001
            pass
    _instance = None
    _disabled_reason = None


def get_store(config: Optional[dict[str, Any]] = None):
    """返回可用 governance store；配置非法/初始化失败 → None + 记录一次错误。

    调用方（controller/server）必须容忍 None；submit 场景由 controller 决定 fail-closed。
    """
    global _instance, _disabled_reason
    if _instance is not None:
        return _instance
    if _disabled_reason is not None:
        return None
    try:
        cfg = config if config is not None else parse_governance_config()
        _instance = _open_store(cfg)
        return _instance
    except Exception as exc:  # noqa: BLE001
        _disabled_reason = str(exc)
        logger.error("Governance store 初始化失败：%s", exc)
        return None
