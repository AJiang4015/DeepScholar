"""P2-1 — Runtime Policy 默认值单一来源 + normalize（决策 D-Phase2-P2-1-001）。

背景：P2-1 之前，生产提交路径（server → gov_service.submit_task）在调用方未传
policy（policy=None）时把 None 原样透传给 controller.execute → 落入 _execute_bare
（无 watchdog / 无 deadline / 无 budget / 无 terminal lifecycle event），执行静默挂起
即永久 RUNNING（见 RUNTIME_OBSERVABILITY_AUDIT_REPORT.md §1/§2/§6）。

本模块提供：
- DEFAULT_GOVERNED_POLICY：默认 Runtime Policy 单一来源（引用 controller /
  counters 的冻结常量，防双源漂移；同源由测试锁定）；
- normalize_policy()：提交层默认注入（None/{} → 默认表副本；显式 dict → 默认+覆盖；
  已知 key 类型/边界校验，fail-closed）；
- PolicyValidationError：非法 policy（server 层映射 HTTP 400，任务不启动）。

契约（D-Phase2-P2-1-001）：
- 生产任务必 governed：submit 时 policy=None ⇒ 注入 DEFAULT_GOVERNED_POLICY，
  使 controller.execute 恒走 _execute_governed（watchdog / budget / 异常 re-raise /
  terminal event 常开）；
- bare execution（controller.execute(policy=None)）语义保留，仅限 unit test / local
  debugging / internal development —— 本模块不触碰 controller.execute 的分发逻辑，
  默认注入只发生在 gov_service.submit_task 提交层（唯一收敛点）；
- 默认值沿用 M-Spec §7.4 冻结定义，P2-1 不调参（env 覆盖归 P2-2 决策 D-Phase2-P2-2-002）。
"""

from __future__ import annotations

from typing import Any, Optional

from app.runtime.governance.controller import (
    DEFAULT_WALL_CLOCK_SECONDS,
    MAX_LIMIT_TO_COUNTER,
)
from app.runtime.governance.counters import DEFAULT_LIMITS

#: 默认 Runtime Policy（M-Spec §7.4 冻结默认表；与 controller/counters 常量同源）。
#: policy 使用 max_* 键名（controller.MAX_LIMIT_TO_COUNTER 语义），
#: 值来自 counters.DEFAULT_LIMITS（kind → count 上限），单源防漂移。
DEFAULT_GOVERNED_POLICY: dict[str, int] = {
    "wall_clock_timeout": DEFAULT_WALL_CLOCK_SECONDS,
}
for _max_key, _kind in MAX_LIMIT_TO_COUNTER.items():
    DEFAULT_GOVERNED_POLICY[_max_key] = DEFAULT_LIMITS[_kind]

#: 必须 > 0（禁止显式传 0/负数关闭 watchdog 的逃逸；bool 视为非法）
_POSITIVE_KEYS = frozenset({"wall_clock_timeout"})

#: 必须为 ≥0 int（BudgetCounter limit 语义；bool 视为非法）
_NONNEG_INT_KEYS = frozenset(
    {"max_agent_steps", "max_llm_calls", "max_tool_calls", "max_search_calls"}
)


class PolicyValidationError(ValueError):
    """非法 policy（fail-closed：任务不启动；server 层映射 HTTP 400）。"""


def normalize_policy(policy: Optional[dict[str, Any]]) -> dict[str, Any]:
    """归一 policy：None/{} → DEFAULT_GOVERNED_POLICY 副本；显式 dict → 默认 + 覆盖。

    - 非 dict/None 输入 → PolicyValidationError；
    - 未知键透传（惰性，不参与 enforcement，不报错）；
    - 已知 `max_*` key 必须为 ≥0 int（bool 非法）；`wall_clock_timeout` 必须 >0
      （int/float，bool 非法）——校验失败抛 PolicyValidationError（fail-closed）。

    Returns:
        完整 policy dict（含全部默认键；显式键优先）。调用方直接用于
        create_task(policy_snapshot/effective_limits) 与 controller.execute(policy=...)。
    """
    if policy is None:
        return dict(DEFAULT_GOVERNED_POLICY)
    if not isinstance(policy, dict):
        raise PolicyValidationError(
            f"policy 必须是 dict 或 None，收到 {type(policy).__name__}"
        )
    merged: dict[str, Any] = dict(DEFAULT_GOVERNED_POLICY)
    merged.update(policy)
    for key in _POSITIVE_KEYS:
        value = merged[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or value <= 0
        ):
            raise PolicyValidationError(
                f"{key} 必须为正数（>0），收到 {value!r}（禁止关闭 watchdog）"
            )
    for key in _NONNEG_INT_KEYS:
        value = merged[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PolicyValidationError(f"{key} 必须为 ≥0 的 int，收到 {value!r}")
    return merged
