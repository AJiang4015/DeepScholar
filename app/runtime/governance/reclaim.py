"""P2-2 S4 — Reclaim decision 纯逻辑（owner 分类 / 证据谓词 / 三路决策 / 终态映射 / 模式语义）。

规范与决策依据（唯一 authority）：
- `P2-2_SPEC_v2.md` **Rev 2.2**：§5.1（stale 公式）、§5.2（grace + 双阶段确认）、§5.4（防误杀）、
  §5.5（owner 三分法与行为）、§6.1（reclaim 触发条件）、§6.2（终态映射）、§13.1（模式矩阵）；
- `DECISION.md`：`D-Phase2-P2-2-003`（D5 终态映射：restart→aborted / stale·registry→orphan_reclaimed）、
  `-005`（scanner 边界，S5 接线）、`-006`（D18 registry 证据）、`-010`（**D15** `SAME_HOST_PREV`
  liveness 证据）、`-011`（确认次数 2）、`-013`（startup 时序，S6）、`-014`（interval）、`-015`（PG 写约束）。

本模块是**纯决策逻辑**（S4 范围）：
    input state → classification → evidence predicate → decision → reason / terminal mapping

MUST（边界）：
- **deterministic**、**可注入 clock**（不 sleep、不依赖真实时间）；
- **不**访问 HTTP、**不**创建 background task、**不**创建 scanner、**不**操作 controller / server；
- **不**直接写数据库（health_store 属 S1/S2；本模块不 import 它）；
- **不**修改 TaskStatus、**不**发布 event、**不**调用 lifecycle funnel（`terminalize` /
  `finalize_with_event` / CAS / pending 属 S5/S6 接线）；
- 本模块只返回 **decision / reason / terminal mapping**，由后续 Stage 的 caller 执行收敛。

安全性质（D15，必须由测试锁定）：
「仍在心跳的 `SAME_HOST_PREV` 旧进程（rolling / blue-green overlap）**不得**被误判为可 reclaim」——
`SAME_HOST_PREV` 必须叠加 **liveness 证据**（停止心跳 ≥ `max(3 × interval, 15s)`）才允许进入 reclaim，
**禁止**恢复为「同 host 异 owner 即立即 reclaim」。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional

from app.runtime.governance.heartbeat import HeartbeatConfig
from app.runtime.governance.models import TerminalReason

logger = logging.getLogger("deepsearch.runtime.governance.reclaim")

#: env 键（Spec Rev 2.2 §13）
MODE_ENV = "RUNTIME_RECLAIM_MODE"
GRACE_ENV = "RUNTIME_STALE_GRACE_PERIOD"
CONFIRMATIONS_ENV = "RUNTIME_STALE_CONFIRMATIONS"
SCAN_INTERVAL_ENV = "RUNTIME_SCAN_INTERVAL"
SCAN_BATCH_ENV = "RUNTIME_SCAN_BATCH_LIMIT"
STARTUP_BUDGET_ENV = "RUNTIME_STARTUP_SWEEP_BUDGET"
MAX_PER_CYCLE_ENV = "RUNTIME_RECLAIM_MAX_PER_CYCLE"
MIN_AGE_ENV = "RUNTIME_RECLAIM_MIN_AGE"
LOCAL_ONLY_ENV = "RUNTIME_RECLAIM_LOCAL_ONLY"
WALL_CLOCK_ENV = "RUNTIME_WALL_CLOCK_TIMEOUT"

MODE_OFF = "off"
MODE_DETECT = "detect"
MODE_ENFORCE = "enforce"
VALID_MODES = (MODE_OFF, MODE_DETECT, MODE_ENFORCE)

DEFAULT_GRACE_SECONDS = 30.0
DEFAULT_CONFIRMATIONS = 2
DEFAULT_SCAN_INTERVAL_SECONDS = 15.0
DEFAULT_SCAN_BATCH_LIMIT = 200
DEFAULT_STARTUP_BUDGET_SECONDS = 2.0
DEFAULT_MAX_PER_CYCLE = 20
DEFAULT_MIN_AGE_SECONDS = 10.0
DEFAULT_WALL_CLOCK_SECONDS = 600.0

#: liveness 证据的绝对下限（Spec §5.5 / D15：`max(3 × interval, 15s)`）
MIN_LIVENESS_THRESHOLD_SECONDS = 15.0

#: owner 分类（Spec §5.5）
OWNER_CURRENT = "CURRENT"
OWNER_SAME_HOST_PREV = "SAME_HOST_PREV"
OWNER_FOREIGN = "FOREIGN"

#: trigger（Spec §6.1/§6.2；terminal 映射见 `terminal_for`）
TRIGGER_RESTART_LEFTOVER = "restart_leftover"
TRIGGER_HEARTBEAT_STALE = "heartbeat_stale"
TRIGGER_REGISTRY_DISCONNECT = "registry_disconnect"

#: stage（startup sweep 为单遍；periodic 需双阶段确认，Spec §5.2/§7.3）
STAGE_STARTUP = "startup"
STAGE_PERIODIC = "periodic"
VALID_STAGES = (STAGE_STARTUP, STAGE_PERIODIC)

_TERMINAL_BY_TRIGGER = {
    TRIGGER_RESTART_LEFTOVER: TerminalReason.ABORTED,
    TRIGGER_HEARTBEAT_STALE: TerminalReason.ORPHAN_RECLAIMED,
    TRIGGER_REGISTRY_DISCONNECT: TerminalReason.ORPHAN_RECLAIMED,
}


def terminal_for(trigger: str) -> Optional[TerminalReason]:
    """trigger → terminal reason（D5 / D-Phase2-P2-2-003 冻结映射）。

    - `restart_leftover`（startup/上一世代遗留）→ `aborted`
    - `heartbeat_stale` / `registry_disconnect` → `orphan_reclaimed`
    """
    return _TERMINAL_BY_TRIGGER.get(trigger)


# ---------------------------------------------------------------------------
# config（Spec §13；模式矩阵 §13.1）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ReclaimConfig:
    """P2-2 运行期配置（默认值 = Spec Rev 2.2 §13 冻结默认）。

    `mode`：
    - `off`     → 不判定、不 reclaim（心跳写入由装配方另行停用，Spec §13.1）；
    - `detect`  → 产出 decision（eligible/reason/terminal），但 `should_reclaim=False`；
    - `enforce` → `should_reclaim=True`（实际收敛由 S5/S6 caller 执行）。
    """

    heartbeat: HeartbeatConfig = field(default_factory=HeartbeatConfig)
    mode: str = MODE_ENFORCE
    grace_seconds: float = DEFAULT_GRACE_SECONDS
    confirmations: int = DEFAULT_CONFIRMATIONS
    scan_interval_seconds: float = DEFAULT_SCAN_INTERVAL_SECONDS
    scan_batch_limit: int = DEFAULT_SCAN_BATCH_LIMIT
    startup_budget_seconds: float = DEFAULT_STARTUP_BUDGET_SECONDS
    max_per_cycle: int = DEFAULT_MAX_PER_CYCLE
    min_age_seconds: float = DEFAULT_MIN_AGE_SECONDS
    local_only: bool = True
    wall_clock_fallback_seconds: float = DEFAULT_WALL_CLOCK_SECONDS

    @property
    def enabled(self) -> bool:
        """`off` ⇒ 健康平面整体停用（不判定、不写心跳、不清理）。"""
        return self.mode != MODE_OFF

    @property
    def detect_only(self) -> bool:
        return self.mode == MODE_DETECT

    @property
    def liveness_threshold_seconds(self) -> float:
        """`max(3 × RUNTIME_HEARTBEAT_INTERVAL, 15s)`（Spec §5.5 / D15）。"""
        return max(
            3.0 * self.heartbeat.interval_seconds, MIN_LIVENESS_THRESHOLD_SECONDS
        )

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "ReclaimConfig":
        """从 env 解析（缺省 `os.environ`）；非法值 → 回退默认 + 诊断（**不** fail-fast）。"""
        source = os.environ if env is None else env
        raw_mode = str(source.get(MODE_ENV) or "").strip().lower()
        if raw_mode in VALID_MODES:
            mode = raw_mode
        else:
            if raw_mode:
                logger.warning(
                    "%s 取值非法（%r）→ 回退默认 %s", MODE_ENV, raw_mode, MODE_ENFORCE
                )
            mode = MODE_ENFORCE
        confirmations = _env_int(
            source, CONFIRMATIONS_ENV, DEFAULT_CONFIRMATIONS, minimum=1
        )
        if confirmations == 1:
            logger.warning(
                "%s=1：aggressive 确认模式（单次 stale 即收敛），请确认这是预期配置",
                CONFIRMATIONS_ENV,
            )
        return cls(
            heartbeat=HeartbeatConfig.from_env(source),
            mode=mode,
            grace_seconds=_env_float(
                source, GRACE_ENV, DEFAULT_GRACE_SECONDS, minimum=0.0
            ),
            confirmations=confirmations,
            scan_interval_seconds=_env_float(
                source, SCAN_INTERVAL_ENV, DEFAULT_SCAN_INTERVAL_SECONDS, minimum=1.0
            ),
            scan_batch_limit=_env_int(
                source, SCAN_BATCH_ENV, DEFAULT_SCAN_BATCH_LIMIT, minimum=1
            ),
            startup_budget_seconds=_env_float(
                source, STARTUP_BUDGET_ENV, DEFAULT_STARTUP_BUDGET_SECONDS, minimum=0.0
            ),
            max_per_cycle=_env_int(
                source, MAX_PER_CYCLE_ENV, DEFAULT_MAX_PER_CYCLE, minimum=1
            ),
            min_age_seconds=_env_float(
                source, MIN_AGE_ENV, DEFAULT_MIN_AGE_SECONDS, minimum=0.0
            ),
            local_only=_env_bool(source, LOCAL_ONLY_ENV, True),
            wall_clock_fallback_seconds=_env_float(
                source, WALL_CLOCK_ENV, DEFAULT_WALL_CLOCK_SECONDS, minimum=1.0
            ),
        )


def _env_float(
    env: Mapping[str, str], key: str, default: float, *, minimum: float
) -> float:
    raw = env.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("%s 取值非法（%r）→ 回退默认 %s", key, raw, default)
        return default
    if value < minimum:
        logger.warning("%s 低于下限（%s < %s）→ 回退默认", key, value, minimum)
        return default
    return value


def _env_int(env: Mapping[str, str], key: str, default: int, *, minimum: int) -> int:
    raw = env.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        logger.warning("%s 取值非法（%r）→ 回退默认 %s", key, raw, default)
        return default
    if value < minimum:
        logger.warning("%s 低于下限（%s < %s）→ 回退默认", key, value, minimum)
        return default
    return value


def _env_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    text = str(raw).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    logger.warning("%s 取值非法（%r）→ 回退默认 %s", key, raw, default)
    return default


# ---------------------------------------------------------------------------
# owner classification（Spec §5.5；三分法不得压缩为二分类）
# ---------------------------------------------------------------------------
def classify_owner(
    owner_instance: Optional[str],
    *,
    current_owner: str,
    hostname: str,
) -> str:
    """owner 三分法（deterministic）：

    - `CURRENT`：`owner_instance == current_owner`（含 `GOVERNANCE_OWNER_INSTANCE` 显式固定场景）；
    - `SAME_HOST_PREV`：可解析为 `<host>-<pid>`（`rsplit('-', 1)`，pid 为数字）且
      `host == hostname`，但 owner 不等于 current owner → 上一世代（restart 遗留）；
    - `FOREIGN`：其余（其它主机 / 不可解析且不等于 current owner / 未知格式）→ **只观测**。
    """
    if not owner_instance:
        return OWNER_FOREIGN
    if owner_instance == current_owner:
        return OWNER_CURRENT
    host, pid = _split_owner(owner_instance)
    if host is not None and pid is not None and host == hostname:
        return OWNER_SAME_HOST_PREV
    return OWNER_FOREIGN


def _split_owner(owner: str) -> tuple[Optional[str], Optional[str]]:
    if "-" not in owner:
        return None, None
    host, _, pid = owner.rpartition("-")
    if not host or not pid.isdigit():
        return None, None
    return host, pid


# ---------------------------------------------------------------------------
# 时间与证据谓词（Spec §5.1 / §5.5 / §6.1）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Evidence:
    """证据谓词结果（供 decision 诊断与测试断言）。"""

    satisfied: bool
    age_seconds: Optional[float]
    threshold_seconds: float
    reference_ts: Optional[datetime]
    source: Optional[str]  # "heartbeat" | "started_at" | "created_at" | None


def _age_seconds(now: datetime, ts: Optional[datetime]) -> Optional[float]:
    """`now - ts`（秒）；ts 为 None → None；负值 → 0（时钟回拨不触发 stale，Spec §5.3）。"""
    if ts is None:
        return None
    age = (now - ts).total_seconds()
    return age if age > 0 else 0.0


def _max_ts(*values: Optional[datetime]) -> Optional[datetime]:
    present = [v for v in values if v is not None]
    return max(present) if present else None


def liveness_evidence(
    *,
    now: datetime,
    created_at: Optional[datetime],
    started_at: Optional[datetime],
    health: Optional[Mapping[str, Any]],
    threshold_seconds: float,
) -> Evidence:
    """**liveness 证据**（D15 / Spec §5.5）：执行者已停止心跳。

    - 有 health row（`last_heartbeat_at` 非空）→ `now - last_heartbeat_at > threshold`；
    - 无 health row（legacy fallback）→ `now - max(started_at, created_at) > threshold`；
    - 两者皆无 / 时间不可解析 → 不满足（不 reclaim）。
    比较为**严格大于**（Spec 阈值语义）。
    """
    last_beat = health.get("last_heartbeat_at") if health else None
    if last_beat is not None:
        reference, source = last_beat, "heartbeat"
    else:
        reference = _max_ts(started_at, created_at)
        if reference is None:
            return Evidence(False, None, threshold_seconds, None, None)
        source = "started_at" if reference is started_at else "created_at"
    age = _age_seconds(now, reference)
    assert age is not None  # reference 非空 ⇒ age 非空
    return Evidence(age > threshold_seconds, age, threshold_seconds, reference, source)


def deadline_evidence(
    *,
    now: datetime,
    created_at: Optional[datetime],
    started_at: Optional[datetime],
    health: Optional[Mapping[str, Any]],
    effective_limits: Optional[Mapping[str, Any]],
    grace_seconds: float,
    fallback_wall_clock_seconds: float,
) -> Evidence:
    """**deadline 证据**（Spec §5.1 / §5.2）：超过该任务自身 wall-clock + grace 无任何心跳。

    - 逐任务基准：`effective_limits.wall_clock_timeout`（P2-1 落库）；缺失/非法 → 回退配置值；
    - 参照点：`max(last_heartbeat_at?, started_at, created_at)`；
    - 严格大于阈值（`wall_clock + grace`）；无任何可用时间 → 不满足。
    """
    wall_clock = _wall_clock_of(effective_limits, fallback_wall_clock_seconds)
    threshold = wall_clock + grace_seconds
    last_beat = health.get("last_heartbeat_at") if health else None
    reference = _max_ts(last_beat, started_at, created_at)
    if reference is None:
        return Evidence(False, None, threshold, None, None)
    if last_beat is not None and reference is last_beat:
        source = "heartbeat"
    elif reference is started_at:
        source = "started_at"
    else:
        source = "created_at"
    age = _age_seconds(now, reference)
    assert age is not None
    return Evidence(age > threshold, age, threshold, reference, source)


def _wall_clock_of(
    effective_limits: Optional[Mapping[str, Any]], fallback: float
) -> float:
    """逐任务 `wall_clock_timeout`（缺失/非法 → fallback；不回退到 0）。"""
    if not effective_limits:
        return fallback
    raw = effective_limits.get("wall_clock_timeout")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return fallback
    return float(raw) if raw > 0 else fallback


# ---------------------------------------------------------------------------
# decision
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CandidateState:
    """running 候选（字段与 `health_store.list_running_tasks` 输出一一对应）。"""

    task_id: str
    owner_instance: Optional[str]
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    thread_id: Optional[str] = None
    run_id: Optional[str] = None
    effective_limits: Mapping[str, Any] = field(default_factory=dict)
    status: Optional[str] = None  # 提供时必须是 "running"（防御性校验）


@dataclass(frozen=True)
class DecisionContext:
    """判定上下文（全部由 caller 提供；本模块不查询任何外部状态）。"""

    now: datetime
    current_owner: str
    hostname: str
    #: 本进程是否持有该 task 的 active handle（P1；True ⇒ 绝不 reclaim）
    has_active_handle: bool = False
    #: 本进程内存中是否已有终态裁决（P2；True ⇒ 跳过）
    has_memory_decision: bool = False
    #: `startup`（单遍 sweep，不适用确认次数）| `periodic`（需双阶段确认）
    stage: str = STAGE_PERIODIC
    #: 连续 stale 次数（S5 scanner 维护；periodic 的 heartbeat_stale 路径要求 ≥ confirmations）
    consecutive_stale: int = 0


@dataclass(frozen=True)
class ReclaimDecision:
    """判定结果（**不**执行任何 lifecycle 写操作）。"""

    task_id: str
    owner_class: str
    mode: str
    eligible: bool
    should_reclaim: bool
    trigger: Optional[str] = None
    terminal_reason: Optional[str] = None
    skip_reason: Optional[str] = None
    age_seconds: Optional[float] = None
    threshold_seconds: Optional[float] = None
    evidence_source: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "owner_class": self.owner_class,
            "mode": self.mode,
            "eligible": self.eligible,
            "should_reclaim": self.should_reclaim,
            "trigger": self.trigger,
            "terminal_reason": self.terminal_reason,
            "skip_reason": self.skip_reason,
            "age_seconds": self.age_seconds,
            "threshold_seconds": self.threshold_seconds,
            "evidence_source": self.evidence_source,
        }


def _skip(
    candidate: CandidateState,
    owner_class: str,
    mode: str,
    reason: str,
    *,
    evidence: Optional[Evidence] = None,
) -> ReclaimDecision:
    return ReclaimDecision(
        task_id=candidate.task_id,
        owner_class=owner_class,
        mode=mode,
        eligible=False,
        should_reclaim=False,
        skip_reason=reason,
        age_seconds=evidence.age_seconds if evidence else None,
        threshold_seconds=evidence.threshold_seconds if evidence else None,
        evidence_source=evidence.source if evidence else None,
    )


def _decide(
    candidate: CandidateState,
    owner_class: str,
    mode: str,
    trigger: str,
    evidence: Evidence,
) -> ReclaimDecision:
    terminal = terminal_for(trigger)
    eligible = True
    return ReclaimDecision(
        task_id=candidate.task_id,
        owner_class=owner_class,
        mode=mode,
        eligible=eligible,
        should_reclaim=(eligible and mode == MODE_ENFORCE),
        trigger=trigger,
        terminal_reason=terminal.value if terminal is not None else None,
        skip_reason=None if mode == MODE_ENFORCE else f"mode_{mode}",
        age_seconds=evidence.age_seconds,
        threshold_seconds=evidence.threshold_seconds,
        evidence_source=evidence.source,
    )


def evaluate(
    candidate: CandidateState,
    health: Optional[Mapping[str, Any]],
    context: DecisionContext,
    config: ReclaimConfig,
) -> ReclaimDecision:
    """单任务 reclaim 判定（纯函数；deterministic；不产生任何副作用）。

    判定顺序（`skip_reason` 可诊断）：
    1. `mode == off` → 直接跳过（`mode_off`）；
    2. `status` 提供且非 `running` → `not_running`；
    3. `FOREIGN` → `foreign_observe_only`（**永不 reclaim**）；
    4. 内存终态裁决存在 → `memory_terminal_decision`；
    5. 本进程 handle 存活 → `handle_alive`；
    6. `created_at` 缺失或未满足 MIN_AGE → `missing_created_at` / `min_age`；
    7. 按 owner 分类与 stage 选择证据：
       - `SAME_HOST_PREV`：startup → `liveness_evidence`；periodic → `liveness_evidence` 或
         `deadline_evidence`；满足 → trigger `restart_leftover` → `aborted`；
       - `CURRENT`：startup → `deadline_evidence` → `restart_leftover` → `aborted`；
         periodic → registry 证据（D18）→ `registry_disconnect` → `orphan_reclaimed`，
         或 `deadline_evidence` + 双阶段确认 → `heartbeat_stale` → `orphan_reclaimed`。
    """
    mode = config.mode
    owner_class = classify_owner(
        candidate.owner_instance,
        current_owner=context.current_owner,
        hostname=context.hostname,
    )

    if mode == MODE_OFF:
        return _skip(candidate, owner_class, mode, "mode_off")
    if candidate.status is not None and candidate.status != "running":
        return _skip(candidate, owner_class, mode, "not_running")
    if owner_class == OWNER_FOREIGN:
        return _skip(candidate, owner_class, mode, "foreign_observe_only")
    if context.has_memory_decision:
        return _skip(candidate, owner_class, mode, "memory_terminal_decision")
    if context.has_active_handle:
        return _skip(candidate, owner_class, mode, "handle_alive")

    created_age = _age_seconds(context.now, candidate.created_at)
    if created_age is None:
        return _skip(candidate, owner_class, mode, "missing_created_at")
    if created_age < config.min_age_seconds:
        return _skip(candidate, owner_class, mode, "min_age")

    stage = context.stage if context.stage in VALID_STAGES else STAGE_PERIODIC
    liveness = liveness_evidence(
        now=context.now,
        created_at=candidate.created_at,
        started_at=candidate.started_at,
        health=health,
        threshold_seconds=config.liveness_threshold_seconds,
    )
    deadline = deadline_evidence(
        now=context.now,
        created_at=candidate.created_at,
        started_at=candidate.started_at,
        health=health,
        effective_limits=candidate.effective_limits,
        grace_seconds=config.grace_seconds,
        fallback_wall_clock_seconds=config.wall_clock_fallback_seconds,
    )

    if owner_class == OWNER_SAME_HOST_PREV:
        # D15：必须叠加 liveness 证据（防止 rolling/blue-green overlap 误杀健康旧进程）
        if liveness.satisfied:
            return _decide(
                candidate, owner_class, mode, TRIGGER_RESTART_LEFTOVER, liveness
            )
        if stage == STAGE_PERIODIC and deadline.satisfied:
            return _decide(
                candidate, owner_class, mode, TRIGGER_RESTART_LEFTOVER, deadline
            )
        return _skip(
            candidate,
            owner_class,
            mode,
            "same_host_prev_liveness_not_met",
            evidence=liveness,
        )

    # OWNER_CURRENT
    if stage == STAGE_STARTUP:
        if deadline.satisfied:
            return _decide(
                candidate, owner_class, mode, TRIGGER_RESTART_LEFTOVER, deadline
            )
        return _skip(
            candidate, owner_class, mode, "deadline_not_met", evidence=deadline
        )

    # periodic：先看 registry 脱节（证据更强：要求「曾进入执行」+ liveness 超时），再看 heartbeat stale
    registry = _registry_disconnect_evidence(
        candidate=candidate,
        health=health,
        now=context.now,
        threshold_seconds=config.liveness_threshold_seconds,
    )
    if registry.evidence_present:
        if registry.satisfied:
            return _decide(
                candidate,
                owner_class,
                mode,
                TRIGGER_REGISTRY_DISCONNECT,
                registry.evidence,
            )
        # 曾进入执行且心跳新鲜（≤ liveness 阈值）⇒ 健康/仍在运行：不 reclaim
        # （rolling / blue-green overlap 中的旧进程即靠此路径免于误杀）
        return _skip(
            candidate, owner_class, mode, "heartbeat_fresh", evidence=registry.evidence
        )
    # 无任何执行证据（无 health row 且无 started_at）⇒ 视为「policy 超期」路径（F8 §10.2 d），
    # 需双阶段确认 + deadline 证据（逐任务 wall-clock + grace）。
    if context.consecutive_stale < config.confirmations:
        return _skip(
            candidate, owner_class, mode, "awaiting_confirmation", evidence=deadline
        )
    if deadline.satisfied:
        return _decide(candidate, owner_class, mode, TRIGGER_HEARTBEAT_STALE, deadline)
    return _skip(candidate, owner_class, mode, "deadline_not_met", evidence=deadline)


@dataclass(frozen=True)
class RegistryEvidence:
    """registry 脱节证据（D18 / Spec §6.1 第三行）。"""

    satisfied: bool
    evidence_present: bool
    age_seconds: Optional[float]
    threshold_seconds: float
    source: Optional[str]
    evidence: Evidence


def _registry_disconnect_evidence(
    *,
    candidate: CandidateState,
    health: Optional[Mapping[str, Any]],
    now: datetime,
    threshold_seconds: float,
) -> RegistryEvidence:
    """D18 证据条件：

    `age > MIN_AGE`（在 `evaluate` 中已校验）
    **且**（health row 存在 **或** `started_at` 非空）→「曾真正进入执行」
    **且** `now - max(last_beat, started_at) > max(3 × interval, 15s)`。
    """
    has_health = bool(health and health.get("last_heartbeat_at") is not None)
    evidence_present = has_health or candidate.started_at is not None
    if not evidence_present:
        return RegistryEvidence(
            satisfied=False,
            evidence_present=False,
            age_seconds=None,
            threshold_seconds=threshold_seconds,
            source=None,
            evidence=Evidence(False, None, threshold_seconds, None, None),
        )
    reference = health.get("last_heartbeat_at") if has_health else candidate.started_at
    source = "heartbeat" if has_health else "started_at"
    age = _age_seconds(now, reference)
    assert age is not None
    evidence = Evidence(
        age > threshold_seconds, age, threshold_seconds, reference, source
    )
    return RegistryEvidence(
        satisfied=evidence.satisfied,
        evidence_present=True,
        age_seconds=age,
        threshold_seconds=threshold_seconds,
        source=source,
        evidence=evidence,
    )
