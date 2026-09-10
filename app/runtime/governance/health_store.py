"""P2-2 S1 — Runtime Health Plane 数据访问层（health plane store；决策 D-Phase2-P2-2-001 / -012 / -015 / -017）。

职责边界（P2-2_SPEC_v2.md Rev 2.2 §3/§4.6/§12.2）：
- **只**承载 health plane（`governance_runtime_health`）的读写：心跳 UPSERT、健康行读取、
  running 候选只读查询、清理候选查询与删除；
- **MUST NOT** 写 `governance_tasks` 的任何 lifecycle 字段（status / terminal_reason / error /
  counters_snapshot / finished_at / version 等）——LA-1 / invariant I2；
- **MUST NOT** 绕过 terminal funnel 做终态收敛——LA-2 / I3（收敛只经
  `controller.finalize_with_event`，本模块不涉及）；
- **MUST NOT** 引入连接池 / 新依赖（D-Phase2-P2-2-015）；写路径一律单语句短事务；
- 时间语义：写入由调用方提供 `now_iso`（clock 注入在 writer/scanner 层，见 I10）；读取经
  `parse_ts()` 归一（sqlite 返回 str、PG 返回 datetime）。

SQL 纪律：统一 `%s` 参数化（sqlite 由 governance store 转 `?`）；UPSERT 使用双方言同形语法；
清理候选使用 LEFT JOIN 单查询（避免 N+1）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

#: lifecycle 非终态（只读过滤用；不得写回）
_RUNNING_STATUS = "running"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def parse_ts(value: Any) -> Optional[datetime]:
    """把 DB 时间值归一为 **aware UTC datetime**；不可解析返回 None。

    - sqlite（TEXT/str）与 PG（TIMESTAMPTZ/datetime）双方言统一入口；
    - naive datetime / 无时区 ISO 字符串 → 视为 UTC（仓库时间列约定为 UTC）；
    - None / 非法字符串 / 其它类型 → None（调用方按「不可解析 ⇒ 跳过该行」处理）。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        return (
            parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
        )
    return None


def _decode_json(value: Any) -> dict[str, Any]:
    """JSON 列归一：sqlite=TEXT / PG=JSONB → dict；非法/空 → {}。"""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _health_view(row: dict[str, Any]) -> dict[str, Any]:
    """健康行 → 归一视图（时间列为 aware datetime；beat_count 为 int）。"""
    return {
        "task_id": row["task_id"],
        "thread_id": row["thread_id"],
        "run_id": row.get("run_id"),
        "owner_instance": row["owner_instance"],
        "last_heartbeat_at": parse_ts(row.get("last_heartbeat_at")),
        "beat_count": int(row.get("beat_count") or 0),
        "created_at": parse_ts(row.get("created_at")),
        "updated_at": parse_ts(row.get("updated_at")),
    }


def _placeholders(count: int) -> str:
    return ", ".join(["%s"] * count)


# ---------------------------------------------------------------------------
# health plane 写入（单语句 UPSERT；不触碰 lifecycle 表）
# ---------------------------------------------------------------------------
def upsert_heartbeat(
    store: Any,
    *,
    task_id: str,
    thread_id: str,
    run_id: Optional[str],
    owner_instance: str,
    now_iso: str,
) -> bool:
    """写入一次心跳（首拍插入 / 后续更新）。

    - 首拍：`beat_count=1`、`created_at=updated_at=now_iso`；
    - 后续：`last_heartbeat_at/updated_at=now_iso`、`beat_count+1`、owner/thread/run 刷新；
    - 单语句 + `store.transaction()`；返回是否发生写入（受影响行数 ≥1）。
    """
    sql = (
        "INSERT INTO governance_runtime_health "
        "(task_id, thread_id, run_id, owner_instance, last_heartbeat_at, "
        " beat_count, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, 1, %s, %s) "
        "ON CONFLICT (task_id) DO UPDATE SET "
        "last_heartbeat_at=EXCLUDED.last_heartbeat_at, "
        "updated_at=EXCLUDED.updated_at, "
        "beat_count=governance_runtime_health.beat_count + 1, "
        "owner_instance=EXCLUDED.owner_instance, "
        "thread_id=EXCLUDED.thread_id, "
        "run_id=EXCLUDED.run_id"
    )
    params = (task_id, thread_id, run_id, owner_instance, now_iso, now_iso, now_iso)
    with store.transaction() as tx:
        rc = tx.execute_rc(sql, params)
    return rc >= 1


