"""Research 表族轻量 migration runner（F1，零新增依赖）。

- 只管理 research_* 表族 + schema_migrations；checkpoint 表族归官方 saver.setup()；
- 按 store.dialect 选择 db/migrations/{version}_*.{dialect}.sql；
- 幂等：已执行的版本记录于 schema_migrations，重跑跳过；
- 未来版本追加 = 新增 NNNN_research_*.{dialect}.sql 文件（如 0002_…）。
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "db" / "migrations"
_VERSION_RE = re.compile(r"^(\d{4})_")


def _load_versions(dialect: str) -> list[tuple[int, Path]]:
    out = []
    for f in _MIGRATIONS_DIR.glob(f"*.{dialect}.sql"):
        m = _VERSION_RE.match(f.name)
        if m:
            out.append((int(m.group(1)), f))
    out.sort(key=lambda pair: pair[0])
    return out


def _applied_versions(store) -> set[int]:
    try:
        rows = store.execute("SELECT version FROM schema_migrations")
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
    stmts = [s.strip() for s in sql.split(";")]
    return [s for s in stmts if s]


def ensure_schema(store) -> None:
    """确保 schema_migrations + 最新 research schema（幂等）。"""
    # 先建迁移表（通用 DDL，双后端一致）
    with store.transaction() as tx:
        tx.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
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
                "INSERT INTO schema_migrations (version, applied_at) VALUES (%s, %s)",
                (
                    f"{version:04d}",
                    datetime.datetime.now(datetime.timezone.utc).isoformat(),
                ),
            )


def applied_migration_versions(store) -> list[str]:
    """返回已应用版本列表（测试/报告用）。"""
    rows = store.execute("SELECT version FROM schema_migrations ORDER BY version")
    return [str(row["version"]) for row in rows]
