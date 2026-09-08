"""F8 Step 4 Batch 1/2 — governance 内部 lifecycle 事件单写者 + replay 读取（observation-plane）。

边界（Step4 Audit/Spec §5–§8 / Batch 2 cursor contract）：
- TaskRecord 是 terminal truth；event 是 observation：可 delayed/missing/durable_gap，
  绝不驱动 TaskRecord 状态；
- 单写者：分配 `(task_id, seq)`（task-scoped 全序，UNIQUE(task_id, seq)）；
- `event_id` publish 时生成；live/durable/replay 同一身份；PK 幂等（重发不重复）；
- 只写必要 lifecycle：task_started / task_completed / task_failed / task_cancelled /
  task_timed_out / task_budget_exceeded / task_aborted（+ superseded 映射备查）；
- durable 写失败 = observation failure → fail-open + durability_gap（返回 gap），
  **不影响** control（budget/cancel/timeout 仍由冻结 funnel/controller 内存权威执行）；
- **replay cursor = (task_id, governance_seq)**（Batch 2 冻结契约）：查询限定
  thread_id + task_id、`seq > since_seq`、ASC、limit（默认 200、上限 1000）、返回 next_seq；
  同 task 内无洞（单写者）；gap 仅同 task 语义；跨 task 无全局无洞承诺；
- monitor 不写 DB、不复制 monitor seq；本模块不复制 monitor._emit。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from app.runtime.governance import store as gov_store

logger = logging.getLogger("deepsearch.runtime.governance.events")

#: TaskRecord status → lifecycle event_type（Batch 1 白名单）
STATUS_TO_EVENT_TYPE: dict[str, str] = {
    "running": "task_started",
    "completed": "task_completed",
    "failed": "task_failed",
    "cancelled": "task_cancelled",
    "timed_out": "task_timed_out",
    "budget_exceeded": "task_budget_exceeded",
    "aborted": "task_aborted",
    "superseded": "task_superseded",
    "orphan_reclaimed": "task_orphan_reclaimed",
}

_MAX_SEQ_CONFLICT_RETRY = 3


def lifecycle_event(
    store: Any,
    *,
    task_id: str,
    thread_id: str,
    run_id: Optional[str],
    status: str,
    terminal_reason: Optional[str] = None,
    error_kind: Optional[str] = None,
    error: Optional[str] = None,
    counters_snapshot: Optional[dict[str, Any]] = None,
    finished_at: Optional[str] = None,
    event_id: Optional[str] = None,
) -> dict[str, Any]:
    """发布一条 durable lifecycle event（观察面，fail-open）。

    返回 {"durable": bool, "event_id": str, "seq": Optional[int],
          "durability_gap": bool, "duplicate": bool}
    - durable=True：行已落库（或该 event_id 已存在=幂等成功）；
    - durable=False：写失败（含 seq 冲突耗尽）→ durability_gap=True（仅 audit，
      不阻断任何 control）；
    - 不修改 TaskRecord。
    """
    event_type = STATUS_TO_EVENT_TYPE.get(status, f"task_{status}")
    eid = event_id or uuid.uuid4().hex
    payload: dict[str, Any] = {
        "status": status,
    }
    if terminal_reason is not None:
        payload["terminal_reason"] = terminal_reason
    if error_kind is not None:
        payload["error_kind"] = error_kind
    if error is not None:
        payload["error"] = error[:800]
    if counters_snapshot is not None:
        payload["counters_snapshot"] = counters_snapshot
    if finished_at is not None:
        payload["finished_at"] = finished_at

    for attempt in range(_MAX_SEQ_CONFLICT_RETRY):
        try:
            rows = store.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq "
                "FROM governance_events WHERE task_id=%s",
                (task_id,),
            )
            next_seq = int(rows[0]["next_seq"]) if rows else 1
            with store.transaction() as tx:
                rc = tx.execute_rc(
                    "INSERT INTO governance_events "
                    "(event_id, task_id, thread_id, run_id, seq, event_type, "
                    " payload, durable, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (event_id) DO NOTHING",
                    (
                        eid,
                        task_id,
                        thread_id,
                        run_id,
                        next_seq,
                        event_type,
                        gov_store.json_param(payload, store.dialect),
                        True,
                        finished_at or _now_iso(),
                    ),
                )
            if rc == 0:
                return {
                    "durable": True,
                    "event_id": eid,
                    "seq": next_seq,
                    "durability_gap": False,
                    "duplicate": True,
                }
            return {
                "durable": True,
                "event_id": eid,
                "seq": next_seq,
                "durability_gap": False,
                "duplicate": False,
            }
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            # seq 唯一冲突（并发同 task 发布）→ 重试；其它异常 → fail-open gap
            msg = str(exc).lower()
            is_seq_conflict = "unique" in msg and ("task_id" in msg or "seq" in msg)
            if not is_seq_conflict:
                logger.warning(
                    "lifecycle event durable 写失败（task %s）：%s", task_id, exc
                )
                return {
                    "durable": False,
                    "event_id": eid,
                    "seq": None,
                    "durability_gap": True,
                    "duplicate": False,
                    "error": str(exc)[:200],
                }
            if attempt == _MAX_SEQ_CONFLICT_RETRY - 1:
                logger.warning(
                    "lifecycle event seq 冲突耗尽重试（task %s）：%s",
                    task_id,
                    last_error,
                )
                return {
                    "durable": False,
                    "event_id": eid,
                    "seq": None,
                    "durability_gap": True,
                    "duplicate": False,
                    "error": str(last_error)[:200],
                }
    return {
        "durable": False,
        "event_id": eid,
        "seq": None,
        "durability_gap": True,
        "duplicate": False,
    }


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Batch 2：replay 读取（只读；不触碰写路径/schema）
# ---------------------------------------------------------------------------
DEFAULT_REPLAY_LIMIT = 200
MAX_REPLAY_LIMIT = 1000


def replay_events(
    store: Any,
    *,
    thread_id: str,
    task_id: str,
    since_seq: int = 0,
    limit: int = DEFAULT_REPLAY_LIMIT,
) -> dict[str, Any]:
    """task-scoped durable replay（Batch 2 冻结 cursor contract）。

    返回 {"events": [normalized durable event], "next_seq": int|None, "gap": bool}
    - 查询限定 thread_id + task_id + seq > since_seq，按 seq ASC；
    - gap 仅同 task 语义：期望连续 seq（since_seq+1 起）；跨 task 不检测、不承诺。
    """
    lim = min(max(int(limit), 1), MAX_REPLAY_LIMIT)
    rows = store.execute(
        "SELECT * FROM governance_events "
        "WHERE thread_id=%s AND task_id=%s AND seq>%s "
        "ORDER BY seq LIMIT %s",
        (thread_id, task_id, int(since_seq), lim),
    )
    events = [_event_view(r) for r in rows]
    gap = False
    expected = int(since_seq) + 1
    for ev in events:
        if ev["governance_seq"] != expected:
            gap = True
            break
        expected += 1
    return {
        "events": events,
        "next_seq": events[-1]["governance_seq"] if events else None,
        "gap": gap,
    }


def _event_view(row: dict[str, Any]) -> dict[str, Any]:
    from app.runtime.governance.models import _as_bool, _as_json, _as_str  # noqa: PLC0415

    return {
        "event_id": row["event_id"],
        "task_id": row["task_id"],
        "thread_id": row["thread_id"],
        "run_id": _as_str(row.get("run_id")),
        "governance_seq": int(row["seq"]),
        "event_type": row["event_type"],
        "payload": _as_json(row.get("payload")) or {},
        "durable": _as_bool(row.get("durable"), default=True),
        "created_at": _as_str(row.get("created_at")),
    }
