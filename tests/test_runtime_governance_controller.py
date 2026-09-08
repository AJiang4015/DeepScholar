"""F8 Runtime Governance — Step 2：Task Lifecycle / GovernanceController / Terminal Funnel（SQLite）。

覆盖（Plan Rev2 §5–§7 / §21）：
- TaskRecord 最小 CRUD（create/start/read；duplicate 拒绝；Thread≠Task≠Run 分离）；
- in-process handle registry（execute 期间占位；结束摘除；funnel 经 registry 发 cancel 信号）；
- terminalize 唯一 funnel：winner/durable/already_terminal 语义、counters/superseded_by 确定性、
  version+1 恰一次、完成后不可翻转、unknown task no-op、非法 reason/字段校验；
- §5.2 race matrix 全 9 组 × 双向（exactly one winner / exactly one durable transition）；
- §6 persistence failure：retry 阶梯 3 次 → pending（degraded_durability=true，绝不谎报）、
  内存裁决即停执行（DB 故障下无无限运行）、第二 contender no-op、flush_pending 收敛、
  跨实例采纳 DB terminal；DB 读失败仍内存裁决；
- execute wrapper：正常完成 → completed；异常 → failed(agent_failure)；拒绝 unknown/already/duplicate；
- Step 2 边界：funnel 不写 governance_events（event 归 sequencer Step）。

每个测试场景在单次 asyncio.run 内完成（controller 的 asyncio.Lock 绑定单一 loop）。
"""

import asyncio
import shutil
import uuid
from pathlib import Path

import pytest

from app.runtime.governance import controller as gov_controller
from app.runtime.governance import migrations as gov_migrations
from app.runtime.governance import store as gov_store
from app.runtime.governance.controller import (
    GovernanceController,
    GovernanceControllerError,
)
from app.runtime.governance.models import ErrorKind, TaskStatus, TerminalReason

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST_TMP = _REPO_ROOT / "_testtmp"

#: §5.2 race matrix：9 组 × 双向（每行先到者=期望赢家；reversed 组验证对称）
RACE_PAIRS = [
    ("completed", "cancel"),
    ("completed", "timed_out"),
    ("completed", "budget_exceeded"),
    ("completed", "superseded"),
    ("failed", "cancel"),
    ("failed", "timed_out"),
    ("budget_exceeded", "timed_out"),
    ("cancel", "timed_out"),
    ("superseded", "cancel"),
]

REASON_BY_NAME = {
    "completed": TerminalReason.COMPLETED,
    "cancel": TerminalReason.CANCELLED,
    "timed_out": TerminalReason.TIMED_OUT,
    "budget_exceeded": TerminalReason.BUDGET_EXCEEDED,
    "failed": TerminalReason.FAILED,
    "superseded": TerminalReason.SUPERSEDED,
}


@pytest.fixture
def gov_tmp():
    d = _TEST_TMP / f"gov-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    try:
        shutil.rmtree(d)
    except OSError:
        pass


def _make_controller(gov_tmp, *, owner="inst-test", retry_delays=(0.01, 0.02)):
    db = str(gov_tmp / f"gov-{uuid.uuid4().hex}.sqlite")
    store = gov_store._GovernanceSqliteStore(db)
    gov_migrations.ensure_schema(store)
    ctl = GovernanceController(store, owner_instance=owner, retry_delays=retry_delays)
    return store, ctl


def _db_record(store, task_id):
    return gov_store.get_task(store, task_id)


def _event_count(store):
    rows = store.execute("SELECT COUNT(*) AS n FROM governance_events")
    return rows[0]["n"]


