"""F8 Step 3 — Governance execution context（Spec §8 S1：main_agent 接线读取端）。

- per-execution 状态经 ContextVar 传递（**禁止 global mutable singleton 存 execution 数据**）；
- main_agent 只读取（get_governance_execution）并在 governance-active 时注入 callback +
  recursion_limit；enter 由 controller（Phase D）或 probe/测试建立；
- 无 context（= 非 governance 执行）→ 今天行为逐字节不变。
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator, Optional

from app.runtime.governance.callbacks import GovernanceCallbackHandler
from app.runtime.governance.counters import BudgetCounter

#: framework recursion 安全上限（M-Spec §7.5：默认如 5000；独立于 agent_steps）
DEFAULT_RECURSION_LIMIT = 5000


@dataclass
class GovernanceExecution:
    """单次 governed execution 的进程内执行上下文（controller/submit 建立）。

    字段：
    - task_id：governance TaskRecord 主键（生命周期键）；
    - run_id：可选 execution correlation（run_deep_agent 内部另有独立 run_id；
      三平面不混淆，这里仅透传供接线诊断）；
    - counter：本 execution 唯一 BudgetCounter（C2：main/subagent 共享同一预算语义）；
    - recursion_limit：astream config 注入值（默认 5000）；
    - search_tool_names：search 判定工具名集合。
    """

    task_id: str
    counter: BudgetCounter
    run_id: Optional[str] = None
    recursion_limit: int = DEFAULT_RECURSION_LIMIT
    search_tool_names: tuple[str, ...] = ()
    diagnostics: list[dict] = field(default_factory=list, repr=False)
    _handler: Optional[GovernanceCallbackHandler] = field(
        default=None, repr=False, init=False
    )

    def make_handler(self) -> GovernanceCallbackHandler:
        """单次 execution 单一 handler（C2：唯一 producer 实例）。"""
        if self._handler is None:
            self._handler = GovernanceCallbackHandler(
                self.counter,
                search_tool_names=self.search_tool_names or ("internet_search",),
                diagnostics=self.diagnostics,
            )
        return self._handler


_EXEC_CONTEXT: contextvars.ContextVar[Optional[GovernanceExecution]] = (
    contextvars.ContextVar("deepsearch_governance_execution", default=None)
)


def get_governance_execution() -> Optional[GovernanceExecution]:
    """读取当前 execution 的 governance context（无 → None = 非 governance 执行）。"""
    return _EXEC_CONTEXT.get()


def is_governance_active() -> bool:
    return _EXEC_CONTEXT.get() is not None


@contextmanager
def enter_governance_execution(execution: GovernanceExecution) -> Iterator[None]:
    """进入 governance-active 执行段（controller/测试建立；退出自动还原）。"""
    token = _EXEC_CONTEXT.set(execution)
    try:
        yield
    finally:
        _EXEC_CONTEXT.reset(token)
