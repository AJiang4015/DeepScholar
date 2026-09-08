"""F8 Step 3 — Phase D：Controller Runtime Governance（SQLite 层，funnel 冻结面原样）。

覆盖（Spec 2026-09-15-f8-step3）：D1 normal→completed；D2 GovernanceLimitExceeded→budget_exceeded
（controller 不重新实现 budget 判断）；D3 cancel→cancelled（不产生 completed）；D4 watchdog
timed_out（单调 deadline，先 terminalize 后底层停）；D5 GraphRecursionError→failed
(framework_recursion_safety) ≠ budget_exceeded；D6 ordinary→failed(agent_failure)；
controller internal failure→failed(governance_control_failure)（c2）；D7/D8/D9 单 winner；
D10 linger（TaskRecord terminal ≠ 底层已停）；watchdog 不重复 terminalize；
counters_snapshot 随 funnel 写入；Step 2 funnel/CAS/pending 语义保持不变。

PG-specific race 留 Phase F Gate（Phase D 验证 runtime/control 语义）。
"""

import asyncio
import shutil
import time
import uuid
from pathlib import Path

import pytest

from langgraph.errors import GraphRecursionError

from app.runtime.governance import migrations as gov_migrations
from app.runtime.governance import store as gov_store
from app.runtime.governance.controller import GovernanceController
from app.runtime.governance.counters import GovernanceLimitExceeded
from app.runtime.governance.models import TaskStatus

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST_TMP = _REPO_ROOT / "_testtmp"


@pytest.fixture
def gov_tmp():
    d = _TEST_TMP / f"gov-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    try:
        shutil.rmtree(d)
    except OSError:
        pass


def _make_controller(gov_tmp, *, owner="inst-d", retry_delays=(0.01, 0.02)):
    db = str(gov_tmp / f"gov-{uuid.uuid4().hex}.sqlite")
    store = gov_store._GovernanceSqliteStore(db)
    gov_migrations.ensure_schema(store)
    ctl = GovernanceController(store, owner_instance=owner, retry_delays=retry_delays)
    ctl.watchdog_tick = (
        0.02  # 测试缩时（生产默认 1.0；只改采样间隔，不改 deadline 语义）
    )
    return store, ctl


def _rec(store, task_id):
    return gov_store.get_task(store, task_id)


class TestMappingD1D2D5D6:
    async def _scenario(self, gov_tmp, coro_factory, policy=None, extra_coro=None):
        store, ctl = _make_controller(gov_tmp)
        rec = ctl.create_task("th-d", policy_snapshot={"limits": policy or {}})
        return store, ctl, rec

    def test_d1_normal_completed(self, gov_tmp):
        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-d1")

            async def work():
                return 42

            assert await ctl.execute(rec.task_id, work(), policy={}) == 42
            row = _rec(store, rec.task_id)
            assert row.status == TaskStatus.COMPLETED.value
            assert row.version == 1
            assert row.counters_snapshot is not None
            assert row.counters_snapshot["llm_calls"] == 0
            store.close()

        asyncio.run(scenario())

    def test_d2_budget_exceeded_mapping(self, gov_tmp):
        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-d2")

            async def boom():
                raise GovernanceLimitExceeded("llm_calls", 1, 1)

            with pytest.raises(GovernanceLimitExceeded):
                await ctl.execute(rec.task_id, boom(), policy={})
            row = _rec(store, rec.task_id)
            assert row.status == TaskStatus.BUDGET_EXCEEDED.value
            assert row.terminal_reason == "budget_exceeded"
            assert row.error_kind is None
            store.close()

        asyncio.run(scenario())

    def test_d5_recursion_maps_failed_framework(self, gov_tmp):
        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-d5")

            async def boom():
                raise GraphRecursionError("recursion exceeded")

            with pytest.raises(GraphRecursionError):
                await ctl.execute(rec.task_id, boom(), policy={})
            row = _rec(store, rec.task_id)
            assert row.status == TaskStatus.FAILED.value
            assert row.error_kind == "framework_recursion_safety"  # ≠ budget_exceeded
            store.close()

        asyncio.run(scenario())

    def test_d6_ordinary_exception_failed_agent(self, gov_tmp):
        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-d6")

            async def boom():
                raise RuntimeError("agent boom")

            with pytest.raises(RuntimeError):
                await ctl.execute(rec.task_id, boom(), policy={})
            row = _rec(store, rec.task_id)
            assert row.status == TaskStatus.FAILED.value
            assert row.error_kind == "agent_failure"
            store.close()

        asyncio.run(scenario())


