"""P2-2 S5 — RuntimeHealthScanner 测试（startup sweep / periodic / mode / funnel / cleanup）。

覆盖（S5 要求 1–11）：
1. startup sweep 调用 `reclaim.evaluate`；
2. periodic tick 调用 `reclaim.evaluate`（含 loop 启动/停止）；
3. `off` 不运行 scanner；
4. `detect` 产生 decision 但不 terminalize；
5. `enforce` 经既有冻结 funnel 收敛（终态 + durable event）；
6. `FOREIGN` 不处理；
7. `SAME_HOST_PREV` + fresh heartbeat 不误杀（D15）；
8. cancellation 安全（stop 幂等、无泄漏 task）；
9. scanner 单实例保护（不创建第二个 background task）；
10. cleanup 删除 health row、不影响 lifecycle row；
11. scanner failure 不影响 controller 主流程。

纪律：测试只驱动 scanner + 既有 controller/funnel；不修改任何 frozen lifecycle 语义。
"""

import asyncio
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.runtime.governance import health_store as hstore
from app.runtime.governance import heartbeat as hb
from app.runtime.governance import migrations as gov_migrations
from app.runtime.governance import reclaim as rc
from app.runtime.governance import scanner as scanner_mod
from app.runtime.governance import store as gov_store
from app.runtime.governance.controller import GovernanceController
from app.runtime.governance.models import TaskRecord

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST_TMP = _REPO_ROOT / "_testtmp"

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
HOST = "hostA"
CURRENT_OWNER = "hostA-1000"
PREV_OWNER = "hostA-999"
FOREIGN_OWNER = "hostB-777"


@pytest.fixture
def gov_tmp():
    d = _TEST_TMP / f"p22s5-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def ago(seconds: float) -> datetime:
    return NOW - timedelta(seconds=seconds)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _store(gov_tmp):
    store = gov_store._GovernanceSqliteStore(  # noqa: SLF001
        str(gov_tmp / f"gov-{uuid.uuid4().hex}.sqlite")
    )
    gov_migrations.ensure_schema(store)
    return store


def _controller(store):
    ctl = GovernanceController(
        store, owner_instance=CURRENT_OWNER, retry_delays=(0.01, 0.02)
    )
    ctl.watchdog_tick = 0.01
    return ctl


def _seed_task(
    store,
    task_id,
    *,
    owner=CURRENT_OWNER,
    created_ago=1000.0,
    started_ago=None,
    limits=None,
    status=None,
):
    gov_store.insert_task(
        store,
        TaskRecord(
            task_id=task_id,
            thread_id="th-1",
            owner_instance=owner,
            effective_limits=dict(limits or {}),
            created_at=iso(ago(created_ago)),
            started_at=iso(ago(started_ago)) if started_ago is not None else None,
            status=status or "running",
        ),
    )


def _seed_health(store, task_id, *, owner=PREV_OWNER, beat_ago=60.0):
    hstore.upsert_heartbeat(
        store,
        task_id=task_id,
        thread_id="th-1",
        run_id="run-1",
        owner_instance=owner,
        now_iso=iso(ago(beat_ago)),
    )


def _end_task(store, task_id, *, status="completed"):
    gov_store.terminal_update(
        store,
        task_id,
        expected_version=0,
        status=status,
        terminal_reason=status,
        error_kind=None,
        error=None,
        counters_snapshot=None,
        superseded_by=None,
        underlying_linger_observed=None,
        finished_at=iso(NOW),
    )


def _scanner(ctl, store, monotonic_fn=None, **overrides):
    config = rc.ReclaimConfig(
        heartbeat=hb.HeartbeatConfig(interval_seconds=5.0, jitter_percent=0.0),
        **overrides,
    )
    return scanner_mod.RuntimeHealthScanner(
        ctl,
        config=config,
        clock=lambda: NOW,
        monotonic_fn=monotonic_fn or (lambda: 1000.0),
        rng=__import__("random").Random(0),
        hostname=HOST,
    )


def _advancing_monotonic(step: float = 1.0):
    """推进式 monotonic 时钟（验证 budget 提前退出；不依赖真实时间）。"""
    state = {"t": 1000.0}

    def _fn() -> float:
        state["t"] += step
        return state["t"]

    return _fn


def _event_types(store, task_id):
    rows = store.execute(
        "SELECT event_type FROM governance_events WHERE task_id=%s ORDER BY seq",
        (task_id,),
    )
    return [r["event_type"] for r in rows]


def _task_status(store, task_id):
    rows = store.execute(
        "SELECT status, terminal_reason, version FROM governance_tasks WHERE task_id=%s",
        (task_id,),
    )
    return rows[0]


