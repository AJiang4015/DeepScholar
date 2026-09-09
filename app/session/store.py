"""Multi-Session store（函数式 repository；接收 governance store 连接句柄）。

- sessions 表族与 governance_tasks/governance_events 同库（governance store 连接/后端选择/
  migration runner 全复用 —— Decision Closure #2：无 Session 独立 DB / env / Store 单例 / 依赖）；
- 与 governance store / research store 同一套函数式风格：只读用 store.execute；
  写一律 `with store.transaction() as tx:` + tx.execute / tx.execute_rc；%s 参数化双方言复用；
- 跨族只读：task_summary 在同一 store 上读 governance_tasks（thread_id 作用域），
  不跨库 JOIN、不改 governance 写路径。
"""

from __future__ import annotations

from typing import Any, Optional

from app.session.models import Session, SessionStatus, _as_str

#: task_summary 只读列（latest_task 的字段面）
_TASK_SUMMARY_COLUMNS = (
    "thread_id, task_id, run_id, status, terminal_reason, "
    "created_at, started_at, finished_at"
)

_ACTIVE_STATUS = SessionStatus.ACTIVE.value
_ARCHIVED_STATUS = SessionStatus.ARCHIVED.value
_RUNNING_STATUS = "running"


def create_session(store: Any, session: "Session") -> None:
    """插入 session 行（重复 session_id → store 抛 IntegrityError/UniqueViolation）。"""
    with store.transaction() as tx:
        tx.execute(
            "INSERT INTO sessions (session_id, title, description, status, "
            "created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s)",
            (
                session.session_id,
                session.title,
                session.description,
                session.status,
                session.created_at,
                session.updated_at,
            ),
        )


def get_session(store: Any, session_id: str) -> Optional["Session"]:
    rows = store.execute("SELECT * FROM sessions WHERE session_id=%s", (session_id,))
    if not rows:
        return None
    return Session.from_row(rows[0])


def list_sessions(
    store: Any,
    *,
    include_archived: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list["Session"], int]:
    """session 页查询（updated_at 降序，session_id 兜底稳定排序）。

    include_archived=False（缺省）→ 只返回 active（Frontend Sidebar 语义）；
    include_archived=True → 全部。返回 (页 Sessions, 匹配总数 total)。
    """
    where = ""
    params: tuple[Any, ...] = ()
    if not include_archived:
        where = " WHERE status=%s"
        params = (_ACTIVE_STATUS,)
    total_row = store.execute(f"SELECT COUNT(*) AS c FROM sessions{where}", params)
    total = int(total_row[0]["c"])
    rows = store.execute(
        "SELECT * FROM sessions"
        f"{where} ORDER BY updated_at DESC, session_id LIMIT %s OFFSET %s",
        params + (limit, offset),
    )
    return [Session.from_row(r) for r in rows], total


def set_status(store: Any, session_id: str, status: str, updated_at: str) -> int:
    """更新 session 状态与 updated_at；返回受影响行数（0 = 行不存在）。"""
    with store.transaction() as tx:
        rc = tx.execute_rc(
            "UPDATE sessions SET status=%s, updated_at=%s WHERE session_id=%s",
            (status, updated_at, session_id),
        )
    return rc


def archive_if_no_running(store: Any, session_id: str, updated_at: str) -> int:
    """原子归档守卫：仅当 session 为 active 且该 thread 无 running Task 时置为 archived。

    单事务内以 EXISTS 子查询跨表检查（sessions 与 governance_tasks 同一 governance store /
    同事务，双方言可移植），关闭「读 running → 置 archived」两步之间的并发窗口
    （archive-vs-新任务创建）。返回受影响行数：1 = 归档成功；
    0 = session 非 active（不存在/已归档）或存在 running Task。
    """
    with store.transaction() as tx:
        rc = tx.execute_rc(
            "UPDATE sessions SET status=%s, updated_at=%s "
            "WHERE session_id=%s AND status=%s AND NOT EXISTS "
            "(SELECT 1 FROM governance_tasks WHERE thread_id=%s AND status=%s)",
            (
                _ARCHIVED_STATUS,
                updated_at,
                session_id,
                _ACTIVE_STATUS,
                session_id,
                _RUNNING_STATUS,
            ),
        )
    return rc


def task_summary(store: Any, thread_ids: list[str]) -> dict[str, dict[str, Any]]:
    """在 governance_tasks 上按 thread_id 集合做只读聚合（thread 作用域隔离天然成立）。

    返回 {thread_id: {"task_count": int, "running_tasks": int,
                      "latest_task": {…}|None}}；
    latest_task 取 (created_at DESC, task_id DESC) 最新一条，字段 = _TASK_SUMMARY_COLUMNS。
    """
    out: dict[str, dict[str, Any]] = {
        tid: {
            "task_count": 0,
            "running_tasks": 0,
            "latest_task": None,
        }
        for tid in thread_ids
    }
    if not thread_ids:
        return out
    placeholders = ", ".join(["%s"] * len(thread_ids))
    rows = store.execute(
        "SELECT "
        + _TASK_SUMMARY_COLUMNS
        + f" FROM governance_tasks WHERE thread_id IN ({placeholders}) "
        + "ORDER BY created_at DESC, task_id DESC",
        tuple(thread_ids),
    )
    seen_latest: set[str] = set()
    for row in rows:
        tid = row["thread_id"]
        bucket = out.setdefault(
            tid,
            {
                "task_count": 0,
                "running_tasks": 0,
                "latest_task": None,
            },
        )
        bucket["task_count"] += 1
        if row.get("status") == "running":
            bucket["running_tasks"] += 1
        if tid not in seen_latest:
            seen_latest.add(tid)
            bucket["latest_task"] = {
                "task_id": row["task_id"],
                "run_id": row.get("run_id"),
                "status": row.get("status"),
                "terminal_reason": row.get("terminal_reason"),
                "created_at": _as_str(row.get("created_at")),
                "started_at": _as_str(row.get("started_at")),
                "finished_at": _as_str(row.get("finished_at")),
            }
    return out
