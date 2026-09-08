"""F8 governance migration runner（Plan Rev2 §4；独立于 research/checkpoint 表族）。

- governance 表族自管：版本表 `governance_schema_migrations`；
- versioned migration：`db/governance_migrations/NNNN_*.{dialect}.sql`；
- idempotent apply（已应用版本跳过）；无 migration rollback（测试环境清库属 fixture/reset）；
- SQLite TEXT-JSON / PostgreSQL JSONB 同语义。
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Any

_MIGRATIONS_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "db"
    / "governance_migrations"
)
_VERSION_RE = re.compile(r"^(\d{4})_")


def _load_versions(dialect: str) -> list[tuple[int, Path]]:
    out = []
    for f in _MIGRATIONS_DIR.glob(f"*.{dialect}.sql"):
        m = _VERSION_RE.match(f.name)
        if m:
            out.append((int(m.group(1)), f))
    out.sort(key=lambda pair: pair[0])
    return out


def _applied_versions(store: Any) -> set[int]:
    try:
        rows = store.execute("SELECT version FROM governance_schema_migrations")
    except Exception:  # 表尚不存在 → 视为空
        return set()
    versions = set()
    for row in rows:
        try:
            versions.add(int(str(row["version"])))
        except (TypeError, ValueError):
            continue
    return versions


def _split_statements(sql: str) -> list[str]:
    return [s.strip() for s in sql.split(";") if s.strip()]


def ensure_schema(store: Any) -> None:
    """确保 governance 版本表 + 最新 governance schema（幂等）。"""
    with store.transaction() as tx:
        tx.execute(
            "CREATE TABLE IF NOT EXISTS governance_schema_migrations ("
            "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
    dialect = store.dialect
    applied = _applied_versions(store)
    for version, path in _load_versions(dialect):
        if version in applied:
            continue
        sql = path.read_text(encoding="utf-8")
        statements = _split_statements(sql)
        with store.transaction() as tx:
            for stmt in statements:
                tx.execute(stmt)
            tx.execute(
                "INSERT INTO governance_schema_migrations (version, applied_at) "
                "VALUES (%s, %s)",
                (
                    f"{version:04d}",
                    datetime.datetime.now(datetime.timezone.utc).isoformat(),
                ),
            )


def applied_migration_versions(store: Any) -> list[str]:
    try:
        rows = store.execute(
            "SELECT version FROM governance_schema_migrations ORDER BY version"
        )
    except Exception:  # 表尚不存在（fresh DB）→ 空
        return []
    return [str(row["version"]) for row in rows]