class TestTaskLifecycleSqlite:
    def test_create_and_read_task_record(self, gov_tmp):
        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-1", run_id="run-a", policy_snapshot={"m": 1})
            assert rec.status == TaskStatus.RUNNING.value
            assert rec.version == 0
            assert rec.owner_instance == "inst-test"
            assert rec.run_id == "run-a"
            assert rec.created_at is not None
            got = ctl.get_task(rec.task_id)
            assert got is not None
            assert got.thread_id == "th-1"
            assert got.policy_snapshot == {"m": 1}
            assert got.is_terminal is False
            assert got.started_at is None
            # lifecycle 视图
            view = ctl.lifecycle(rec.task_id)
            assert view["found"] is True
            assert view["record"]["status"] == "running"
            assert view["degraded_durability"] is False
            store.close()

        asyncio.run(scenario())

    def test_create_duplicate_task_id_rejected(self, gov_tmp):
        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-1")
            with pytest.raises(Exception):  # sqlite IntegrityError
                ctl.create_task("th-2", task_id=rec.task_id)
            store.close()

        asyncio.run(scenario())

    def test_start_task_sets_started_at_only_once(self, gov_tmp):
        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-1")
            assert ctl.start_task(rec.task_id, "2026-01-01T00:00:00+00:00") is True
            assert ctl.start_task(rec.task_id, "2026-01-01T00:00:00+00:00") is False
            got = _db_record(store, rec.task_id)
            assert got.started_at == "2026-01-01T00:00:00+00:00"
            # 已 terminal → 不再 start
            await ctl.terminalize(rec.task_id, TerminalReason.COMPLETED)
            assert ctl.start_task(rec.task_id, "2026-02-01T00:00:00+00:00") is False
            store.close()

        asyncio.run(scenario())

    def test_thread_task_run_separation_and_registry_keying(self, gov_tmp):
        """Thread ≠ Task ≠ Run：一 thread 多 task；run_id 独立；registry 按 task_id 键控。"""

        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            t1 = ctl.create_task("th-1")
            t2 = ctl.create_task("th-1")
            t3 = ctl.create_task("th-2", run_id="run-99")
            ids = {t1.task_id, t2.task_id, t3.task_id}
            assert len(ids) == 3  # Task 唯一
            assert t1.thread_id == t2.thread_id == "th-1"
            assert ctl.get_task(t3.task_id).run_id == "run-99"

            # registry：execute 期间占位，按 task_id
            async def long_running():
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    raise

            exec_task = asyncio.ensure_future(ctl.execute(t1.task_id, long_running()))
            await asyncio.sleep(0)
            assert ctl.active_handle_ids() == [t1.task_id]
            assert ctl.has_active_handle(t1.task_id)
            assert not ctl.has_active_handle(t2.task_id)
            await ctl.cancel(t1.task_id)
            with pytest.raises(asyncio.CancelledError):
                await exec_task
            assert not ctl.has_active_handle(t1.task_id)
            # t2 不受影响（同 thread 独立生命周期）
            assert ctl.get_task(t2.task_id).status == TaskStatus.RUNNING.value
            assert _db_record(store, t1.task_id).status == TaskStatus.CANCELLED.value
            store.close()

        asyncio.run(scenario())


