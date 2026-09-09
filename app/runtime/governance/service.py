"""F8 Step 4 Batch 1 — governance service 接线助手（server 薄层；不含 HTTP 语义）。

- submit：TaskRecord 创建（task_id/thread_id/run_id 绑定）→ durable task_started event →
  GovernanceController.execute(policy=...) 运行 run_deep_agent；
- cancel：controller.cancel_governed（冻结 funnel + event）；
- query：task 详情 / 列表（read-only）；
- TaskRecord 是 terminal truth；event 仅 observation。

P2-1（D-Phase2-P2-1-001）：submit 为生产提交唯一 normalize 收敛点 —— policy 缺省
（None/{}）时注入 DEFAULT_GOVERNED_POLICY，使所有经本函数的任务恒走
controller.execute 的 governed 路径（watchdog/budget/terminal event 常开）；
非法 policy（PolicyValidationError）fail-closed 拒绝，任务不启动。
bare execution（controller.execute(policy=None)）语义保留，仅限 unit test / local
debugging / internal development（本函数不会落入 bare）。

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
from app.runtime.governance.policy import normalize_policy

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
    """创建 TaskRecord + 启动 governed 执行；返回 (record, asyncio.Task)。

    P2-1（D-Phase2-P2-1-001）：policy 先经 normalize_policy —— None/{} → 注入
    DEFAULT_GOVERNED_POLICY（生产必 governed）；显式 dict 与默认合并并做
    fail-closed 校验（非法抛 PolicyValidationError，任务不启动）。
    normalize 置于 runner 解析之前：非法 policy 在任何 agent 组装/导入前即被拒绝。
    """
    #: 默认注入 + 校验（唯一 normalize 收敛点；此后 policy 恒为完整 dict）。
    #: 必须在 runner 解析之前：非法 policy 不应触发 agent/LLM 相关导入或组装。
    policy = normalize_policy(policy)
    runner = run_deep_agent if run_deep_agent is not None else _ensure_run_deep_agent()
    run_id = new_run_id()
    record = controller.create_task(
        thread_id,
        run_id=run_id,
        policy_snapshot=dict(policy),
        effective_limits=dict(policy),
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
