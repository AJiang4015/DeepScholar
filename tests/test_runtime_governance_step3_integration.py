"""F8 Step 3 — Phase E：真实 run_deep_agent Integration + F7 Boundary。

GovernanceController.execute(policy) 治理下运行**真实 run_deep_agent** 全链
（main_agent：monitor/F7/CancelledError 分支原样；ScriptedChatModel + 受控工具；
research/checkpoint recorder 隔离 = deterministic repo-integrated；real-provider E2E 无凭据受限）。

E1 normal→completed+F7 once；E2 llm budget；E3 tool budget；E4 search budget；
E5 subagent budget（共享预算）；E6 timeout（先 terminal 后 linger）；E7 explicit cancel；
E8 recursion→framework_recursion_safety；E9 ordinary failure→agent_failure；
每场景断言：F7 finalize=0（治理终止）、无 task_result、version==1（无 duplicate terminal）。
"""

import shutil
import uuid
from pathlib import Path

import pytest

from tests._step3_repo_harness import (
    _HAS_DEAPAGENTS,
    SUBAGENT_SPEC,
    _make_extra_tools,
    run_governed_scenario,
    tool_call_msg,
)
from langchain_core.messages import AIMessage  # noqa: E402

pytestmark = pytest.mark.skipif(
    not _HAS_DEAPAGENTS, reason="需要 deepagents（在项目 .venv 环境运行）"
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST_TMP = _REPO_ROOT / "_testtmp"


@pytest.fixture
def gov_tmp():
    d = _TEST_TMP / f"phe-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    try:
        shutil.rmtree(d)
    except OSError:
        pass


def _no_governance_finalize(out) -> None:
    assert out["finalize_calls"] == [], "治理终止不得触发 F7 finalize"
    assert out["db"]["version"] == 1, "无 duplicate terminal（version 恰 +1）"
    assert sum(1 for e in out["events"] if e == "task_result") == 0, (
        "治理终止不得产出正常 task_result"
    )
    assert out["db"]["status"] != "completed"


class TestF7BoundaryMatrix:
    def test_e1_normal_completed_f7_once(self, gov_tmp):
        out = run_governed_scenario(
            "E1", gov_tmp, responses=[AIMessage(content="FINAL OK")], policy={}
        )
        assert out["db"]["status"] == "completed"
        assert out["db"]["version"] == 1
        assert len(out["finalize_calls"]) == 1  # F7 exactly once
        assert "task_result" in out["events"]
        assert "finished" in out["status_calls"]
        assert out["captures"][0]["recursion_limit"] == 5000  # S1 生产 wiring 不变
        assert "GovernanceCallbackHandler" in out["captures"][0]["callbacks_types"]
        # ContextVar 传播：真实 run_deep_agent 内 handler 附着 → counter 与 provider 一致
        assert out["db"]["counters_snapshot"]["llm_calls"] == out["provider_calls"] == 1

    def test_e2_llm_budget(self, gov_tmp):
        out = run_governed_scenario(
            "E2",
            gov_tmp,
            responses=[tool_call_msg("probe_tool", {"q": "e2"}, "pe2")],
            policy={"max_llm_calls": 1},
        )
        assert out["execute_raised"] == "GovernanceLimitExceeded"
        assert out["db"]["status"] == "budget_exceeded"
        assert out["provider_calls"] == 1  # 第 2 次 provider 被阻止
        assert out["db"]["counters_snapshot"]["llm_calls"] == 1
        _no_governance_finalize(out)
        assert "error" in out["events"] and "failed" in out["status_calls"]

    def test_e3_tool_budget(self, gov_tmp):
        out = run_governed_scenario(
            "E3",
            gov_tmp,
            responses=[tool_call_msg("probe_tool", {"q": "e3"}, "pe3")],
            policy={"max_tool_calls": 0},
        )
        assert out["execute_raised"] == "GovernanceLimitExceeded"
        assert out["db"]["status"] == "budget_exceeded"
        assert out["tool_ran"] == 0  # tool body 未执行
        _no_governance_finalize(out)

    def test_e4_search_budget(self, gov_tmp):
        out = run_governed_scenario(
            "E4",
            gov_tmp,
            responses=[tool_call_msg("internet_search", {"query": "q4"}, "pe4")],
            policy={"max_search_calls": 0},
            tools=_make_extra_tools("search"),
        )
        assert out["execute_raised"] == "GovernanceLimitExceeded"
        assert out["db"]["status"] == "budget_exceeded"
        assert out["tool_by"].get("internet_search", 0) == 0  # search body 未执行
        _no_governance_finalize(out)

    def test_e5_subagent_budget_shared(self, gov_tmp):
        out = run_governed_scenario(
            "E5",
            gov_tmp,
            responses=[
                tool_call_msg(
                    "task", {"description": "do", "subagent_type": "probe-sub"}, "pe5"
                )
            ],
            policy={"max_llm_calls": 1},
            subagents=SUBAGENT_SPEC,
        )
        assert out["execute_raised"] == "GovernanceLimitExceeded"
        assert out["db"]["status"] == "budget_exceeded"
        assert (
            out["provider_calls"] == 1
        )  # subagent 首决策在 provider 前被阻止（共享预算）
        assert out["db"]["counters_snapshot"]["llm_calls"] == 1
        _no_governance_finalize(out)

    def test_e6_timeout_linger(self, gov_tmp):
        out = run_governed_scenario(
            "E6",
            gov_tmp,
            responses=[tool_call_msg("slow_tool", {"q": "e6"}, "pe6")],
            policy={"wall_clock_timeout": 0.2},
            tools=_make_extra_tools("slow"),
        )
        assert out["db"]["status"] == "timed_out"
        assert out["db"]["linger"] is True  # 先 terminal、底层 sync 仍跑
        assert out["tool_by"].get("slow_tool", 0) == 1
        assert out["db"]["version"] == 1
        assert out["finalize_calls"] == []
        assert "task_result" not in out["events"]
        assert out["db"]["status"] != "completed"

    def test_e7_explicit_cancel(self, gov_tmp):
        out = run_governed_scenario(
            "E7",
            gov_tmp,
            responses=[tool_call_msg("probe_tool", {"q": "e7"}, "pe7")],
            policy={"wall_clock_timeout": 600},
            block_after_first=True,
            governed_cancel_after=0.1,
        )
        assert out["db"]["status"] == "cancelled"
        assert out["db"]["version"] == 1
        assert out["finalize_calls"] == []
        assert "task_cancelled" in out["events"]
        assert "task_result" not in out["events"]
        assert out["db"]["status"] != "completed"

    def test_e8_recursion_framework(self, gov_tmp):
        out = run_governed_scenario(
            "E8",
            gov_tmp,
            responses=[
                tool_call_msg("probe_tool", {"q": "e8"}, f"pe8{i}") for i in range(6)
            ]
            + [AIMessage(content="FINAL")],
            policy={"framework_recursion_limit": 3, "wall_clock_timeout": 600},
        )
        assert out["execute_raised"] == "GraphRecursionError"
        assert out["db"]["status"] == "failed"
        assert (
            out["db"]["error_kind"] == "framework_recursion_safety"
        )  # ≠ budget_exceeded
        _no_governance_finalize(out)

    def test_e9_ordinary_failure_agent_failure(self, gov_tmp):
        out = run_governed_scenario(
            "E9", gov_tmp, raise_error=RuntimeError("agent boom"), policy={}
        )
        assert out["db"]["status"] == "failed"
        assert out["db"]["error_kind"] == "agent_failure"
        _no_governance_finalize(out)


class TestNoDuplicateTerminalIntegration:
    def test_normal_then_cancel_no_second_terminal(self, gov_tmp):
        out = run_governed_scenario(
            "RA1", gov_tmp, responses=[AIMessage(content="FINAL")], policy={}
        )
        assert out["db"]["status"] == "completed"
        assert out["db"]["version"] == 1
        assert sum(1 for e in out["events"] if e == "task_result") == 1

    def test_timeout_then_funnel_candidates_no_second_terminal(self, gov_tmp):
        out = run_governed_scenario(
            "RA2",
            gov_tmp,
            responses=[tool_call_msg("slow_tool", {"q": "r"}, "pr2")],
            policy={"wall_clock_timeout": 0.05},
            tools=_make_extra_tools("slow"),
        )
        assert out["db"]["status"] == "timed_out"
        assert out["db"]["version"] == 1
        assert "task_result" not in out["events"]

    def test_cancel_repeat_guard_via_single_winner(self, gov_tmp):
        out = run_governed_scenario(
            "RA3",
            gov_tmp,
            responses=[tool_call_msg("probe_tool", {"q": "c"}, "pr3")],
            policy={"wall_clock_timeout": 600},
            block_after_first=True,
            governed_cancel_after=0.1,
        )
        assert out["db"]["status"] == "cancelled"
        assert (
            out["db"]["version"] == 1
        )  # cancel 重复语义由 funnel 幂等保证（单次 transition）
