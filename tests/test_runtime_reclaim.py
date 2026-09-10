"""P2-2 S4 — reclaim 纯逻辑测试（owner 分类 / 证据谓词 / 三路决策 / 终态映射 / 模式语义）。

范围（S4）：只验证**决策纯逻辑**；不涉及 controller / server / scanner / funnel / DB 写入。
时间全部由注入的 `now` 驱动（无 sleep）。

关键安全性质（D15）：仍在心跳的 `SAME_HOST_PREV` 旧进程（rolling / blue-green overlap）
**不得**被判为可 reclaim。
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.runtime.governance import reclaim as rc
from app.runtime.governance.heartbeat import HeartbeatConfig
from app.runtime.governance.models import TerminalReason

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
HOST = "hostA"
CURRENT_OWNER = "hostA-1000"
PREV_OWNER = "hostA-999"  # 同 host、异 pid ⇒ SAME_HOST_PREV


def ago(seconds: float) -> datetime:
    return NOW - timedelta(seconds=seconds)


def cfg(**overrides) -> rc.ReclaimConfig:
    """默认配置：interval=5s ⇒ liveness 阈值 = max(15, 15) = 15s；grace=30s；确认 2 次。"""
    base = {
        "heartbeat": HeartbeatConfig(
            interval_seconds=5.0, jitter_percent=0.0, enabled=True
        )
    }
    base.update(overrides)
    return rc.ReclaimConfig(**base)


def candidate(
    *,
    task_id="t-1",
    owner=CURRENT_OWNER,
    created_ago=1000.0,
    started_ago=None,
    limits=None,
    status=None,
) -> rc.CandidateState:
    return rc.CandidateState(
        task_id=task_id,
        owner_instance=owner,
        created_at=ago(created_ago),
        started_at=ago(started_ago) if started_ago is not None else None,
        thread_id="th-1",
        run_id="run-1",
        effective_limits=dict(limits or {}),
        status=status,
    )


def health(*, beat_ago=None):
    if beat_ago is None:
        return None
    return {"task_id": "t-1", "last_heartbeat_at": ago(beat_ago), "beat_count": 3}


def ctx(
    *,
    stage=rc.STAGE_PERIODIC,
    handle=False,
    decision=False,
    consecutive=0,
    owner=CURRENT_OWNER,
    hostname=HOST,
) -> rc.DecisionContext:
    return rc.DecisionContext(
        now=NOW,
        current_owner=owner,
        hostname=hostname,
        has_active_handle=handle,
        has_memory_decision=decision,
        stage=stage,
        consecutive_stale=consecutive,
    )


# ---------------------------------------------------------------------------
# owner classification（三分法；不得压缩为二分类）
# ---------------------------------------------------------------------------
class TestOwnerClassification:
    def test_current_owner(self):
        assert (
            rc.classify_owner(CURRENT_OWNER, current_owner=CURRENT_OWNER, hostname=HOST)
            == rc.OWNER_CURRENT
        )

    def test_same_host_prev(self):
        assert (
            rc.classify_owner(PREV_OWNER, current_owner=CURRENT_OWNER, hostname=HOST)
            == rc.OWNER_SAME_HOST_PREV
        )

    def test_foreign_other_host(self):
        assert (
            rc.classify_owner("hostB-777", current_owner=CURRENT_OWNER, hostname=HOST)
            == rc.OWNER_FOREIGN
        )

    @pytest.mark.parametrize("owner", ["weird", "hostA-notapid", "hostA-", "", None])
    def test_unparseable_is_foreign(self, owner):
        assert (
            rc.classify_owner(owner, current_owner=CURRENT_OWNER, hostname=HOST)
            == rc.OWNER_FOREIGN
        )

    def test_custom_env_owner_equals_current_is_current(self):
        # GOVERNANCE_OWNER_INSTANCE 固定为不可解析字符串时：相等 ⇒ CURRENT（跨重启不变场景）
        assert (
            rc.classify_owner(
                "custom-owner", current_owner="custom-owner", hostname=HOST
            )
            == rc.OWNER_CURRENT
        )

    def test_hostname_with_dash(self):
        assert (
            rc.classify_owner(
                "my-host-42", current_owner="my-host-7", hostname="my-host"
            )
            == rc.OWNER_SAME_HOST_PREV
        )


# ---------------------------------------------------------------------------
# 证据谓词
# ---------------------------------------------------------------------------
class TestEvidencePredicates:
    def test_liveness_fresh_heartbeat_not_satisfied(self):
        ev = rc.liveness_evidence(
            now=NOW,
            created_at=ago(1000),
            started_at=ago(1000),
            health=health(beat_ago=10),
            threshold_seconds=15.0,
        )
        assert ev.satisfied is False
        assert ev.source == "heartbeat"
        assert ev.age_seconds == pytest.approx(10.0)

    def test_liveness_stale_heartbeat_satisfied(self):
        ev = rc.liveness_evidence(
            now=NOW,
            created_at=ago(1000),
            started_at=ago(1000),
            health=health(beat_ago=16),
            threshold_seconds=15.0,
        )
        assert ev.satisfied is True

    def test_liveness_boundary_strictly_greater(self):
        boundary = rc.liveness_evidence(
            now=NOW,
            created_at=ago(1000),
            started_at=None,
            health=health(beat_ago=15),
            threshold_seconds=15.0,
        )
        beyond = rc.liveness_evidence(
            now=NOW,
            created_at=ago(1000),
            started_at=None,
            health=health(beat_ago=15.001),
            threshold_seconds=15.0,
        )
        assert boundary.satisfied is False
        assert beyond.satisfied is True

    def test_liveness_legacy_fallback_started_at(self):
        # 无 health row：参照点 = max(started_at, created_at)；started_at 更晚 ⇒ 用它
        ev = rc.liveness_evidence(
            now=NOW,
            created_at=ago(30),
            started_at=ago(20),
            health=None,
            threshold_seconds=15.0,
        )
        assert ev.satisfied is True
        assert ev.source == "started_at"
        assert ev.age_seconds == pytest.approx(20.0)

    def test_liveness_legacy_reference_is_max_of_started_and_created(self):
        # created_at 比 started_at 更新（异常数据）⇒ max 取 created_at（更保守，不 stale）
        ev = rc.liveness_evidence(
            now=NOW,
            created_at=ago(5),
            started_at=ago(20),
            health=None,
            threshold_seconds=15.0,
        )
        assert ev.source == "created_at"
        assert ev.satisfied is False

    def test_liveness_legacy_fallback_created_at(self):
        ev = rc.liveness_evidence(
            now=NOW,
            created_at=ago(30),
            started_at=None,
            health=None,
            threshold_seconds=15.0,
        )
        assert ev.satisfied is True
        assert ev.source == "created_at"

    def test_liveness_without_any_timestamp_not_satisfied(self):
        ev = rc.liveness_evidence(
            now=NOW,
            created_at=None,
            started_at=None,
            health=None,
            threshold_seconds=15.0,
        )
        assert ev.satisfied is False
        assert ev.source is None

    def test_liveness_future_heartbeat_clamped_to_zero(self):
        ev = rc.liveness_evidence(
            now=NOW,
            created_at=ago(1000),
            started_at=None,
            health={"last_heartbeat_at": NOW + timedelta(seconds=30)},
            threshold_seconds=15.0,
        )
        assert ev.age_seconds == 0.0
        assert ev.satisfied is False

    def test_deadline_uses_per_task_wall_clock(self):
        # wall_clock=100 + grace=30 → 阈值 130s
        stale = rc.deadline_evidence(
            now=NOW,
            created_at=ago(200),
            started_at=None,
            health=None,
            effective_limits={"wall_clock_timeout": 100},
            grace_seconds=30.0,
            fallback_wall_clock_seconds=600.0,
        )
        boundary = rc.deadline_evidence(
            now=NOW,
            created_at=ago(130),
            started_at=None,
            health=None,
            effective_limits={"wall_clock_timeout": 100},
            grace_seconds=30.0,
            fallback_wall_clock_seconds=600.0,
        )
        assert stale.satisfied is True
        assert stale.threshold_seconds == pytest.approx(130.0)
        assert boundary.satisfied is False

    @pytest.mark.parametrize(
        "limits", [None, {}, {"wall_clock_timeout": 0}, {"wall_clock_timeout": "x"}]
    )
    def test_deadline_falls_back_to_configured_wall_clock(self, limits):
        ev = rc.deadline_evidence(
            now=NOW,
            created_at=ago(700),
            started_at=None,
            health=None,
            effective_limits=limits,
            grace_seconds=30.0,
            fallback_wall_clock_seconds=600.0,
        )
        assert ev.threshold_seconds == pytest.approx(630.0)
        assert ev.satisfied is True

    def test_deadline_reference_is_max_of_available(self):
        # created 很久以前，但最近有心跳 ⇒ 不满足 deadline
        ev = rc.deadline_evidence(
            now=NOW,
            created_at=ago(5000),
            started_at=ago(5000),
            health=health(beat_ago=30),
            effective_limits={"wall_clock_timeout": 100},
            grace_seconds=30.0,
            fallback_wall_clock_seconds=600.0,
        )
        assert ev.satisfied is False
        assert ev.source == "heartbeat"


# ---------------------------------------------------------------------------
# 模式语义（off / detect / enforce）
# ---------------------------------------------------------------------------
class TestModeSemantics:
    def _stale_same_host_prev(self):
        return (
            candidate(owner=PREV_OWNER, created_ago=600, started_ago=600),
            health(beat_ago=100),
            ctx(stage=rc.STAGE_STARTUP),
        )

    def test_off_never_reclaims(self):
        cand, hlt, context = self._stale_same_host_prev()
        decision = rc.evaluate(cand, hlt, context, cfg(mode=rc.MODE_OFF))
        assert decision.eligible is False
        assert decision.should_reclaim is False
        assert decision.skip_reason == "mode_off"

    def test_detect_produces_decision_without_reclaim(self):
        cand, hlt, context = self._stale_same_host_prev()
        decision = rc.evaluate(cand, hlt, context, cfg(mode=rc.MODE_DETECT))
        assert decision.eligible is True
        assert decision.should_reclaim is False
        assert decision.trigger == rc.TRIGGER_RESTART_LEFTOVER
        assert decision.terminal_reason == TerminalReason.ABORTED.value
        assert decision.skip_reason == "mode_detect"

    def test_enforce_allows_reclaim(self):
        cand, hlt, context = self._stale_same_host_prev()
        decision = rc.evaluate(cand, hlt, context, cfg(mode=rc.MODE_ENFORCE))
        assert decision.eligible is True
        assert decision.should_reclaim is True
        assert decision.skip_reason is None

    def test_config_from_env_defaults(self):
        conf = rc.ReclaimConfig.from_env(env={})
        assert conf.mode == rc.MODE_ENFORCE
        assert conf.grace_seconds == 30.0
        assert conf.confirmations == 2
        assert conf.scan_interval_seconds == 15.0
        assert conf.scan_batch_limit == 200
        assert conf.startup_budget_seconds == 2.0
        assert conf.max_per_cycle == 20
        assert conf.min_age_seconds == 10.0
        assert conf.local_only is True
        assert conf.wall_clock_fallback_seconds == 600.0
        assert conf.heartbeat.interval_seconds == 5.0
        assert conf.liveness_threshold_seconds == pytest.approx(15.0)

    def test_config_from_env_overrides_and_invalid_fallback(self):
        conf = rc.ReclaimConfig.from_env(
            env={
                "RUNTIME_RECLAIM_MODE": "detect",
                "RUNTIME_STALE_GRACE_PERIOD": "45",
                "RUNTIME_STALE_CONFIRMATIONS": "1",
                "RUNTIME_RECLAIM_MIN_AGE": "20",
                "RUNTIME_WALL_CLOCK_TIMEOUT": "1200",
                "RUNTIME_RECLAIM_LOCAL_ONLY": "false",
                "RUNTIME_HEARTBEAT_INTERVAL": "10",
            }
        )
        assert conf.mode == rc.MODE_DETECT
        assert conf.grace_seconds == 45.0
        assert conf.confirmations == 1  # 显式 aggressive（日志告警）
        assert conf.min_age_seconds == 20.0
        assert conf.wall_clock_fallback_seconds == 1200.0
        assert conf.local_only is False
        assert conf.liveness_threshold_seconds == pytest.approx(30.0)  # 3 × 10s

        invalid = rc.ReclaimConfig.from_env(
            env={
                "RUNTIME_RECLAIM_MODE": "bogus",
                "RUNTIME_STALE_GRACE_PERIOD": "abc",
                "RUNTIME_STALE_CONFIRMATIONS": "-3",
            }
        )
        assert invalid.mode == rc.MODE_ENFORCE
        assert invalid.grace_seconds == 30.0
        assert invalid.confirmations == 2


# ---------------------------------------------------------------------------
# 终态映射（D5 / D-Phase2-P2-2-003）
# ---------------------------------------------------------------------------
class TestTerminalMapping:
    def test_restart_leftover_maps_to_aborted(self):
        assert rc.terminal_for(rc.TRIGGER_RESTART_LEFTOVER) == TerminalReason.ABORTED

    @pytest.mark.parametrize(
        "trigger", [rc.TRIGGER_HEARTBEAT_STALE, rc.TRIGGER_REGISTRY_DISCONNECT]
    )
    def test_stale_and_registry_map_to_orphan_reclaimed(self, trigger):
        assert rc.terminal_for(trigger) == TerminalReason.ORPHAN_RECLAIMED

    def test_unknown_trigger_maps_to_none(self):
        assert rc.terminal_for("nope") is None


# ---------------------------------------------------------------------------
# 决策流水线（S4 要求 1–17）
# ---------------------------------------------------------------------------
class TestDecisionPipeline:
    def test_01_current_deadline_not_reached_no_reclaim(self):
        """① CURRENT + deadline 未超时 → 不 reclaim。"""
        decision = rc.evaluate(
            candidate(created_ago=100, started_ago=100),
            health(beat_ago=10),
            ctx(consecutive=5),
            cfg(),
        )
        assert decision.eligible is False
        assert decision.should_reclaim is False
        assert decision.skip_reason == "heartbeat_fresh"

    def test_01b_current_no_evidence_deadline_not_reached(self):
        decision = rc.evaluate(
            candidate(created_ago=100), None, ctx(consecutive=5), cfg()
        )
        assert decision.eligible is False
        assert decision.skip_reason == "deadline_not_met"

    def test_02_current_deadline_exceeded_reclaims(self):
        """② CURRENT + deadline/grace 超时 → reclaim decision（policy 超期路径）。"""
        decision = rc.evaluate(
            candidate(created_ago=700, limits={"wall_clock_timeout": 100}),
            None,
            ctx(consecutive=2),
            cfg(),
        )
        assert decision.eligible is True
        assert decision.should_reclaim is True
        assert decision.trigger == rc.TRIGGER_HEARTBEAT_STALE
        assert decision.terminal_reason == TerminalReason.ORPHAN_RECLAIMED.value

    def test_02b_current_registry_disconnect_reclaims(self):
        """②′ CURRENT + registry 证据（liveness 超时）→ orphan_reclaimed。"""
        decision = rc.evaluate(
            candidate(created_ago=600, started_ago=600),
            health(beat_ago=60),
            ctx(consecutive=0),
            cfg(),
        )
        assert decision.eligible is True
        assert decision.trigger == rc.TRIGGER_REGISTRY_DISCONNECT
        assert decision.terminal_reason == TerminalReason.ORPHAN_RECLAIMED.value

    def test_03_same_host_prev_fresh_heartbeat_no_reclaim(self):
        """③ SAME_HOST_PREV + fresh heartbeat → 不 reclaim。"""
        for stage in (rc.STAGE_STARTUP, rc.STAGE_PERIODIC):
            decision = rc.evaluate(
                candidate(owner=PREV_OWNER, created_ago=5000, started_ago=5000),
                health(beat_ago=5),
                ctx(stage=stage, consecutive=9),
                cfg(),
            )
            assert decision.eligible is False, stage
            assert decision.should_reclaim is False, stage
            assert decision.skip_reason == "same_host_prev_liveness_not_met"

    def test_04_same_host_prev_stale_heartbeat_reclaims(self):
        """④ SAME_HOST_PREV + heartbeat stale → reclaim（aborted）。"""
        decision = rc.evaluate(
            candidate(owner=PREV_OWNER, created_ago=5000, started_ago=5000),
            health(beat_ago=16),
            ctx(stage=rc.STAGE_PERIODIC),
            cfg(),
        )
        assert decision.should_reclaim is True
        assert decision.trigger == rc.TRIGGER_RESTART_LEFTOVER
        assert decision.terminal_reason == TerminalReason.ABORTED.value

    def test_05_same_host_prev_no_health_row_old_started_at(self):
        """⑤ 无 health row + started_at 足够老 → reclaim（legacy fallback）。"""
        decision = rc.evaluate(
            candidate(owner=PREV_OWNER, created_ago=5000, started_ago=20),
            None,
            ctx(stage=rc.STAGE_STARTUP),
            cfg(),
        )
        assert decision.should_reclaim is True
        assert decision.evidence_source == "started_at"
        assert decision.terminal_reason == TerminalReason.ABORTED.value

    def test_06_same_host_prev_no_health_row_created_at_fallback(self):
        """⑥ 无 health row + 无 started_at → 按 created_at 判定（老 → reclaim；新 → 不）。"""
        old = rc.evaluate(
            candidate(owner=PREV_OWNER, created_ago=60),
            None,
            ctx(stage=rc.STAGE_STARTUP),
            cfg(),
        )
        fresh = rc.evaluate(
            candidate(owner=PREV_OWNER, created_ago=14),
            None,
            ctx(stage=rc.STAGE_STARTUP),
            cfg(),
        )
        assert old.should_reclaim is True
        assert old.evidence_source == "created_at"
        assert fresh.should_reclaim is False
        assert fresh.skip_reason == "same_host_prev_liveness_not_met"

    def test_07_rolling_overlap_healthy_old_process_not_reclaimed(self):
        """⑦ D15 关键安全性质：overlap 中持续心跳的旧进程不得被 startup sweep 误杀。"""
        for beat_ago in (0.0, 3.0, 10.0, 14.999):
            decision = rc.evaluate(
                candidate(owner=PREV_OWNER, created_ago=99999, started_ago=99999),
                health(beat_ago=beat_ago),
                ctx(stage=rc.STAGE_STARTUP),
                cfg(),
            )
            assert decision.eligible is False, beat_ago
            assert decision.should_reclaim is False, beat_ago
        # 对照：心跳停止超过阈值 ⇒ 允许 reclaim
        stopped = rc.evaluate(
            candidate(owner=PREV_OWNER, created_ago=99999, started_ago=99999),
            health(beat_ago=15.001),
            ctx(stage=rc.STAGE_STARTUP),
            cfg(),
        )
        assert stopped.should_reclaim is True

    def test_08_foreign_never_reclaims(self):
        """⑧ FOREIGN → 永不 reclaim（任何 stage / 任何陈旧度 / 任何 mode=enforce）。"""
        for stage in (rc.STAGE_STARTUP, rc.STAGE_PERIODIC):
            for hlt in (None, health(beat_ago=99999)):
                decision = rc.evaluate(
                    candidate(owner="hostB-777", created_ago=99999, started_ago=99999),
                    hlt,
                    ctx(stage=stage, consecutive=9),
                    cfg(),
                )
                assert decision.owner_class == rc.OWNER_FOREIGN
                assert decision.eligible is False
                assert decision.should_reclaim is False
                assert decision.skip_reason == "foreign_observe_only"

    def test_09_registry_disconnect_below_min_age_no_reclaim(self):
        """⑨ registry 脱节 + 未满足 MIN_AGE → 不 reclaim。"""
        decision = rc.evaluate(
            candidate(created_ago=5, started_ago=5),
            health(beat_ago=60),
            ctx(),
            cfg(),
        )
        assert decision.eligible is False
        assert decision.skip_reason == "min_age"

    def test_10_registry_disconnect_without_evidence_no_reclaim(self):
        """⑩ registry 脱节 + 无 health row + 无 started_at → 不得仅凭无 handle 就 reclaim。"""
        # created_at 尚新（未超 deadline）⇒ 不 reclaim
        fresh = rc.evaluate(candidate(created_ago=60), None, ctx(consecutive=9), cfg())
        assert fresh.eligible is False
        assert fresh.skip_reason == "deadline_not_met"
        # created_at 超 deadline 但未达确认次数 ⇒ 仍不 reclaim（双阶段确认）
        awaiting = rc.evaluate(
            candidate(created_ago=700, limits={"wall_clock_timeout": 100}),
            None,
            ctx(consecutive=1),
            cfg(),
        )
        assert awaiting.eligible is False
        assert awaiting.skip_reason == "awaiting_confirmation"

    def test_11_registry_disconnect_with_evidence_reclaims(self):
        """⑪ registry 脱节 + 证据满足 → orphan_reclaimed。"""
        decision = rc.evaluate(
            candidate(created_ago=600, started_ago=600),
            health(beat_ago=20),
            ctx(),
            cfg(),
        )
        assert decision.should_reclaim is True
        assert decision.trigger == rc.TRIGGER_REGISTRY_DISCONNECT
        assert decision.terminal_reason == TerminalReason.ORPHAN_RECLAIMED.value

    def test_12_startup_leftover_maps_to_aborted(self):
        """⑫ startup/restart leftover → aborted（CURRENT 与 SAME_HOST_PREV 两路）。"""
        prev = rc.evaluate(
            candidate(owner=PREV_OWNER, created_ago=5000, started_ago=5000),
            health(beat_ago=30),
            ctx(stage=rc.STAGE_STARTUP),
            cfg(),
        )
        current = rc.evaluate(
            candidate(owner=CURRENT_OWNER, created_ago=5000, started_ago=5000),
            health(beat_ago=700),
            ctx(stage=rc.STAGE_STARTUP),
            cfg(),
        )
        for decision in (prev, current):
            assert decision.should_reclaim is True
            assert decision.trigger == rc.TRIGGER_RESTART_LEFTOVER
            assert decision.terminal_reason == TerminalReason.ABORTED.value

    def test_12b_startup_current_requires_deadline(self):
        """startup 阶段 CURRENT 用 deadline 语义，不因普通 heartbeat stale 提前收敛。"""
        decision = rc.evaluate(
            candidate(created_ago=100, started_ago=100),
            health(beat_ago=30),  # 超过 liveness(15s) 但未超 deadline(630s)
            ctx(stage=rc.STAGE_STARTUP),
            cfg(),
        )
        assert decision.eligible is False
        assert decision.skip_reason == "deadline_not_met"

    def test_13_to_15_mode_matrix_end_to_end(self):
        """⑬⑭⑮ off/detect/enforce 在真实 stale 输入上的行为矩阵。"""
        cand = candidate(created_ago=700, started_ago=700)
        hlt = health(beat_ago=700)
        context = ctx(consecutive=2)
        off = rc.evaluate(cand, hlt, context, cfg(mode=rc.MODE_OFF))
        detect = rc.evaluate(cand, hlt, context, cfg(mode=rc.MODE_DETECT))
        enforce = rc.evaluate(cand, hlt, context, cfg(mode=rc.MODE_ENFORCE))
        assert (off.eligible, off.should_reclaim) == (False, False)
        assert (detect.eligible, detect.should_reclaim) == (True, False)
        assert (enforce.eligible, enforce.should_reclaim) == (True, True)
        assert detect.trigger == enforce.trigger == rc.TRIGGER_REGISTRY_DISCONNECT
        assert detect.terminal_reason == enforce.terminal_reason

    def test_16_threshold_boundary_exact(self):
        """⑯ 阈值边界：`>` 严格大于（liveness 与 deadline 各验一次）。"""
        # liveness：age == 15 不 reclaim；age = 15.001 reclaim
        at_threshold = rc.evaluate(
            candidate(owner=PREV_OWNER, created_ago=5000, started_ago=5000),
            health(beat_ago=15.0),
            ctx(stage=rc.STAGE_STARTUP),
            cfg(),
        )
        just_over = rc.evaluate(
            candidate(owner=PREV_OWNER, created_ago=5000, started_ago=5000),
            health(beat_ago=15.001),
            ctx(stage=rc.STAGE_STARTUP),
            cfg(),
        )
        assert at_threshold.should_reclaim is False
        assert just_over.should_reclaim is True
        # deadline：created 恰好等于阈值 100+30 不 reclaim；+0.001 reclaim
        at_deadline = rc.evaluate(
            candidate(created_ago=130.0, limits={"wall_clock_timeout": 100}),
            None,
            ctx(consecutive=2),
            cfg(),
        )
        over_deadline = rc.evaluate(
            candidate(created_ago=130.001, limits={"wall_clock_timeout": 100}),
            None,
            ctx(consecutive=2),
            cfg(),
        )
        assert at_deadline.should_reclaim is False
        assert over_deadline.should_reclaim is True

    def test_17_deterministic_and_clock_injected(self):
        """⑰ 纯函数：同输入同输出（clock 注入，无 sleep、无副作用）。"""
        cand = candidate(owner=PREV_OWNER, created_ago=500, started_ago=500)
        hlt = health(beat_ago=60)
        context = ctx(stage=rc.STAGE_PERIODIC)
        conf = cfg()
        first = rc.evaluate(cand, hlt, context, conf)
        second = rc.evaluate(cand, hlt, context, conf)
        assert first == second
        assert first.as_dict() == second.as_dict()

    def test_defensive_skips(self):
        """防御性：非 running / 内存已有裁决 / handle 存活 → 一律不 reclaim。"""
        stale = candidate(owner=PREV_OWNER, created_ago=5000, started_ago=5000)
        hlt = health(beat_ago=600)
        assert (
            rc.evaluate(
                candidate(
                    owner=PREV_OWNER,
                    created_ago=5000,
                    started_ago=5000,
                    status="completed",
                ),
                hlt,
                ctx(stage=rc.STAGE_STARTUP),
                cfg(),
            ).skip_reason
            == "not_running"
        )
        assert (
            rc.evaluate(
                stale, hlt, ctx(stage=rc.STAGE_STARTUP, decision=True), cfg()
            ).skip_reason
            == "memory_terminal_decision"
        )
        assert (
            rc.evaluate(
                stale, hlt, ctx(stage=rc.STAGE_STARTUP, handle=True), cfg()
            ).skip_reason
            == "handle_alive"
        )

    def test_missing_created_at_is_skipped(self):
        """created_at 缺失 → 无法证明最小年龄 ⇒ 不 reclaim（fail-closed）。"""
        cand = rc.CandidateState(task_id="t-x", owner_instance=PREV_OWNER)
        decision = rc.evaluate(
            cand, health(beat_ago=600), ctx(stage=rc.STAGE_STARTUP), cfg()
        )
        assert decision.eligible is False
        assert decision.skip_reason == "missing_created_at"
