"""F8 Step 4 Batch 3 — Observation Closure（A: governance_terminal live bridge；C: task_id additive）。

覆盖：monitor identity（governed task_id / bare null / 旧字段兼容）；bridge 各终态、event_id 与
durable 对齐、无 governance_seq、sink 失败 fail-open、bare 无 bridge、同 thread 双 task 区分、
bridge 不进 replay；回归 Batch1/2/F1。
"""

import asyncio
import os
import shutil
import uuid
from pathlib import Path

import pytest

from app.runtime.governance import migrations as gov_migrations
from app.runtime.governance import store as gov_store
from app.runtime.governance.controller import GovernanceController
from app.runtime.governance.counters import GovernanceLimitExceeded
from app.runtime.governance.models import TerminalReason

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST_TMP = _REPO_ROOT / "_testtmp"


@pytest.fixture
def gov_tmp():
    d = _TEST_TMP / f"g4c-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    try:
        shutil.rmtree(d)
    except OSError:
        pass


def _controller(gov_tmp, backend="sqlite", sink=None):
    if backend == "sqlite":
        db = str(gov_tmp / f"gov-{uuid.uuid4().hex}.sqlite")
        store = gov_store._GovernanceSqliteStore(db)
    else:
        store = gov_store._GovernancePostgresStore(
            os.environ["AGENT_CHECKPOINT_DSN_TEST"]
        )
    gov_migrations.ensure_schema(store)
    ctl = GovernanceController(
        store, owner_instance="inst-b3", retry_delays=(0.01, 0.02)
    )
    ctl.watchdog_tick = 0.02
    if sink is not None:
        ctl.live_sink = sink
    return store, ctl


def _fake_agent(mode="normal"):
    async def fake_run():
        if mode == "budget":
            raise GovernanceLimitExceeded("llm_calls", 1, 1)
        if mode == "fail":
            raise RuntimeError("boom")
        if mode == "hang":
            await asyncio.Event().wait()
        return "ok"

    return fake_run


def _last_durable_event_id(store, task_id):
    rows = store.execute(
        "SELECT event_id FROM governance_events WHERE task_id=%s ORDER BY seq DESC LIMIT 1",
        (task_id,),
    )
    return rows[0]["event_id"] if rows else None


class _BridgeRecorder:
    def __init__(self):
        self.frames = []

    def __call__(self, frame):
        self.frames.append(dict(frame))


class TestMonitorIdentitySqlite:
    def _emit_guard(self):
        from app.api.monitor import monitor  # noqa: PLC0415

        return monitor

    def test_governed_and_bare_task_id_and_compat(self, gov_tmp, monkeypatch):
        from app.api import monitor as mon

        m = self._emit_guard()
        # governed：set run+task ctx
        tr = mon.set_run_context("run-g")
        tt = mon.set_task_context("task-g")
        try:
            p1 = m.report_task_started("q1")
        finally:
            mon.reset_run_context(tr)
            mon.reset_task_context(tt)
        assert p1["task_id"] == "task-g"
        assert p1["run_id"] == "run-g"
        # 旧字段完全兼容（type/event/event_id/thread_id/seq/message/data/timestamp）
        for key in (
            "type",
            "event",
            "event_id",
            "thread_id",
            "seq",
            "message",
            "data",
            "timestamp",
        ):
            assert key in p1
        assert p1["type"] == "monitor_event"
        # bare：task ctx 未设置 → null（run 也空但 key 存在）
        p2 = m.report_task_started("q2")
        assert p2["task_id"] is None
        assert p2["event"] == "task_started"
        # seq 单调递增（monitor_seq 语义不变）
        assert p2["seq"] > p1["seq"]


