"""P2-2 S1 — Runtime Health Plane 数据层测试（migration 0003 + health_store）。

范围（已实现）：
- S1：迁移应用与版本清单、健康表/索引存在性与 lifecycle 表零污染、心跳 UPSERT 语义、
  health plane 读取、running 候选只读查询、清理候选与删除、时间归一 `parse_ts`；
- S2：`HeartbeatConfig`（env 解析 / 默认值 / 非法回退 / 边界钳制）、确定性相位、
  `HeartbeatWriter`（首拍立即写、窗口内节流、跨窗口与 `force` 写入、`beat_count` 增长、
  fail-open、`forget` 释放、`enabled=False` 不写、lifecycle 行零污染）。

不在本文件（后续 Step）：
- S3：首拍 / watchdog tick 的 controller 接线测试（本文件不修改 controller）；
- S4/S5：owner 三分法、证据谓词、`RuntimeHealthScanner`（startup sweep / periodic / reclaim）；
- S7：PG 门控套件（`tests/test_runtime_health_postgres.py`）与冻结断言更新。

纪律：本文件只验证 health plane 数据访问；不触碰 controller/server；不修改生产代码路径。
"""

import asyncio
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.runtime.governance import health_store as hstore
from app.runtime.governance import heartbeat as hb
from app.runtime.governance import migrations as gov_migrations
from app.runtime.governance import store as gov_store
from app.runtime.governance.models import TaskRecord

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST_TMP = _REPO_ROOT / "_testtmp"

NOW1 = "2026-10-01T00:00:00+00:00"
NOW2 = "2026-10-01T00:00:05+00:00"


@pytest.fixture
def gov_tmp():
    d = _TEST_TMP / f"p22s1-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _store(gov_tmp):
    """fresh sqlite governance store（已 apply 全部迁移，含 0003）。"""
    store = gov_store._GovernanceSqliteStore(  # noqa: SLF001
        str(gov_tmp / f"gov-{uuid.uuid4().hex}.sqlite")
    )
    gov_migrations.ensure_schema(store)
    return store


def _insert_task(
    store,
    task_id="t-1",
    thread_id="th-1",
    owner="hostA-111",
    *,
    effective_limits=None,
):
    gov_store.insert_task(
        store,
        TaskRecord(
            task_id=task_id,
            thread_id=thread_id,
            owner_instance=owner,
            effective_limits=dict(effective_limits or {}),
            created_at=NOW1,
        ),
    )


def _task_row(store, task_id):
    rows = store.execute("SELECT * FROM governance_tasks WHERE task_id=%s", (task_id,))
    return rows[0]


# ---------------------------------------------------------------------------
# migration 0003
# ---------------------------------------------------------------------------
class TestMigration0003:
    def test_versions_include_0003(self, gov_tmp):
        store = _store(gov_tmp)
        assert gov_migrations.applied_migration_versions(store) == [
            "0001",
            "0002",
            "0003",
        ]

    def test_health_table_and_index_exist(self, gov_tmp):
        store = _store(gov_tmp)
        tables = store.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='governance_runtime_health'"
        )
        assert len(tables) == 1
        indexes = {
            r["name"]
            for r in store.execute("PRAGMA index_list(governance_runtime_health)")
        }
        assert "idx_runtime_health_heartbeat" in indexes

    def test_governance_tasks_untouched_by_0003(self, gov_tmp):
        """方案 A：不得给 governance_tasks 加列（冻结字段权威）。"""
        store = _store(gov_tmp)
        cols = {r["name"] for r in store.execute("PRAGMA table_info(governance_tasks)")}
        assert "last_heartbeat_at" not in cols
        # 0001/0002 表族仍存在
        for table in ("governance_tasks", "governance_events", "sessions"):
            rows = store.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=%s",
                (table,),
            )
            assert len(rows) == 1, table


