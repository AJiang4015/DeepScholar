"""F8 Step 4 Batch 1 — Service Wiring + Minimal Durable Lifecycle（SQLite 快速逻辑 + PG 子集）。

在 service 层（真实 GovernanceController + run_deep_agent 同源接线点）验证：
- submit → TaskRecord + task_started + Controller.execute(policy)；
- terminal 经冻结 funnel + lifecycle event（completed/budget_exceeded/cancelled）；
- tasks 查询/detail 字段；
- run_id correlation（TaskRecord.run_id == 执行 ctx 所见 run_id）；
- event_id 幂等；(task_id,seq) 唯一且升序；event 与 TaskRecord 对齐；
- event persistence failure → fail-open（不影响 control）。

run_deep_agent 以受控替身注入 gov_service 模块（避免无凭据环境）；controller/store 真实。
HTTP 端点（server.py）仅做薄层——真实 HTTP E2E 需 LLM 凭据，见 Limitation。
"""

import asyncio
import shutil
import uuid
from pathlib import Path

import pytest

from app.runtime.governance import events as gov_events
from app.runtime.governance import migrations as gov_migrations
from app.runtime.governance import service as gov_service
from app.runtime.governance import store as gov_store
from app.runtime.governance.context import get_governance_execution
from app.runtime.governance.controller import GovernanceController
from app.runtime.governance.counters import GovernanceLimitExceeded

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST_TMP = _REPO_ROOT / "_testtmp"


@pytest.fixture
def gov_tmp():
    d = _TEST_TMP / f"g4-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    try:
        shutil.rmtree(d)
    except OSError:
        pass


def _make_controller(gov_tmp, backend="sqlite"):
    if backend == "sqlite":
        db = str(gov_tmp / f"gov-{uuid.uuid4().hex}.sqlite")
        store = gov_store._GovernanceSqliteStore(db)
    else:
        dsn = __import__("os").environ.get("AGENT_CHECKPOINT_DSN_TEST")
        if not dsn:
            return None, None
        store = gov_store._GovernancePostgresStore(dsn)
    gov_migrations.ensure_schema(store)
    ctl = GovernanceController(
        store, owner_instance="inst-g4", retry_delays=(0.01, 0.02)
    )
    ctl.watchdog_tick = 0.02
    return store, ctl


def _event_rows(store, task_id):
    return store.execute(
        "SELECT event_id, task_id, run_id, seq, event_type, durable FROM governance_events "
        "WHERE task_id=%s ORDER BY seq",
        (task_id,),
    )


def _install_fake_agent(monkeypatch, mode="normal"):
    """把 gov_service 引用的 run_deep_agent 替换为受控替身（execution ctx 可见性断言）。"""

    async def fake_run(query, thread_id):
        ctx = get_governance_execution()
        used_run_id = ctx.run_id if ctx is not None else None
        if mode == "budget":
            raise GovernanceLimitExceeded("llm_calls", 1, 1)
        if mode == "hang":
            await asyncio.Event().wait()
        return used_run_id

    monkeypatch.setattr(gov_service, "run_deep_agent", fake_run)