class TestCancelTimeoutLingerD3D4D10:
    def test_d3_explicit_cancel_not_completed(self, gov_tmp):
        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-d3")

            async def forever():
                await asyncio.Event().wait()

            task = asyncio.create_task(ctl.execute(rec.task_id, forever(), policy={}))
            for _ in range(100):
                if ctl.has_active_handle(rec.task_id):
                    break
                await asyncio.sleep(0.001)
            assert ctl.has_active_handle(rec.task_id)
            res = await ctl.cancel(rec.task_id)
            assert res["winner"] is True
            with pytest.raises(asyncio.CancelledError):
                await task
            row = _rec(store, rec.task_id)
            assert row.status == TaskStatus.CANCELLED.value
            assert row.version == 1  # 无第二次 transition
            store.close()

        asyncio.run(scenario())

    def test_d4_d10_timeout_terminates_while_underlying_lingers(self, gov_tmp):
        """deadline 到 → timed_out 先落库，底层 sync 线程仍在跑（linger）。"""

        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-d4")

            def blocking():
                time.sleep(0.6)
                return "done"

            async def slow():
                return await asyncio.to_thread(blocking)

            t0 = time.monotonic()
            task = asyncio.create_task(
                ctl.execute(rec.task_id, slow(), policy={"wall_clock_timeout": 0.05})
            )
            with pytest.raises(asyncio.CancelledError):
                await task
            dt = time.monotonic() - t0
            # 先 terminalize：dt 远小于 blocking 0.6s
            assert dt < 0.45, f"terminalize 被 linger 阻塞：{dt:.3f}s"
            row = _rec(store, rec.task_id)
            assert row.status == TaskStatus.TIMED_OUT.value
            # TaskRecord terminal ≠ 底层已停：lingering 观察置位
            assert row.underlying_linger_observed is True
            # watchdog 不重复 terminalize（第二收敛 no-op、version 不二次 +1）
            r2 = await ctl.cancel(rec.task_id)
            assert r2["winner"] is False and r2["already_terminal"] is True
            assert _rec(store, rec.task_id).version == 1
            store.close()

        asyncio.run(scenario())

    def test_c2_control_failure_converges_governance_control_failure(self, gov_tmp):
        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-c2")

            async def forever():
                await asyncio.Event().wait()

            task = asyncio.create_task(ctl.execute(rec.task_id, forever(), policy={}))
            for _ in range(100):
                if ctl.has_active_handle(rec.task_id):
                    break
                await asyncio.sleep(0.001)
            assert ctl.has_active_handle(rec.task_id)
            await ctl._converge_control_failure(  # noqa: SLF001 — 控制器内部控制故障路径
                rec.task_id, None, RuntimeError("ctl internal bug")
            )
            with pytest.raises(asyncio.CancelledError):
                await task
            row = _rec(store, rec.task_id)
            assert row.status == TaskStatus.FAILED.value
            assert row.error_kind == "governance_control_failure"
            assert "ctl internal bug" in (row.error or "")
            store.close()

        asyncio.run(scenario())


class TestRacesSingleWinnerD7D8D9:
    def test_d7_completed_then_cancel_noop(self, gov_tmp):
        """normal completed 后 cancel → no-op（不二次 transition，不翻转 completed）。"""

        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-d7")

            async def work():
                return 1

            assert await ctl.execute(rec.task_id, work(), policy={}) == 1
            r = await ctl.cancel(rec.task_id)
            assert r["winner"] is False and r["already_terminal"] is True
            row = _rec(store, rec.task_id)
            assert row.status == TaskStatus.COMPLETED.value
            assert row.version == 1
            store.close()

        asyncio.run(scenario())

    def test_d8_budget_vs_completed_single_winner(self, gov_tmp):
        """budget_exceeded 收敛后 completed 候选迟到 → funnel no-op：恰一 winner、version 恰 +1。"""

        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-d8")

            async def boom():
                raise GovernanceLimitExceeded("tool_calls", 1, 1)

            with pytest.raises(GovernanceLimitExceeded):
                await ctl.execute(rec.task_id, boom(), policy={})
            row = _rec(store, rec.task_id)
            assert row.status == TaskStatus.BUDGET_EXCEEDED.value
            assert row.version == 1
            # completed 候选迟到 → no-op（不翻转、不二次 +1）
            r2 = await ctl.terminalize(rec.task_id, "completed")
            assert r2["winner"] is False and r2["already_terminal"] is True
            row2 = _rec(store, rec.task_id)
            assert row2.status == TaskStatus.BUDGET_EXCEEDED.value
            assert row2.version == 1
            store.close()

        asyncio.run(scenario())

    def test_d9_timeout_then_cancel_single_winner(self, gov_tmp):
        """timed_out 先到 → cancel 后到 no-op；恰一 winner。"""

        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-d9")

            async def forever():
                await asyncio.Event().wait()

            task = asyncio.create_task(
                ctl.execute(rec.task_id, forever(), policy={"wall_clock_timeout": 0.05})
            )
            with pytest.raises(asyncio.CancelledError):
                await task
            row = _rec(store, rec.task_id)
            assert row.status == TaskStatus.TIMED_OUT.value
            r2 = await ctl.cancel(rec.task_id)  # race 败者语义
            assert r2["winner"] is False and r2["already_terminal"] is True
            assert _rec(store, rec.task_id).version == 1
            store.close()

        asyncio.run(scenario())


class TestPolicyAndSnapshot:
    def test_policy_limits_reach_counters_snapshot(self, gov_tmp):
        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-limits")
            policy = {
                "max_llm_calls": 3,
                "max_agent_steps": 2,
                "wall_clock_timeout": 600,
                "framework_recursion_limit": 5000,
            }

            async def work():
                return 0

            await ctl.execute(rec.task_id, work(), policy=policy)
            row = _rec(store, rec.task_id)
            # counters_snapshot 含全部四个 counter（Phase A keys）
            assert set(row.counters_snapshot) >= {
                "llm_calls",
                "tool_calls",
                "search_calls",
                "agent_steps",
            }
            store.close()

        asyncio.run(scenario())

    def test_cancelled_error_never_completed(self, gov_tmp):
        """CancelledError 路径（外部取消、无裁决）→ 不伪造 completed；run 原样上抛。"""

        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-cxe")

            async def work():
                await asyncio.sleep(30)

            task = asyncio.create_task(ctl.execute(rec.task_id, work(), policy={}))
            for _ in range(100):
                if ctl.has_active_handle(rec.task_id):
                    break
                await asyncio.sleep(0.001)
            task.cancel()  # 外部取消（不经 funnel）
            with pytest.raises(asyncio.CancelledError):
                await task
            row = _rec(store, rec.task_id)
            assert (
                row.status == TaskStatus.RUNNING.value
            )  # 未伪造 completed（sweeper/startup 兜底属后续）
            store.close()

        asyncio.run(scenario())
