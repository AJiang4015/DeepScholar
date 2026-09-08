"""checkpoint 配置解析纯函数测试（backend 抽象）。

覆盖 docs/spec/2026-09-03-postgres-checkpoint-migration.md §4.1：
backend 取值 / DSN 缺失 fail-fast / sqlite 路径解析 / 向后兼容 /
get_checkpoint_db_path 守卫。全部纯函数，无需 DB。
"""

import pytest

from app.runtime import checkpoint as cp


class TestParseBackend:
    def test_default_is_sqlite(self):
        cfg = cp.parse_checkpoint_config({})
        assert cfg.backend == "sqlite"
        assert cfg.db_path == cp.DEFAULT_CHECKPOINT_DB_PATH

    def test_sqlite_explicit(self):
        cfg = cp.parse_checkpoint_config({cp.CHECKPOINT_BACKEND_ENV: "sqlite"})
        assert cfg.backend == "sqlite"

    def test_sqlite_case_insensitive(self):
        cfg = cp.parse_checkpoint_config({cp.CHECKPOINT_BACKEND_ENV: "SQLite"})
        assert cfg.backend == "sqlite"

    def test_invalid_backend_rejected(self):
        with pytest.raises(ValueError, match="AGENT_CHECKPOINT_BACKEND 取值非法"):
            cp.parse_checkpoint_config({cp.CHECKPOINT_BACKEND_ENV: "mongo"})

    def test_postgres_requires_dsn(self):
        with pytest.raises(ValueError, match="AGENT_CHECKPOINT_DSN"):
            cp.parse_checkpoint_config({cp.CHECKPOINT_BACKEND_ENV: "postgres"})

    def test_postgres_with_dsn(self):
        cfg = cp.parse_checkpoint_config(
            {
                cp.CHECKPOINT_BACKEND_ENV: "postgres",
                cp.CHECKPOINT_DSN_ENV: "postgresql://u:p@h:5432/db",
            }
        )
        assert cfg.backend == "postgres"
        assert cfg.dsn == "postgresql://u:p@h:5432/db"


class TestSqlitePathResolution:
    def test_db_env_override_resolves(self):
        cfg = cp.parse_checkpoint_config({cp.CHECKPOINT_DB_ENV: "C:/tmp/x.sqlite"})
        assert cfg.backend == "sqlite"
        assert str(cfg.db_path).lower().replace("/", "\\").endswith("x.sqlite")

    def test_db_env_without_backend_stays_sqlite(self):
        """向后兼容：只设 AGENT_CHECKPOINT_DB 不设 backend → sqlite。"""
        cfg = cp.parse_checkpoint_config({cp.CHECKPOINT_DB_ENV: "D:/a/b.sqlite"})
        assert cfg.backend == "sqlite"
        assert cfg.db_path is not None

    def test_get_checkpoint_db_path_guard_on_postgres(self):
        cfg_env = {
            cp.CHECKPOINT_BACKEND_ENV: "postgres",
            cp.CHECKPOINT_DSN_ENV: "postgresql://u:p@h:5432/db",
        }
        with pytest.raises(RuntimeError, match="仅适用于 sqlite"):
            cp.get_checkpoint_db_path(cfg_env)
