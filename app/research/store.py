"""Research Artifact Store（F1）：sqlite（缺省，本地开发/测试）与 postgres 双后端。

原则：
- Research 数据面与 Agent 主链路解耦：本模块抛错只应被 registry 的 fail-open 捕获；
- 同步实现（工具在 executor 线程执行；run 钩子量小），PG 用短连接 + 单事务；
- 同一套 %s 参数化 SQL 跨后端复用；JSON 载荷：sqlite=TEXT，postgres=JSONB
  （psycopg3 以 Jsonb 包装 dict 适配 JSONB 列）；
- 写入一律在 `with store.transaction() as tx:` 内，经 tx.execute 执行（双后端原子性一致）；
  只读查询用 store.execute（select）。
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

logger = logging.getLogger("deepsearch.research.store")

try:  # 可选驱动：postgres 后端才需要
    from psycopg.types.json import Jsonb

    _HAS_PSYCOPG = True
except Exception:  # pragma: no cover - 依赖缺失路径
    _HAS_PSYCOPG = False


class ResearchStoreError(Exception):
    """Research store 操作失败（registry 层捕获并 fail-open）。"""


def json_param(value: Any, dialect: str) -> Any:
    """JSON 列参数编码：sqlite 存 TEXT（json.dumps），postgres 用 Jsonb 适配。"""
    payload = value if value is not None else {}
    if dialect == "postgres":
        return Jsonb(payload)
    return json.dumps(payload, ensure_ascii=False)


def _sqlite_placeholder(sql: str) -> str:
    """统一 SQL 使用 psycopg 风格 %s；sqlite 驱动要求 ? ——在 sqlite 边界转换。"""
    return re.sub(r"%s", "?", sql)


def _rows_from_sqlite_cursor(cur: Any) -> list[dict]:
    if cur.description is None:
        return []
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


class _SqliteTx:
    def __init__(self, store: "_SqliteStore") -> None:
        self._store = store

    def execute(self, sql: str, params: tuple = ()) -> list[dict]:
        cur = self._store._conn.execute(_sqlite_placeholder(sql), params)
        return _rows_from_sqlite_cursor(cur)


class _SqliteStore:
    """sqlite 后端：进程级单连接 + RLock + WAL。"""

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
        """只读查询（select）；写操作请用 transaction()。"""
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


class _PostgresStore:
    """postgres 后端：每次 transaction/查询一个短连接（v1 不引入 pool）。"""

    dialect = "postgres"

    def __init__(self, dsn: str) -> None:
        if not _HAS_PSYCOPG:
            raise ResearchStoreError(
                "postgres store 需要 psycopg[binary] 依赖（RESEARCH_DSN 场景）"
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


# ---------------------------------------------------------------------------
# 进程级 store 工厂（配置失效/初始化失败 → disabled + 一次清晰错误日志）
# ---------------------------------------------------------------------------
_instance: Any = None
_disabled_reason: Optional[str] = None


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


def get_store(config=None):
    """返回可用 store；不可用（disabled/配置错/初始化失败）返回 None 并记录一次错误。

    调用方（registry/tools/run 钩子）必须容忍 None（F1 fail-open contract）。
    """
    global _instance, _disabled_reason
    if _instance is not None:
        return _instance
    if _disabled_reason is not None:
        return None
    try:
        from app.research.config import parse_research_config
        from app.research import migrations

        cfg = config if config is not None else parse_research_config()
        if cfg.store == "disabled":
            _disabled_reason = "RESEARCH_STORE=disabled"
            return None
        if cfg.store == "sqlite":
            store: Any = _SqliteStore(str(cfg.db_path))
        else:
            store = _PostgresStore(cfg.dsn or "")
        migrations.ensure_schema(store)
        _instance = store
        return _instance
    except Exception as exc:  # noqa: BLE001 - F1 fail-open contract
        _disabled_reason = str(exc)
        logger.error("Research store 初始化失败，research plane disabled：%s", exc)
        return None