# ---------------------------------------------------------------------------
# heartbeat UPSERT
# ---------------------------------------------------------------------------
class TestUpsertHeartbeat:
    def test_first_beat_inserts_row(self, gov_tmp):
        store = _store(gov_tmp)
        assert (
            hstore.upsert_heartbeat(
                store,
                task_id="t-1",
                thread_id="th-1",
                run_id="run-1",
                owner_instance="hostA-111",
                now_iso=NOW1,
            )
            is True
        )
        row = hstore.get_health(store, "t-1")
        assert row is not None
        assert row["task_id"] == "t-1"
        assert row["thread_id"] == "th-1"
        assert row["run_id"] == "run-1"
        assert row["owner_instance"] == "hostA-111"
        assert row["beat_count"] == 1
        assert row["last_heartbeat_at"] == datetime(2026, 10, 1, tzinfo=timezone.utc)
        assert row["created_at"] == row["updated_at"] == row["last_heartbeat_at"]

    def test_second_beat_increments_and_keeps_created_at(self, gov_tmp):
        store = _store(gov_tmp)
        hstore.upsert_heartbeat(
            store,
            task_id="t-1",
            thread_id="th-1",
            run_id="run-1",
            owner_instance="hostA-111",
            now_iso=NOW1,
        )
        hstore.upsert_heartbeat(
            store,
            task_id="t-1",
            thread_id="th-1",
            run_id="run-1",
            owner_instance="hostA-111",
            now_iso=NOW2,
        )
        row = hstore.get_health(store, "t-1")
        assert row["beat_count"] == 2
        assert row["created_at"] == datetime(2026, 10, 1, tzinfo=timezone.utc)
        assert row["last_heartbeat_at"] == datetime(
            2026, 10, 1, 0, 0, 5, tzinfo=timezone.utc
        )
        assert row["updated_at"] == row["last_heartbeat_at"]

    def test_upsert_refreshes_owner_and_run(self, gov_tmp):
        store = _store(gov_tmp)
        hstore.upsert_heartbeat(
            store,
            task_id="t-1",
            thread_id="th-1",
            run_id="run-1",
            owner_instance="hostA-111",
            now_iso=NOW1,
        )
        hstore.upsert_heartbeat(
            store,
            task_id="t-1",
            thread_id="th-2",
            run_id="run-2",
            owner_instance="hostA-222",
            now_iso=NOW2,
        )
        row = hstore.get_health(store, "t-1")
        assert row["owner_instance"] == "hostA-222"
        assert row["run_id"] == "run-2"
        assert row["thread_id"] == "th-2"

    def test_upsert_does_not_touch_lifecycle_row(self, gov_tmp):
        """LA-1 / invariant I2：心跳只写 health plane。"""
        store = _store(gov_tmp)
        _insert_task(store, task_id="t-1")
        before = _task_row(store, "t-1")
        hstore.upsert_heartbeat(
            store,
            task_id="t-1",
            thread_id="th-1",
            run_id="run-1",
            owner_instance="hostA-111",
            now_iso=NOW1,
        )
        hstore.upsert_heartbeat(
            store,
            task_id="t-1",
            thread_id="th-1",
            run_id="run-1",
            owner_instance="hostA-111",
            now_iso=NOW2,
        )
        after = _task_row(store, "t-1")
        for key in (
            "status",
            "version",
            "terminal_reason",
            "error_kind",
            "error",
            "counters_snapshot",
            "finished_at",
            "started_at",
        ):
            assert after[key] == before[key], key
        assert after["status"] == "running"
        assert int(after["version"]) == 0


