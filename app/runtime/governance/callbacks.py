"""F8 Step 3 — GovernanceCallbackHandler（Spec 2026-09-15-f8-step3 §4/§6；唯一 budget producer）。

职责边界（Spec/Phase B 锁死）：
- GovernanceCallbackHandler：runtime callback enforcement / observation（唯一 canonical counting path）；
- BudgetCounter：计数、limit、compare-before-execute（Phase A）；
- GovernanceLimitExceeded：governance abort signal（extends GraphBubbleUp，Probe B 穿透语义）。

Enforcement（全部先比较后放行，超限 raise，不返回 False、不转 ToolMessage）：
- on_llm_start：
   1) llm_calls：对**所有**实际 LLM call（decision + summarization）先比较后放行（M-Spec §7.4
      "含 main/subagent 全部"）；超限 → raise GovernanceLimitExceeded(llm_calls)；
   2) 分类（Probe A LOCKED 规则，Spec §3）：
        metadata.lc_source == "summarization" → summarizer：只计 llm_calls；
        无 lc_source 且 metadata.langgraph_node == "model" → decision：llm_calls + agent_steps
        （先比较后放行 agent_steps；超限 → raise GovernanceLimitExceeded(agent_steps)）；
        其它（判别信息缺失/异常）→ **fail-safe：不计 agent_step** + degraded diagnostic/warning；
   3) **禁止**以 "是否有 tool call" 判定 decision（handler 不读 tool_calls 做分类）。
- on_tool_start：
   - name == internet_search（可配置 search_tool_names）→ acquire_search_call()
     （tool+search 双槽 both-or-nothing，search ⊆ tool 由 BudgetCounter 保证）；
   - 其它工具 → acquire('tool_calls')；
   - 超限 → raise GovernanceLimitExceeded(tool_calls/search_calls)。
- on_llm_end：**observation only**：不修改任何计数；仅（可选）usage sink 采集（fail-open）。

异常策略：
- 只允许 GovernanceLimitExceeded 向框架传播（GraphBubbleUp 穿透 ToolNode/SubAgentMiddleware）；
- handler 自身其它异常（回调 bug）→ 记 diagnostic + log，继续（Spec §10 F3：不伪造终态、
  不因观察问题削弱 enforcement——计数仍只经 BudgetCounter）。

零 DB / Redis / Kafka 依赖。
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional, Sequence

from langchain_core.callbacks import BaseCallbackHandler

from app.runtime.governance.counters import (
    BudgetCounter,
    GovernanceLimitExceeded,
)

logger = logging.getLogger("deepsearch.runtime.governance.callbacks")

#: 判定为 search 的 tool 名（repo 语义 = internet_search；可注入覆盖）
DEFAULT_SEARCH_TOOL_NAMES: tuple[str, ...] = ("internet_search",)

#: 分类结果（Spec §3 锁定语义）
CLASS_SUMMARIZER = "summarizer"
CLASS_DECISION = "decision"
CLASS_UNCLASSIFIED = "unclassified"


def classify_llm_start(metadata: Mapping[str, Any]) -> tuple[str, str]:
    """Probe A LOCKED 分类（Spec §3）。

    返回 (class, reason)：
    - summarizer：metadata.lc_source == "summarization"；
    - decision：无 lc_source 且 langgraph_node == "model"（agent model-node execution）；
    - unclassified：其余（判别信息缺失 → fail-safe 不计 agent_step）。
    """
    lc_source = metadata.get("lc_source")
    if lc_source == "summarization":
        return CLASS_SUMMARIZER, "lc_source==summarization"
    if lc_source is not None:
        return CLASS_UNCLASSIFIED, f"unexpected lc_source={lc_source!r}"
    if metadata.get("langgraph_node") == "model":
        return CLASS_DECISION, "langgraph_node==model (agent model-node)"
    return (
        CLASS_UNCLASSIFIED,
        f"no lc_source; langgraph_node={metadata.get('langgraph_node')!r}",
    )


class GovernanceCallbackHandler(BaseCallbackHandler):
    """单一 canonical producer：on_llm_start / on_tool_start 是 hard-control enforcement point。

    用法：作为 run config `callbacks` 之一注入（Phase C S1 接线）；probe/测试直接构造后
    驱动事件。
    """

    raise_error = True  # GovernanceLimitExceeded 必须上抛（Probe B）

    def __init__(
        self,
        counter: BudgetCounter,
        *,
        search_tool_names: Sequence[str] = DEFAULT_SEARCH_TOOL_NAMES,
        usage_sink=None,
        diagnostics: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        self._counter = counter
        self._search_tool_names = set(search_tool_names)
        #: observation sink：callable(class, llm_end 上下文) 或 None；失败 fail-open
        self._usage_sink = usage_sink
        #: 测试/诊断可见的 degraded 记录（None → 内部 list）
        self.diagnostics: list[dict[str, Any]] = (
            diagnostics if diagnostics is not None else []
        )
        self._class_counts: dict[str, int] = {}

    # -- 只读访问（测试/probe 断言） --------------------------------------
    @property
    def counter(self) -> BudgetCounter:
        return self._counter

    @property
    def class_counts(self) -> dict[str, int]:
        return dict(self._class_counts)

    def _diag(self, kind: str, **kw: Any) -> None:
        self.diagnostics.append({"event": kind, **kw})
        logger.warning("governance callback diagnostic [%s]: %s", kind, kw)

    def _raise_limit(self, kind: str) -> None:
        raise GovernanceLimitExceeded(
            kind,
            limit=self._counter.limit(kind),
            current=self._counter.count(kind),
        )

    # -- enforcement: LLM ------------------------------------------------
    def on_llm_start(self, serialized, prompts, **kwargs) -> None:  # noqa: ANN001
        metadata = dict(kwargs.get("metadata") or {})
        cls, reason = classify_llm_start(metadata)
        self._class_counts[cls] = self._class_counts.get(cls, 0) + 1
        if cls == CLASS_DECISION:
            # decision round：llm_calls + agent_steps 原子先比较后递增（拒绝 ⇒ 两者都不计，
            # 保证 llm_calls == 实际 provider call，Spec §5/§7）
            if not self._counter.acquire_decision_round():
                if self._counter.count("agent_steps") >= self._counter.limit(
                    "agent_steps"
                ):
                    self._raise_limit("agent_steps")
                self._raise_limit("llm_calls")
            return
        # summarizer / unclassified：只消耗 llm 槽位
        if not self._counter.acquire_llm_call():
            self._raise_limit("llm_calls")
        if cls == CLASS_UNCLASSIFIED:
            # fail-safe：判别信息缺失 → 不计 agent_step + degraded（Spec §3）
            self._diag(
                "llm_classification_unresolved",
                reason=reason,
                llm_count=self._counter.count("llm_calls"),
            )

    # -- enforcement: Tool / Search ---------------------------------------
    def on_tool_start(self, serialized, input_str, **kwargs) -> None:  # noqa: ANN001
        name = ""
        if isinstance(serialized, dict):
            name = str(serialized.get("name", ""))
        try:
            name = name or str(getattr(serialized, "name", ""))
        except Exception:  # noqa: BLE001
            name = ""
        if name in self._search_tool_names:
            if not self._counter.acquire_search_call():
                # search 受 tool/search 双预算约束；报告先达限者
                if self._counter.count("tool_calls") >= self._counter.limit(
                    "tool_calls"
                ):
                    self._raise_limit("tool_calls")
                self._raise_limit("search_calls")
            return
        if not self._counter.acquire("tool_calls"):
            self._raise_limit("tool_calls")

    # -- observation only: LLM end ---------------------------------------
    def on_llm_end(self, response, **kwargs) -> None:  # noqa: ANN001
        """observation only：不修改任何 hard counter（Spec §6/§10 F1/F2）。"""
        if self._usage_sink is None:
            return
        metadata = dict(kwargs.get("metadata") or {})
        usage = None
        try:
            gen = response.generations[0][0]
            msg = getattr(gen, "message", None)
            if msg is not None:
                usage = getattr(msg, "usage_metadata", None)
        except Exception as exc:  # noqa: BLE001 — malformed telemetry → fail-open
            self._diag("llm_end_parse_failed", error=str(exc)[:200])
            return
        try:
            cls, _ = classify_llm_start(metadata)
            self._usage_sink(cls, usage)
        except Exception as exc:  # noqa: BLE001 — observation sink 失败不得削弱 hard budget
            self._diag("usage_sink_failed", error=str(exc)[:200])

    # -- diagnostic helpers ------------------------------------------------
    def llm_calls(self) -> int:
        return self._counter.count("llm_calls")

    def agent_steps(self) -> int:
        return self._counter.count("agent_steps")

    def tool_calls(self) -> int:
        return self._counter.count("tool_calls")

    def search_calls(self) -> int:
        return self._counter.count("search_calls")