class TestTerminalBridgeSqlite:
    def test_bridge_for_terminal_states_and_event_id_alignment(self, gov_tmp):
        async def scenario():
            recorder = _BridgeRecorder()
            store, ctl = _controller(gov_tmp, sink=recorder)
            th = "th-b3"

            async def run_case(name, policy, mode, expect_status):
                rec = ctl.create_task(th, run_id=f"run-{name}")
                frames_before = len(recorder.frames)
                if mode == "cancel":
                    t = asyncio.create_task(
                        ctl.execute(rec.task_id, _fake_agent("hang")(), policy={})
                    )
                    for _ in range(100):
                        if ctl.has_active_handle(rec.task_id):
                            break
                        await asyncio.sleep(0.001)
                    await ctl.cancel_governed(rec.task_id)
                    with pytest.raises(asyncio.CancelledError):
                        await t
                elif mode == "timeout":
                    t = asyncio.create_task(
                        ctl.execute(
                            rec.task_id,
                            _fake_agent("hang")(),
                            policy={"wall_clock_timeout": 0.05},
                        )
                    )
                    with pytest.raises(asyncio.CancelledError):
                        await t
                elif mode in ("budget", "fail"):
                    expected = (
                        GovernanceLimitExceeded if mode == "budget" else RuntimeError
                    )
                    with pytest.raises(expected):
                        await ctl.execute(
                            rec.task_id, _fake_agent(mode)(), policy=policy
                        )
                else:  # normal
                    assert (
                        await ctl.execute(rec.task_id, _fake_agent()(), policy=policy)
                        == "ok"
                    )
                # 本 case 应有恰一个 bridge 帧
                new = recorder.frames[frames_before:]
                assert len(new) == 1, (name, recorder.frames[frames_before:])
                frame = new[0]
                assert frame["type"] == "governance_terminal"
                assert frame["status"] == expect_status
                assert frame["task_id"] == rec.task_id
                assert frame["run_id"] == rec.run_id
                assert frame["thread_id"] == th
                assert "governance_seq" not in frame  # bridge 无 governance_seq
                row = ctl.get_task(rec.task_id)
                assert row.status == expect_status
                # bridge.event_id == durable terminal event.event_id
                assert frame["event_id"] == _last_durable_event_id(store, rec.task_id)
                return rec

            await run_case("completed", {}, "normal", "completed")
            await run_case("failed", {}, "fail", "failed")
            await run_case("budget", {"max_llm_calls": 1}, "budget", "budget_exceeded")
            await run_case("cancel", {}, "cancel", "cancelled")
            await run_case(
                "timeout", {"wall_clock_timeout": 0.05}, "timeout", "timed_out"
            )
            store.close()

        asyncio.run(scenario())

    def test_superseded_terminalize_not_governed_bridge(self, gov_tmp):
        """superseded 若经 terminalize 直调（非 governed 终态入口）→ 无 durable event、无 bridge
        （bridge 仅随 finalize_with_event 等 governed 发布点；现有 funnel/contract 不变）。"""

        async def scenario():
            recorder = _BridgeRecorder()
            store, ctl = _controller(gov_tmp, sink=recorder)
            rec = ctl.create_task("th-sup", run_id="run-s")
            await ctl.terminalize(
                rec.task_id, TerminalReason.SUPERSEDED, superseded_by="new"
            )
            assert _last_durable_event_id(store, rec.task_id) is None
            assert recorder.frames == []
            store.close()

        asyncio.run(scenario())

    def test_bare_execution_no_bridge(self, gov_tmp):
        async def scenario():
            recorder = _BridgeRecorder()
            store, ctl = _controller(gov_tmp, sink=recorder)
            rec = ctl.create_task("th-bare")
            assert (
                await ctl.execute(rec.task_id, _fake_agent()()) == "ok"
            )  # policy=None → bare
            assert recorder.frames == []
            assert ctl.get_task(rec.task_id).status == "completed"
            store.close()

        asyncio.run(scenario())

    def test_sink_failure_fail_open_and_no_replay_entry(self, gov_tmp):
        async def scenario():
            def bad_sink(frame):
                raise RuntimeError("sink down")

            store, ctl = _controller(gov_tmp, sink=bad_sink)
            rec = ctl.create_task("th-sink", run_id="run-sink")
            res = await ctl.finalize_with_event(rec.task_id, TerminalReason.COMPLETED)
            assert res["winner"] is True
            row = ctl.get_task(rec.task_id)
            assert (
                row.status == "completed" and row.version == 1
            )  # sink 故障不阻断 lifecycle
            # bridge 不进 replay（governance_events 只有 started? 无 started——此处 durable terminal 1 条）
            rows = store.execute(
                "SELECT COUNT(*) AS n FROM governance_events WHERE task_id=%s",
                (rec.task_id,),
            )
            assert rows[0]["n"] == 1  # 只有 durable terminal（bridge 不落库）
            store.close()

        asyncio.run(scenario())

    def test_same_thread_two_tasks_bridge_distinct(self, gov_tmp):
        async def scenario():
            recorder = _BridgeRecorder()
            store, ctl = _controller(gov_tmp, sink=recorder)
            a = ctl.create_task("th-x", run_id="ra")
            b = ctl.create_task("th-x", run_id="rb")
            await ctl.finalize_with_event(a.task_id, TerminalReason.COMPLETED)
            await ctl.finalize_with_event(b.task_id, TerminalReason.CANCELLED)
            frames = recorder.frames
            assert len(frames) == 2
            by_task = {f["task_id"]: f for f in frames}
            assert set(by_task) == {a.task_id, b.task_id}
            assert by_task[a.task_id]["run_id"] == "ra"
            assert by_task[b.task_id]["run_id"] == "rb"
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
class TestTerminalBridgePostgres:
    def test_completed_budget_bridge_event_id_alignment_on_pg(self):
        async def scenario():
            recorder = _BridgeRecorder()
            store, ctl = _controller(None, backend="postgres", sink=recorder)
            # completed
            rec = ctl.create_task("th-pg-b3", run_id="run-pg1")
            await ctl.finalize_with_event(rec.task_id, TerminalReason.COMPLETED)
            frame = recorder.frames[-1]
            assert frame["status"] == "completed"
            assert frame["event_id"] == _last_durable_event_id(store, rec.task_id)
            # budget via governed execute（真实 funnel + durable）
            rec2 = ctl.create_task("th-pg-b3", run_id="run-pg2")
            with pytest.raises(GovernanceLimitExceeded):
                await ctl.execute(
                    rec2.task_id,
                    _fake_agent("budget")(),
                    policy={"max_llm_calls": 1},
                )
            frame2 = recorder.frames[-1]
            assert frame2["task_id"] == rec2.task_id
            assert frame2["status"] == "budget_exceeded"
            assert frame2["event_id"] == _last_durable_event_id(store, rec2.task_id)
            assert "governance_seq" not in frame2
            store.close()

        asyncio.run(scenario())
