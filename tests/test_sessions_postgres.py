"""Multi-Session — sessions store（PostgreSQL 镜像）集成测试。

门控：AGENT_CHECKPOINT_DSN_TEST（governance PG 独立测试库；sessions 与 governance_tasks 同库，
不触碰 checkpoint 官方表族）。覆盖 migration（0002 fresh/idempotent）、CRUD、list 过滤、
set_status、task_summary（JSONB/TIMESTAMPTZ 语义与双后端一致）。

纪律：AGENT_CHECKPOINT_DSN_TEST 只允许指向独立测试库（TESTING.md §1 PG 测试环境纪律）。
"""

import os
import uuid

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

from app.runtime.governance import migrations as gov_migrations  # noqa: E402
from app.runtime.governance import store as gov_store  # noqa: E402
from app.runtime.governance.models import TaskRecord  # noqa: E402
from app.session import store as sess_store  # noqa: E402
from app.session.models import Session, SessionStatus  # noqa: E402


@needs_pg
class TestSessionsPostgres:
    def _dsn(self) -> str:
        return os.getenv(PG_TEST_DSN_ENV)

    def _bind(self):
        store = gov_store._GovernancePostgresStore(self._dsn())
        gov_migrations.ensure_schema(store)
        return store

    def _sess(self, session_id: str, *, status: str = "active") -> Session:
        return Session(
            session_id=session_id,
            title=f"t-{session_id}",
            description="desc",
            status=status,
            created_at="2026-10-03T00:00:00+00:00",
            updated_at="2026-10-03T00:00:00+00:00",
        )

    def test_schema_and_migration_idempotent(self):
        store = self._bind()
        try:
            assert store.dialect == "postgres"
            assert sorted(gov_migrations.applied_migration_versions(store)) == [
                "0001",
                "0002",
            ]
            gov_migrations.ensure_schema(store)  # 幂等
            assert sorted(gov_migrations.applied_migration_versions(store)) == [
                "0001",
                "0002",
            ]
        finally:
            store.close()

    def test_crud_and_list_filter(self):
        store = self._bind()
        try:
            active = self._sess("pg-active", status=SessionStatus.ACTIVE.value)
            archived = self._sess("pg-archived", status=SessionStatus.ARCHIVED.value)
            sess_store.create_session(store, active)
            sess_store.create_session(store, archived)

            got = sess_store.get_session(store, "pg-active")
            assert got is not None and got.title == "t-pg-active"
            assert got.description == "desc"
            assert got.created_at == "2026-10-03T00:00:00+00:00"

            sessions, total = sess_store.list_sessions(store)
            assert total == 1 and sessions[0].session_id == "pg-active"
            all_sessions, all_total = sess_store.list_sessions(
                store, include_archived=True
            )
            assert all_total == 2

            rc = sess_store.set_status(
                store,
                "pg-active",
                SessionStatus.ARCHIVED.value,
                "2026-10-03T00:00:01+00:00",
            )
            assert rc == 1
            assert sess_store.get_session(store, "pg-active").is_archived
        finally:
            store.close()

    def test_task_summary_aggregation(self):
        store = self._bind()
        try:
            sess_store.create_session(store, self._sess("pg-s1"))
            sess_store.create_session(store, self._sess("pg-s2"))
            gov_store.insert_task(
                store,
                TaskRecord(
                    task_id=uuid.uuid4().hex,
                    thread_id="pg-s1",
                    owner_instance="test",
                    status="running",
                    run_id="r1",
                    created_at="2026-10-03T00:00:02+00:00",
                ),
            )
            gov_store.insert_task(
                store,
                TaskRecord(
                    task_id=uuid.uuid4().hex,
                    thread_id="pg-s1",
                    owner_instance="test",
                    status="completed",
                    run_id="r2",
                    created_at="2026-10-03T00:00:01+00:00",
                ),
            )
            summary = sess_store.task_summary(store, ["pg-s1", "pg-s2"])
            assert summary["pg-s1"]["task_count"] == 2
            assert summary["pg-s1"]["running_tasks"] == 1
            assert summary["pg-s1"]["latest_task"]["run_id"] == "r1"
            assert summary["pg-s2"]["task_count"] == 0
            assert summary["pg-s2"]["latest_task"] is None
        finally:
            store.close()
