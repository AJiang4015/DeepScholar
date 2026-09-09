"""Multi-Session — Runtime 隔离 / 并发测试（两个 Session 同时运行 + Cancel 互不影响）。

覆盖（Decision Closure #7/#10、Task §16.3/§16.4）：
- 双 Session（thread A / thread B）同时 running：状态独立、handle 独立、task/run id 互异；
- Cancel Session A 的任务 → 只收敛 A（cancelled），Session B 继续 running 且可正常完成；
- Session-scoped 前置校验（task.thread_id == session_id）在运行路径上拒绝跨 Session 操作。

不引入 Session cancel / budget / controller —— Task 生命周期全部走既有 GovernanceController
（submit_task → controller.execute；取消 = controller.cancel_governed 冻结 funnel）。
"""

import asyncio
import shutil
import uuid
from contextlib import suppress
from pathlib import Path

import pytest

from app.runtime.governance import migrations as gov_migrations
from app.runtime.governance import service as gov_service
from app.runtime.governance import store as gov_store
from app.runtime.governance.controller import GovernanceController
from app.session import service as session_service

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST_TMP = _REPO_ROOT / "_testtmp"


@pytest.fixture
def gov_tmp():
    d = _TEST_TMP / f"ms-conc-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    try:
        shutil.rmtree(d)
    except OSError:
        pass


def _stub_runner(ev_a: asyncio.Event, ev_b: asyncio.Event, sid_a: str, sid_b: str):
    """按 session_id（== thread_id）分发的 stub 执行器（模拟两个独立研究运行）。"""

    async def runner(query, session_id):
        if session_id == sid_a:
            await ev_a.wait()
        elif session_id == sid_b:
            await ev_b.wait()
        else:
            raise AssertionError(f"未预期的 session_id: {session_id}")
        return f"done:{session_id}"

    return runner


def _make_controller(gov_tmp) -> tuple[GovernanceController, object]:
    store = gov_store._GovernanceSqliteStore(str(gov_tmp / "gov.sqlite"))
    gov_migrations.ensure_schema(store)
    return GovernanceController(store), store


class TestConcurrentSessions:
    def test_two_sessions_run_concurrently_and_cancel_is_isolated(
        self, gov_tmp, monkeypatch
    ):
        async def scenario():
            ctl, store = _make_controller(gov_tmp)
            try:
                sid_a = "conc-a"
                sid_b = "conc-b"
                sess_a = session_service.create_session(ctl, title="A", thread_id=sid_a)
                sess_b = session_service.create_session(ctl, title="B", thread_id=sid_b)
                ev_a = asyncio.Event()
                ev_b = asyncio.Event()
                monkeypatch.setattr(
                    gov_service,
                    "run_deep_agent",
                    _stub_runner(ev_a, ev_b, sid_a, sid_b),
                )
                rec_a, task_a = gov_service.submit_task(
                    ctl, thread_id=sess_a.session_id, query="qa"
                )
                rec_b, task_b = gov_service.submit_task(
                    ctl, thread_id=sess_b.session_id, query="qb"
                )
                await asyncio.sleep(0.05)  # 两个 runner 进入 event 等待

                # 双 Session 同时 running，状态 / handle 独立
                assert ctl.get_task(rec_a.task_id).status == "running"
                assert ctl.get_task(rec_b.task_id).status == "running"
                assert ctl.has_active_handle(rec_a.task_id)
                assert ctl.has_active_handle(rec_b.task_id)
                assert rec_a.task_id != rec_b.task_id
                assert rec_a.run_id != rec_b.run_id

                # Session A 不能操作 Session B 的任务（归属校验拒绝）
                with pytest.raises(session_service.TaskNotInSessionError):
                    session_service.validate_task_in_session(
                        ctl, sess_a.session_id, rec_b.task_id
                    )

                # Cancel A（A 自己的任务，校验通过）→ 只收敛 A
                session_service.validate_task_in_session(
                    ctl, sess_a.session_id, rec_a.task_id
                )
                res = await gov_service.cancel_task(ctl, rec_a.task_id)
                assert res["status"] == "cancelled"
                with suppress(asyncio.CancelledError):
                    await task_a
                assert ctl.get_task(rec_a.task_id).status == "cancelled"
                assert not ctl.has_active_handle(rec_a.task_id)

                # B 不受 A cancel 影响：仍 running，且可正常完成
                assert ctl.get_task(rec_b.task_id).status == "running"
                assert ctl.has_active_handle(rec_b.task_id)
                ev_b.set()
                await task_b
                assert ctl.get_task(rec_b.task_id).status == "completed"
            finally:
                store.close()

        asyncio.run(scenario())

    def test_two_sessions_complete_independently(self, gov_tmp, monkeypatch):
        async def scenario():
            ctl, store = _make_controller(gov_tmp)
            try:
                sid_a = "indep-a"
                sid_b = "indep-b"
                sess_a = session_service.create_session(ctl, title="A", thread_id=sid_a)
                sess_b = session_service.create_session(ctl, title="B", thread_id=sid_b)
                ev_a = asyncio.Event()
                ev_b = asyncio.Event()
                monkeypatch.setattr(
                    gov_service,
                    "run_deep_agent",
                    _stub_runner(ev_a, ev_b, sid_a, sid_b),
                )
                rec_a, task_a = gov_service.submit_task(
                    ctl, thread_id=sess_a.session_id, query="qa"
                )
                rec_b, task_b = gov_service.submit_task(
                    ctl, thread_id=sess_b.session_id, query="qb"
                )
                await asyncio.sleep(0.05)
                # A 先完成、B 后完成 —— 生命周期互不耦合
                ev_a.set()
                await task_a
                assert ctl.get_task(rec_a.task_id).status == "completed"
                assert ctl.get_task(rec_b.task_id).status == "running"
                ev_b.set()
                await task_b
                assert ctl.get_task(rec_b.task_id).status == "completed"
            finally:
                store.close()

        asyncio.run(scenario())
