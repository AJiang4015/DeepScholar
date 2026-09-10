"""P2-2 S6 — server lifespan 装配测试（health writer + scanner 接线 / mode / fail-open / 顺序）。

覆盖 S6 要求 1–16：
- Startup：建 scanner、startup sweep 在 yield 前、bounded 配置、失败 fail-open、startup 不用确认计数；
- Periodic：启动、shutdown stop/cancel/await、无 task 泄漏、单实例；
- Mode：off 不装配、detect 不 terminalize、enforce 经既有 funnel 收敛；
- Integration：复用 controller singleton、writer 与 scanner 同一 store、health 失败不影响执行、
  既有 checkpoint/prewarm/yield/shutdown 顺序不变。

纪律：不修改任何 frozen lifecycle 语义；测试通过既有 controller/funnel 驱动。
"""

import asyncio
import shutil
import socket
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI

from app.api import server as srv
from app.runtime.governance import controller as controller_mod
from app.runtime.governance import health_store as hstore
from app.runtime.governance import reclaim as rc
from app.runtime.governance import store as gov_store
from app.runtime.governance.models import TaskRecord

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST_TMP = _REPO_ROOT / "_testtmp"


@pytest.fixture
def gov_tmp():
    d = _TEST_TMP / f"p22s6-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def gov_env(gov_tmp, monkeypatch):
    """隔离 governance store/controller 单例（tmp sqlite）；测试后重置。"""
    monkeypatch.setenv("GOVERNANCE_BACKEND", "sqlite")
    monkeypatch.setenv("GOVERNANCE_DB", str(gov_tmp / "gov.sqlite"))
    monkeypatch.delenv("GOVERNANCE_DSN", raising=False)
    gov_store.reset_store()
    controller_mod.reset_controller()
    yield
    gov_store.reset_store()
    controller_mod.reset_controller()


def ago(seconds: float) -> datetime:
    """相对**真实 UTC now**（生产路径使用真实时钟；不注入假时钟）。"""
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _prev_owner() -> str:
    """同 host、异 pid ⇒ SAME_HOST_PREV（与 `_default_owner_instance()` 同格式）。"""
    return f"{socket.gethostname()}-1"


def _ctl():
    ctl = srv.get_governance_controller()
    assert ctl is not None
    return ctl


def _seed_leftover(store, task_id="t-old", *, owner=None, beat_ago=600.0):
    """SAME_HOST_PREV 上一世代遗留（心跳已停）。"""
    owner = owner or _prev_owner()
    gov_store.insert_task(
        store,
        TaskRecord(
            task_id=task_id,
            thread_id="th-1",
            owner_instance=owner,
            created_at=iso(ago(5000)),
            started_at=iso(ago(5000)),
        ),
    )
    hstore.upsert_heartbeat(
        store,
        task_id=task_id,
        thread_id="th-1",
        run_id="run-1",
        owner_instance=owner,
        now_iso=iso(ago(beat_ago)),
    )


def _seed_never_started(store, task_id="t-never", limits=None):
    """CURRENT 任务：无 health 行、无 started_at、created 超 deadline（policy 超期路径）。"""
    gov_store.insert_task(
        store,
        TaskRecord(
            task_id=task_id,
            thread_id="th-1",
            owner_instance=_ctl().owner_instance,
            effective_limits=dict(limits or {"wall_clock_timeout": 100}),
            created_at=iso(ago(700)),
        ),
    )


def _status(store, task_id):
    rows = store.execute(
        "SELECT status, terminal_reason, version FROM governance_tasks WHERE task_id=%s",
        (task_id,),
    )
    return rows[0]


def _events(store, task_id):
    rows = store.execute(
        "SELECT event_type FROM governance_events WHERE task_id=%s ORDER BY seq",
        (task_id,),
    )
    return [r["event_type"] for r in rows]


def _pending_tasks():
    current = asyncio.current_task()
    return [t for t in asyncio.all_tasks() if t is not current and not t.done()]