class TestTerminalFunnelSqlite:
    def test_terminalize_completed_once_and_not_flippable(self, gov_tmp):
        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-1")
            counters = {"agent_steps": 4, "llm_calls": 5, "tokens": {"total": 100}}
            r1 = await ctl.terminalize(
                rec.task_id, TerminalReason.COMPLETED, counters=counters
            )
            assert r1["winner"] is True
            assert r1["already_terminal"] is False
            assert r1["durable"] is True
            assert r1["degraded_durability"] is False
            assert r1["status"] == TaskStatus.COMPLETED.value
            got = _db_record(store, rec.task_id)
            assert got.status == TaskStatus.COMPLETED.value
            assert got.terminal_reason == TerminalReason.COMPLETED.value
            assert got.version == 1  # rowcount==1 恰一次 → version 仅 +1
            assert got.counters_snapshot == counters
            assert got.finished_at is not None
            assert got.superseded_by is None
            # 第二次（race 败者语义）：completed 不被 cancel 翻转
            r2 = await ctl.terminalize(rec.task_id, TerminalReason.CANCELLED)
            assert r2["winner"] is False
            assert r2["already_terminal"] is True
            assert r2["durable"] is True
            assert r2["status"] == TaskStatus.COMPLETED.value
            # 第三次同样 no-op
            r3 = await ctl.terminalize(rec.task_id, TerminalReason.TIMED_OUT)
            assert r3["winner"] is False and r3["already_terminal"] is True
            got2 = _db_record(store, rec.task_id)
            assert got2.status == TaskStatus.COMPLETED.value
            assert got2.version == 1
            store.close()

        asyncio.run(scenario())

    def test_terminalize_validation_errors(self, gov_tmp):
        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-1")
            # superseded 必须携带 superseded_by
            with pytest.raises(GovernanceControllerError):
                await ctl.terminalize(rec.task_id, TerminalReason.SUPERSEDED)
            # 非法 error_kind
            with pytest.raises(GovernanceControllerError):
                await ctl.terminalize(
                    rec.task_id, TerminalReason.FAILED, error_kind="banana"
                )
            # running 不是终态
            with pytest.raises(GovernanceControllerError):
                await ctl.terminalize(rec.task_id, TaskStatus.RUNNING)
            # str reason 非法值
            with pytest.raises(GovernanceControllerError):
                await ctl.terminalize(rec.task_id, "queued")
            # 校验失败不留脏状态
            got = _db_record(store, rec.task_id)
            assert got.status == TaskStatus.RUNNING.value
            assert got.version == 0
            store.close()

        asyncio.run(scenario())

    def test_terminalize_unknown_task_noop(self, gov_tmp):
        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            r = await ctl.terminalize("never-created", TerminalReason.CANCELLED)
            assert r["winner"] is False
            assert r["already_terminal"] is False
            assert r["found"] is False
            assert ctl.terminal_decision("never-created") is None
            store.close()

        asyncio.run(scenario())

    def test_execute_normal_completion_auto_completed(self, gov_tmp):
        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-1")

            async def work():
                return 42

            out = await ctl.execute(rec.task_id, work())
            assert out == 42
            got = _db_record(store, rec.task_id)
            assert got.status == TaskStatus.COMPLETED.value
            assert got.terminal_reason == TerminalReason.COMPLETED.value
            assert got.version == 1
            assert got.started_at is not None
            assert ctl.active_handle_ids() == []
            # 之后任何 funnel 都 no-op
            r = await ctl.cancel(rec.task_id)
            assert r["winner"] is False and r["already_terminal"] is True
            assert _db_record(store, rec.task_id).status == TaskStatus.COMPLETED.value
            store.close()

        asyncio.run(scenario())

    def test_execute_exception_maps_failed_agent_failure(self, gov_tmp):
        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-1")

            async def boom():
                raise ValueError("agent exploded")

            with pytest.raises(ValueError, match="agent exploded"):
                await ctl.execute(rec.task_id, boom())
            got = _db_record(store, rec.task_id)
            assert got.status == TaskStatus.FAILED.value
            assert got.error_kind == ErrorKind.AGENT_FAILURE.value
            assert got.error is not None and "agent exploded" in got.error
            store.close()

        asyncio.run(scenario())

    def test_execute_rejections(self, gov_tmp):
        async def scenario():
            store, ctl = _make_controller(gov_tmp)

            async def expect_reject(task_id):
                coro = (
                    _dummy_coro()
                )  # 拒绝路径在 ensure_future 前抛错 → 手动 close 防泄漏
                try:
                    with pytest.raises(GovernanceControllerError):
                        await ctl.execute(task_id, coro)
                finally:
                    coro.close()

            # unknown task
            await expect_reject("never-created")
            rec = ctl.create_task("th-1")
            # already terminal → 拒绝启动
            await ctl.terminalize(rec.task_id, TerminalReason.COMPLETED)
            await expect_reject(rec.task_id)
            # duplicate active handle
            rec2 = ctl.create_task("th-2")

            async def long_running():
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    raise

            exec_task = asyncio.ensure_future(ctl.execute(rec2.task_id, long_running()))
            await asyncio.sleep(0)
            await expect_reject(rec2.task_id)
            await ctl.cancel(rec2.task_id)
            with pytest.raises(asyncio.CancelledError):
                await exec_task
            store.close()

        asyncio.run(scenario())

    def test_terminalize_cancels_underlying_execution(self, gov_tmp):
        """funnel 裁决后本进程底层执行收到 cancel 信号（内存裁决即停执行）。"""

        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-1")
            started = asyncio.Event()
            stopped = asyncio.Event()

            async def work():
                started.set()
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    stopped.set()
                    raise

            exec_task = asyncio.ensure_future(ctl.execute(rec.task_id, work()))
            await started.wait()
            assert ctl.has_active_handle(rec.task_id)
            res = await ctl.terminalize(rec.task_id, TerminalReason.CANCELLED)
            assert res["winner"] is True and res["durable"] is True
            with pytest.raises(asyncio.CancelledError):
                await exec_task
            assert stopped.is_set()  # 底层真的停了
            assert not ctl.has_active_handle(rec.task_id)
            assert _db_record(store, rec.task_id).status == TaskStatus.CANCELLED.value
            store.close()

        asyncio.run(scenario())