# ---------------------------------------------------------------------------
# health plane 读取
# ---------------------------------------------------------------------------
class TestHealthReads:
    def test_get_health_missing_returns_none(self, gov_tmp):
        store = _store(gov_tmp)
        assert hstore.get_health(store, "nope") is None

    def test_list_health_for_partial_hits(self, gov_tmp):
        store = _store(gov_tmp)
        for task_id in ("t-1", "t-2"):
            hstore.upsert_heartbeat(
                store,
                task_id=task_id,
                thread_id="th-1",
                run_id=None,
                owner_instance="hostA-111",
                now_iso=NOW1,
            )
        out = hstore.list_health_for(store, ["t-1", "t-2", "t-missing"])
        assert set(out) == {"t-1", "t-2"}
        assert out["t-1"]["beat_count"] == 1
        assert hstore.list_health_for(store, []) == {}

    def test_list_running_tasks_filters_status_and_decodes_limits(self, gov_tmp):
        store = _store(gov_tmp)
        _insert_task(
            store,
            task_id="t-run",
            effective_limits={"wall_clock_timeout": 1800},
        )
        _insert_task(store, task_id="t-done")
        assert gov_store.terminal_update(
            store,
            "t-done",
            expected_version=0,
            status="completed",
            terminal_reason="completed",
            error_kind=None,
            error=None,
            counters_snapshot=None,
            superseded_by=None,
            underlying_linger_observed=None,
            finished_at=NOW2,
        )
        rows = hstore.list_running_tasks(store, limit=10)
        assert [r["task_id"] for r in rows] == ["t-run"]
        assert rows[0]["thread_id"] == "th-1"
        assert rows[0]["owner_instance"] == "hostA-111"
        assert rows[0]["effective_limits"] == {"wall_clock_timeout": 1800}
        assert rows[0]["created_at"] == datetime(2026, 10, 1, tzinfo=timezone.utc)
        assert rows[0]["started_at"] is None

    def test_list_running_tasks_respects_limit(self, gov_tmp):
        store = _store(gov_tmp)
        for idx in range(3):
            _insert_task(store, task_id=f"t-{idx}")
        assert len(hstore.list_running_tasks(store, limit=2)) == 2


# ---------------------------------------------------------------------------
# 清理（scanner 职责；S1 只验证数据层语义）
# ---------------------------------------------------------------------------
class TestCleanup:
    def test_running_task_health_row_not_candidate(self, gov_tmp):
        store = _store(gov_tmp)
        _insert_task(store, task_id="t-1")
        hstore.upsert_heartbeat(
            store,
            task_id="t-1",
            thread_id="th-1",
            run_id=None,
            owner_instance="hostA-111",
            now_iso=NOW1,
        )
        assert hstore.list_cleanup_candidates(store, limit=10) == []

    def test_terminal_and_orphan_rows_are_candidates_oldest_first(self, gov_tmp):
        store = _store(gov_tmp)
        _insert_task(store, task_id="t-done")
        gov_store.terminal_update(
            store,
            "t-done",
            expected_version=0,
            status="aborted",
            terminal_reason="aborted",
            error_kind=None,
            error="reclaim: test",
            counters_snapshot=None,
            superseded_by=None,
            underlying_linger_observed=None,
            finished_at=NOW2,
        )
        for task_id, ts in (("t-done", NOW1), ("t-orphan", NOW2)):
            hstore.upsert_heartbeat(
                store,
                task_id=task_id,
                thread_id="th-1",
                run_id=None,
                owner_instance="hostA-111",
                now_iso=ts,
            )
        candidates = hstore.list_cleanup_candidates(store, limit=10)
        assert [c["task_id"] for c in candidates] == ["t-done", "t-orphan"]

    def test_delete_health_rows(self, gov_tmp):
        store = _store(gov_tmp)
        for task_id in ("t-1", "t-2"):
            hstore.upsert_heartbeat(
                store,
                task_id=task_id,
                thread_id="th-1",
                run_id=None,
                owner_instance="hostA-111",
                now_iso=NOW1,
            )
        assert hstore.delete_health_rows(store, ["t-1", "t-missing"]) == 1
        assert hstore.get_health(store, "t-1") is None
        assert hstore.get_health(store, "t-2") is not None
        assert hstore.delete_health_rows(store, []) == 0


