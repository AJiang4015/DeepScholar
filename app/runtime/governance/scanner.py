"""P2-2 S5 — RuntimeHealthScanner（runtime orchestration 层）。

规范与决策依据：`P2-2_SPEC_v2.md` **Rev 2.2** §6.5（restart recovery）、§7（startup scanner）、
§8（periodic scanner）、§10（可观测语义）、§12.2（清理责任）、§13.1（模式矩阵）；
`DECISION.md` `D-Phase2-P2-2-005`（SC-1…SC-4 组件边界）、`-007`（清理责任 = scanner）、
`-011`（确认次数）、`-013`（startup 时序）、`-014`（interval + 抖动）、`-017`（零 frontend）。

职责边界（MUST）：
- **判定不在此层**：所有 stale / registry / owner 判定一律经 `reclaim.evaluate()`（S4 纯逻辑）；
  scanner 只做 **candidate discovery → decision consume → (optional) cleanup**；
- **收敛只经冻结 funnel**：`enforce` 模式下调用 `controller.finalize_with_event(...)`；
  **MUST NOT** 直写 `governance_tasks`、**MUST NOT** 自行 emit event、**MUST NOT** 绕过 CAS/funnel；
- **MUST** 复用现有 controller 单例（SC-1/SC-2）：构造时注入 `GovernanceController`，
  **禁止**新建 controller 实例（否则 handle registry 为空 ⇒ 同进程全量误回收）；
- 清理仅限 health plane（`health_store.delete_health_rows`），**禁止**删除 lifecycle 数据；
- `off` → 整体停用（不扫描、不判定、不清理）；`detect` → 判定/记录/清理但**不收敛**；
- periodic loop 单实例、可取消、不阻塞 controller execution；所有扫描异常 fail-open。

S5 范围：本模块提供 `startup_sweep()` / `scan_once()` / `start_periodic()` / `stop()` /
`cleanup_health_rows()` / `health_view()` 等 hook；**server lifespan 的装配与 start/stop 接线属 S6**
（Approved Plan S6），本阶段不修改 `server.py`。
"""

from __future__ import annotations

import asyncio
import logging
import random
import socket
import time
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from app.runtime.governance import health_store
from app.runtime.governance import reclaim as rc
from app.runtime.governance.models import TerminalReason

logger = logging.getLogger("deepsearch.runtime.governance.scanner")

#: 模式矩阵（Spec §13.1）：`off` 不运行；`detect` 只判定；`enforce` 允许收敛
_LOOP_JITTER_RATIO = 0.20


@dataclass
class ScanStats:
    """单次扫描统计（日志/测试用；不含任何 lifecycle 写）。"""

    stage: str = rc.STAGE_PERIODIC
    mode: str = rc.MODE_ENFORCE
    scanned: int = 0
    eligible: int = 0
    reclaimed: int = 0
    cleaned: int = 0
    errors: int = 0
    skipped_inflight: bool = False
    budget_exceeded: bool = False
    skip_reasons: dict[str, int] = field(default_factory=dict)
    last_error: Optional[str] = None

    def note_skip(self, reason: Optional[str]) -> None:
        if reason:
            self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1


