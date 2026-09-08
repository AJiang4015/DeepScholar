"""F8 Step 3 — Phase B：GovernanceCallbackHandler（Spec §6；SQLite 层逻辑测试）。

覆盖：LLM 120 无 121；Tool 300 无 301；Search 40 无 41；search 受 tool 预算先限；
agent_step 200 无 201；summarizer 只计 llm_calls；no-tool decision 计 agent_step
（分类不读 tool_calls）；subagent LLM 同规则；GovernanceLimitExceeded extends
GraphBubbleUp；超限 raise（不返回 False）；on_llm_end 不改 hard counter；observation
sink 失败 fail-open 不削弱 budget；classification 缺失 fail-safe 不计 agent_step。

真实 graph 语义（穿透/中止/provider 阻止）由 .deepagents-fc probe A–F 在 runtime 验证。
"""

import pytest

from langgraph.errors import GraphBubbleUp

from app.runtime.governance.callbacks import (
    CLASS_DECISION,
    CLASS_SUMMARIZER,
    CLASS_UNCLASSIFIED,
    DEFAULT_SEARCH_TOOL_NAMES,
    GovernanceCallbackHandler,
    classify_llm_start,
)
from app.runtime.governance.counters import (
    BudgetCounter,
    GovernanceLimitExceeded,
)


def _limits(**over):
    base = {"llm_calls": 120, "tool_calls": 300, "search_calls": 40, "agent_steps": 200}
    base.update(over)
    return base


def _handler(**over) -> tuple[GovernanceCallbackHandler, BudgetCounter]:
    counter = BudgetCounter(limits=_limits(**over))
    return GovernanceCallbackHandler(counter), counter


#: metadata 形态（真实 graph 事件所携带；Probe A/B 观测）
DECISION_MD = {"langgraph_node": "model"}
SUMMARIZER_MD = {"langgraph_node": "model", "lc_source": "summarization"}
EMPTY_MD = {}


def _llm_start(h: GovernanceCallbackHandler, md: dict) -> None:
    h.on_llm_start({}, ["prompt"], metadata=dict(md), run_id="r", parent_run_id="p")


class TestClassification:
    def test_classifier_probe_a_rules(self):
        assert classify_llm_start(SUMMARIZER_MD)[0] == CLASS_SUMMARIZER
        assert classify_llm_start(DECISION_MD)[0] == CLASS_DECISION
        # 判别信息缺失 → unclassified（fail-safe，不计 agent_step）
        assert classify_llm_start(EMPTY_MD)[0] == CLASS_UNCLASSIFIED
        # 未知 lc_source → unclassified
        assert classify_llm_start({"lc_source": "weird"})[0] == CLASS_UNCLASSIFIED
        # 非 model node → unclassified
        assert classify_llm_start({"langgraph_node": "tools"})[0] == CLASS_UNCLASSIFIED

    def test_no_tool_call_basis(self):
        """分类完全不以 '是否有 tool call' 为判据：decision 由 node/无 lc_source 判定。"""
        cls, _ = classify_llm_start(DECISION_MD)
        assert cls == CLASS_DECISION  # 与响应里有没有 tool_calls 无关


class TestLLMBudget:
    def test_llm_120_no_121(self):
        h, counter = _handler()
        accepted = 0
        with pytest.raises(GovernanceLimitExceeded) as excinfo:
            for _ in range(121):
                _llm_start(h, DECISION_MD)
                accepted += 1
        assert accepted == 120
        assert excinfo.value.kind == "llm_calls"
        assert counter.count("llm_calls") == 120
        assert counter.count("agent_steps") == 120  # 已放行的 120 个 decision
        assert h.llm_calls() == 120

    def test_agent_step_200_no_201(self):
        """decision round 限额独立于 llm 限额；被拒回合 llm/step 都不计（计数==实际调用）。"""
        h, counter = _handler(llm_calls=1000, agent_steps=200)
        with pytest.raises(GovernanceLimitExceeded) as excinfo:
            for _ in range(201):
                _llm_start(h, DECISION_MD)
        assert excinfo.value.kind == "agent_steps"
        assert counter.count("agent_steps") == 200
        # provider 未发起 → llm 槽也未被被拒回合消耗
        assert counter.count("llm_calls") == 200

    def test_summarizer_only_llm_calls(self):
        h, counter = _handler(agent_steps=2)
        for _ in range(5):
            _llm_start(h, SUMMARIZER_MD)  # llm 120 内
        assert counter.count("llm_calls") == 5
        assert counter.count("agent_steps") == 0
        assert h.class_counts[CLASS_SUMMARIZER] == 5

    def test_no_tool_decision_counts_agent_step(self):
        """即使 LLM 无 tool call，decision（model-node, 无 lc_source）也计 agent_step。"""
        h, counter = _handler()
        _llm_start(h, DECISION_MD)
        assert counter.count("agent_steps") == 1
        assert counter.count("llm_calls") == 1

    def test_mixed_sequence_separates_classes(self):
        h, counter = _handler()
        _llm_start(h, DECISION_MD)
        _llm_start(h, SUMMARIZER_MD)
        _llm_start(h, DECISION_MD)
        assert counter.count("llm_calls") == 3
        assert counter.count("agent_steps") == 2
        assert counter.count("agent_steps") != counter.count("llm_calls")

    def test_subagent_llm_same_rule(self):
        """subagent LLM 与 main LLM 同分类同预算（事件层无法区分图归属时以 node 判定）。"""
        h, counter = _handler(agent_steps=1)
        _llm_start(h, DECISION_MD)  # main decision #1
        with pytest.raises(GovernanceLimitExceeded) as excinfo:
            _llm_start(h, DECISION_MD)  # subagent decision #2 → step 达限
        assert excinfo.value.kind == "agent_steps"

    def test_unclassified_fail_safe(self):
        """判别信息缺失：只计 llm_calls、不计 agent_step、产生 degraded diagnostic。"""
        h, counter = _handler()
        _llm_start(h, EMPTY_MD)
        assert counter.count("llm_calls") == 1
        assert counter.count("agent_steps") == 0
        assert any(d["event"] == "llm_classification_unresolved" for d in h.diagnostics)
        assert h.class_counts[CLASS_UNCLASSIFIED] == 1