# ---------------------------------------------------------------------------
# health plane 读取
# ---------------------------------------------------------------------------
def get_health(store: Any, task_id: str) -> Optional[dict[str, Any]]:
    """读取单个任务的健康行（归一视图）；不存在 → None。"""
    rows = store.execute(
        "SELECT * FROM governance_runtime_health WHERE task_id=%s", (task_id,)
    )
    return _health_view(rows[0]) if rows else None


def list_health_for(store: Any, task_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    """批量读取健康行 → `{task_id: 归一视图}`（缺失的任务不出现在结果中）。"""
    ids = [str(t) for t in task_ids]
    if not ids:
        return {}
    rows = store.execute(
        "SELECT * FROM governance_runtime_health "
        f"WHERE task_id IN ({_placeholders(len(ids))})",
        tuple(ids),
    )
    return {row["task_id"]: _health_view(row) for row in rows}


def list_running_tasks(store: Any, limit: int) -> list[dict[str, Any]]:
    """只读：`governance_tasks` 中 status='running' 的候选（scanner 输入）。

    返回字段：task_id / thread_id / run_id / owner_instance / created_at / started_at /
    effective_limits（逐任务 `wall_clock_timeout` 来源，见 Spec §5.1）。
    **只读查询**，不写任何 lifecycle 字段。
    """
    rows = store.execute(
        "SELECT task_id, thread_id, run_id, owner_instance, status, created_at, "
        "started_at, effective_limits FROM governance_tasks WHERE status=%s "
        "ORDER BY created_at, task_id LIMIT %s",
        (_RUNNING_STATUS, int(limit)),
    )
    return [
        {
            "task_id": row["task_id"],
            "thread_id": row["thread_id"],
            "run_id": row.get("run_id"),
            "owner_instance": row["owner_instance"],
            "created_at": parse_ts(row.get("created_at")),
            "started_at": parse_ts(row.get("started_at")),
            "effective_limits": _decode_json(row.get("effective_limits")),
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# 清理（责任方 = scanner；controller/funnel 不触碰 health 表 — LA-4 / I11）
# ---------------------------------------------------------------------------
def list_cleanup_candidates(store: Any, limit: int) -> list[dict[str, Any]]:
    """健康行清理候选：对应任务已不存在，或已不在 running 状态（终态）。

    单查询 LEFT JOIN，按 `updated_at` 升序（最旧优先），受 `limit` 约束。
    """
    rows = store.execute(
        "SELECT h.* FROM governance_runtime_health h "
        "LEFT JOIN governance_tasks t ON t.task_id = h.task_id "
        "WHERE t.task_id IS NULL OR t.status <> %s "
        "ORDER BY h.updated_at LIMIT %s",
        (_RUNNING_STATUS, int(limit)),
    )
    return [_health_view(row) for row in rows]


def delete_health_rows(store: Any, task_ids: Sequence[str]) -> int:
    """删除指定任务 id 的健康行（单语句，分批调用由调用方控制）；返回删除行数。"""
    ids = [str(t) for t in task_ids]
    if not ids:
        return 0
    with store.transaction() as tx:
        rc = tx.execute_rc(
            "DELETE FROM governance_runtime_health "
            f"WHERE task_id IN ({_placeholders(len(ids))})",
            tuple(ids),
        )
    return rc