# ---------------------------------------------------------------------------
# 1 / 2 — evaluate 调用路径
# ---------------------------------------------------------------------------
class TestEvaluateInvocation:
    def test_startup_sweep_calls_reclaim_evaluate(self, gov_tmp, monkeypatch):
        store = _store(gov_tmp)
        ctl = _controller(store)
        _seed_task(store, "t-1", owner=PREV_OWNER, created_ago=1000, started_ago=1000)
        _seed_health(store, "t-1", beat_ago=120)
        calls: list[str] = []
        original = rc.evaluate

        def _counting(candidate, health, context, config):
            calls.append(candidate.task_id)
            return original(candidate, health, context, config)

        monkeypatch.setattr(rc, "evaluate", _counting)
        stats = asyncio.run(_scanner(ctl, store).startup_sweep())
        assert calls == ["t-1"]
        assert stats.scanned == 1
        assert stats.eligible == 1

    def test_periodic_scan_calls_reclaim_evaluate(self, gov_tmp, monkeypatch):
        store = _store(gov_tmp)
        ctl = _controller(store)
        _seed_task(store, "t-1", owner=PREV_OWNER, created_ago=1000, started_ago=1000)
        _seed_health(store, "t-1", beat_ago=120)
        calls: list[str] = []
        original = rc.evaluate

        def _counting(candidate, health, context, config):
            calls.append((candidate.task_id, context.stage))
            return original(candidate, health, context, config)

        monkeypatch.setattr(rc, "evaluate", _counting)
        scanner = _scanner(ctl, store)
        stats = asyncio.run(scanner.scan_once(stage=rc.STAGE_PERIODIC))
        assert calls == [("t-1", rc.STAGE_PERIODIC)]
        assert stats.stage == rc.STAGE_PERIODIC

    def test_periodic_loop_runs_and_stops(self, gov_tmp, monkeypatch):
        store = _store(gov_tmp)
        ctl = _controller(store)
        _seed_task(store, "t-1", owner=PREV_OWNER, created_ago=1000, started_ago=1000)
        _seed_health(store, "t-1", beat_ago=120)
        calls: list[str] = []
        original = rc.evaluate

        def _counting(candidate, health, context, config):
            calls.append(candidate.task_id)
            return original(candidate, health, context, config)

        monkeypatch.setattr(rc, "evaluate", _counting)

        async def scenario():
            scanner = _scanner(
                ctl, store, scan_interval_seconds=1.0, mode=rc.MODE_DETECT
            )
            assert scanner.start_periodic() is True
            await asyncio.sleep(0.05)
            await scanner.stop()
            return scanner

        scanner = asyncio.run(scenario())
        assert len(calls) >= 1
        assert scanner.running is False


