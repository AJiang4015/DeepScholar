"""F8 Step 4 — F1 minimal fix regression：governed execution run_id 必绑。

- service.submit 原有 run_id 全链路一致（不回生成）；
- create_task(run_id=None) 进入 governed execution → backfill；
- backfill 后 TaskRecord.run_id == GovernanceExecution.run_id（执行所见）；
- 已有 run_id 严禁重新生成；
- 并发 execution 不共享 run_id；
- terminal durable event 使用同一 run_id；
- 不触碰 version/terminal CAS（version 仅终态 +1）；
- Batch 2 (task_id, governance_seq) replay 读取不受影响。
"""

import asyncio
import os
import shutil
import uuid
from pathlib import Path

import pytest

from app.runtime.governance import migrations as gov_migrations
from app.runtime.governance import store as gov_store
from app.runtime.governance.context import get_governance_execution
from app.runtime.governance.controller import GovernanceController
from app.runtime.governance.counters import GovernanceLimitExceeded

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST_TMP = _REPO_ROOT / "_testtmp"


@pytest.fixture
def gov_tmp():
    d = _TEST_TMP / f"g4f-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    try:
        shutil.rmtree(d)
    except OSError:
        pass


def _controller(gov_tmp, backend="sqlite"):
    if backend == "sqlite":
        db = str(gov_tmp / f"gov-{uuid.uuid4().hex}.sqlite")
        store = gov_store._GovernanceSqliteStore(db)
    else:
        store = gov_store._GovernancePostgresStore(
            os.environ["AGENT_CHECKPOINT_DSN_TEST"]
        )
    gov_migrations.ensure_schema(store)
    ctl = GovernanceController(
        store, owner_instance="inst-f1", retry_delays=(0.01, 0.02)
    )
    ctl.watchdog_tick = 0.02
    return store, ctl


def _fake_agent(seen, mode="normal"):
    async def fake_run():
        ctx = get_governance_execution()
        seen.append(ctx.run_id if ctx is not None else None)
        if mode == "budget":
            raise GovernanceLimitExceeded("llm_calls", 1, 1)
        return ctx.run_id if ctx is not None else None

    return fake_run


def _last_event_run_id(store, task_id):
    rows = store.execute(
        "SELECT run_id FROM governance_events WHERE task_id=%s ORDER BY seq DESC LIMIT 1",
        (task_id,),
    )
    return rows[0]["run_id"] if rows else None


class TestRunIdBackfillSqlite:
    def test_existing_run_id_never_regenerated(self, gov_tmp):
        async def scenario():
            store, ctl = _controller(gov_tmp)
            rec = ctl.create_task("th-keep", run_id="R-keep")
            seen = []
            result = await ctl.execute(rec.task_id, _fake_agent(seen)(), policy={})
            assert seen == ["R-keep"]
            assert result == "R-keep"
            row = ctl.get_task(rec.task_id)
            assert row.run_id == "R-keep"
            assert (
                row.status == "completed" and row.version == 1
            )  # CAS/version 语义不变
            store.close()

        asyncio.run(scenario())

    def test_backfill_when_run_id_none(self, gov_tmp):
        async def scenario():
            store, ctl = _controller(gov_tmp)
            rec = ctl.create_task("th-new")  # run_id=None
            seen = []
            result = await ctl.execute(rec.task_id, _fake_agent(seen)(), policy={})
            row = ctl.get_task(rec.task_id)
            assert row.run_id is not None
            assert seen == [row.run_id]  # ctx/execution 使用回填值
            assert result == row.run_id
            assert row.status == "completed" and row.version == 1
            store.close()

        asyncio.run(scenario())

    def test_concurrent_executions_do_not_share_run_id(self, gov_tmp):
        async def scenario():
            store, ctl = _controller(gov_tmp)
            a = ctl.create_task("th-a")
            b = ctl.create_task("th-b")

            async def run(rec):
                seen = []
                res = await ctl.execute(rec.task_id, _fake_agent(seen)(), policy={})
                return seen, res

            (sa, ra), (sb, rb) = await asyncio.gather(run(a), run(b))
            ra_ = ctl.get_task(a.task_id).run_id
            rb_ = ctl.get_task(b.task_id).run_id
            assert ra_ != rb_ and sa == [ra_] and sb == [rb_]
            assert ra == ra_ and rb == rb_
            store.close()

        asyncio.run(scenario())

    def test_terminal_durable_event_run_id_backfilled(self, gov_tmp):
        async def scenario():
            store, ctl = _controller(gov_tmp)
            rec = ctl.create_task("th-ev")  # run_id=None
            with pytest.raises(GovernanceLimitExceeded):
                await ctl.execute(
                    rec.task_id, _fake_agent([], mode="budget")(), policy={}
                )
            row = ctl.get_task(rec.task_id)
            assert row.status == "budget_exceeded" and row.run_id is not None
            assert _last_event_run_id(store, rec.task_id) == row.run_id
            store.close()

        asyncio.run(scenario())


PG_TEST_DSN_ENV = "AGENT_CHECKPOINT_DSN_TEST"

try:
    import psycopg  # noqa: F401

    HAS_PSYCOPG = True
except Exception:  # pragma: no cover
    HAS_PSYCOPG = False

needs_pg = pytest.mark.skipif(
    not (HAS_PSYCOPG and os.getenv(PG_TEST_DSN_ENV)),
    reason=f"需要 psycopg 与 {PG_TEST_DSN_ENV}",
)


@needs_pg
class TestRunIdBackfillPostgres:
    def test_backfill_and_keep_on_pg(self):
        async def scenario():
            store, ctl = _controller(None, backend="postgres")
            # existing
            rec_keep = ctl.create_task("th-pg-keep", run_id="R-pg")
            seen = []
            assert (
                await ctl.execute(rec_keep.task_id, _fake_agent(seen)(), policy={})
                == "R-pg"
            )
            assert ctl.get_task(rec_keep.task_id).run_id == "R-pg"
            # none → backfill
            rec_new = ctl.create_task("th-pg-new")
            seen2 = []
            res = await ctl.execute(rec_new.task_id, _fake_agent(seen2)(), policy={})
            row = ctl.get_task(rec_new.task_id)
            assert row.run_id == res == seen2[0] and row.run_id != "R-pg"
            store.close()

        asyncio.run(scenario())
