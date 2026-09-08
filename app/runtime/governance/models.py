"""F8 governance domain models（Plan Rev2 §2/§3 单一字段权威；无 queued）。

本文件是 TaskRecord / Event 字段的**唯一权威**：store DDL 与 telemetry 均从此导出，
禁止 models/schema/telemetry 各自定义一套。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class TaskStatus(str, Enum):
    """Task 生命周期状态（无 queued；running + 8 终态）。"""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    BUDGET_EXCEEDED = "budget_exceeded"
    SUPERSEDED = "superseded"
    ABORTED = "aborted"
    ORPHAN_RECLAIMED = "orphan_reclaimed"


TERMINAL_STATUSES = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.TIMED_OUT,
        TaskStatus.BUDGET_EXCEEDED,
        TaskStatus.SUPERSEDED,
        TaskStatus.ABORTED,
        TaskStatus.ORPHAN_RECLAIMED,
    }
)


class TerminalReason(str, Enum):
    """terminal 细分原因（status=终态时必填）。"""

    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    BUDGET_EXCEEDED = "budget_exceeded"
    SUPERSEDED = "superseded"
    ABORTED = "aborted"
    ORPHAN_RECLAIMED = "orphan_reclaimed"


class ErrorKind(str, Enum):
    """failed 的错误分类（P1-11：不把所有 governance 异常归同一语义）。"""

    AGENT_FAILURE = "agent_failure"
    GOVERNANCE_CONTROL_FAILURE = "governance_control_failure"
    FRAMEWORK_RECURSION_SAFETY = "framework_recursion_safety"


#: terminal status → 默认 terminal_reason 映射（helper；controller 可显式传 reason）
_STATUS_TO_REASON: dict[str, str] = {s.value: s.value for s in TERMINAL_STATUSES}


def terminal_reason_for(status: TaskStatus) -> TerminalReason:
    """status 对应默认 terminal_reason（仅对终态有效）。"""
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"{status.value} 不是终态，无 terminal_reason")
    return TerminalReason(_STATUS_TO_REASON[status.value])


@dataclass
class TaskRecord:
    """持久化 Task 记录（与 governance_tasks 行一一对应；JSON 列在此处归一为 dict）。"""

    task_id: str
    thread_id: str
    owner_instance: str
    status: str = TaskStatus.RUNNING.value
    run_id: Optional[str] = None
    terminal_reason: Optional[str] = None
    error_kind: Optional[str] = None
    error: Optional[str] = None
    policy_snapshot: dict[str, Any] = field(default_factory=dict)
    effective_limits: dict[str, Any] = field(default_factory=dict)
    counters_snapshot: Optional[dict[str, Any]] = None
    superseded_by: Optional[str] = None
    parent_session: Optional[str] = None
    underlying_linger_observed: Optional[bool] = None
    version: int = 0
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None

    @property
    def is_terminal(self) -> bool:
        return self.status in {s.value for s in TERMINAL_STATUSES}

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "TaskRecord":
        """DB 行 → 模型（JSON/时间列双后端归一）。"""
        return cls(
            task_id=row["task_id"],
            thread_id=row["thread_id"],
            run_id=row.get("run_id"),
            status=row["status"],
            terminal_reason=row.get("terminal_reason"),
            error_kind=row.get("error_kind"),
            error=row.get("error"),
            policy_snapshot=_as_json(row.get("policy_snapshot")) or {},
            effective_limits=_as_json(row.get("effective_limits")) or {},
            counters_snapshot=_as_json(row.get("counters_snapshot")),
            superseded_by=row.get("superseded_by"),
            parent_session=row.get("parent_session"),
            underlying_linger_observed=_as_bool(row.get("underlying_linger_observed")),
            owner_instance=row["owner_instance"],
            version=row.get("version", 0) or 0,
            created_at=_as_str(row.get("created_at")),
            started_at=_as_str(row.get("started_at")),
            finished_at=_as_str(row.get("finished_at")),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "task_id": self.task_id,
            "thread_id": self.thread_id,
            "run_id": self.run_id,
            "status": self.status,
            "terminal_reason": self.terminal_reason,
            "error_kind": self.error_kind,
            "error": self.error,
            "policy_snapshot": self.policy_snapshot,
            "effective_limits": self.effective_limits,
            "counters_snapshot": self.counters_snapshot,
            "superseded_by": self.superseded_by,
            "parent_session": self.parent_session,
            "underlying_linger_observed": self.underlying_linger_observed,
            "owner_instance": self.owner_instance,
            "version": self.version,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
        return out


@dataclass
class GovernanceEvent:
    """append-only 事件（与 governance_events 行对应；event_id 跨 live/durable/replay 唯一）。"""

    event_id: str
    task_id: str
    thread_id: str
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    seq: Optional[int] = None  # durable 成功由 sequencer 分配；live 预览无 seq
    run_id: Optional[str] = None
    durable: bool = True
    created_at: Optional[str] = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "GovernanceEvent":
        return cls(
            event_id=row["event_id"],
            task_id=row["task_id"],
            thread_id=row["thread_id"],
            run_id=row.get("run_id"),
            seq=row.get("seq"),
            event_type=row["event_type"],
            payload=_as_json(row.get("payload")) or {},
            durable=_as_bool(row.get("durable"), default=True),
            created_at=_as_str(row.get("created_at")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "task_id": self.task_id,
            "thread_id": self.thread_id,
            "run_id": self.run_id,
            "seq": self.seq,
            "event_type": self.event_type,
            "payload": self.payload,
            "durable": self.durable,
            "created_at": self.created_at,
        }


def _as_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return value


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    return default


def _as_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    # datetime 等 → ISO（PG timestamptz 读取归一）
    iso = getattr(value, "isoformat", None)
    return iso() if iso else str(value)
