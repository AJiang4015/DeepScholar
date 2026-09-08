"""F8 Step 4 Batch 1 — governance service 接线助手（server 薄层；不含 HTTP 语义）。

- submit：TaskRecord 创建（task_id/thread_id/run_id 绑定）→ durable task_started event →
  GovernanceController.execute(policy=...) 运行 run_deep_agent；
- cancel：controller.cancel_governed（冻结 funnel + event）；
- query：task 详情 / 列表（read-only）；
- TaskRecord 是 terminal truth；event 仅 observation。

run_deep_agent 延迟解析：避免 import 期拉入 deepagents/llm（无凭据环境也能导入本模块）；
测试可替换模块属性 `run_deep_agent` 注入受控替身。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Optional

from app.runtime.governance import events as gov_events
from app.runtime.governance import store as gov_store
from app.runtime.governance.controller import GovernanceController
from app.runtime.governance.models import TaskRecord

run_deep_agent: Any = None  # lazy：见 _ensure_run_deep_agent
_loaded = False


def _ensure_run_deep_agent() -> Any:
    global run_deep_agent, _loaded
    if run_deep_agent is None and not _loaded:
        from app.agent.main_agent import run_deep_agent as _f  # noqa: PLC0415

        run_deep_agent = _f
        _loaded = True
    return run_deep_agent


def new_run_id() -> str:
    return uuid.uuid4().hex


def submit_task(
    controller: GovernanceController,
    *,
    thread_id: str,
    query: str,
    policy: Optional[dict[str, Any]] = None,
) -> tuple[TaskRecord, asyncio.Task]:
    """创建 TaskRecord + 启动 governed 执行；返回 (record, asyncio.Task)。"""
    runner = run_deep_agent if run_deep_agent is not None else _ensure_run_deep_agent()
    run_id = new_run_id()
    record = controller.create_task(
        thread_id,
        run_id=run_id,
        policy_snapshot=dict(policy or {}),
        effective_limits=dict(policy or {}),
    )
    try:
        gov_events.lifecycle_event(
            controller._store,  # noqa: SLF001
            task_id=record.task_id,
            thread_id=record.thread_id,
            run_id=run_id,
            status="running",
            finished_at=record.created_at,
        )
    except Exception as exc:  # noqa: BLE001 — fail-open
        print(f"[GovernanceService] task_started durable 写失败（fail-open）：{exc}")
    task = asyncio.create_task(
        controller.execute(record.task_id, runner(query, thread_id), policy=policy)
    )
    return record, task


def get_task(controller: GovernanceController, task_id: str) -> Optional[TaskRecord]:
    return controller.get_task(task_id)


def list_tasks(
    controller: GovernanceController, thread_id: Optional[str] = None, limit: int = 50
) -> list[TaskRecord]:
    return gov_store.list_tasks(controller._store, thread_id=thread_id, limit=limit)  # noqa: SLF001


async def cancel_task(controller: GovernanceController, task_id: str) -> dict[str, Any]:
    """server cancel → 冻结 funnel(cancelled) + lifecycle event。"""
    return await controller.cancel_governed(task_id)


def task_dict(record: TaskRecord) -> dict[str, Any]:
    d = record.to_dict()
    return {
        k: d[k]
        for k in (
            "task_id",
            "thread_id",
            "run_id",
            "status",
            "terminal_reason",
            "error_kind",
            "error",
            "counters_snapshot",
            "policy_snapshot",
            "effective_limits",
            "created_at",
            "started_at",
            "finished_at",
        )
    }
