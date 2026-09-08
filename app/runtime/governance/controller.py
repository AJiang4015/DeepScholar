"""F8 GovernanceController —— Task Lifecycle / 唯一 Terminal Funnel（Plan Rev2 §5–§7，Step 2）。

Step 2 范围：TaskRecord 最小 CRUD（create/start/read）、in-process handle registry、
唯一 `terminalize()` funnel（内存裁决 + 乐观 DB 写 + retry → `pending_terminal`）、
`execute` wrapper（观察正常完成 → completed；异常 → failed(agent_failure)）。

**不在 Step 2**：BudgetCounter/callbacks/watchdog/sweeper/sequencer/adapter/replay/API 接线；
terminal Event（task_status_change）持久化归 sequencer（§12）——本漏斗**不写** governance_events，
事件持久化在 sequencer Step 接入（observation 层，fail-open）。

红线（Spec §7.2/§7.3 + Plan §6，全部由测试锁定）：
- TaskRecord durable write = **control-path**：内存置 terminal 后立即停执行（底层 cancel），
  DB 只是记录侧 —— 不存在“DB 故障 → 无状态无限运行”；硬预算/cancel/收敛不因 DB 故障关闭。
- 内存 terminal 裁决权威；durable 写失败 → retry 阶梯（立即 + 0.5s + 2s，共 3 次）→ 仍失败记入
  `pending_terminal`（dec.pending）；返回值带 `degraded_durability=true`，**绝不谎报 durable**。
- 单一写入口：一切 lifecycle 收敛必须经 `terminalize()`；`rowcount==1` 是 durable transition 的
  客观证据（恰一次）。
- controller = 唯一 in-process handle registry（取代 server.active_tasks 作为 Task Model；
  transitional：server 接线在后续 Step）。
- single-instance：owner_instance 仅 audit metadata；无 lease/heartbeat/多实例分支。

调用方注意：store 为同步访问（sqlite/PG 短连接）；controller 方法在 asyncio 事件循环内调用，
store 调用为短同步段，Step 2 不引入 async driver。
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from app.runtime.governance import events as gov_events
from app.runtime.governance import store as gov_store
from app.runtime.governance.context import (
    GovernanceExecution,
    enter_governance_execution,
)
from app.runtime.governance.counters import (
    DEFAULT_LIMITS,
    BudgetCounter,
    GovernanceLimitExceeded,
)
from app.runtime.governance.models import (
    ErrorKind,
    TaskRecord,
    TaskStatus,
    TerminalReason,
    TERMINAL_STATUSES,
)
from langgraph.errors import GraphRecursionError  # noqa: E402

logger = logging.getLogger("deepsearch.runtime.governance.controller")

#: durable 写失败重试延迟（秒）：立即一次 + 各延迟后再试 → 共 3 次（Plan §6）
TERMINAL_RETRY_DELAYS = (0.5, 2.0)

#: Step 3 policy 默认（M-Spec §7.4 / Spec 2026-09-15-f8-step3 §2/§9）
DEFAULT_WALL_CLOCK_SECONDS = 600
DEFAULT_FRAMEWORK_RECURSION_LIMIT = 5000
WATCHDOG_TICK_SECONDS = 1.0

#: policy limit key（max_*，M-Spec §7.4）→ BudgetCounter kind
MAX_LIMIT_TO_COUNTER = {
    "max_llm_calls": "llm_calls",
    "max_tool_calls": "tool_calls",
    "max_search_calls": "search_calls",
    "max_agent_steps": "agent_steps",
}

_ERROR_CLIP = 800  # error 文本入库上限（审计列；完整错误在事件/日志）

_TERMINAL_STATUS_VALUES = {s.value for s in TERMINAL_STATUSES}


class GovernanceControllerError(RuntimeError):
    """controller 层错误：重复 execute、unknown task、非法 reason、store 不可用等。"""


@dataclass
class ExecutionHandle:
    """in-process handle registry 条目：task_id → 底层执行的 asyncio.Future。

    controller 是唯一 registry：funnel 经此向本进程内正在执行的底层 coroutine 发 cancel 信号。
    """

    task_id: str
    fut: asyncio.Future
    created_at: str
    run_id: Optional[str] = None

    @property
    def done(self) -> bool:
        return self.fut.done()

    def cancel(self) -> None:
        if not self.fut.done():
            self.fut.cancel()


@dataclass
class _TerminalDecision:
    """内存 terminal 裁决（control 权威；DB 只是记录侧）。"""

    task_id: str
    status: str
    terminal_reason: str
    error_kind: Optional[str]
    error: Optional[str]
    counters_snapshot: Optional[dict]
    superseded_by: Optional[str]
    finished_at: str
    durable: bool = False  # 已确认落库（rowcount==1 恰一次）
    pending: bool = False  # durable 写失败，等待 reconciler/shutdown flush/startup 兜底
    attempts: int = 0  # durable 写尝试次数（≤3 本轮；flush 追加）
    underlying_linger_observed: Optional[bool] = None  # cancel 时底层仍在跑（观察字段）
    last_error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "terminal_reason": self.terminal_reason,
            "error_kind": self.error_kind,
            "error": self.error,
            "counters_snapshot": self.counters_snapshot,
            "superseded_by": self.superseded_by,
            "finished_at": self.finished_at,
            "durable": self.durable,
            "pending": self.pending,
            "attempts": self.attempts,
            "underlying_linger_observed": self.underlying_linger_observed,
            "last_error": self.last_error,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _default_owner_instance() -> str:
    inst = (os.environ.get("GOVERNANCE_OWNER_INSTANCE") or "").strip()
    if inst:
        return inst
    return f"{socket.gethostname()}-{os.getpid()}"


def _clip_error(text: str) -> str:
    return text if len(text) <= _ERROR_CLIP else text[:_ERROR_CLIP] + "…"


class GovernanceController:
    """single-instance Task Lifecycle controller（Spec §5.3 单运行时实例）。"""

    def __init__(
        self,
        store: Any,
        *,
        owner_instance: Optional[str] = None,
        retry_delays: tuple[float, ...] = TERMINAL_RETRY_DELAYS,
    ) -> None:
        if store is None:
            raise GovernanceControllerError(
                "store 不可用：submit 场景 fail-closed（c1）"
            )
        self._store = store
        self._owner = owner_instance or _default_owner_instance()
        self._retry_delays = tuple(retry_delays)
        #: watchdog tick（生产 1.0s；测试可缩时——不影响 deadline 语义，仅采样间隔）
        self.watchdog_tick = WATCHDOG_TICK_SECONDS
        #: Batch3(A) live sink：callable(frame: dict) → None（observation，fail-open；默认无=不广播）。
        #: 由 server/WS 层注入（现有 manager.enqueue 抽象），controller 不依赖 manager。
        self.live_sink = None
        self._lock = asyncio.Lock()
        #: task_id → 内存 terminal 裁决（含未 durable 的 pending 裁决）
        self._decisions: dict[str, _TerminalDecision] = {}
        #: pending（durable 写未成功）裁决的插入序，供 flush/reconciler 收敛
        self._pending_order: list[str] = []
        #: in-process handle registry（取代 server.active_tasks 作为 Task Model）
        self._handles: dict[str, ExecutionHandle] = {}

    # ------------------------------------------------------------------
    # 最小 TaskRecord CRUD（Step 2）
    # ------------------------------------------------------------------
    @property
    def owner_instance(self) -> str:
        return self._owner

    def create_task(
        self,
        thread_id: str,
        *,
        task_id: Optional[str] = None,
        run_id: Optional[str] = None,
        policy_snapshot: Optional[dict[str, Any]] = None,
        effective_limits: Optional[dict[str, Any]] = None,
        parent_session: Optional[str] = None,
    ) -> "TaskRecord":
        """创建 running TaskRecord（重复 task_id → store 抛 IntegrityError/UniqueViolation）。"""
        if not thread_id or not isinstance(thread_id, str):
            raise GovernanceControllerError("thread_id 必须为非空字符串")
        rec = TaskRecord(
            task_id=task_id or uuid.uuid4().hex,
            thread_id=thread_id,
            owner_instance=self._owner,
            status=TaskStatus.RUNNING.value,
            run_id=run_id,
            policy_snapshot=dict(policy_snapshot or {}),
            effective_limits=dict(effective_limits or {}),
            parent_session=parent_session,
            version=0,
            created_at=_now_iso(),
        )
        gov_store.insert_task(self._store, rec)
        return rec

    def get_task(self, task_id: str) -> Optional["TaskRecord"]:
        return gov_store.get_task(self._store, task_id)

    def start_task(self, task_id: str, started_at: Optional[str] = None) -> bool:
        """running → 记 started_at（best-effort；已非 running 或已记 → False）。"""
        return gov_store.start_task(self._store, task_id, started_at or _now_iso())

    # ------------------------------------------------------------------
    # in-process handle registry
    # ------------------------------------------------------------------
    def active_handle_ids(self) -> list[str]:
        return sorted(self._handles)

    def active_handles(self) -> dict[str, ExecutionHandle]:
        return dict(self._handles)

    def has_active_handle(self, task_id: str) -> bool:
        return task_id in self._handles

    # ------------------------------------------------------------------
    # Terminal funnel（Plan §5/§6：唯一 terminal 写入口）
    # ------------------------------------------------------------------
    async def terminalize(
        self,
        task_id: str,
        reason: Any,
        *,
        error_kind: Optional[str] = None,
        error: Optional[str] = None,
        counters: Optional[dict[str, Any]] = None,
        superseded_by: Optional[str] = None,
        underlying_linger_observed: Optional[bool] = None,
    ) -> dict[str, Any]:
        """唯一 terminal 收敛入口（内存裁决权威；乐观 DB 写；retry → pending）。

        返回（调用方/API 用）：
          {task_id, status, terminal_reason, error_kind, error, counters_snapshot,
           superseded_by, finished_at, winner, already_terminal,
           durable, pending, degraded_durability, found}
        - winner=True：本次调用完成内存裁决（首个到达者）；
        - already_terminal=True：内存已有裁决（race 败者 / 重复收敛）→ no-op；
        - durable=True：DB 已确认（rowcount==1 恰一次）；durable=False 且 pending=True 时
          degraded_durability=True（绝不谎报已落库）；
        - found=False：task 不存在且 store 可读 → 无裁决 no-op。
        """
        status, treason = self._coerce_terminal(reason)
        self._validate_terminal_fields(status, error_kind, superseded_by)

        async with self._lock:
            dec = self._decisions.get(task_id)
            if dec is not None:
                return self._funnel_result(dec, winner=False, already_terminal=True)

            # 决策前读取：存在性 + version（CAS 期望值）。读失败（DB 故障）不阻塞内存裁决。
            record: Optional["TaskRecord"] = None
            read_failed = False
            try:
                record = gov_store.get_task(self._store, task_id)
            except Exception as exc:  # noqa: BLE001
                read_failed = True
                logger.warning(
                    "terminalize 读 task %s 失败（继续内存裁决）：%s", task_id, exc
                )
            if record is not None and record.is_terminal:
                # DB 已 terminal（out-of-band / 重启后其它收敛）→ 采纳 DB 事实，不再裁决
                return self._already_terminal_db_result(task_id, record)
            if record is None and not read_failed:
                # store 健康但 task 不存在 → 无可收敛对象
                return self._unknown_result(task_id, status, treason)

            dec = _TerminalDecision(
                task_id=task_id,
                status=status,
                terminal_reason=treason,
                error_kind=error_kind,
                error=_clip_error(error) if error else None,
                counters_snapshot=counters,
                superseded_by=superseded_by,
                finished_at=_now_iso(),
            )
            # 1) 内存裁决（control 权威，先于任何 durable 尝试）
            self._decisions[task_id] = dec
            # 2) 底层执行（若在本进程）→ cancel 信号（内存裁决后立即停执行）。
            #    linger 观察（Step 3 §9）：cancel 发生时底层仍活着 → True（底层稍后才真正停，
            #    TaskRecord terminal ≠ 底层已停）；显式调用方覆盖优先；默认自动观测。
            handle = self._handles.get(task_id)
            alive_at_cancel = handle is not None and not handle.done
            self._cancel_underlying(task_id)
            if underlying_linger_observed is not None:
                dec.underlying_linger_observed = underlying_linger_observed
            elif alive_at_cancel:
                dec.underlying_linger_observed = True
            # 3) durable 写：第一次（立即）
            self._attempt_terminal_write(dec)

        # 锁外 retry 阶梯（等待期间其他 funnel 见内存裁决 → no-op，无重复决策）
        if not dec.durable:
            for delay in self._retry_delays:
                await asyncio.sleep(delay)
                self._attempt_terminal_write(dec)
                if dec.durable:
                    break

        return self._funnel_result(dec, winner=True, already_terminal=False)

    async def cancel(
        self,
        task_id: str,
        *,
        error_kind: Optional[str] = None,
        error: Optional[str] = None,
        counters: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """API /cancel 语义：funnel(CANCELLED)。已 terminal（含 completed）→ no-op。"""
        return await self.terminalize(
            task_id,
            TerminalReason.CANCELLED,
            error_kind=error_kind,
            error=error,
            counters=counters,
        )

    # ------------------------------------------------------------------
    # Step 4 Batch 1：funnel + lifecycle event（observation，fail-open；server/governed 用）
    # ------------------------------------------------------------------
    async def finalize_with_event(
        self,
        task_id: str,
        reason: Any,
        *,
        error_kind: Optional[str] = None,
        error: Optional[str] = None,
        counters: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """冻结 funnel 收敛 + 发布 terminal lifecycle event（只写 observation，不改 TaskRecord）。"""
        res = await self.terminalize(
            task_id,
            reason,
            error_kind=error_kind,
            error=error,
            counters=counters,
        )
        if not res.get("found") or res.get("already_terminal"):
            return res  # 无新收敛/已是历史裁决 → 不重复发布
        self._publish_terminal_event(task_id, res)
        return res

    async def cancel_governed(
        self,
        task_id: str,
        *,
        error_kind: Optional[str] = None,
        error: Optional[str] = None,
        counters: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """server /api/task/{...}/cancel 走冻结 funnel + lifecycle event。"""
        return await self.finalize_with_event(
            task_id,
            TerminalReason.CANCELLED,
            error_kind=error_kind,
            error=error,
            counters=counters,
        )

    def _publish_terminal_event(self, task_id: str, res: dict[str, Any]) -> None:
        """发布 terminal lifecycle event + governance_terminal live bridge（observation，fail-open）。

        - durable：governance_events（event_id 幂等）；
        - live bridge：仅当 live_sink 已注入（server/WS）；帧 event_id == durable event event_id；
          不产生 governance_seq、不进 durable replay、不改 TaskRecord。
        """
        status = res.get("status")
        if not status:
            return
        try:
            rec = gov_store.get_task(self._store, task_id)
        except Exception:  # noqa: BLE001
            rec = None
        durable_result = None
        try:
            durable_result = gov_events.lifecycle_event(
                self._store,
                task_id=task_id,
                thread_id=(rec.thread_id if rec is not None else ""),
                run_id=(rec.run_id if rec is not None else None),
                status=status,
                terminal_reason=res.get("terminal_reason"),
                error_kind=res.get("error_kind"),
                error=res.get("error"),
                counters_snapshot=res.get("counters_snapshot"),
                finished_at=res.get("finished_at"),
            )
        except Exception as exc:  # noqa: BLE001 — observation failure：不阻断 control
            logger.warning(
                "lifecycle event 发布失败（task %s，fail-open）：%s", task_id, exc
            )
        if self.live_sink is None:
            return
        # 轻量 bridge 帧：只通知 live client；不塞完整 error/counters；event_id 与 durable 对齐
        frame = {
            "type": "governance_terminal",
            "event_id": (durable_result or {}).get("event_id") or "",
            "thread_id": rec.thread_id if rec is not None else "",
            "task_id": rec.task_id if rec is not None else task_id,
            "run_id": rec.run_id if rec is not None else None,
            "status": status,
            "terminal_reason": res.get("terminal_reason"),
            "error_kind": res.get("error_kind"),
            "message": f"governance terminal: {status}",
        }
        try:
            self.live_sink(frame)
        except Exception as exc:  # noqa: BLE001 — sink failure fail-open，不影响 TaskRecord
            logger.warning(
                "governance_terminal live sink 失败（task %s，fail-open）：%s",
                task_id,
                exc,
            )

    # ------------------------------------------------------------------
    # execute wrapper（Plan §7；F7 位置不变，见 §17；Step 3 policy 走 governed 路径）
    # ------------------------------------------------------------------
    async def execute(
        self,
        task_id: str,
        coroutine: Any,
        *,
        policy: Optional[dict[str, Any]] = None,
    ) -> Any:
        """运行 task 的底层 coroutine（F7 run_deep_agent 在 controller 外保持不变）。

        - `policy=None`（Step 2 语义原样）：正常返回 → funnel(completed)；底层抛异常 →
          funnel(failed, agent_failure)；funnel 已裁决后底层被 cancel → CancelledError 原样上抛、
          不伪造 completed（§17：governance terminal 不触发 F7 finalize）；
        - `policy` 给定（Step 3 governed）：额外绑定 BudgetCounter / GovernanceExecutionContext
          （经 ContextVar 供 main_agent S1 注入 callback + recursion_limit）+ watchdog（单调
          deadline）+ 异常映射 → 全部收敛到冻结 Terminal Funnel；counter/context/异常语义沿用
          Phase A/B/C；不引入第二套 budget counter。
        """
        if not asyncio.iscoroutine(coroutine):
            raise GovernanceControllerError(
                "execute 需要 coroutine 对象（asyncio.iscoroutine）"
            )
        if task_id in self._handles:
            raise GovernanceControllerError(
                f"task {task_id} 已有 active handle（重复执行）"
            )
        if self._decisions.get(task_id) is not None:
            raise GovernanceControllerError(f"task {task_id} 已 terminal，拒绝启动")
        try:
            record = gov_store.get_task(self._store, task_id)
        except Exception as exc:  # noqa: BLE001
            raise GovernanceControllerError(
                f"task {task_id} 读取失败，拒绝启动：{exc}"
            ) from exc
        if record is None:
            raise GovernanceControllerError(f"task {task_id} 不存在（先 create_task）")
        if record.is_terminal:
            raise GovernanceControllerError(
                f"task {task_id} 已 terminal（{record.status}），拒绝启动"
            )
        if policy is None:
            return await self._execute_bare(record, task_id, coroutine)
        return await self._execute_governed(record, task_id, coroutine, policy)

    async def _execute_bare(
        self, record: "TaskRecord", task_id: str, coroutine: Any
    ) -> Any:
        """Step 2 原语义执行（policy=None）。"""
        fut = asyncio.ensure_future(coroutine)
        handle = ExecutionHandle(
            task_id=task_id,
            fut=fut,
            run_id=record.run_id,
            created_at=_now_iso(),
        )
        self._handles[task_id] = handle
        try:
            try:
                gov_store.start_task(self._store, task_id, _now_iso())
            except Exception as exc:  # noqa: BLE001 — started_at 非关键；terminal 写才是 control
                logger.warning("start_task %s 失败（非致命）：%s", task_id, exc)
            result = await fut
        except asyncio.CancelledError:
            # 由 funnel 裁决后的 cancel 信号引起 → 内存裁决权威，不在此伪造状态
            raise
        except Exception as exc:  # noqa: BLE001
            if self._decisions.get(task_id) is None:
                try:
                    await self.terminalize(
                        task_id,
                        TerminalReason.FAILED,
                        error_kind=ErrorKind.AGENT_FAILURE.value,
                        error=str(exc),
                    )
                except Exception as gexc:  # noqa: BLE001 — 收敛失败不能掩盖 agent 失败
                    logger.exception(
                        "task %s 收敛 failed 失败（原始异常继续上抛）：%s",
                        task_id,
                        gexc,
                    )
            raise
        else:
            if self._decisions.get(task_id) is None:
                await self.terminalize(task_id, TerminalReason.COMPLETED)
            return result
        finally:
            if self._handles.get(task_id) is handle:
                del self._handles[task_id]

    # ------------------------------------------------------------------
    # Step 3 governed execution（policy 路径；funnel/CAS/pending 冻结语义不变）
    # ------------------------------------------------------------------
    @staticmethod
    def _policy_limits(policy: dict[str, Any]) -> dict[str, int]:
        """policy 的 max_* 覆盖（M-Spec §7.4 keys）→ BudgetCounter limits。"""
        limits = dict(DEFAULT_LIMITS)
        for max_key, kind in MAX_LIMIT_TO_COUNTER.items():
            value = policy.get(max_key)
            if value is not None:
                limits[kind] = int(value)
        return limits

    async def _execute_governed(
        self,
        record: "TaskRecord",
        task_id: str,
        coroutine: Any,
        policy: dict[str, Any],
    ) -> Any:
        """Step 3 governed execution：

        绑定本 execution 唯一 BudgetCounter + GovernanceExecution ContextVar（S1 main_agent
        据此注入 callback/recursion_limit）；启动 watchdog（单调 deadline）；按 Frozen Spec
        异常映射收敛到冻结 Terminal Funnel；CancelledError 不伪造 completed。

        F1（Run Identity Contract）：agent 真正执行前保证 TaskRecord.run_id 非空 ——
        无则生成并以最小方式回填 TaskRecord（仅 running 且 run_id IS NULL 才写，不触碰
        version/terminal CAS）；后续 ctx/run_deep_agent/research/monitor/durable event
        全部沿用该值。已有 run_id 严禁重新生成。
        """
        if not record.run_id:
            run_id = uuid.uuid4().hex
            try:
                if not gov_store.update_run_id(self._store, task_id, run_id):
                    # 并发 out-of-band 已绑定或已非 running → 重读采纳，拒绝覆盖
                    fresh = gov_store.get_task(self._store, task_id)
                    if fresh is None or not fresh.run_id:
                        raise GovernanceControllerError(
                            f"task {task_id} 无法绑定 run_id（fail-closed，拒绝启动）"
                        )
                    run_id = fresh.run_id
            except GovernanceControllerError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise GovernanceControllerError(
                    f"task {task_id} run_id 持久化失败（fail-closed，拒绝启动）：{exc}"
                ) from exc
            record.run_id = run_id

        counter = BudgetCounter(limits=self._policy_limits(policy))
        timeout = float(
            policy.get("wall_clock_timeout", DEFAULT_WALL_CLOCK_SECONDS) or 0
        )
        rec_limit = int(
            policy.get("framework_recursion_limit") or DEFAULT_FRAMEWORK_RECURSION_LIMIT
        )
        ctx = GovernanceExecution(
            task_id=task_id,
            counter=counter,
            run_id=record.run_id,
            recursion_limit=rec_limit,
        )

        deadline_mono = time.monotonic() + timeout
        watchdog: Optional[asyncio.Task] = None
        handle: Optional[ExecutionHandle] = None

        async def _terminal_if_needed(
            reason: Any,
            *,
            error_kind: Optional[str] = None,
            error: Optional[str] = None,
        ) -> None:
            if self._decisions.get(task_id) is None:
                await self.finalize_with_event(
                    task_id,
                    reason,
                    error_kind=error_kind,
                    error=_clip_error(error) if error else None,
                    counters=counter.snapshot(),
                )

        try:
            try:
                gov_store.start_task(self._store, task_id, _now_iso())
            except Exception as exc:  # noqa: BLE001 — started_at 非关键
                logger.warning("start_task %s 失败（非致命）：%s", task_id, exc)
            # 关键：coroutine task 必须在 governance ContextVar 内创建 —— asyncio task 会
            # 快照创建时的 contextvars；否则 S1（main_agent 读取 ctx 注入 callback/recursion）
            # 在该 execution 内不可见（Phase C repo probe 实证的注入契约）。
            with enter_governance_execution(ctx):
                fut = asyncio.ensure_future(coroutine)
                handle = ExecutionHandle(
                    task_id=task_id,
                    fut=fut,
                    run_id=record.run_id,
                    created_at=_now_iso(),
                )
                self._handles[task_id] = handle
                if timeout > 0:
                    watchdog = asyncio.create_task(
                        self._watchdog_loop(
                            task_id,
                            deadline_mono,
                            counter,
                            tick=self.watchdog_tick,
                        )
                    )
                result = await fut
        except GovernanceLimitExceeded as exc:
            await _terminal_if_needed(TerminalReason.BUDGET_EXCEEDED, error=str(exc))
            raise
        except GraphRecursionError as exc:
            await _terminal_if_needed(
                TerminalReason.FAILED,
                error_kind=ErrorKind.FRAMEWORK_RECURSION_SAFETY.value,
                error=str(exc),
            )
            raise
        except asyncio.CancelledError:
            # watchdog/cancel 已由 funnel 裁决（timed_out/cancelled）或外部取消 → 不伪造
            raise
        except Exception as exc:  # noqa: BLE001
            if self._decisions.get(task_id) is None:
                try:
                    await _terminal_if_needed(
                        TerminalReason.FAILED,
                        error_kind=ErrorKind.AGENT_FAILURE.value,
                        error=str(exc),
                    )
                except Exception as gexc:  # noqa: BLE001
                    logger.exception(
                        "task %s 收敛 failed 失败（原始异常继续上抛）：%s",
                        task_id,
                        gexc,
                    )
            raise
        else:
            if self._decisions.get(task_id) is None:
                await _terminal_if_needed(TerminalReason.COMPLETED)
            return result
        finally:
            if watchdog is not None and not watchdog.done():
                watchdog.cancel()
                with suppress(asyncio.CancelledError):
                    await watchdog
            if handle is not None and self._handles.get(task_id) is handle:
                del self._handles[task_id]

    async def _watchdog_loop(
        self,
        task_id: str,
        deadline_mono: float,
        counter: BudgetCounter,
        *,
        tick: float = WATCHDOG_TICK_SECONDS,
    ) -> None:
        """watchdog：单调 clock 检查单一 authoritative deadline（Spec §9）。

        - handle 结束 / 已裁决 → 退出；
        - deadline 到达 → funnel(timed_out)（funnel 会 cancel 底层 handle）→ 退出；
        - watchdog 自身异常（F5/c2）→ 收敛 failed(governance_control_failure)。
        """
        try:
            while True:
                handle = self._handles.get(task_id)
                if handle is None or handle.done:
                    return
                if self._decisions.get(task_id) is not None:
                    return
                if time.monotonic() >= deadline_mono:
                    await self.finalize_with_event(
                        task_id,
                        TerminalReason.TIMED_OUT,
                        counters=counter.snapshot(),
                    )
                    return
                await asyncio.sleep(tick)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — watchdog 自身故障不得静默丢执行
            logger.exception("watchdog tick 异常（task %s）：%s", task_id, exc)
            await self._converge_control_failure(task_id, counter, exc)

    async def _converge_control_failure(
        self,
        task_id: str,
        counter: Optional[BudgetCounter],
        exc: Exception,
    ) -> None:
        """c2：controller/governance 内部控制故障 → cancel 底层 + failed(governance_control_failure)。"""
        try:
            handle = self._handles.get(task_id)
            if handle is not None and not handle.done:
                handle.cancel()
            if self._decisions.get(task_id) is None:
                await self.finalize_with_event(
                    task_id,
                    TerminalReason.FAILED,
                    error_kind=ErrorKind.GOVERNANCE_CONTROL_FAILURE.value,
                    error=_clip_error(str(exc)),
                    counters=counter.snapshot() if counter is not None else None,
                )
        except Exception as e2:  # noqa: BLE001 — 收敛本身失败：log 保底（sweeper/startup 兜底）
            logger.exception(
                "task %s c2 收敛失败（governance_control_failure）：%s", task_id, e2
            )

    # ------------------------------------------------------------------
    # pending_terminal：shutdown flush / 周期 reconciler（15s，后续 Step）共用
    # ------------------------------------------------------------------
    async def flush_pending(self) -> dict[str, Any]:
        """best-effort 收敛 pending 裁决（正常 shutdown flush 与 reconciler 共用）。

        对每个 pending：重读最新 version 后单次乐观写/采纳；成功 → durable=True。
        返回 {"flushed": n, "remaining": m, "attempted": k}。
        """
        attempted = 0
        flushed = 0
        for task_id in list(self._pending_order):
            dec = self._decisions.get(task_id)
            if dec is None or not dec.pending:
                continue
            attempted += 1
            self._attempt_terminal_write(dec)
            if dec.durable:
                flushed += 1
        self._prune_pending_order()
        remaining = sum(
            1 for t in self._pending_order if self._decisions.get(t, None) is not None
        )
        return {
            "attempted": attempted,
            "flushed": flushed,
            "remaining": remaining,
        }

    def pending_snapshot(self) -> list[dict[str, Any]]:
        """pending_terminal 集合只读快照（API/debug；含 reason/快照/attempts）。"""
        return [
            self._decisions[t].to_dict()
            for t in self._pending_order
            if self._decisions.get(t) is not None and self._decisions[t].pending
        ]

    def terminal_decision(self, task_id: str) -> Optional[dict[str, Any]]:
        """内存 terminal 裁决（权威）只读视图。"""
        dec = self._decisions.get(task_id)
        return dec.to_dict() if dec else None

    def lifecycle(self, task_id: str) -> dict[str, Any]:
        """task 生命周期合并视图（DB 记录 + 内存裁决 + durability 标记；供后续 API）。"""
        record: Optional["TaskRecord"] = None
        read_ok = True
        try:
            record = gov_store.get_task(self._store, task_id)
        except Exception:  # noqa: BLE001
            read_ok = False
        dec = self._decisions.get(task_id)
        return {
            "found": read_ok and (record is not None or dec is not None),
            "record": record.to_dict() if record is not None else None,
            "memory_terminal": dec.to_dict() if dec is not None else None,
            "degraded_durability": bool(dec is not None and not dec.durable),
            "pending_terminal": bool(dec is not None and dec.pending),
            "owner_instance": self._owner,
            "store_readable": read_ok,
        }

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _cancel_underlying(self, task_id: str) -> None:
        handle = self._handles.get(task_id)
        if handle is not None and not handle.done:
            handle.cancel()

    def _attempt_terminal_write(self, dec: _TerminalDecision) -> bool:
        """单次 durable 尝试：重读最新行 → CAS 乐观写/采纳。成功 → durable=True。

        返回是否已 durable（本函数内成功或 DB 已 terminal 采纳）。
        """
        dec.attempts += 1
        try:
            record = gov_store.get_task(self._store, dec.task_id)
        except Exception as exc:  # noqa: BLE001
            dec.last_error = f"read: {exc}"
            dec.pending = True
            return False
        if record is None:
            dec.last_error = (
                "no task row（durable 无从写；pending 由 startup reconciliation 兜底）"
            )
            dec.pending = True
            return False
        if record.is_terminal:
            # 已由他处（out-of-band / 其它收敛方）落库 → 采纳 DB 事实（内存裁决以 DB 为真），
            # 防止 memory/DB 两套 terminal 状态并存
            dec.status = record.status
            dec.terminal_reason = record.terminal_reason or dec.terminal_reason
            dec.error_kind = record.error_kind
            dec.error = record.error
            dec.counters_snapshot = record.counters_snapshot
            dec.superseded_by = record.superseded_by
            dec.finished_at = record.finished_at or dec.finished_at
            dec.underlying_linger_observed = record.underlying_linger_observed
            dec.durable = True
            dec.pending = False
            dec.last_error = None
            return True
        try:
            ok = gov_store.terminal_update(
                self._store,
                dec.task_id,
                expected_version=record.version,
                status=dec.status,
                terminal_reason=dec.terminal_reason,
                error_kind=dec.error_kind,
                error=dec.error,
                counters_snapshot=dec.counters_snapshot,
                superseded_by=dec.superseded_by,
                underlying_linger_observed=dec.underlying_linger_observed,
                finished_at=dec.finished_at,
            )
        except Exception as exc:  # noqa: BLE001
            dec.last_error = f"update: {exc}"
            dec.pending = True
            return False
        if ok:
            dec.durable = True
            dec.pending = False
            dec.last_error = None
            return True
        # rowcount==0：version 漂移 / 并发 out-of-band 写 → 交由下一轮重试重读
        dec.last_error = "CAS rowcount==0（version 漂移）；重读重试"
        dec.pending = True
        return False

    def _prune_pending_order(self) -> None:
        self._pending_order = [
            t
            for t in self._pending_order
            if self._decisions.get(t) is not None and self._decisions[t].pending
        ]

    # -- funnel result helpers -------------------------------------------
    def _funnel_result(
        self,
        dec: _TerminalDecision,
        *,
        winner: bool,
        already_terminal: bool,
    ) -> dict[str, Any]:
        if dec.pending:
            if dec.task_id not in self._pending_order:
                self._pending_order.append(dec.task_id)
        else:
            self._prune_pending_order()
        return {
            "task_id": dec.task_id,
            "status": dec.status,
            "terminal_reason": dec.terminal_reason,
            "error_kind": dec.error_kind,
            "error": dec.error,
            "counters_snapshot": dec.counters_snapshot,
            "superseded_by": dec.superseded_by,
            "finished_at": dec.finished_at,
            "underlying_linger_observed": dec.underlying_linger_observed,
            "winner": winner,
            "already_terminal": already_terminal,
            "durable": dec.durable,
            "pending": dec.pending,
            "degraded_durability": not dec.durable and dec.pending,
            "attempts": dec.attempts,
            "last_error": dec.last_error,
            "found": True,
            "adopted_from_db": False,
        }

    @staticmethod
    def _already_terminal_db_result(
        task_id: str, record: "TaskRecord"
    ) -> dict[str, Any]:
        """DB 已 terminal → no-op 采纳（race 败者 / 跨实例收敛的客观事实）。"""
        return {
            "task_id": task_id,
            "status": record.status,
            "terminal_reason": record.terminal_reason,
            "error_kind": record.error_kind,
            "error": record.error,
            "counters_snapshot": record.counters_snapshot,
            "superseded_by": record.superseded_by,
            "finished_at": record.finished_at,
            "underlying_linger_observed": record.underlying_linger_observed,
            "winner": False,
            "already_terminal": True,
            "durable": True,
            "pending": False,
            "degraded_durability": False,
            "attempts": 0,
            "last_error": None,
            "found": True,
            "adopted_from_db": True,
        }

    @staticmethod
    def _unknown_result(task_id: str, status: str, treason: str) -> dict[str, Any]:
        return {
            "task_id": task_id,
            "status": status,
            "terminal_reason": treason,
            "error_kind": None,
            "error": None,
            "counters_snapshot": None,
            "superseded_by": None,
            "finished_at": None,
            "underlying_linger_observed": None,
            "winner": False,
            "already_terminal": False,
            "durable": False,
            "pending": False,
            "degraded_durability": False,
            "attempts": 0,
            "last_error": None,
            "found": False,
            "adopted_from_db": False,
        }

    @staticmethod
    def _coerce_terminal(reason: Any) -> tuple[str, str]:
        """reason（TaskStatus|TerminalReason|str）→ (status_value, terminal_reason_value)。

        仅接受终态；running 显式拒绝（funnel 只做 terminal 收敛）。
        """
        if isinstance(reason, TerminalReason):
            status = TaskStatus(reason.value)
            return status.value, reason.value
        if isinstance(reason, TaskStatus):
            if reason == TaskStatus.RUNNING:
                raise GovernanceControllerError(
                    "funnel 只接受终态；running 不可 terminalize"
                )
            return reason.value, reason.value
        if isinstance(reason, str):
            if reason not in _TERMINAL_STATUS_VALUES:
                raise GovernanceControllerError(f"非法 terminal reason：{reason!r}")
            return reason, reason
        raise GovernanceControllerError(f"非法 reason 类型：{type(reason).__name__}")

    @staticmethod
    def _validate_terminal_fields(
        status: str,
        error_kind: Optional[str],
        superseded_by: Optional[str],
    ) -> None:
        if error_kind is not None and error_kind not in {e.value for e in ErrorKind}:
            raise GovernanceControllerError(f"非法 error_kind：{error_kind!r}")
        if status == TaskStatus.SUPERSEDED.value and not superseded_by:
            raise GovernanceControllerError(
                "superseded 必须携带 superseded_by（新 task_id）"
            )
        if status != TaskStatus.SUPERSEDED.value and superseded_by is not None:
            raise GovernanceControllerError("superseded_by 仅在 superseded 时使用")


#: 进程级默认 controller（单实例假设，Spec §5.3）；store 不可用 → None（server fail-closed）
_controller_instance: Optional[GovernanceController] = None
_controller_disabled_reason: Optional[str] = None


def reset_controller() -> None:
    """测试用：清空进程级 controller 单例。"""
    global _controller_instance, _controller_disabled_reason
    _controller_instance = None
    _controller_disabled_reason = None


def get_controller() -> Optional[GovernanceController]:
    """返回默认 controller（store 复用 get_store；单实例）。"""
    global _controller_instance, _controller_disabled_reason
    if _controller_instance is not None:
        return _controller_instance
    if _controller_disabled_reason is not None:
        return None
    store = gov_store.get_store()
    if store is None:
        _controller_disabled_reason = "governance store 不可用（c1 fail-closed）"
        return None
    try:
        _controller_instance = GovernanceController(store)
        return _controller_instance
    except Exception as exc:  # noqa: BLE001
        _controller_disabled_reason = str(exc)
        logger.error("GovernanceController 初始化失败：%s", exc)
        return None
