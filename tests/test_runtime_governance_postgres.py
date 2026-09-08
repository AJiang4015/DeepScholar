"""F8 Runtime Governance — PostgreSQL 镜像（Step 1：governance schema/migration）。

门控：AGENT_CHECKPOINT_DSN_TEST（checkpoint PG 独立测试库；governance 复用其 DSN 的**独立表**，
不触碰 checkpoint 官方表族）。覆盖 fresh/upgrade/idempotent migration、schema 字段一致性、
UNIQUE/FK/index、JSONB 语义、models.from_row 双后端归一。
"""

import asyncio
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
from app.runtime.governance.controller import (  # noqa: E402
    GovernanceController,
    GovernanceControllerError,
)
from app.runtime.governance.models import (  # noqa: E402
    GovernanceEvent,
    TaskRecord,
    TaskStatus,
    TerminalReason,
)

TASK_COLUMNS = {
    "task_id",
    "thread_id",
    "run_id",
    "status",
    "terminal_reason",
    "error_kind",
    "error",
    "policy_snapshot",
    "effective_limits",
    "counters_snapshot",
    "superseded_by",
    "parent_session",
    "underlying_linger_observed",
    "owner_instance",
    "version",
    "created_at",
    "started_at",
    "finished_at",
}
EVENT_COLUMNS = {
    "event_id",
    "task_id",
    "thread_id",
    "run_id",
    "seq",
    "event_type",
    "payload",
    "durable",
    "created_at",
}


@needs_pg
class TestGovernanceStorePostgres:
    def _dsn(self):
        return os.getenv(PG_TEST_DSN_ENV)

    def _store(self):
        store = gov_store._GovernancePostgresStore(self._dsn())
        gov_migrations.ensure_schema(store)
        return store

    def test_fresh_migration_and_idempotent(self):
        store = gov_store._GovernancePostgresStore(self._dsn())
        # 先确保空（表族可能残留自其它测试：幂等 ensure 即可，不做 DROP——fixture/reset 由 CI 库清场）
        gov_migrations.ensure_schema(store)
        gov_migrations.ensure_schema(store)
        assert sorted(gov_migrations.applied_migration_versions(store)) == ["0001"]
        store.close()

    def test_schema_columns_types_indexes(self):
        store = self._store()
        try:
            tcols = {
                r["column_name"]
                for r in store.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='governance_tasks'"
                )
            }
            assert TASK_COLUMNS == tcols
            ecols = {
                r["column_name"]
                for r in store.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='governance_events'"
                )
            }
            assert EVENT_COLUMNS == ecols
            # JSONB 列
            rows = store.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name='governance_tasks' AND column_name IN "
                "('policy_snapshot','effective_limits','counters_snapshot')"
            )
            types = {r["column_name"]: r["data_type"] for r in rows}
            assert types["policy_snapshot"] == "jsonb"
            assert types["effective_limits"] == "jsonb"
            assert types["counters_snapshot"] == "jsonb"
            # UNIQUE(task_id, seq) 存在
            idx = store.execute(
                "SELECT indexdef FROM pg_indexes WHERE tablename='governance_events'"
            )
            assert any("(task_id, seq)" in i["indexdef"] for i in idx)
            idx2 = store.execute(
                "SELECT indexdef FROM pg_indexes WHERE tablename='governance_tasks'"
            )
            assert any("idx_governance_tasks_thread" in i["indexdef"] for i in idx2)
        finally:
            store.close()

    def test_fk_and_unique(self):
        store = self._store()
        try:
            task_id = f"t-{uuid.uuid4().hex[:10]}"
            with store.transaction() as tx:
                tx.execute(
                    "INSERT INTO governance_tasks (task_id, thread_id, owner_instance,"
                    " status, created_at) VALUES (%s,%s,%s,%s,%s)",
                    (task_id, "th", "inst", "running", "2026-01-01T00:00:00+00:00"),
                )
            with store.transaction() as tx:
                tx.execute(
                    "INSERT INTO governance_events (event_id, task_id, thread_id, seq,"
                    " event_type, payload, durable, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        f"e-{uuid.uuid4().hex}",
                        task_id,
                        "th",
                        1,
                        "x",
                        gov_store.json_param({}, "postgres"),
                        True,
                        "2026-01-01T00:00:00+00:00",
                    ),
                )
            # duplicate (task_id, seq)
            with pytest.raises(Exception):
                with store.transaction() as tx:
                    tx.execute(
                        "INSERT INTO governance_events (event_id, task_id, thread_id, seq,"
                        " event_type, payload, durable, created_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            f"e2-{uuid.uuid4().hex}",
                            task_id,
                            "th",
                            1,
                            "x",
                            gov_store.json_param({}, "postgres"),
                            True,
                            "2026-01-01T00:00:00+00:00",
                        ),
                    )
            # FK violation
            with pytest.raises(Exception):
                with store.transaction() as tx:
                    tx.execute(
                        "INSERT INTO governance_events (event_id, task_id, thread_id, seq,"
                        " event_type, payload, durable, created_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            f"e3-{uuid.uuid4().hex}",
                            "no-such-task",
                            "th",
                            2,
                            "x",
                            gov_store.json_param({}, "postgres"),
                            True,
                            "2026-01-01T00:00:00+00:00",
                        ),
                    )
        finally:
            store.close()

    def test_jsonb_roundtrip_and_models(self):
        store = self._store()
        try:
            policy = {"wall_clock_timeout": 60, "max_search_calls": 40}
            task_id = f"t-{uuid.uuid4().hex[:10]}"
            with store.transaction() as tx:
                tx.execute(
                    "INSERT INTO governance_tasks (task_id, thread_id, owner_instance,"
                    " status, policy_snapshot, version, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (
                        task_id,
                        "th",
                        "inst",
                        "running",
                        gov_store.json_param(policy, "postgres"),
                        0,
                        "2026-01-01T00:00:00+00:00",
                    ),
                )
            row = store.execute(
                "SELECT * FROM governance_tasks WHERE task_id=%s", (task_id,)
            )[0]
            rec = TaskRecord.from_row(row)
            assert rec.policy_snapshot == policy
            assert rec.status == TaskStatus.RUNNING.value
            assert rec.owner_instance == "inst"
            # event jsonb roundtrip
            with store.transaction() as tx:
                tx.execute(
                    "INSERT INTO governance_events (event_id, task_id, thread_id, seq,"
                    " event_type, payload, durable, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        "e-r",
                        task_id,
                        "th",
                        5,
                        "task_status_change",
                        gov_store.json_param({"status": "completed"}, "postgres"),
                        True,
                        "2026-01-02T00:00:00+00:00",
                    ),
                )
            erow = store.execute(
                "SELECT * FROM governance_events WHERE event_id=%s", ("e-r",)
            )[0]
            ev = GovernanceEvent.from_row(erow)
            assert ev.payload == {"status": "completed"}
            assert ev.seq == 5
            assert ev.durable is True
        finally:
            store.close()