# ---------------------------------------------------------------------------
# parse_ts（双方言时间归一）
# ---------------------------------------------------------------------------
class TestParseTs:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("2026-10-01T00:00:00+00:00", datetime(2026, 10, 1, tzinfo=timezone.utc)),
            ("2026-10-01T00:00:00Z", datetime(2026, 10, 1, tzinfo=timezone.utc)),
            ("2026-10-01T00:00:00", datetime(2026, 10, 1, tzinfo=timezone.utc)),
            (
                datetime(2026, 10, 1, 0, 0, 0),
                datetime(2026, 10, 1, tzinfo=timezone.utc),
            ),
            (
                datetime(2026, 10, 1, 0, 0, 0, tzinfo=timezone.utc),
                datetime(2026, 10, 1, tzinfo=timezone.utc),
            ),
            (None, None),
            ("", None),
            ("not-a-time", None),
            (123, None),
        ],
    )
    def test_parse_ts(self, value, expected):
        assert hstore.parse_ts(value) == expected


# ---------------------------------------------------------------------------
# S2 — HeartbeatWriter（配置 / 相位 / 节流 / fail-open / forget）
# ---------------------------------------------------------------------------
class _FakeClock:
    """可注入 monotonic 时钟（I10：时间用例不 sleep）。"""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _BrokenStore:
    """transaction() 恒抛错的 store 替身（验证 fail-open）。"""

    def transaction(self):
        raise RuntimeError("health store unavailable")


def _writer(store, clock, *, interval=5.0, jitter=20.0, enabled=True, ts=NOW1):
    """构造 writer：注入 monotonic 时钟 + 固定写入时间戳。"""
    config = hb.HeartbeatConfig(
        interval_seconds=interval, jitter_percent=jitter, enabled=enabled
    )
    return hb.HeartbeatWriter(
        store,
        config=config,
        monotonic_fn=clock,
        utcnow_fn=lambda: datetime.fromisoformat(ts),
    )


class TestHeartbeatConfig:
    def test_defaults_without_env(self):
        cfg = hb.HeartbeatConfig.from_env(env={})
        assert cfg.interval_seconds == 5.0
        assert cfg.jitter_percent == 20.0
        assert cfg.enabled is True

    def test_env_overrides(self):
        cfg = hb.HeartbeatConfig.from_env(
            env={"RUNTIME_HEARTBEAT_INTERVAL": "30", "RUNTIME_HEARTBEAT_JITTER": "5"}
        )
        assert cfg.interval_seconds == 30.0
        assert cfg.jitter_percent == 5.0

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("abc", 5.0),  # 非法 → 默认
            ("", 5.0),  # 空 → 默认
            ("-3", 1.0),  # 越下界 → 钳制到 1s
            ("0", 1.0),  # 0 → 钳制到 1s（禁止 0 窗口）
            ("0.5", 1.0),
            ("1", 1.0),  # 边界：下界合法
            ("9999", 3600.0),  # 越上界 → 钳制
        ],
    )
    def test_interval_bounds_and_fallback(self, raw, expected):
        cfg = hb.HeartbeatConfig.from_env(env={"RUNTIME_HEARTBEAT_INTERVAL": raw})
        assert cfg.interval_seconds == expected

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("abc", 20.0),  # 非法 → 默认
            ("-5", 0.0),  # 越下界 → 0
            ("0", 0.0),  # 关闭抖动
            ("50", 50.0),  # 边界：上界合法
            ("90", 50.0),  # 越上界 → 钳制
        ],
    )
    def test_jitter_bounds_and_fallback(self, raw, expected):
        cfg = hb.HeartbeatConfig.from_env(env={"RUNTIME_HEARTBEAT_JITTER": raw})
        assert cfg.jitter_percent == expected