def _dummy_coro():
    async def _noop():
        return None

    return _noop()


class TestTerminalRaceMatrixSqlite:
    @pytest.mark.parametrize(
        "first,second", RACE_PAIRS + [(b, a) for a, b in RACE_PAIRS]
    )
    def test_race_exactly_one_winner_one_transition(self, gov_tmp, first, second):
        """§5.2：exactly one terminal state；exactly one durable transition（version+1 恰一次）。"""

        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-race")

            def contender(name, idx):
                kwargs = {"counters": {"probe": idx}}
                if name == "failed":
                    kwargs["error_kind"] = ErrorKind.AGENT_FAILURE.value
                if name == "superseded":
                    kwargs["superseded_by"] = "task-new"
                return ctl.terminalize(rec.task_id, REASON_BY_NAME[name], **kwargs)

            r_first, r_second = await asyncio.gather(
                contender(first, 1), contender(second, 2)
            )
            # 期望：先到达者赢（锁内无挂起点 → gather 顺序确定性）
            expected_status = REASON_BY_NAME[
                first
            ].value  # TerminalReason ≡ TaskStatus 取值
            assert r_first["winner"] is True
            assert r_first["durable"] is True
            assert r_first["already_terminal"] is False
            assert r_first["status"] == expected_status
            assert r_second["winner"] is False
            assert r_second["already_terminal"] is True
            assert r_second["durable"] is True
            got = _db_record(store, rec.task_id)
            assert got.status == expected_status  # 单一终态 = 赢家
            assert got.version == 1  # 恰一次成功 transition
            assert got.counters_snapshot == {"probe": 1}  # 赢家快照确定性
            if first == "superseded":
                assert got.superseded_by == "task-new"
            else:
                assert got.superseded_by is None
            # 第三次收敛仍 no-op
            r3_kwargs = {"superseded_by": "task-new"} if second == "superseded" else {}
            r3 = await ctl.terminalize(rec.task_id, REASON_BY_NAME[second], **r3_kwargs)
            assert r3["winner"] is False and r3["already_terminal"] is True
            assert _db_record(store, rec.task_id).version == 1
            store.close()

        asyncio.run(scenario())