@needs_pg
class TestGovernanceControllerPostgres:
    """Step 2 PG 镜像：create/start/terminal funnel 乐观写 rowcount 证据 + race 单赢家。"""

    def _store(self):
        store = gov_store._GovernancePostgresStore(os.getenv(PG_TEST_DSN_ENV))
        gov_migrations.ensure_schema(store)
        return store

    def test_create_start_terminalize_once_and_noop(self):
        async def scenario():
            store = self._store()
            try:
                ctl = GovernanceController(
                    store, owner_instance="inst-pg", retry_delays=(0.01, 0.02)
                )
                rec = ctl.create_task("th-pg", run_id="run-pg")
                assert rec.version == 0
                assert ctl.start_task(rec.task_id) is True
                assert ctl.start_task(rec.task_id) is False
                counters = {"agent_steps": 2}
                r1 = await ctl.terminalize(
                    rec.task_id, TerminalReason.COMPLETED, counters=counters
                )
                assert r1["winner"] is True and r1["durable"] is True
                got = ctl.get_task(rec.task_id)
                assert got.status == TaskStatus.COMPLETED.value
                assert got.version == 1  # rowcount==1 恰一次
                assert got.counters_snapshot == counters
                assert got.started_at is not None
                # 第二次 no-op（不翻转、不再 +1）
                r2 = await ctl.terminalize(rec.task_id, TerminalReason.CANCELLED)
                assert r2["winner"] is False and r2["already_terminal"] is True
                assert ctl.get_task(rec.task_id).version == 1
            finally:
                store.close()

        asyncio.run(scenario())

    def test_race_exactly_one_winner_one_transition(self):
        async def scenario():
            store = self._store()
            try:
                ctl = GovernanceController(
                    store, owner_instance="inst-pg", retry_delays=(0.01, 0.02)
                )
                rec = ctl.create_task("th-race-pg")

                async def contender(reason, idx):
                    return await ctl.terminalize(
                        rec.task_id, reason, counters={"probe": idx}
                    )

                r1, r2 = await asyncio.gather(
                    contender(TerminalReason.BUDGET_EXCEEDED, 1),
                    contender(TerminalReason.TIMED_OUT, 2),
                )
                assert r1["winner"] is True and r1["status"] == "budget_exceeded"
                assert r2["winner"] is False and r2["already_terminal"] is True
                got = ctl.get_task(rec.task_id)
                assert got.status == TaskStatus.BUDGET_EXCEEDED.value
                assert got.version == 1
                assert got.counters_snapshot == {"probe": 1}
            finally:
                store.close()

        asyncio.run(scenario())

    def test_terminalize_superseded_persists_superseded_by(self):
        async def scenario():
            store = self._store()
            try:
                ctl = GovernanceController(
                    store, owner_instance="inst-pg", retry_delays=(0.01, 0.02)
                )
                rec = ctl.create_task("th-sup-pg")
                r = await ctl.terminalize(
                    rec.task_id, TerminalReason.SUPERSEDED, superseded_by="task-new-pg"
                )
                assert r["winner"] is True and r["durable"] is True
                got = ctl.get_task(rec.task_id)
                assert got.status == TaskStatus.SUPERSEDED.value
                assert got.superseded_by == "task-new-pg"
            finally:
                store.close()

        asyncio.run(scenario())

    def test_execute_normal_completion_and_reject_unknown(self):
        async def scenario():
            store = self._store()
            try:
                ctl = GovernanceController(
                    store, owner_instance="inst-pg", retry_delays=(0.01, 0.02)
                )
                rec = ctl.create_task("th-exec-pg")

                async def work():
                    return "ok"

                assert await ctl.execute(rec.task_id, work()) == "ok"
                got = ctl.get_task(rec.task_id)
                assert got.status == TaskStatus.COMPLETED.value
                assert got.version == 1
                assert ctl.active_handle_ids() == []
                # unknown task 拒绝启动
                coro = work()
                try:
                    with pytest.raises(GovernanceControllerError):
                        await ctl.execute("never-pg", coro)
                finally:
                    coro.close()
            finally:
                store.close()

        asyncio.run(scenario())