class TestToolBudget:
    def test_tool_300_no_301(self):
        h, counter = _handler()
        for _ in range(300):
            assert h._counter.acquire("tool_calls")  # noqa: SLF001 - 等价路径
        with pytest.raises(GovernanceLimitExceeded) as excinfo:
            h.on_tool_start({"name": "other_tool"}, "{}")
        assert excinfo.value.kind == "tool_calls"

    def test_tool_raise_via_handler_counts(self):
        h, counter = _handler(tool_calls=2)
        h.on_tool_start({"name": "read_file"}, "{}")
        h.on_tool_start({"name": "task"}, "{}")
        assert counter.count("tool_calls") == 2
        with pytest.raises(GovernanceLimitExceeded) as excinfo:
            h.on_tool_start({"name": "probe_tool"}, "{}")
        assert excinfo.value.kind == "tool_calls"
        assert counter.count("tool_calls") == 2

    def test_search_40_no_41(self):
        h, counter = _handler()
        for _ in range(40):
            h.on_tool_start({"name": "internet_search"}, "{}")
        assert counter.count("search_calls") == 40
        with pytest.raises(GovernanceLimitExceeded) as excinfo:
            h.on_tool_start({"name": "internet_search"}, "{}")
        assert excinfo.value.kind == "search_calls"
        assert counter.count("search_calls") == 40

    def test_search_consumes_tool_slot_too(self):
        h, counter = _handler()
        h.on_tool_start({"name": "internet_search"}, "{}")
        assert counter.count("tool_calls") == 1
        assert counter.count("search_calls") == 1

    def test_search_constrained_by_tool_budget_first(self):
        h, counter = _handler(tool_calls=1, search_calls=40)
        h.on_tool_start({"name": "plain_tool"}, "{}")  # 用掉唯一 tool 槽
        with pytest.raises(GovernanceLimitExceeded) as excinfo:
            h.on_tool_start({"name": "internet_search"}, "{}")
        assert excinfo.value.kind == "tool_calls"  # tool 先达限
        assert counter.count("search_calls") == 0

    def test_custom_search_names(self):
        counter = BudgetCounter(limits=_limits())
        h = GovernanceCallbackHandler(counter, search_tool_names=("my_search",))
        h.on_tool_start({"name": "my_search"}, "{}")
        assert counter.count("search_calls") == 1
        assert DEFAULT_SEARCH_TOOL_NAMES == ("internet_search",)


class TestObservationOnlyAndFailOpen:
    def test_llm_end_never_modifies_budget(self):
        h, counter = _handler()
        _llm_start(h, DECISION_MD)

        class _Resp:
            generations = [
                [
                    type(
                        "G",
                        (),
                        {
                            "message": type(
                                "M", (), {"usage_metadata": {"total_tokens": 5}}
                            )()
                        },
                    )()
                ]
            ]

        h.on_llm_end(_Resp())
        h.on_llm_end(_Resp())
        assert counter.count("llm_calls") == 1
        assert counter.count("agent_steps") == 1

    def test_usage_sink_failure_fail_open(self):
        def bad_sink(cls, usage):
            raise RuntimeError("sink exploded")

        counter = BudgetCounter(limits=_limits())
        h = GovernanceCallbackHandler(counter, usage_sink=bad_sink)

        class _Resp:
            generations = [
                [
                    type(
                        "G", (), {"message": type("M", (), {"usage_metadata": None})()}
                    )()
                ]
            ]

        _llm_start(h, DECISION_MD)
        h.on_llm_end(_Resp())  # sink 抛错 → fail-open
        assert counter.count("llm_calls") == 1
        assert counter.count("agent_steps") == 1
        assert any(d["event"] == "usage_sink_failed" for d in h.diagnostics)

    def test_llm_end_malformed_response_fail_open(self):
        counter = BudgetCounter(limits=_limits())
        h = GovernanceCallbackHandler(counter, usage_sink=lambda cls, usage: None)
        _llm_start(h, DECISION_MD)
        h.on_llm_end(object())  # 无法解析 → diagnostic，不崩溃、不改计数
        assert counter.count("llm_calls") == 1
        assert counter.count("agent_steps") == 1
        assert any(d["event"] == "llm_end_parse_failed" for d in h.diagnostics)


class TestExceptionSemantics:
    def test_limit_exceeded_is_graph_bubble_up(self):
        assert issubclass(GovernanceLimitExceeded, GraphBubbleUp)

    def test_handler_raise_error_flag(self):
        assert GovernanceCallbackHandler.raise_error is True
        h, _ = _handler()
        assert h.raise_error is True
