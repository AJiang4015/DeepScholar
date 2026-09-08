"""Research store（sqlite）与 migration runner 测试（F1 §4/§18 + F2 0002）。"""

import sqlite3

import pytest

from app.research import migrations, store as rstore
from app.research.config import RESEARCH_DB_ENV


def _table_names(db_path: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


class TestStoreSchema:
    def test_get_store_creates_schema(self, research_sqlite):
        store = rstore.get_store()
        assert store is not None
        assert store.dialect == "sqlite"
        db_path = store._conn.execute("PRAGMA database_list").fetchone()[2]  # noqa: SLF001
        assert db_path.endswith("research.sqlite")

        tables = _table_names(db_path)
        expected = {
            "schema_migrations",
            "research_runs",
            "sub_questions",
            "search_queries",
            "sources",
            "evidences",
            # F2 (0002)
            "claims",
            "claim_evidences",
            "citations",
        }
        assert expected <= tables

    def test_migration_applied_once_idempotent(self, research_sqlite):
        store = rstore.get_store()
        assert sorted(migrations.applied_migration_versions(store)) == [
            "0001",
            "0002",
            "0003",
            "0004",
            "0005",
            "0006",
        ]
        # 幂等：重复 ensure_schema 不重复执行、不报错
        migrations.ensure_schema(store)
        assert sorted(migrations.applied_migration_versions(store)) == [
            "0001",
            "0002",
            "0003",
            "0004",
            "0005",
            "0006",
        ]

    def test_fk_enforced(self, research_sqlite):
        store = rstore.get_store()
        # sources 引用不存在的 run → FK 拒绝
        with pytest.raises(Exception):
            with store.transaction() as tx:
                tx.execute(
                    "INSERT INTO sources (source_id, run_id, query_id, source_type, "
                    "title, canonical_url, locator, canonical_key, fetched_at, agent, "
                    "metadata) VALUES ('s', 'no-run', 'no-q', 'web', 't', NULL, 'l', "
                    "'k', '2026-01-01T00:00:00+00:00', 'agent', '{}')"
                )

    def test_disabled_store_returns_none(self, research_tmp, monkeypatch):
        monkeypatch.setenv("RESEARCH_STORE", "disabled")
        rstore.reset_store()
        assert rstore.get_store() is None

    def test_invalid_config_disables_store(self, research_tmp, monkeypatch):
        monkeypatch.setenv("RESEARCH_STORE", "mongo")
        rstore.reset_store()
        assert rstore.get_store() is None

    def test_default_path_resolution(self, research_tmp, monkeypatch):
        monkeypatch.delenv(RESEARCH_DB_ENV, raising=False)
        from app.research.config import get_research_db_path

        p = get_research_db_path()
        assert p.endswith("research.sqlite")