class TestHeartbeatPhase:
    def test_phase_is_deterministic_for_same_task(self):
        cfg = hb.HeartbeatConfig(interval_seconds=5.0, jitter_percent=20.0)
        other = hb.HeartbeatConfig(interval_seconds=5.0, jitter_percent=20.0)
        assert cfg.phase_seconds("task-a") == other.phase_seconds("task-a")

    def test_phase_within_jitter_span(self):
        cfg = hb.HeartbeatConfig(interval_seconds=10.0, jitter_percent=20.0)
        span = 10.0 * 0.20
        for task_id in ("t-1", "t-2", "t-3", "t-4"):
            assert 0.0 <= cfg.phase_seconds(task_id) <= span

    def test_zero_jitter_yields_zero_phase(self):
        cfg = hb.HeartbeatConfig(interval_seconds=5.0, jitter_percent=0.0)
        assert cfg.phase_seconds("task-a") == 0.0

    def test_phase_spreads_tasks(self):
        cfg = hb.HeartbeatConfig(interval_seconds=5.0, jitter_percent=20.0)
        phases = {cfg.phase_seconds(f"task-{i}") for i in range(20)}
        assert len(phases) > 1


class TestHeartbeatWriter:
    def test_first_beat_writes_immediately(self, gov_tmp):
        store = _store(gov_tmp)
        clock = _FakeClock()
        writer = _writer(store, clock)
        assert (
            writer.beat(
                task_id="t-1",
                thread_id="th-1",
                run_id="run-1",
                owner_instance="hostA-1",
            )
            is True
        )
        row = hstore.get_health(store, "t-1")
        assert row["beat_count"] == 1
        assert row["thread_id"] == "th-1"
        assert writer.local_beats("t-1") == 1
        assert writer.tracked_tasks() == ["t-1"]

    def test_second_beat_throttled_within_window(self, gov_tmp):
        store = _store(gov_tmp)
        clock = _FakeClock()
        writer = _writer(store, clock)
        writer.beat(
            task_id="t-1", thread_id="th-1", run_id=None, owner_instance="hostA-1"
        )
        assert (
            writer.beat(
                task_id="t-1", thread_id="th-1", run_id=None, owner_instance="hostA-1"
            )
            is False
        )
        assert hstore.get_health(store, "t-1")["beat_count"] == 1

    def test_beat_after_window_increments_count(self, gov_tmp):
        store = _store(gov_tmp)
        clock = _FakeClock()
        writer = _writer(store, clock, interval=5.0, jitter=20.0)
        writer.beat(
            task_id="t-1", thread_id="th-1", run_id=None, owner_instance="hostA-1"
        )
        phase = writer.config.phase_seconds("t-1")
        clock.advance(5.0 + phase + 0.001)
        assert (
            writer.beat(
                task_id="t-1", thread_id="th-1", run_id=None, owner_instance="hostA-1"
            )
            is True
        )
        assert hstore.get_health(store, "t-1")["beat_count"] == 2
        assert writer.local_beats("t-1") == 2

    def test_force_bypasses_throttle(self, gov_tmp):
        store = _store(gov_tmp)
        clock = _FakeClock()
        writer = _writer(store, clock)
        writer.beat(
            task_id="t-1", thread_id="th-1", run_id=None, owner_instance="hostA-1"
        )
        assert (
            writer.beat(
                task_id="t-1",
                thread_id="th-1",
                run_id=None,
                owner_instance="hostA-1",
                force=True,
            )
            is True
        )
        assert hstore.get_health(store, "t-1")["beat_count"] == 2

    def test_deterministic_phase_across_writers(self, gov_tmp):
        store = _store(gov_tmp)
        clock_a, clock_b = _FakeClock(), _FakeClock()
        writer_a = _writer(store, clock_a)
        writer_b = _writer(store, clock_b)
        assert writer_a.config.phase_seconds("t-x") == writer_b.config.phase_seconds(
            "t-x"
        )
        # 同一时刻、同一 task：两者节流决策一致
        writer_a.beat(
            task_id="t-x", thread_id="th-1", run_id=None, owner_instance="hostA-1"
        )
        writer_b.beat(
            task_id="t-x", thread_id="th-1", run_id=None, owner_instance="hostA-1"
        )
        clock_a.advance(1.0)
        clock_b.advance(1.0)
        assert writer_a.beat(
            task_id="t-x", thread_id="th-1", run_id=None, owner_instance="hostA-1"
        ) == writer_b.beat(
            task_id="t-x", thread_id="th-1", run_id=None, owner_instance="hostA-1"
        )

    def test_fail_open_on_store_error(self, gov_tmp):
        clock = _FakeClock()
        writer = _writer(_BrokenStore(), clock)
        assert (
            writer.beat(
                task_id="t-1", thread_id="th-1", run_id=None, owner_instance="hostA-1"
            )
            is False
        )  # 不抛异常
        assert writer.write_failures == 1
        assert writer.tracked_tasks() == []  # 失败不建 throttle 状态

    def test_disabled_writer_writes_nothing(self, gov_tmp):
        store = _store(gov_tmp)
        clock = _FakeClock()
        writer = _writer(store, clock, enabled=False)
        assert (
            writer.beat(
                task_id="t-1", thread_id="th-1", run_id=None, owner_instance="hostA-1"
            )
            is False
        )
        assert hstore.get_health(store, "t-1") is None
        assert writer.tracked_tasks() == []

    def test_forget_releases_throttle_state(self, gov_tmp):
        store = _store(gov_tmp)
        clock = _FakeClock()
        writer = _writer(store, clock)
        writer.beat(
            task_id="t-1", thread_id="th-1", run_id=None, owner_instance="hostA-1"
        )
        assert (
            writer.beat(
                task_id="t-1", thread_id="th-1", run_id=None, owner_instance="hostA-1"
            )
            is False
        )
        writer.forget("t-1")
        assert writer.tracked_tasks() == []
        # 释放后视为新执行：立即写（不改 DB 行，只新增 beat）
        assert (
            writer.beat(
                task_id="t-1", thread_id="th-1", run_id=None, owner_instance="hostA-1"
            )
            is True
        )
        assert hstore.get_health(store, "t-1")["beat_count"] == 2
        writer.forget("t-missing")  # 幂等

    def test_beats_do_not_touch_lifecycle_row(self, gov_tmp):
        """LA-1 / I2：writer 只经 health_store 写 health plane。"""
        store = _store(gov_tmp)
        _insert_task(store, task_id="t-1")
        before = _task_row(store, "t-1")
        clock = _FakeClock()
        writer = _writer(store, clock)
        writer.beat(
            task_id="t-1", thread_id="th-1", run_id=None, owner_instance="hostA-1"
        )
        clock.advance(100.0)
        writer.beat(
            task_id="t-1", thread_id="th-1", run_id=None, owner_instance="hostA-1"
        )
        after = _task_row(store, "t-1")
        for key in (
            "status",
            "version",
            "terminal_reason",
            "finished_at",
            "started_at",
        ):
            assert after[key] == before[key], key