# ---------------------------------------------------------------------------
# lifespan 顺序 / fail-open（用 stub 驱动，不依赖真实 checkpoint/agent 凭据）
# ---------------------------------------------------------------------------
class TestLifespanWiring:
    def _harness(self, monkeypatch, order, *, start=None, stop=None):
        async def _checkpoint_init():
            order.append("checkpoint_init")

        async def _checkpoint_shutdown():
            order.append("checkpoint_shutdown")

        async def _prewarm():
            order.append("prewarm")

        async def _start():
            order.append("runtime_health_start")
            return start() if start else None

        async def _stop(_scanner):
            order.append("runtime_health_stop")
            if stop:
                stop()

        monkeypatch.setattr(srv, "init_checkpoint_lifespan", _checkpoint_init)
        monkeypatch.setattr(srv, "shutdown_checkpoint_lifespan", _checkpoint_shutdown)
        monkeypatch.setitem(
            sys.modules,
            "app.agent.main_agent",
            types.SimpleNamespace(get_main_agent=_prewarm),
        )
        monkeypatch.setattr(srv, "_start_runtime_health", _start)
        monkeypatch.setattr(srv, "_stop_runtime_health", _stop)

    def test_existing_lifecycle_order_preserved(self, monkeypatch):
        order: list[str] = []
        self._harness(monkeypatch, order)

        async def scenario():
            async with srv.lifespan(FastAPI()):
                order.append("yield")
            return order

        result = asyncio.run(scenario())
        # 既有顺序（checkpoint init → prewarm → yield → checkpoint shutdown）不变，
        # runtime health 装配插在 prewarm 之后、yield 之前；停止在 yield 之后、checkpoint shutdown 之前。
        assert result == [
            "checkpoint_init",
            "prewarm",
            "runtime_health_start",
            "yield",
            "runtime_health_stop",
            "checkpoint_shutdown",
        ]

    def test_startup_failure_is_fail_open(self, monkeypatch):
        order: list[str] = []

        def _boom():
            raise RuntimeError("governance store unavailable")

        self._harness(monkeypatch, order, start=_boom)
        reached_yield = False

        async def scenario():
            nonlocal reached_yield
            async with srv.lifespan(FastAPI()):
                reached_yield = True
            return order

        result = asyncio.run(scenario())
        assert reached_yield is True  # 服务继续启动（D7：不 fail-fast）
        assert "checkpoint_shutdown" in result

    def test_stop_failure_does_not_block_shutdown(self, monkeypatch):
        order: list[str] = []

        def _boom():
            raise RuntimeError("scanner stop failed")

        self._harness(monkeypatch, order, stop=_boom)

        async def scenario():
            async with srv.lifespan(FastAPI()):
                pass
            return order

        result = asyncio.run(scenario())
        assert result[-1] == "checkpoint_shutdown"  # 既有 shutdown 顺序未被阻塞


