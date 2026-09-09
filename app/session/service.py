"""Multi-Session service（Session 容器域逻辑；Task/Runtime 生命周期仍归 governance）。

职责（Decision Closure）：
- Session 元数据 CRUD 与状态机（ACTIVE ⇄ ARCHIVED；archive 前置 running-task 守卫）；
- Session ↔ Task 归属校验（task.thread_id == session_id）与 thread 作用域只读聚合；
- 隔离：所有 Session-scoped 操作先验证 session 存在 → 状态合法 → 归属，否则拒绝；
- 不拥有 Runtime lifecycle：不新增 Session cancel / budget / controller / execution 状态机；
- Task 创建/取消复用 governance service（gov_service.submit_task / cancel_task / list_tasks）。

依赖方向：app/session → app/runtime/governance（复用既有 Task 能力）+ 可选 lazy 读
app/research（query enrich，fail-open）。本模块不被 governance / research import。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.runtime.governance import service as gov_service
from app.session import store as sess_store
from app.session.models import Session, SessionStatus
from app.utils.session_id import is_safe_thread_id

DEFAULT_SESSION_TITLE = "未命名会话"
MAX_TITLE_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 2000
DEFAULT_LIST_LIMIT = 100
MAX_LIST_LIMIT = 500
DEFAULT_TASK_LIST_LIMIT = 50
MAX_TASK_LIST_LIMIT = 200


class SessionError(RuntimeError):
    """session 域错误基类（server 层映射 HTTP 状态）。"""


class SessionNotFoundError(SessionError):
    """session 不存在。"""


class SessionStateError(SessionError):
    """非法 session 状态（archived 创建任务 / running 归档 / 重复注册等）。"""


class SessionValidationError(SessionError):
    """请求非法（title 超长 / thread_id 字符集非法 / 分页越界）。"""


class TaskNotFoundError(SessionError):
    """task 不存在。"""


class TaskNotInSessionError(SessionError):
    """task 存在但不属于该 session（拒绝，防跨 Session 操作）。"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _store(controller: Any) -> Any:
    """复用 controller 持有的 governance store（与 gov_service 同先例）。"""
    return controller._store  # noqa: SLF001


def _require_session(controller: Any, session_id: str) -> Session:
    sess = sess_store.get_session(_store(controller), session_id)
    if sess is None:
        raise SessionNotFoundError(f"会话不存在: {session_id}")
    return sess


def _default_summary() -> dict[str, Any]:
    return {"task_count": 0, "running_tasks": 0, "latest_task": None}