# ---------------------------------------------------------------------------
# S3 — controller / lifespan 接线（首拍 / watchdog beat / forget / fail-open）
# ---------------------------------------------------------------------------
class _RecordingWriter:
    """记录调用的 writer 替身（验证 controller 接线时序，不触 DB）。"""

    def __init__(self, result: bool = True) -> None:
        self.calls: list[dict[str, object]] = []
        self.forgotten: list[str] = []
        self.result = result

    def beat(self, *, task_id, thread_id, run_id, owner_instance, force=False):
        self.calls.append(
            {
                "task_id": task_id,
                "thread_id": thread_id,
                "run_id": run_id,
                "owner_instance": owner_instance,
                "force": force,
            }
        )
        return self.result

    def forget(self, task_id):
        self.forgotten.append(task_id)


def _controller(gov_tmp, *, watchdog_tick=0.01):
    from app.runtime.governance.controller import GovernanceController

    store = gov_store._GovernanceSqliteStore(  # noqa: SLF001
        str(gov_tmp / f"gov-{uuid.uuid4().hex}.sqlite")
    )
    gov_migrations.ensure_schema(store)
    ctl = GovernanceController(
        store, owner_instance="hostA-111", retry_delays=(0.01, 0.02)
    )
    ctl.watchdog_tick = watchdog_tick
    return store, ctl


async def _ok_coro(value="ok"):
    return value


async def _sleep_coro(seconds):
    await asyncio.sleep(seconds)
    return "done"