class RuntimeHealthScanner:
    """health plane 扫描器（candidate discovery → reclaim decision → 可选收敛 + 清理）。"""

    def __init__(
        self,
        controller: Any,
        *,
        config: Optional[rc.ReclaimConfig] = None,
        clock: Optional[Callable[[], datetime]] = None,
        monotonic_fn: Optional[Callable[[], float]] = None,
        rng: Optional[random.Random] = None,
        hostname: Optional[str] = None,
    ) -> None:
        if controller is None:  # SC-1/SC-3：必须复用进程级 controller 单例
            raise ValueError(
                "RuntimeHealthScanner 需要既有 controller 实例（禁止新建）"
            )
        self._controller = controller
        self._config = config or rc.ReclaimConfig()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic_fn or time.monotonic
        self._rng = rng or random.Random()
        self._hostname = hostname or socket.gethostname()
        self._lock = asyncio.Lock()
        self._periodic_task: Optional[asyncio.Task] = None
        #: task_id → 连续 stale 次数（periodic 的 heartbeat_stale 路径要求 ≥ confirmations）
        self._stale_counts: dict[str, int] = {}
        self._last_stats = ScanStats(mode=self._config.mode)

    # -- introspection ---------------------------------------------------
    @property
    def config(self) -> rc.ReclaimConfig:
        return self._config

    @property
    def running(self) -> bool:
        return self._periodic_task is not None and not self._periodic_task.done()

    @property
    def last_stats(self) -> ScanStats:
        return self._last_stats

    @property
    def _store(self) -> Any:
        return self._controller._store  # noqa: SLF001 — 复用既有 store 句柄（service 同先例）

    # -- lifecycle hooks（装配属 S6） ------------------------------------
    def start_periodic(self) -> bool:
        """启动 periodic loop（单实例；`off` 或已运行 → False，不创建第二个 task）。"""
        if not self._config.enabled:
            return False
        if self.running:
            return False
        self._periodic_task = asyncio.create_task(self.run_periodic())
        return True

    async def stop(self) -> None:
        """停止 periodic loop（cancellation 安全；幂等）。"""
        task = self._periodic_task
        self._periodic_task = None
        if task is None or task.done():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def run_periodic(self) -> None:
        """periodic loop：tick 间隔 = `scan_interval_seconds`（±20% 抖动）；单飞；fail-open。"""
        while True:
            await self.scan_once(stage=rc.STAGE_PERIODIC)
            await asyncio.sleep(self._next_delay())

    def _next_delay(self) -> float:
        base = self._config.scan_interval_seconds
        return max(
            0.01,
            base * (1.0 + self._rng.uniform(-_LOOP_JITTER_RATIO, _LOOP_JITTER_RATIO)),
        )

    # -- scans -----------------------------------------------------------
    async def startup_sweep(self) -> ScanStats:
        """启动期单遍 sweep（有界 + 预算 + fail-open；不适用双阶段确认）。"""
        return await self.scan_once(stage=rc.STAGE_STARTUP)

    async def scan_once(self, *, stage: str = rc.STAGE_PERIODIC) -> ScanStats:
        """单次扫描（candidate discovery → decision → 可选收敛 + 清理）。

        - `off` → 直接返回（无判定、无清理）；
        - 单飞：上一次扫描未结束 → 跳过；
        - 全部异常 fail-open（记入 stats.errors，绝不外抛给调用方/controller）。
        """
        stats = ScanStats(stage=stage, mode=self._config.mode)
        if not self._config.enabled:
            self._last_stats = stats
            return stats
        if self._lock.locked():
            stats.skipped_inflight = True
            self._last_stats = stats
            return stats
        async with self._lock:
            try:
                await self._scan(
                    stage=stage if stage in rc.VALID_STAGES else rc.STAGE_PERIODIC,
                    stats=stats,
                )
            except Exception as exc:  # noqa: BLE001 — 扫描失败不得影响 controller 主流程
                stats.errors += 1
                stats.last_error = str(exc)
                logger.warning("runtime health scan 失败（fail-open）：%s", exc)
        self._last_stats = stats
        if stats.scanned or stats.errors or stats.reclaimed or stats.cleaned:
            logger.info(
                "runtime health scan[%s mode=%s]: scanned=%d eligible=%d reclaimed=%d "
                "cleaned=%d errors=%d skips=%s",
                stats.stage,
                stats.mode,
                stats.scanned,
                stats.eligible,
                stats.reclaimed,
                stats.cleaned,
                stats.errors,
                stats.skip_reasons,
            )
        return stats

    async def _scan(self, *, stage: str, stats: ScanStats) -> None:
        candidates = health_store.list_running_tasks(
            self._store, limit=self._config.scan_batch_limit
        )
        seen: set[str] = set()
        budget_deadline = self._monotonic() + self._config.startup_budget_seconds
        for row in candidates:
            if stage == rc.STAGE_STARTUP and self._monotonic() > budget_deadline:
                stats.budget_exceeded = True
                break
            if stats.reclaimed >= self._config.max_per_cycle:
                stats.budget_exceeded = True
                break
            stats.scanned += 1
            task_id = row["task_id"]
            seen.add(task_id)
            health = health_store.get_health(self._store, task_id)
            candidate = rc.CandidateState(
                task_id=task_id,
                thread_id=row.get("thread_id"),
                run_id=row.get("run_id"),
                owner_instance=row.get("owner_instance"),
                created_at=row.get("created_at"),
                started_at=row.get("started_at"),
                effective_limits=row.get("effective_limits") or {},
                status="running",
            )
            consecutive = self._stale_counts.get(task_id, 0) + 1
            context = rc.DecisionContext(
                now=self._now(),
                current_owner=self._controller.owner_instance,
                hostname=self._hostname,
                has_active_handle=self._controller.has_active_handle(task_id),
                has_memory_decision=(
                    self._controller.terminal_decision(task_id) is not None
                ),
                stage=stage,
                consecutive_stale=consecutive,
            )
            decision = rc.evaluate(candidate, health, context, self._config)
            # 连续 stale 计数：仅 `awaiting_confirmation`（pursuing 确认中）累积；其余一律清零
            if decision.skip_reason == "awaiting_confirmation":
                self._stale_counts[task_id] = consecutive
            else:
                self._stale_counts.pop(task_id, None)
            if not decision.eligible:
                stats.note_skip(decision.skip_reason)
                continue
            stats.eligible += 1
            if decision.should_reclaim:
                await self._reclaim(decision, stats=stats, stage=stage)
        await self.cleanup_health_rows(stats=stats)
        self._prune_confirmations(seen)

    def _now(self) -> datetime:
        return self._clock()

    def _prune_confirmations(self, seen: set[str]) -> None:
        for task_id in list(self._stale_counts):
            if task_id not in seen:
                self._stale_counts.pop(task_id, None)

    async def _reclaim(
        self, decision: rc.ReclaimDecision, *, stats: ScanStats, stage: str
    ) -> None:
        """enforce 收敛：唯一路径 = 既有冻结 funnel（`finalize_with_event`）。"""
        reason = decision.terminal_reason
        if not reason:
            stats.errors += 1
            stats.last_error = "decision 缺少 terminal_reason"
            return
        error_text = self._reclaim_error(decision, stage=stage)
        try:
            await self._controller.finalize_with_event(
                decision.task_id,
                TerminalReason(reason),
                error=error_text,
            )
        except Exception as exc:  # noqa: BLE001 — 单个任务收敛失败不阻断扫描
            stats.errors += 1
            stats.last_error = str(exc)
            logger.warning(
                "reclaim 收敛失败（task %s，fail-open）：%s", decision.task_id, exc
            )
            return
        stats.reclaimed += 1
        self._stale_counts.pop(decision.task_id, None)
        logger.info(
            "reclaimed task=%s trigger=%s owner_class=%s terminal=%s stage=%s",
            decision.task_id,
            decision.trigger,
            decision.owner_class,
            reason,
            stage,
        )

    @staticmethod
    def _reclaim_error(decision: rc.ReclaimDecision, *, stage: str) -> str:
        """诊断载体 = 终态 `error` 字符串（D16 / `-008`；不改 events payload）。"""
        return (
            f"reclaim: trigger={decision.trigger} owner_class={decision.owner_class} "
            f"stage={stage} source={decision.evidence_source} "
            f"age={decision.age_seconds}s threshold={decision.threshold_seconds}s"
        )

    # -- cleanup（仅 health plane；Spec §12.2 / LA-4 / I11） --------------
    async def cleanup_health_rows(self, *, stats: Optional[ScanStats] = None) -> int:
        """删除终态 / 孤儿任务的 health 行（**不触碰** governance_tasks）。"""
        rows = health_store.list_cleanup_candidates(
            self._store, limit=self._config.scan_batch_limit
        )
        task_ids = [row["task_id"] for row in rows]
        if not task_ids:
            return 0
        deleted = health_store.delete_health_rows(self._store, task_ids)
        if stats is not None:
            stats.cleaned += deleted
        return deleted

    # -- 只读派生视图（供 P2-4 后续消费；本阶段不暴露端点） --------------
    def health_view(self, task_id: str) -> Optional[dict[str, Any]]:
        """单任务 health 派生视图：`healthy` / `stale` / `expired`（只读，无写操作）。"""
        record = self._controller.get_task(task_id)
        health = health_store.get_health(self._store, task_id)
        if record is None and health is None:
            return None
        now = self._now()
        # TaskRecord 时间列为字符串（store 双后端归一），谓词要求 aware datetime ⇒ 先归一
        created_at = health_store.parse_ts(record.created_at) if record else None
        started_at = health_store.parse_ts(record.started_at) if record else None
        if record is not None and record.is_terminal:
            state = "expired"
        else:
            liveness = rc.liveness_evidence(
                now=now,
                created_at=created_at,
                started_at=started_at,
                health=health,
                threshold_seconds=self._config.liveness_threshold_seconds,
            )
            deadline = rc.deadline_evidence(
                now=now,
                created_at=created_at,
                started_at=started_at,
                health=health,
                effective_limits=(record.effective_limits if record else None),
                grace_seconds=self._config.grace_seconds,
                fallback_wall_clock_seconds=self._config.wall_clock_fallback_seconds,
            )
            state = "stale" if (liveness.satisfied or deadline.satisfied) else "healthy"
        return {
            "task_id": task_id,
            "status": record.status if record is not None else None,
            "owner_instance": record.owner_instance if record is not None else None,
            "health": state,
            "last_heartbeat_at": health["last_heartbeat_at"] if health else None,
            "beat_count": health["beat_count"] if health else 0,
        }

    def health_snapshot(self, limit: int = 100) -> list[dict[str, Any]]:
        """running 任务的 health 派生视图列表（只读）。"""
        rows = health_store.list_running_tasks(self._store, limit=limit)
        views = [self.health_view(row["task_id"]) for row in rows]
        return [view for view in views if view is not None]