class TestTerminalPersistenceFailureSqlite:
    def test_durable_failure_pending_degraded_then_flush(self, gov_tmp, monkeypatch):
        """§6：durable 写 3 次失败 → pending（degraded_durability=true）；flush 收敛不谎报。"""

        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-fail")
            counters = {"agent_steps": 3}
            calls = {"n": 0}

            def boom(*a, **k):
                calls["n"] += 1
                raise RuntimeError("governance db down")

            with monkeypatch.context() as m:
                m.setattr(gov_store, "terminal_update", boom)
                r = await ctl.terminalize(
                    rec.task_id, TerminalReason.CANCELLED, counters=counters
                )
                # 内存裁决权威；durable 未落库且不谎报
                assert r["winner"] is True
                assert r["durable"] is False
                assert r["pending"] is True
                assert r["degraded_durability"] is True
                assert r["attempts"] == 3  # 立即 + 0.5s + 2s 等价阶梯（测试用缩时）
                assert calls["n"] == 3
                # 第二 contender：内存已裁决 → no-op（即使 DB 仍 running）
                r2 = await ctl.cancel(rec.task_id)
                assert r2["winner"] is False and r2["already_terminal"] is True
                assert r2["durable"] is False and r2["pending"] is True
                # DB 真实状态：仍是 running（durable 未成功）——由 flush/兜底收敛
                assert _db_record(store, rec.task_id).status == TaskStatus.RUNNING.value
                assert _db_record(store, rec.task_id).version == 0
                snap = ctl.pending_snapshot()
                assert len(snap) == 1
                assert snap[0]["status"] == TaskStatus.CANCELLED.value
                assert snap[0]["counters_snapshot"] == counters
                view = ctl.lifecycle(rec.task_id)
                assert view["pending_terminal"] is True
                assert view["degraded_durability"] is True
            # DB 恢复 → flush 收敛
            out = await ctl.flush_pending()
            assert out["flushed"] == 1
            assert out["remaining"] == 0
            got = _db_record(store, rec.task_id)
            assert got.status == TaskStatus.CANCELLED.value
            assert got.version == 1
            assert got.counters_snapshot == counters
            assert ctl.pending_snapshot() == []
            view2 = ctl.lifecycle(rec.task_id)
            assert view2["degraded_durability"] is False
            assert view2["pending_terminal"] is False
            store.close()

        asyncio.run(scenario())

    def test_store_read_failure_still_authoritative_and_stops_execution(
        self, gov_tmp, monkeypatch
    ):
        """§6：DB 读/写全故障 → 内存裁决仍停执行（无无限运行）；恢复后 flush 收敛。"""

        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-dbdown")
            started = asyncio.Event()

            async def work():
                started.set()
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    raise

            exec_task = asyncio.ensure_future(ctl.execute(rec.task_id, work()))
            await started.wait()

            def boom(*a, **k):
                raise RuntimeError("db unreachable")

            with monkeypatch.context() as m:
                m.setattr(gov_store, "get_task", boom)
                m.setattr(gov_store, "terminal_update", boom)
                r = await ctl.terminalize(rec.task_id, TerminalReason.TIMED_OUT)
                assert r["winner"] is True
                assert r["durable"] is False and r["degraded_durability"] is True
                assert r["pending"] is True
                # 底层已因内存裁决停止（不依赖 DB）
                with pytest.raises(asyncio.CancelledError):
                    await exec_task
                assert not ctl.has_active_handle(rec.task_id)
                r2 = await ctl.cancel(rec.task_id)
                assert r2["winner"] is False and r2["already_terminal"] is True
            # 恢复 → flush
            await ctl.flush_pending()
            got = _db_record(store, rec.task_id)
            assert got.status == TaskStatus.TIMED_OUT.value
            assert got.version == 1
            store.close()

        asyncio.run(scenario())

    def test_cross_instance_adopts_db_terminal(self, gov_tmp):
        """重启/跨实例：DB 已 terminal → 新实例 funnel 采纳 DB 事实，不另立冲突裁决。"""

        async def scenario():
            store, ctl_a = _make_controller(gov_tmp, owner="inst-a")
            rec = ctl_a.create_task("th-adopt")
            r = await ctl_a.terminalize(
                rec.task_id, TerminalReason.COMPLETED, counters={"c": 1}
            )
            assert r["durable"] is True
            # 新实例（全新内存）对同一 DB 任务做 cancel → 采纳 completed
            ctl_b = GovernanceController(store, owner_instance="inst-b")
            r2 = await ctl_b.terminalize(rec.task_id, TerminalReason.CANCELLED)
            assert r2["winner"] is False
            assert r2["already_terminal"] is True
            assert r2["durable"] is True
            assert r2["status"] == TaskStatus.COMPLETED.value
            assert r2["adopted_from_db"] is True
            got = _db_record(store, rec.task_id)
            assert got.status == TaskStatus.COMPLETED.value
            assert got.version == 1  # 未被二次翻转/二次 +1
            assert got.counters_snapshot == {"c": 1}
            store.close()

        asyncio.run(scenario())

    def test_step2_writes_no_events(self, gov_tmp):
        """Step 2 边界：funnel 不写 governance_events（terminal Event 归 sequencer Step）。"""

        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            r1 = ctl.create_task("th-1")
            r2 = ctl.create_task("th-2")
            await ctl.terminalize(r1.task_id, TerminalReason.COMPLETED)
            await ctl.terminalize(
                r2.task_id, TerminalReason.SUPERSEDED, superseded_by="x"
            )
            assert _event_count(store) == 0
            assert ctl.pending_snapshot() == []
            store.close()

        asyncio.run(scenario())


class TestControllerModuleSingleton:
    def test_get_controller_returns_none_when_store_disabled(self, monkeypatch):
        """单例：store 不可用（c1 fail-closed）→ get_controller None 而非半初始化 controller。"""

        def _boom(*a, **k):
            raise RuntimeError("no store")

        gov_controller.reset_controller()
        gov_store.reset_store()
        monkeypatch.setattr(gov_store, "parse_governance_config", _boom)
        assert gov_controller.get_controller() is None
        assert gov_controller.get_controller() is None  # disabled 缓存
        gov_controller.reset_controller()
        gov_store.reset_store()

    def test_controller_requires_store(self):
        with pytest.raises(GovernanceControllerError):
            GovernanceController(None)