# ---------------------------------------------------------------------------
# 3 / 4 / 5 — mode 三态
# ---------------------------------------------------------------------------
class TestModeSemantics:
    def test_off_does_not_run_scanner(self, gov_tmp):
        store = _store(gov_tmp)
        ctl = _controller(store)
        _seed_task(store, "t-1", owner=PREV_OWNER, created_ago=5000, started_ago=5000)
        _seed_health(store, "t-1", beat_ago=5000)
        scanner = _scanner(ctl, store, mode=rc.MODE_OFF)

        async def scenario():
            assert scanner.start_periodic() is False
            stats = await scanner.scan_once(stage=rc.STAGE_STARTUP)
            return stats

        stats = asyncio.run(scenario())
        assert stats.scanned == 0
        assert stats.reclaimed == 0
        assert stats.cleaned == 0
        assert _task_status(store, "t-1")["status"] == "running"  # 未收敛
        assert hstore.get_health(store, "t-1") is not None  # 未清理

    def test_detect_decides_without_terminalize(self, gov_tmp):
        store = _store(gov_tmp)
        ctl = _controller(store)
        _seed_task(store, "t-1", owner=PREV_OWNER, created_ago=5000, started_ago=5000)
        _seed_health(store, "t-1", beat_ago=600)
        scanner = _scanner(ctl, store, mode=rc.MODE_DETECT)
        stats = asyncio.run(scanner.startup_sweep())
        assert stats.eligible == 1
        assert stats.reclaimed == 0
        assert _task_status(store, "t-1")["status"] == "running"  # 无 terminalize
        assert _event_types(store, "t-1") == []  # 无 event publish

    def test_enforce_uses_terminal_funnel_startup_leftover(self, gov_tmp):
        store = _store(gov_tmp)
        ctl = _controller(store)
        _seed_task(store, "t-1", owner=PREV_OWNER, created_ago=5000, started_ago=5000)
        _seed_health(store, "t-1", beat_ago=600)
        stats = asyncio.run(_scanner(ctl, store).startup_sweep())
        assert stats.reclaimed == 1
        row = _task_status(store, "t-1")
        assert row["status"] == "aborted"
        assert row["terminal_reason"] == "aborted"
        assert int(row["version"]) == 1  # CAS 恰一次
        assert _event_types(store, "t-1") == ["task_aborted"]  # funnel 发布

    def test_enforce_uses_terminal_funnel_registry_disconnect(self, gov_tmp):
        store = _store(gov_tmp)
        ctl = _controller(store)
        _seed_task(store, "t-1", created_ago=600, started_ago=600)
        _seed_health(store, "t-1", owner=CURRENT_OWNER, beat_ago=120)
        stats = asyncio.run(_scanner(ctl, store).scan_once(stage=rc.STAGE_PERIODIC))
        assert stats.reclaimed == 1
        row = _task_status(store, "t-1")
        assert row["status"] == "orphan_reclaimed"
        assert _event_types(store, "t-1") == ["task_orphan_reclaimed"]

    def test_heartbeat_stale_path_requires_confirmations(self, gov_tmp):
        """无执行证据路径：连续 2 次扫描（默认 confirmations=2）后收敛。"""
        store = _store(gov_tmp)
        ctl = _controller(store)
        _seed_task(store, "t-1", created_ago=700, limits={"wall_clock_timeout": 100})

        async def scenario():
            scanner = _scanner(ctl, store)
            first = await scanner.scan_once(stage=rc.STAGE_PERIODIC)
            second = await scanner.scan_once(stage=rc.STAGE_PERIODIC)
            return first, second

        first, second = asyncio.run(scenario())
        assert first.eligible == 0
        assert first.skip_reasons.get("awaiting_confirmation") == 1
        assert second.reclaimed == 1
        assert _task_status(store, "t-1")["status"] == "orphan_reclaimed"

    def test_max_per_cycle_cap(self, gov_tmp):
        store = _store(gov_tmp)
        ctl = _controller(store)
        for idx in range(3):
            _seed_task(
                store, f"t-{idx}", owner=PREV_OWNER, created_ago=5000, started_ago=5000
            )
            _seed_health(store, f"t-{idx}", beat_ago=600)
        stats = asyncio.run(_scanner(ctl, store, max_per_cycle=1).startup_sweep())
        assert stats.reclaimed == 1
        assert stats.budget_exceeded is True

    def test_startup_budget_stops_early(self, gov_tmp):
        store = _store(gov_tmp)
        ctl = _controller(store)
        _seed_task(store, "t-1", owner=PREV_OWNER, created_ago=5000, started_ago=5000)
        _seed_health(store, "t-1", beat_ago=600)
        stats = asyncio.run(
            _scanner(
                ctl,
                store,
                monotonic_fn=_advancing_monotonic(),
                startup_budget_seconds=0.0,
            ).startup_sweep()
        )
        assert stats.budget_exceeded is True
        assert stats.reclaimed == 0
        assert _task_status(store, "t-1")["status"] == "running"


# ---------------------------------------------------------------------------
# 6 / 7 — owner 语义
# ---------------------------------------------------------------------------
class TestOwnerSemantics:
    def test_foreign_task_not_processed(self, gov_tmp):
        store = _store(gov_tmp)
        ctl = _controller(store)
        _seed_task(
            store, "t-f", owner=FOREIGN_OWNER, created_ago=9999, started_ago=9999
        )
        _seed_health(store, "t-f", owner=FOREIGN_OWNER, beat_ago=9999)
        stats = asyncio.run(_scanner(ctl, store).startup_sweep())
        assert stats.eligible == 0
        assert stats.skip_reasons.get("foreign_observe_only") == 1
        assert _task_status(store, "t-f")["status"] == "running"
        assert _event_types(store, "t-f") == []

    def test_same_host_prev_healthy_not_killed(self, gov_tmp):
        """D15：rolling / blue-green overlap 中持续心跳的旧进程不得被 startup sweep 误杀。"""
        store = _store(gov_tmp)
        ctl = _controller(store)
        _seed_task(store, "t-old", owner=PREV_OWNER, created_ago=9999, started_ago=9999)
        _seed_health(store, "t-old", beat_ago=5)
        stats = asyncio.run(_scanner(ctl, store).startup_sweep())
        assert stats.reclaimed == 0
        assert stats.skip_reasons.get("same_host_prev_liveness_not_met") == 1
        assert _task_status(store, "t-old")["status"] == "running"
        assert _event_types(store, "t-old") == []


