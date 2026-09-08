"""F8 Step 3 — BudgetCounter（Spec 2026-09-15-f8-step3-runtime-budget-control.md §5/§6）。

- 四个 hard counter：llm_calls / tool_calls / search_calls / agent_steps；
- 单一 canonical producer：计数只在此处；compare → increment → execute（先比较后放行）；
- 进程内、线程安全（threading.Lock，事件循环线程与 executor 线程都可安全调用）；
- **零 DB 依赖**（control-path 不依赖 observation/record DB）；
- 不允许 overshoot：`count >= limit` 即拒绝，第 limit+1 次不发生；
- `search_calls ⊆ tool_calls`：search 槽位只能经 `acquire_search_call()` 取得（同时占用
  tool+search 两个槽位、both-or-nothing），禁止单独 `acquire('search_calls')`；
- `agent_steps ≠ llm_calls`：两个独立计数器、独立限额（语义见 Spec §2/§3/§5）。

超限的"中止信号"不在本模块发出——`GovernanceLimitExceeded`（extends GraphBubbleUp，
Probe B 锁定的穿透语义）由调用方（GovernanceCallbackHandler，Phase B）在
on_llm_start/on_tool_start 抛出；本模块只返回 bool 并保证计数正确。
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from langgraph.errors import GraphBubbleUp

#: 默认 limits（M-Spec §7.4，FROZEN）
DEFAULT_LIMITS: dict[str, int] = {
    "llm_calls": 120,
    "tool_calls": 300,
    "search_calls": 40,
    "agent_steps": 200,
}

#: 可单独 acquire 的 kind（search_calls 只能经 acquire_search_call）
_ACQUIRABLE = frozenset(("llm_calls", "tool_calls", "agent_steps"))

LIMIT_NAMES = tuple(DEFAULT_LIMITS)


class GovernanceLimitExceeded(GraphBubbleUp):
    """hard-limit 中止信号（extends GraphBubbleUp：穿透 ToolNode，Probe B PROVEN）。

    由 governance enforcement 点（on_llm_start / on_tool_start / step 起点）抛出；
    Step 3 controller 据此收敛 terminalize(reason=budget_exceeded)。
    """

    def __init__(
        self,
        kind: str,
        limit: int,
        current: int,
        message: Optional[str] = None,
    ) -> None:
        self.kind = kind
        self.limit = limit
        self.current = current
        super().__init__(
            message or f"governance hard limit exceeded: {kind} {current} >= {limit}"
        )


class BudgetCounterError(RuntimeError):
    """BudgetCounter 配置/用法错误（非法 kind、负数 limit 等）。"""


class BudgetCounter:
    """进程内 hard counters（controller 每个 governed execution 一个实例）。

    所有 compare+increment 在同一临界区完成（无 await/线程间隙）→ 不允许 overshoot；
    事件循环内外的回调（async LLM / sync tool executor）都能安全调用。
    """

    def __init__(self, limits: Optional[dict[str, int]] = None) -> None:
        cfg = dict(DEFAULT_LIMITS if limits is None else limits)
        unknown = set(cfg) - set(DEFAULT_LIMITS)
        if unknown:
            raise BudgetCounterError(f"未知 counter kind：{sorted(unknown)}")
        for key, value in cfg.items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise BudgetCounterError(f"{key} 的 limit 必须为非负 int：{value!r}")
        self._limits: dict[str, int] = cfg
        self._counts: dict[str, int] = {k: 0 for k in DEFAULT_LIMITS}
        self._lock = threading.Lock()

    # -- limits / counts -------------------------------------------------
    @property
    def limits(self) -> dict[str, int]:
        return dict(self._limits)

    def limit(self, kind: str) -> int:
        return self._limits[kind]

    def count(self, kind: str) -> int:
        with self._lock:
            return self._counts[kind]

    def snapshot(self) -> dict[str, int]:
        """terminal 快照（counters_snapshot 的 count 部分；与 limits 分离）。"""
        with self._lock:
            return dict(self._counts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "limits": self.limits,
            "counts": self.snapshot(),
        }

    # -- compare-before-execute 原语 --------------------------------------
    def acquire_llm_call(self) -> bool:
        """单 LLM 槽位（summarizer / unclassified / 任何非 decision LLM call）。

        compare+increment 原子；拒绝时（llm 达限）不递增。
        """
        with self._lock:
            if self._counts["llm_calls"] >= self._limits["llm_calls"]:
                return False
            self._counts["llm_calls"] += 1
            return True

    def acquire_decision_round(self) -> bool:
        """decision round 的双槽原子放行：llm_calls + agent_steps 同时先比较后递增。

        保证不变量：**llm_calls == 实际发生的 decision+non-decision provider call**；
        被拒绝的回合（agent_steps 或 llm_calls 达限）**两者都不递增**——provider 未发起，
        不能只消耗 llm 槽位造成计数≠实际调用。
        """
        with self._lock:
            if self._counts["agent_steps"] >= self._limits["agent_steps"]:
                return False
            if self._counts["llm_calls"] >= self._limits["llm_calls"]:
                return False
            self._counts["agent_steps"] += 1
            self._counts["llm_calls"] += 1
            return True

    def acquire(self, kind: str) -> bool:
        """llm_calls / tool_calls / agent_steps 的原子 compare+increment。

        search_calls 禁止直接 acquire（见 acquire_search_call，search ⊆ tool 不变量）。
        注意：enforcement 路径请优先用 acquire_llm_call / acquire_decision_round /
        acquire_search_call（保证 计数 == 实际调用 的组合语义）；本方法保留为通用原语。
        """
        if kind not in _ACQUIRABLE:
            if kind == "search_calls":
                raise BudgetCounterError(
                    "search_calls 必须经 acquire_search_call()（search ⊆ tool 不变量）"
                )
            raise BudgetCounterError(f"未知 counter kind：{kind!r}")
        with self._lock:
            if self._counts[kind] >= self._limits[kind]:
                return False
            self._counts[kind] += 1
            return True

    def acquire_search_call(self) -> bool:
        """internet_search 调用的双预算原子槽位：先比较 tool+search，再同时递增。

        失败（任一达限）→ 不递增任何计数（both-or-nothing），保证 search ⊆ tool。
        """
        with self._lock:
            if self._counts["tool_calls"] >= self._limits["tool_calls"]:
                return False
            if self._counts["search_calls"] >= self._limits["search_calls"]:
                return False
            self._counts["tool_calls"] += 1
            self._counts["search_calls"] += 1
            return True