class TestControllerHeartbeatWiring:
    def test_submit_path_does_not_write_health_row(self, gov_tmp, monkeypatch):
        """D13：submit_task 只建 TaskRecord，不写首拍（首拍属执行起点）。"""
        from app.runtime.governance import service as gov_service

        store, ctl = _controller(gov_tmp)
        writer = _RecordingWriter()
        ctl.heartbeat_writer = writer

        async def _fake_runner(_query, _session_id):
            return "ok"

        # 注入 runner 替身：避免 submit 触达真实 agent/LLM 导入链（无凭据环境）
        monkeypatch.setattr(gov_service, "run_deep_agent", _fake_runner)

        executed: list[str] = []

        async def _stub_execute(_task_id, coro, *, policy=None):
            coro.close()  # 替身不执行底层 coroutine，显式关闭避免未 await 警告
            executed.append(_task_id)
            return "ok"

        monkeypatch.setattr(ctl, "execute", _stub_execute)

        async def scenario():
            record, task = gov_service.submit_task(
                ctl, thread_id="th-s", query="q", policy=None
            )
            await task
            return record, task

        record, _task = asyncio.run(scenario())
        assert executed == [record.task_id]  # 已受理并进入 execute（替身）
        assert writer.calls == []  # submit 路径零心跳
        assert hstore.get_health(store, record.task_id) is None

    def test_first_beat_after_handle_registration_with_force(self, gov_tmp):
        store, ctl = _controller(gov_tmp)
        writer = _RecordingWriter()
        ctl.heartbeat_writer = writer
        rec = ctl.create_task("th-1", run_id="run-1")

        async def scenario():
            assert await ctl.execute(rec.task_id, _ok_coro(), policy={}) == "ok"

        asyncio.run(scenario())
        assert len(writer.calls) >= 1
        first = writer.calls[0]
        assert first["task_id"] == rec.task_id
        assert first["thread_id"] == "th-1"
        assert first["run_id"] == "run-1"
        assert first["owner_instance"] == "hostA-111"
        assert first["force"] is True  # 首拍不等待节流窗口

    def test_first_beat_writes_row_without_waiting_window(self, gov_tmp):
        """首拍 force=True：即使 interval 远大于执行时长也立即落库。"""
        store, ctl = _controller(gov_tmp)
        ctl.heartbeat_writer = hb.HeartbeatWriter(
            store,
            config=hb.HeartbeatConfig(interval_seconds=3600.0, jitter_percent=0.0),
            utcnow_fn=lambda: datetime.fromisoformat(NOW1),
        )
        rec = ctl.create_task("th-1", run_id="run-1")

        async def scenario():
            await ctl.execute(rec.task_id, _ok_coro(), policy={})

        asyncio.run(scenario())
        row = hstore.get_health(store, rec.task_id)
        assert row is not None
        assert row["beat_count"] == 1

    def test_watchdog_ticks_beat_again(self, gov_tmp):
        """watchdog 每 tick 追加心跳（非首拍 force=False）。"""
        store, ctl = _controller(gov_tmp, watchdog_tick=0.01)
        writer = _RecordingWriter()
        ctl.heartbeat_writer = writer
        rec = ctl.create_task("th-1", run_id="run-1")

        async def scenario():
            await ctl.execute(rec.task_id, _sleep_coro(0.12), policy={})

        asyncio.run(scenario())
        assert len(writer.calls) >= 2, writer.calls
        assert writer.calls[0]["force"] is True
        assert any(call["force"] is False for call in writer.calls[1:])

    def test_watchdog_beat_advances_db_beat_count(self, gov_tmp):
        """真实 writer + 真实 store：运行期心跳推进 beat_count。"""
        store, ctl = _controller(gov_tmp, watchdog_tick=0.01)
        ctl.heartbeat_writer = hb.HeartbeatWriter(
            store,
            config=hb.HeartbeatConfig(interval_seconds=1.0, jitter_percent=0.0),
            utcnow_fn=lambda: datetime.fromisoformat(NOW1),
        )
        rec = ctl.create_task("th-1", run_id="run-1")

        async def scenario():
            await ctl.execute(rec.task_id, _sleep_coro(0.12), policy={})

        asyncio.run(scenario())
        # interval=1s 时运行期节流窗口未到 → 仅首拍；此处断言「至少首拍已落库且执行正常结束」
        row = hstore.get_health(store, rec.task_id)
        assert row is not None and row["beat_count"] >= 1
        assert ctl.get_task(rec.task_id).status == "completed"

    def test_forget_on_execution_exit(self, gov_tmp):
        store, ctl = _controller(gov_tmp)
        writer = _RecordingWriter()
        ctl.heartbeat_writer = writer
        rec = ctl.create_task("th-1", run_id="run-1")

        async def scenario():
            await ctl.execute(rec.task_id, _ok_coro(), policy={})

        asyncio.run(scenario())
        assert writer.forgotten == [rec.task_id]
        assert ctl.active_handle_ids() == []

    def test_forget_on_failure_exit(self, gov_tmp):
        store, ctl = _controller(gov_tmp)
        writer = _RecordingWriter()
        ctl.heartbeat_writer = writer
        rec = ctl.create_task("th-1", run_id="run-1")

        async def _boom():
            raise RuntimeError("agent boom")

        async def scenario():
            with pytest.raises(RuntimeError):
                await ctl.execute(rec.task_id, _boom(), policy={})

        asyncio.run(scenario())
        assert writer.forgotten == [rec.task_id]
        assert ctl.get_task(rec.task_id).status == "failed"

    def test_heartbeat_failure_is_fail_open(self, gov_tmp):
        """health store 故障：心跳失败但不抛异常，控制流不受影响。"""
        store, ctl = _controller(gov_tmp)
        writer = hb.HeartbeatWriter(_BrokenStore())
        ctl.heartbeat_writer = writer
        rec = ctl.create_task("th-1", run_id="run-1")

        async def scenario():
            assert await ctl.execute(rec.task_id, _ok_coro(), policy={}) == "ok"

        asyncio.run(scenario())
        assert writer.write_failures >= 1
        assert ctl.get_task(rec.task_id).status == "completed"
        assert hstore.get_health(store, rec.task_id) is None  # 无写入

    def test_bare_execution_writes_no_heartbeat(self, gov_tmp):
        """bare（policy=None，dev 路径）不写心跳。"""
        store, ctl = _controller(gov_tmp)
        writer = _RecordingWriter()
        ctl.heartbeat_writer = writer
        rec = ctl.create_task("th-bare")

        async def scenario():
            assert await ctl.execute(rec.task_id, _ok_coro()) == "ok"

        asyncio.run(scenario())
        assert writer.calls == []
        assert writer.forgotten == []
        assert hstore.get_health(store, rec.task_id) is None

    def test_governed_run_health_row_is_separate_from_lifecycle(self, gov_tmp):
        """health 行与 lifecycle 行分离；lifecycle 字段按既有语义收敛、未被心跳污染。"""
        store, ctl = _controller(gov_tmp)
        ctl.heartbeat_writer = hb.HeartbeatWriter(
            store,
            config=hb.HeartbeatConfig(interval_seconds=3600.0, jitter_percent=0.0),
            utcnow_fn=lambda: datetime.fromisoformat(NOW1),
        )
        rec = ctl.create_task("th-1", run_id="run-1")

        async def scenario():
            await ctl.execute(rec.task_id, _ok_coro(), policy={})

        asyncio.run(scenario())
        assert hstore.get_health(store, rec.task_id) is not None
        row = _task_row(store, rec.task_id)
        assert row["status"] == "completed"
        assert row["terminal_reason"] == "completed"
        assert int(row["version"]) == 1
        assert row["finished_at"] is not None
        # governance_tasks DDL 未被 0003 改动（方案 A 冻结字段权威）
        cols = {r["name"] for r in store.execute("PRAGMA table_info(governance_tasks)")}
        assert "last_heartbeat_at" not in cols