# ---------------------------------------------------------------------------
# 8 / 9 — cancellation 与单实例
# ---------------------------------------------------------------------------
class TestLifecycleSafety:
    def test_stop_is_cancellation_safe_and_idempotent(self, gov_tmp):
        store = _store(gov_tmp)
        ctl = _controller(store)

        async def scenario():
            scanner = _scanner(ctl, store, scan_interval_seconds=1.0)
            assert scanner.start_periodic() is True
            await asyncio.sleep(0)  # 让 loop 起跑
            await scanner.stop()
            await scanner.stop()  # 幂等
            return scanner

        scanner = asyncio.run(scenario())
        assert scanner.running is False

    def test_single_instance_guard(self, gov_tmp):
        store = _store(gov_tmp)
        ctl = _controller(store)

        async def scenario():
            scanner = _scanner(ctl, store, scan_interval_seconds=1.0)
            first = scanner.start_periodic()
            second = scanner.start_periodic()  # 已运行 → 不再创建 task
            await asyncio.sleep(0)
            await scanner.stop()
            return first, second

        first, second = asyncio.run(scenario())
        assert first is True
        assert second is False


# ---------------------------------------------------------------------------
# 10 — cleanup 边界
# ---------------------------------------------------------------------------
class TestCleanup:
    def test_cleanup_removes_health_rows_only(self, gov_tmp):
        store = _store(gov_tmp)
        ctl = _controller(store)
        _seed_task(store, "t-run", created_ago=100)
        _seed_task(store, "t-done", created_ago=500)
        _end_task(store, "t-done", status="completed")
        _seed_health(store, "t-run", owner=CURRENT_OWNER, beat_ago=5)
        _seed_health(store, "t-done", owner=CURRENT_OWNER, beat_ago=400)
        _seed_health(store, "t-orphan", owner=CURRENT_OWNER, beat_ago=400)
        before = _task_status(store, "t-done")

        scanner = _scanner(ctl, store)
        deleted = asyncio.run(scanner.cleanup_health_rows())

        assert deleted == 2
        assert hstore.get_health(store, "t-done") is None
        assert hstore.get_health(store, "t-orphan") is None
        assert hstore.get_health(store, "t-run") is not None  # running 行保留
        after = _task_status(store, "t-done")
        assert after["status"] == before["status"] == "completed"  # lifecycle 未被触碰
        assert int(after["version"]) == int(before["version"])


# ---------------------------------------------------------------------------
# 11 — scanner 故障不影响 controller 主流程
# ---------------------------------------------------------------------------
class TestFailureIsolation:
    def test_scan_failure_is_fail_open(self, gov_tmp, monkeypatch):
        store = _store(gov_tmp)
        ctl = _controller(store)

        def _boom(*_args, **_kwargs):
            raise RuntimeError("health store unavailable")

        monkeypatch.setattr(hstore, "list_running_tasks", _boom)
        stats = asyncio.run(_scanner(ctl, store).scan_once(stage=rc.STAGE_PERIODIC))
        assert stats.errors == 1
        assert stats.last_error is not None

    def test_controller_execution_unaffected_by_scanner_failure(
        self, gov_tmp, monkeypatch
    ):
        store = _store(gov_tmp)
        ctl = _controller(store)

        def _boom(*_args, **_kwargs):
            raise RuntimeError("health store unavailable")

        monkeypatch.setattr(hstore, "list_running_tasks", _boom)
        scanner = _scanner(ctl, store)

        async def _ok():
            return "ok"

        async def scenario():
            await scanner.scan_once(stage=rc.STAGE_PERIODIC)  # 扫描失败（fail-open）
            rec = ctl.create_task("th-1", run_id="run-1")
            result = await ctl.execute(rec.task_id, _ok(), policy={})
            return rec, result

        rec, result = asyncio.run(scenario())
        assert result == "ok"
        assert _task_status(store, rec.task_id)["status"] == "completed"


# ---------------------------------------------------------------------------
# health 派生视图（只读；Spec §10）
# ---------------------------------------------------------------------------
class TestHealthView:
    def test_health_view_states(self, gov_tmp):
        store = _store(gov_tmp)
        ctl = _controller(store)
        _seed_task(store, "t-healthy", created_ago=1000, started_ago=1000)
        _seed_health(store, "t-healthy", owner=CURRENT_OWNER, beat_ago=5)
        _seed_task(store, "t-stale", created_ago=1000, started_ago=1000)
        _seed_health(store, "t-stale", owner=CURRENT_OWNER, beat_ago=600)
        _seed_task(store, "t-expired", created_ago=1000, started_ago=1000)
        _end_task(store, "t-expired", status="completed")
        scanner = _scanner(ctl, store)
        assert scanner.health_view("t-healthy")["health"] == "healthy"
        assert scanner.health_view("t-stale")["health"] == "stale"
        assert scanner.health_view("t-expired")["health"] == "expired"
        assert scanner.health_view("nope") is None
        snapshot = scanner.health_snapshot(limit=10)
        assert {item["task_id"] for item in snapshot} == {"t-healthy", "t-stale"}