class TestServiceSqlite:
    def test_submit_normal_completed_events_and_query(self, gov_tmp, monkeypatch):
        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            _install_fake_agent(monkeypatch, "normal")
            rec, task = gov_service.submit_task(
                ctl, thread_id="th-1", query="q", policy={}
            )
            result = await task
            # run_id correlation：执行 ctx 所见 run_id == TaskRecord.run_id
            assert result == rec.run_id == gov_service.get_task(ctl, rec.task_id).run_id
            row = gov_service.get_task(ctl, rec.task_id)
            assert row.status == "completed" and row.version == 1
            events = _event_rows(store, rec.task_id)
            types = [e["event_type"] for e in events]
            assert types == ["task_started", "task_completed"]
            assert events[1]["run_id"] == rec.run_id
            assert events[1]["durable"] == 1
            assert row.counters_snapshot is not None
            # event terminal 与 TaskRecord 对齐（event 不驱动状态）
            assert events[1]["event_type"] == "task_completed"
            # tasks 查询/detail
            lst = gov_service.list_tasks(ctl, thread_id="th-1")
            assert [t.task_id for t in lst] == [rec.task_id]
            d = gov_service.task_dict(row)
            for key in (
                "task_id",
                "thread_id",
                "run_id",
                "status",
                "terminal_reason",
                "error_kind",
                "counters_snapshot",
                "created_at",
                "finished_at",
            ):
                assert key in d
            store.close()

        asyncio.run(scenario())

    def test_budget_mapping_and_event_fail_open(self, gov_tmp, monkeypatch):
        """event persistence failure → fail-open：budget control 不受影响。"""

        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            _install_fake_agent(monkeypatch, "budget")

            def boom(*a, **k):
                raise RuntimeError("events db down")

            monkeypatch.setattr(gov_events, "lifecycle_event", boom)
            rec, task = gov_service.submit_task(
                ctl, thread_id="th-b", query="q", policy={"max_llm_calls": 1}
            )
            with pytest.raises(GovernanceLimitExceeded):
                await task
            row = gov_service.get_task(ctl, rec.task_id)
            assert row.status == "budget_exceeded"  # control 未被 observation 故障削弱
            assert row.version == 1
            store.close()

        asyncio.run(scenario())

    def test_cancel_governed_cancelled_event(self, gov_tmp, monkeypatch):
        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            _install_fake_agent(monkeypatch, "hang")
            rec, task = gov_service.submit_task(
                ctl, thread_id="th-c", query="q", policy={}
            )
            for _ in range(100):
                if ctl.has_active_handle(rec.task_id):
                    break
                await asyncio.sleep(0.001)
            res = await gov_service.cancel_task(ctl, rec.task_id)
            assert res["status"] == "cancelled"
            with pytest.raises(asyncio.CancelledError):
                await task
            row = gov_service.get_task(ctl, rec.task_id)
            assert row.status == "cancelled" and row.version == 1
            types = [e["event_type"] for e in _event_rows(store, rec.task_id)]
            assert "task_cancelled" in types
            store.close()

        asyncio.run(scenario())

    def test_event_id_idempotent_and_seq_unique(self, gov_tmp):
        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-e", run_id="run-e")
            eid = uuid.uuid4().hex
            r1 = gov_events.lifecycle_event(
                store,
                task_id=rec.task_id,
                thread_id="th-e",
                run_id="run-e",
                status="completed",
            )
            r1b = gov_events.lifecycle_event(
                store,
                task_id=rec.task_id,
                thread_id="th-e",
                run_id="run-e",
                status="completed",
                event_id=eid,
            )
            r2 = gov_events.lifecycle_event(
                store,
                task_id=rec.task_id,
                thread_id="th-e",
                run_id="run-e",
                status="completed",
                event_id=eid,
            )
            assert r1["durable"] is True and r1b["durable"] is True
            assert r2["durable"] is True and r2["duplicate"] is True  # 幂等
            rows = _event_rows(store, rec.task_id)
            seqs = [r["seq"] for r in rows]
            assert seqs == sorted(seqs) and len(set(seqs)) == len(
                seqs
            )  # (task_id,seq) 全序唯一
            store.close()

        asyncio.run(scenario())


PG_TEST_DSN_ENV = "AGENT_CHECKPOINT_DSN_TEST"

try:
    import psycopg  # noqa: F401

    HAS_PSYCOPG = True
except Exception:  # pragma: no cover
    HAS_PSYCOPG = False

needs_pg = pytest.mark.skipif(
    not (HAS_PSYCOPG and __import__("os").getenv(PG_TEST_DSN_ENV)),
    reason=f"需要 psycopg 与 {PG_TEST_DSN_ENV}",
)


@needs_pg
class TestServicePostgres:
    def test_submit_normal_events_durable_on_pg(self, gov_tmp, monkeypatch):
        async def scenario():
            store, ctl = _make_controller(gov_tmp, backend="postgres")
            _install_fake_agent(monkeypatch, "normal")
            rec, task = gov_service.submit_task(
                ctl, thread_id="th-pg", query="q", policy={}
            )
            result = await task
            row = gov_service.get_task(ctl, rec.task_id)
            assert row.status == "completed" and row.version == 1
            assert result == rec.run_id == row.run_id
            events = _event_rows(store, rec.task_id)
            assert [e["event_type"] for e in events] == [
                "task_started",
                "task_completed",
            ]
            store.close()

        asyncio.run(scenario())

    def test_cancel_durable_and_idempotent_event_on_pg(self, gov_tmp, monkeypatch):
        async def scenario():
            store, ctl = _make_controller(gov_tmp, backend="postgres")
            _install_fake_agent(monkeypatch, "hang")
            rec, task = gov_service.submit_task(
                ctl, thread_id="th-pg2", query="q", policy={}
            )
            for _ in range(100):
                if ctl.has_active_handle(rec.task_id):
                    break
                await asyncio.sleep(0.001)
            res = await gov_service.cancel_task(ctl, rec.task_id)
            assert res["status"] == "cancelled"
            with pytest.raises(asyncio.CancelledError):
                await task
            row = gov_service.get_task(ctl, rec.task_id)
            assert row.status == "cancelled" and row.version == 1
            # 幂等：重复同 event_id 不产生第二行
            eid = uuid.uuid4().hex
            gov_events.lifecycle_event(
                store,
                task_id=rec.task_id,
                thread_id="th-pg2",
                run_id=row.run_id,
                status="cancelled",
                event_id=eid,
            )
            gov_events.lifecycle_event(
                store,
                task_id=rec.task_id,
                thread_id="th-pg2",
                run_id=row.run_id,
                status="cancelled",
                event_id=eid,
            )
            rows = store.execute(
                "SELECT COUNT(*) AS n FROM governance_events WHERE event_id=%s", (eid,)
            )
            assert rows[0]["n"] == 1
            store.close()

        asyncio.run(scenario())
