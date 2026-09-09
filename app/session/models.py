"""Multi-Session domain models（字段唯一权威；sessions 表 DDL 从本文件机械导出）。

与 F8 governance models.py 同纪律：models / SQL DDL / to_dict 只允许一套字段定义，
禁止各自再定义一套。

Identity 契约（Decision Closure #1）：
    Session.session_id == TaskRecord.thread_id == ResearchRun.thread_id == WS thread_id
    == checkpoint thread_id
Session 与 Thread 为 1:1；不新增第二套 Session/Thread 关联 ID；
TaskRecord.parent_session 保持 NULL / unused（不得复用）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class SessionStatus(str, Enum):
    """Session 容器状态（最终只采用两态，见 Decision Closure #3）。

    明确不实现 CREATED / PAUSED / COMPLETED / FAILED —— 它们没有 Session 容器层面的
    真实生命周期语义（Task / Runtime 的执行状态继续由 GovernanceController 负责）。
    """

    ACTIVE = "active"
    ARCHIVED = "archived"


@dataclass
class Session:
    """持久化 Session 记录（与 sessions 行一一对应）。"""

    session_id: str
    title: str
    status: str = SessionStatus.ACTIVE.value
    description: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @property
    def is_archived(self) -> bool:
        return self.status == SessionStatus.ARCHIVED.value

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Session":
        """DB 行 → 模型（时间列双后端归一）。"""
        return cls(
            session_id=row["session_id"],
            title=row["title"],
            description=row.get("description"),
            status=row["status"],
            created_at=_as_str(row.get("created_at")),
            updated_at=_as_str(row.get("updated_at")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _as_str(value: Any) -> Optional[str]:
    """DB 时间值归一为 str（sqlite TEXT 原样；PG timestamptz 读取为 datetime → ISO）。"""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    iso = getattr(value, "isoformat", None)
    return iso() if iso else str(value)