# ---------------------------------------------------------------------------
# Session CRUD / 生命周期
# ---------------------------------------------------------------------------
def create_session(
    controller: Any,
    *,
    title: Optional[str] = None,
    description: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> Session:
    """创建 Session（ACTIVE）。

    - thread_id 未提供 → 服务端生成安全 uuid（session_id == thread_id）；
    - thread_id 提供 → 既有历史 Thread 注册（校验 [A-Za-z0-9_-]{1,128}；已注册 → 拒绝，不扩大
      Session Migration 范围）；
    - 不得出现 session_id != thread_id。
    """
    raw_title = (title or "").strip()
    if len(raw_title) > MAX_TITLE_LENGTH:
        raise SessionValidationError(f"title 过长（最多 {MAX_TITLE_LENGTH} 字符）")
    title_final = raw_title or DEFAULT_SESSION_TITLE
    if description is not None and len(description) > MAX_DESCRIPTION_LENGTH:
        raise SessionValidationError(
            f"description 过长（最多 {MAX_DESCRIPTION_LENGTH} 字符）"
        )
    if thread_id is None:
        session_id = uuid.uuid4().hex
    else:
        if not is_safe_thread_id(thread_id):
            raise SessionValidationError(
                "thread_id 只能包含字母、数字、下划线或连字符（最多 128 字符）"
            )
        session_id = thread_id
        if sess_store.get_session(_store(controller), session_id) is not None:
            raise SessionStateError(
                f"thread 已注册为 Session: {session_id}（不可重复注册）"
            )
    now = _now_iso()
    sess = Session(
        session_id=session_id,
        title=title_final,
        description=description,
        status=SessionStatus.ACTIVE.value,
        created_at=now,
        updated_at=now,
    )
    try:
        sess_store.create_session(_store(controller), sess)
    except Exception:  # noqa: BLE001 —— PK 冲突（uuid 碰撞/并发重复注册）映射为 409
        if sess_store.get_session(_store(controller), session_id) is not None:
            raise SessionStateError(f"Session 已存在: {session_id}") from None
        raise
    return sess


def list_sessions(
    controller: Any,
    *,
    include_archived: bool = False,
    limit: int = DEFAULT_LIST_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    """Session 列表（+ task_count / running_tasks / latest_task 聚合）。"""
    if not 1 <= limit <= MAX_LIST_LIMIT:
        raise SessionValidationError(f"limit 必须在 1..{MAX_LIST_LIMIT} 之间")
    if offset < 0:
        raise SessionValidationError("offset 不能为负")
    sessions, total = sess_store.list_sessions(
        _store(controller),
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    )
    summary_map = sess_store.task_summary(
        _store(controller), [s.session_id for s in sessions]
    )
    items: list[dict[str, Any]] = []
    for sess in sessions:
        item = sess.to_dict()
        item.update(summary_map.get(sess.session_id, _default_summary()))
        items.append(item)
    return {"sessions": items, "total": total}


def assert_session_active(controller: Any, session_id: str) -> Session:
    """Session 存在 + 非 archived（创建任务等写操作前置守卫）。"""
    sess = _require_session(controller, session_id)
    if sess.is_archived:
        raise SessionStateError(
            f"会话已归档（archived）: {session_id}（不允许创建新任务）"
        )
    return sess


def archive_session(controller: Any, session_id: str) -> dict[str, Any]:
    """逻辑归档 ACTIVE → ARCHIVED（幂等）。

    前置：session 存在；无 running Task（不允许 ARCHIVED + RUNNING 未定义态）。
    不物理删除 session / task / run / 报告 / checkpoint。
    """
    sess = _require_session(controller, session_id)
    if sess.is_archived:
        return {
            "session_id": session_id,
            "status": SessionStatus.ARCHIVED.value,
            "already_archived": True,
            "updated_at": sess.updated_at,
        }
    # 友好错误提示：预读 running（非权威门禁）
    records = gov_service.list_tasks(
        controller, thread_id=session_id, limit=MAX_TASK_LIST_LIMIT
    )
    running_ids = [r.task_id for r in records if r.status == "running"]
    if running_ids:
        raise SessionStateError(
            f"会话存在运行中的任务（{len(running_ids)} 个），"
            f"请先取消后再归档: {', '.join(running_ids[:5])}"
        )
    # 权威门禁：单事务条件更新（active 且无 running Task 才归档），关闭并发窗口
    now = _now_iso()
    rc = sess_store.archive_if_no_running(_store(controller), session_id, now)
    if rc != 1:
        # 并发失败：要么已被他处归档（→ 幂等返回），要么 running Task 恰在窗口内出现
        fresh = _require_session(controller, session_id)
        if fresh.is_archived:
            return {
                "session_id": session_id,
                "status": SessionStatus.ARCHIVED.value,
                "already_archived": True,
                "updated_at": fresh.updated_at,
            }
        raise SessionStateError(
            "会话存在运行中的任务，请先取消后再归档（归档守卫未通过）"
        )
    return {
        "session_id": session_id,
        "status": SessionStatus.ARCHIVED.value,
        "already_archived": False,
        "updated_at": now,
    }


def unarchive_session(controller: Any, session_id: str) -> dict[str, Any]:
    """恢复 ARCHIVED → ACTIVE（幂等）。"""
    sess = _require_session(controller, session_id)
    if not sess.is_archived:
        return {
            "session_id": session_id,
            "status": SessionStatus.ACTIVE.value,
            "already_active": True,
            "updated_at": sess.updated_at,
        }
    now = _now_iso()
    sess_store.set_status(
        _store(controller), session_id, SessionStatus.ACTIVE.value, now
    )
    return {
        "session_id": session_id,
        "status": SessionStatus.ACTIVE.value,
        "already_active": False,
        "updated_at": now,
    }


# ---------------------------------------------------------------------------
# Session ↔ Task（隔离 + 聚合）
# ---------------------------------------------------------------------------
def validate_task_in_session(controller: Any, session_id: str, task_id: str) -> Any:
    """Session-scoped Task 操作前置：session 存在 且 task.thread_id == session_id。

    否则抛 TaskNotFoundError / TaskNotInSessionError（server 映射 404，拒绝跨 Session 操作）。
    """
    _require_session(controller, session_id)
    rec = gov_service.get_task(controller, task_id)
    if rec is None:
        raise TaskNotFoundError(f"任务不存在: {task_id}")
    if rec.thread_id != session_id:
        raise TaskNotInSessionError(
            f"任务不属于该会话: {task_id}（session={session_id}）"
        )
    return rec


def list_session_tasks(
    controller: Any,
    session_id: str,
    *,
    limit: int = DEFAULT_TASK_LIST_LIMIT,
) -> list[dict[str, Any]]:
    """session 下 Task 列表（created_at 降序；archived 仍可读，历史可追溯）。

    只返回 thread_id == session_id 的 task（结构性隔离）；query 字段来自 research 面
    enrich（fail-open；research store disabled → None）。
    """
    if not 1 <= limit <= MAX_TASK_LIST_LIMIT:
        raise SessionValidationError(f"limit 必须在 1..{MAX_TASK_LIST_LIMIT} 之间")
    _require_session(controller, session_id)
    records = gov_service.list_tasks(controller, thread_id=session_id, limit=limit)
    tasks = [gov_service.task_dict(r) for r in records]
    _attach_questions(tasks)
    return tasks


def get_session_detail(
    controller: Any,
    session_id: str,
    *,
    task_limit: int = DEFAULT_TASK_LIST_LIMIT,
) -> dict[str, Any]:
    """Session Detail = 元数据 + Session Tasks + 最新执行信息（task 聚合）。"""
    sess = _require_session(controller, session_id)
    tasks = list_session_tasks(controller, session_id, limit=task_limit)
    summary = sess_store.task_summary(_store(controller), [session_id]).get(
        session_id, _default_summary()
    )
    return {
        "session": sess.to_dict(),
        "tasks": tasks,
        "task_count": summary["task_count"],
        "running_tasks": summary["running_tasks"],
        "latest_task": summary["latest_task"],
    }


# ---------------------------------------------------------------------------
# query enrich（可选，fail-open；Decision Closure #9）
# ---------------------------------------------------------------------------
def _attach_questions(tasks: list[dict[str, Any]]) -> None:
    """为 task dict 附 research_runs.question（run_id 关联；research 面不可用 → None）。

    不做跨库 JOIN —— governance 与 research 是两个 store；此处是独立的只读 enrich，
    fail-open，绝不改变 governance 核心可用性。
    """
    run_ids: list[str] = []
    seen: set[str] = set()
    for t in tasks:
        rid = t.get("run_id")
        t["query"] = None
        if rid and rid not in seen:
            seen.add(rid)
            run_ids.append(rid)
    if not run_ids:
        return
    qmap = _research_question_map(run_ids)
    for t in tasks:
        rid = t.get("run_id")
        if rid and rid in qmap:
            t["query"] = qmap[rid]


def _research_question_map(run_ids: list[str]) -> dict[str, str]:
    try:  # lazy import：避免模块导入期耦合 research plane
        from app.research import store as rstore  # noqa: PLC0415

        store = rstore.get_store()
        if store is None:
            return {}
        placeholders = ", ".join(["%s"] * len(run_ids))
        rows = store.execute(
            "SELECT run_id, question FROM research_runs "
            f"WHERE run_id IN ({placeholders})",
            tuple(run_ids),
        )
        return {str(r["run_id"]): str(r["question"]) for r in rows}
    except Exception:  # noqa: BLE001 —— fail-open（enrich 失败不影响 governance）
        return {}
