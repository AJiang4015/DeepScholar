"""P2-2 — Runtime Health Plane（PostgreSQL 镜像）门控集成测试。

门控：`AGENT_CHECKPOINT_DSN_TEST`（governance PG 独立测试库；不触碰 checkpoint 官方表族）。
覆盖：migration 0003（fresh/idempotent + 表/索引存在）、health_store UPSERT/读取/清理、
TIMESTAMPTZ 读回归一（`parse_ts`）与既有 governance lifecycle 不冲突（心跳只写 health plane）。

纪律：无有效 DSN → 整类 skip（**不伪造 PG PASS**）；DSN 只允许指向独立测试库（TESTING.md §1）。
"""

import os
import uuid
from datetime import datetime, timezone

import pytest

PG_TEST_DSN_ENV = "AGENT_CHECKPOINT_DSN_TEST"

try:
    import psycopg  # noqa: F401

    HAS_PSYCOPG = True
except Exception:  # pragma: no cover
    HAS_PSYCOPG = False

needs_pg = pytest.mark.skipif(
    not (HAS_PSYCOPG and os.getenv(PG_TEST_DSN_ENV)),
    reason=f"需要 psycopg 与 {PG_TEST_DSN_ENV}（独立测试库）",
)

from app.runtime.governance import health_store as hstore  # noqa: E402
from app.runtime.governance import migrations as gov_migrations  # noqa: E402
from app.runtime.governance import store as gov_store  # noqa: E402
from app.runtime.governance.models import TaskRecord  # noqa: E402


@needs_pg
class TestRuntimeHealthPostgres:
    def _dsn(self) -> str:
        return os.getenv(PG_TEST_DSN_ENV)

    def _store(self):
        store = gov_store._GovernancePostgresStore(self._dsn())
        gov_migrations.ensure_schema(store)
        return store

    def _unique_task_id(self) -> str:
        return f"p22pg-{uuid.uuid4().hex}"

    def _iso(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def test_migration_0003_applied_and_schema(self):
        store = self._store()
        try:
            assert store.dialect == "postgres"
            assert sorted(gov_migrations.applied_migration_versions(store)) == [
                "0001",
                "0002",
                "0003",
            ]
            gov_migrations.ensure_schema(store)  # 幂等
            cols = {
                r["column_name"]: r["data_type"]
                for r in store.execute(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_name='governance_runtime_health'"
                )
            }
            assert cols["last_heartbeat_at"] == "timestamp with time zone"
            assert cols["created_at"] == "timestamp with time zone"
            assert cols["updated_at"] == "timestamp with time zone"
            assert cols["beat_count"] == "integer"
            indexes = {
                r["indexname"]
                for r in store.execute(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE tablename='governance_runtime_health'"
                )
            }
            assert "idx_runtime_health_heartbeat" in indexes
        finally:
            store.close()

    def test_heartbeat_upsert_and_reads(self):
        store = self._store()
        task_id = self._unique_task_id()
        try:
            assert (
                hstore.upsert_heartbeat(
                    store,
                    task_id=task_id,
                    thread_id="th-pg",
                    run_id="run-pg",
                    owner_instance="pg-host-1",
                    now_iso=self._iso(),
                )
                is True
            )
            assert (
                hstore.upsert_heartbeat(
                    store,
                    task_id=task_id,
                    thread_id="th-pg",
                    run_id="run-pg",
                    owner_instance="pg-host-1",
                    now_iso=self._iso(),
                )
                is True
            )
            row = hstore.get_health(store, task_id)
            assert row["beat_count"] == 2
            assert isinstance(row["last_heartbeat_at"], datetime)  # TIMESTAMPTZ 归一
            assert row["last_heartbeat_at"].tzinfo is not None
            assert set(hstore.list_health_for(store, [task_id])) == {task_id}
        finally:
            hstore.delete_health_rows(store, [task_id])
            store.close()

    def test_heartbeat_does_not_touch_lifecycle_row(self):
        store = self._store()
        task_id = self._unique_task_id()
        gov_store.insert_task(
            store,
            TaskRecord(
                task_id=task_id,
                thread_id="th-pg",
                owner_instance="pg-host-1",
                created_at=self._iso(),
            ),
        )
        try:
            hstore.upsert_heartbeat(
                store,
                task_id=task_id,
                thread_id="th-pg",
                run_id="run-pg",
                owner_instance="pg-host-1",
                now_iso=self._iso(),
            )
            record = gov_store.get_task(store, task_id)
            assert record.status == "running"  # LA-1：health 路径不写 lifecycle 字段
            assert record.version == 0
            assert record.terminal_reason is None
        finally:
            with store.transaction() as tx:
                tx.execute("DELETE FROM governance_tasks WHERE task_id=%s", (task_id,))
            hstore.delete_health_rows(store, [task_id])
            store.close()

    def test_cleanup_candidates_and_delete(self):
        store = self._store()
        task_id = self._unique_task_id()
        try:
            hstore.upsert_heartbeat(
                store,
                task_id=task_id,
                thread_id="th-pg",
                run_id=None,
                owner_instance="pg-host-1",
                now_iso=self._iso(),
            )
            # 无对应 governance_tasks 行 ⇒ 孤儿 health 行是清理候选
            candidates = {
                c["task_id"] for c in hstore.list_cleanup_candidates(store, limit=200)
            }
            assert task_id in candidates
            assert hstore.delete_health_rows(store, [task_id]) == 1
            assert hstore.get_health(store, task_id) is None
        finally:
            store.close()