# ---------------------------------------------------------------------------
# 真实装配（helper + 单例 controller + tmp governance store）
# ---------------------------------------------------------------------------
class TestRuntimeHealthAssembly:
    def test_enforce_startup_sweep_before_yield_reclaims_leftover(
        self, gov_env, monkeypatch
    ):
        monkeypatch.setenv("RUNTIME_RECLAIM_MODE", "enforce")
        ctl = _ctl()
        _seed_leftover(ctl._store)  # noqa: SLF001

        async def scenario():
            scanner = (
                await srv._start_runtime_health()
            )  # 内含 startup sweep（yield 前语义）
            assert scanner is not None
            # scanner 必须绑定既有 singleton（禁止新建 controller）
            assert scanner._controller is srv.get_governance_controller()  # noqa: SLF001
            assert scanner._controller is ctl  # noqa: SLF001
            # writer 与 scanner 使用同一 governance store
            assert ctl.heartbeat_writer is not None
            assert ctl.heartbeat_writer._store is ctl._store  # noqa: SLF001
            assert scanner._store is ctl._store  # noqa: SLF001
            # bounded 配置（batch ≤ 200 / 预算 ≤ 2s，Spec 冻结默认）
            assert scanner.config.scan_batch_limit == 200
            assert scanner.config.startup_budget_seconds == 2.0
            await srv._stop_runtime_health(scanner)
            return scanner

        scanner = asyncio.run(scenario())
        assert _status(ctl._store, "t-old")["status"] == "aborted"  # noqa: SLF001
        assert _events(ctl._store, "t-old") == ["task_aborted"]  # noqa: SLF001
        assert scanner.last_stats.reclaimed == 1

    def test_startup_sweep_does_not_use_confirmation_count(self, gov_env, monkeypatch):
        """startup 为单遍：`CURRENT` + deadline 证据即可收敛（无需双阶段确认）。"""
        monkeypatch.setenv("RUNTIME_RECLAIM_MODE", "enforce")
        ctl = _ctl()
        _seed_never_started(ctl._store)  # noqa: SLF001

        async def scenario():
            scanner = await srv._start_runtime_health()
            await srv._stop_runtime_health(scanner)
            return scanner

        scanner = asyncio.run(scenario())
        assert scanner.last_stats.reclaimed == 1
        assert _status(ctl._store, "t-never")["status"] == "aborted"  # noqa: SLF001

    def test_detect_mode_does_not_terminalize(self, gov_env, monkeypatch):
        monkeypatch.setenv("RUNTIME_RECLAIM_MODE", "detect")
        ctl = _ctl()
        _seed_leftover(ctl._store)  # noqa: SLF001

        async def scenario():
            scanner = await srv._start_runtime_health()
            await srv._stop_runtime_health(scanner)
            return scanner

        scanner = asyncio.run(scenario())
        assert scanner.config.mode == rc.MODE_DETECT
        assert scanner.last_stats.eligible == 1
        assert scanner.last_stats.reclaimed == 0
        assert _status(ctl._store, "t-old")["status"] == "running"  # noqa: SLF001
        assert _events(ctl._store, "t-old") == []  # noqa: SLF001

    def test_off_mode_assembles_nothing(self, gov_env, monkeypatch):
        monkeypatch.setenv("RUNTIME_RECLAIM_MODE", "off")
        ctl = _ctl()
        _seed_leftover(ctl._store)  # noqa: SLF001

        async def scenario():
            return await srv._start_runtime_health()

        scanner = asyncio.run(scenario())
        assert scanner is None  # 不创建 scanner
        assert ctl.heartbeat_writer is None  # 不写心跳
        assert _status(ctl._store, "t-old")["status"] == "running"  # noqa: SLF001
        assert hstore.get_health(ctl._store, "t-old") is not None  # noqa: SLF001 未清理

    def test_periodic_start_stop_no_leak(self, gov_env, monkeypatch):
        monkeypatch.setenv("RUNTIME_RECLAIM_MODE", "detect")
        _ctl()  # 确保 controller singleton 就绪

        async def scenario():
            scanner = await srv._start_runtime_health()
            assert scanner.start_periodic() is True
            assert scanner.start_periodic() is False  # 单实例保护
            await asyncio.sleep(0.02)  # 让 periodic 至少跑一轮
            await srv._stop_runtime_health(scanner)
            assert scanner.running is False
            await srv._stop_runtime_health(scanner)  # 幂等
            return _pending_tasks()

        leaked = asyncio.run(scenario())
        assert leaked == []  # 无 background task 泄漏

    def test_health_scan_failure_does_not_affect_execution(self, gov_env, monkeypatch):
        monkeypatch.setenv("RUNTIME_RECLAIM_MODE", "enforce")
        ctl = _ctl()

        def _boom(*_args, **_kwargs):
            raise RuntimeError("health store unavailable")

        monkeypatch.setattr(hstore, "list_running_tasks", _boom)

        async def _ok():
            return "ok"

        async def scenario():
            scanner = await srv._start_runtime_health()  # 扫描失败 fail-open
            await srv._stop_runtime_health(scanner)
            rec = ctl.create_task("th-1", run_id="run-1")
            result = await ctl.execute(rec.task_id, _ok(), policy={})
            return scanner, rec, result

        scanner, rec, result = asyncio.run(scenario())
        assert scanner.last_stats.errors >= 1  # 失败被记录
        assert result == "ok"  # controller execution 不受影响
        assert _status(ctl._store, rec.task_id)["status"] == "completed"  # noqa: SLF001

    def test_bounded_config_from_env(self, gov_env, monkeypatch):
        monkeypatch.setenv("RUNTIME_RECLAIM_MODE", "detect")
        monkeypatch.setenv("RUNTIME_SCAN_BATCH_LIMIT", "1")
        monkeypatch.setenv("RUNTIME_STARTUP_SWEEP_BUDGET", "0")
        ctl = _ctl()
        _seed_leftover(ctl._store, "t-1")  # noqa: SLF001
        _seed_leftover(ctl._store, "t-2")  # noqa: SLF001

        async def scenario():
            scanner = await srv._start_runtime_health()
            await srv._stop_runtime_health(scanner)
            return scanner

        scanner = asyncio.run(scenario())
        assert scanner.config.scan_batch_limit == 1
        assert scanner.config.startup_budget_seconds == 0.0
        assert scanner.last_stats.scanned <= 1  # 预算/批量生效
