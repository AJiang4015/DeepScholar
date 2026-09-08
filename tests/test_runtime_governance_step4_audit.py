"""F8 Step 4 Batch 1 — Post-Gate Audit（5 边界，最小测试；不改生产代码）。

A. /api/task 无旧旁路（service.submit_task 唯一入口语义）；
B. 同 thread 重提交无 Task/Event 交叉；
C. task_started(service) 与 terminal(funnel) 顺序/丢失窗口；
D. run_id 在 normal/failure/cancel/budget/timeout 与 execution context 一致；
E. durability failure 严格 fail-open，不影响 Control Plane。
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
    d = _TEST_TMP / f"g4a-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    try:
        shutil.rmtree(d)
    except OSError:
        pass


def _controller(gov_tmp):
    db = str(gov_tmp / f"gov-{uuid.uuid4().hex}.sqlite")
    store = gov_store._GovernanceSqliteStore(db)
    gov_migrations.ensure_schema(store)
    ctl = GovernanceController(
        store, owner_instance="inst-g4a", retry_delays=(0.01, 0.02)
    )
    ctl.watchdog_tick = 0.02
    return store, ctl


def _events(store, task_id):
    return store.execute(
        "SELECT event_type, seq FROM governance_events WHERE task_id=%s ORDER BY seq",
        (task_id,),
    )


def _install(monkeypatch, seen, mode="normal"):
    async def fake_run(query, thread_id):
        ctx = get_governance_execution()
        seen.append(ctx.run_id if ctx is not None else None)
        if mode == "budget":
            raise GovernanceLimitExceeded("llm_calls", 1, 1)
        if mode == "fail":
            raise RuntimeError("agent boom")
        if mode in ("hang", "timeout"):
            await asyncio.Event().wait()
        return seen[-1]

    monkeypatch.setattr(gov_service, "run_deep_agent", fake_run)


class TestAuditBoundaries:
    def test_b_resubmit_same_thread_no_cross(self, gov_tmp, monkeypatch):
        """旧任务取消收敛 + 新任务各自独立 Task/Event（无交叉、无串写）。"""

        async def scenario():
            store, ctl = _controller(gov_tmp)
            _install(monkeypatch, [], "hang")
            rec1, task1 = gov_service.submit_task(
                ctl, thread_id="th-x", query="old", policy={}
            )
            for _ in range(100):
                if ctl.has_active_handle(rec1.task_id):
                    break
                await asyncio.sleep(0.001)
            # 同 thread 重提交：旧 governed 任务经冻结 funnel 收敛
            rec2, task2 = gov_service.submit_task(
                ctl, thread_id="th-x", query="new", policy={}
            )
            await ctl.cancel_governed(rec1.task_id)
            with pytest.raises(asyncio.CancelledError):
                await task1
            assert rec1.task_id != rec2.task_id
            assert rec1.run_id != rec2.run_id
            row1 = gov_service.get_task(ctl, rec1.task_id)
            row2 = gov_service.get_task(ctl, rec2.task_id)
            assert row1.status == "cancelled"
            assert row2.status == "running"  # 新任务未被旧收敛影响
            ev1 = _events(store, rec1.task_id)
            ev2 = _events(store, rec2.task_id)
            # 旧/新 event 各自独立、seq 各自从 1 全序（无交叉）
            assert all(e["seq"] == i for i, e in enumerate(ev1, start=1))
            assert all(e["seq"] == i for i, e in enumerate(ev2, start=1))
            assert "task_cancelled" in [e["event_type"] for e in ev1]
            assert "task_started" in [e["event_type"] for e in ev2]
            store.close()

        asyncio.run(scenario())

    def test_c_event_ordering_and_started_loss_window(self, gov_tmp, monkeypatch):
        """正常：started.seq < terminal.seq；started 写失败 → 仅缺 started（gap），control 不受影响。"""

        async def scenario():
            store, ctl = _controller(gov_tmp)
            _install(monkeypatch, [], "normal")
            rec, task = gov_service.submit_task(
                ctl, thread_id="th-o", query="q", policy={}
            )
            await task
            ev = _events(store, rec.task_id)
            assert [e["event_type"] for e in ev] == ["task_started", "task_completed"]
            assert ev[0]["seq"] < ev[1]["seq"]
            # started 写失败窗口：仍能正常 terminal（TaskRecord 是 truth，事件可缺失）
            store2, ctl2 = _controller(gov_tmp)
            _install(monkeypatch, [], "normal")

            def boom(*a, **k):
                raise RuntimeError("events down")

            monkeypatch.setattr(gov_events, "lifecycle_event", boom)
            rec2, task2 = gov_service.submit_task(
                ctl2, thread_id="th-o2", query="q", policy={}
            )
            assert await task2 == rec2.run_id
            row2 = gov_service.get_task(ctl2, rec2.task_id)
            assert row2.status == "completed" and row2.version == 1
            assert _events(store2, rec2.task_id) == []  # 事件缺失但 control/记录正常
            store.close()
            store2.close()

        asyncio.run(scenario())

    @pytest.mark.parametrize("mode", ["normal", "budget", "fail", "cancel", "timeout"])
    def test_d_run_id_consistent_all_paths(self, gov_tmp, monkeypatch, mode):
        """run_id 在 normal/budget/fail/cancel/timeout 均与 execution ctx 一致（== TaskRecord.run_id）。"""

        async def scenario():
            store, ctl = _controller(gov_tmp)
            seen = []
            _install(
                monkeypatch, seen, "hang" if mode in ("cancel", "timeout") else mode
            )
            policy = (
                {"max_llm_calls": 1}
                if mode == "budget"
                else ({"wall_clock_timeout": 0.05} if mode == "timeout" else {})
            )
            rec, task = gov_service.submit_task(
                ctl, thread_id=f"th-{mode}", query="q", policy=policy
            )
            if mode == "cancel":
                for _ in range(100):
                    if ctl.has_active_handle(rec.task_id):
                        break
                    await asyncio.sleep(0.001)
                await ctl.cancel_governed(rec.task_id)
            if mode == "normal":
                await task
            else:
                with pytest.raises(
                    GovernanceLimitExceeded
                    if mode == "budget"
                    else RuntimeError
                    if mode == "fail"
                    else asyncio.CancelledError
                ):
                    await task
            assert seen and seen[0] == rec.run_id, (
                f"{mode}: execution ctx run_id 与 TaskRecord 不一致"
            )
            assert gov_service.get_task(ctl, rec.task_id).run_id == rec.run_id
            store.close()

        asyncio.run(scenario())

    def test_e_durability_failure_fail_open_control_intact(self, gov_tmp, monkeypatch):
        """事件持久化全故障：budget 与 normal control 均不受影响（TaskRecord terminal 照常）。"""

        async def scenario():
            store, ctl = _controller(gov_tmp)

            def boom(*a, **k):
                raise RuntimeError("events db down")

            monkeypatch.setattr(gov_events, "lifecycle_event", boom)
            _install(monkeypatch, [], "budget")
            rec, task = gov_service.submit_task(
                ctl, thread_id="th-e", query="q", policy={"max_llm_calls": 1}
            )
            with pytest.raises(GovernanceLimitExceeded):
                await task
            row = gov_service.get_task(ctl, rec.task_id)
            assert row.status == "budget_exceeded" and row.version == 1
            store.close()

        asyncio.run(scenario())
