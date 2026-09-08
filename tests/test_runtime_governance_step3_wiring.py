"""F8 Step 3 — Phase C：S1 main_agent wiring（repo-integrated，真实 run_deep_agent）。

用 tests/_step3_repo_harness 在**真实 run_deep_agent**（F7/monitor/CancelledError 分支原样）
上验证：C1 context 注入、C2 callback 自动附着（main/subagent 同一预算）、C3 recursion_limit
=5000 进入 astream config、C4 governance abort 不被吞（re-raise）、C5 normal completion 原样、
C6 ordinary exception re-raise / CancelledError 边界、F7 normal→finalize / abort→不 finalize。

LLM 用 ScriptedChatModel（继承 BaseChatModel → 真实 callback/metadata 链路）；无网络。
真实 provider E2E 仍受凭据限制（Known Limitations）。
"""

import pytest

from tests._step3_repo_harness import (
    _HAS_DEAPAGENTS,
    SUBAGENT_SPEC,
    run_scenario,
    tool_call_msg,
)

pytestmark = pytest.mark.skipif(
    not _HAS_DEAPAGENTS, reason="需要 deepagents（在项目 .venv 环境运行）"
)


def _raise_limit_kind(outcome) -> str:
    return str(outcome.get("raised", ""))


class TestNormalAndF7Boundary:
    def test_c1_c2_c3_normal_injection_f7(self):
        """normal completion：context 注入、callback 附着（同一 counter）、recursion_limit=5000、
        F7 finalize 触发、task_result 一次。"""
        from langchain_core.messages import AIMessage  # noqa: PLC0415

        out = run_scenario("normal", responses=[AIMessage(content="FINAL OK")])
        assert out["returned_normal"] is True
        # C5：正常结束 + 无 fake failure
        assert "task_result" in out["events"]
        assert "error" not in out["events"]
        # F7 boundary（normal）→ finalize 恰一次；research finished
        assert len(out["finalize_calls"]) == 1
        assert "finished" in out["status_calls"]
        # C1/C3：注入证据
        assert out["captures"], "astream config spy 未捕获"
        cap = out["captures"][0]
        assert cap["recursion_limit"] == 5000  # recursion 与 agent_steps 分离
        assert "GovernanceCallbackHandler" in cap["callbacks_types"]
        # C2：决策计数进入同一 BudgetCounter（llm=1, agent_steps=1）
        assert out["counts"]["llm_calls"] == 1
        assert out["counts"]["agent_steps"] == 1
        assert out["diagnostics"] == []

    def test_normal_without_governance_unchanged(self):
        """非 governance 执行：行为与今天一致（普通异常仍被吞、无注入）。"""
        out = run_scenario("no-gov", governance=False, raise_error=RuntimeError("boom"))
        assert out["returned_normal"] is True  # 现状：except Exception 吞掉
        assert "error" in out["events"]
        assert "task_result" not in out["events"]
        assert out["captures"] and out["captures"][0]["recursion_limit"] is None
        assert out["captures"][0]["callbacks_types"] == []


class TestGovernanceAbortNotSwallowed:
    def test_c4_llm_budget_abort_reraises_no_f7(self):
        out = run_scenario(
            "llm-abort",
            responses=[tool_call_msg("probe_tool", {"q": "a"}, "ca1")],
            limits={"llm_calls": 1},
        )
        assert "GovernanceLimitExceeded" in out["raised_type"]
        assert "llm_calls" in _raise_limit_kind(out)
        assert out["returned_normal"] is False  # 不被吞、不被当正常完成
        assert "task_result" not in out["events"]
        assert "error" in out["events"]
        assert "failed" in out["status_calls"]
        assert len(out["finalize_calls"]) == 0  # F7 abort → 不 finalize
        assert out["counts"]["llm_calls"] == 1

    def test_tool_budget_abort_body_never_runs(self):
        out = run_scenario(
            "tool-abort",
            responses=[tool_call_msg("probe_tool", {"q": "b"}, "cb1")],
            limits={"tool_calls": 0},
        )
        assert "GovernanceLimitExceeded" in out["raised_type"]
        assert "tool_calls" in _raise_limit_kind(out)
        assert out["returned_normal"] is False
        assert out["tool_ran"] == 0  # tool body 未执行
        assert "task_result" not in out["events"]
        assert len(out["finalize_calls"]) == 0

    def test_subagent_llm_budget_abort_penetrates_run(self):
        out = run_scenario(
            "sub-abort",
            responses=[
                tool_call_msg(
                    "task", {"description": "do", "subagent_type": "probe-sub"}, "cc1"
                )
            ],
            limits={"llm_calls": 1},
            subagents=SUBAGENT_SPEC,
        )
        assert "GovernanceLimitExceeded" in out["raised_type"]
        assert "llm_calls" in _raise_limit_kind(out)
        assert out["returned_normal"] is False
        assert "task_result" not in out["events"]
        assert len(out["finalize_calls"]) == 0
        # main decision 1 次；subagent 首决策被拒（不消耗 llm/step）
        assert out["counts"]["llm_calls"] == 1
        assert out["counts"]["agent_steps"] == 1


class TestExceptionBoundary:
    def test_ordinary_agent_exception_reraises_when_active(self):
        """C6：governance-active 下普通 agent 异常可传播（不被吞）。"""
        out = run_scenario("agent-exc", raise_error=RuntimeError("agent boom"))
        assert out["raised_type"] == "RuntimeError"
        assert out["returned_normal"] is False
        assert "task_result" not in out["events"]
        assert "error" in out["events"]
        assert len(out["finalize_calls"]) == 0

    def test_cancelled_error_not_converted_to_completed(self):
        """C6：CancelledError 经 task_cancelled 分支上抛，不伪造 completed。"""
        out = run_scenario(
            "cancel",
            responses=[tool_call_msg("probe_tool", {"q": "c"}, "cd1")],
            cancel_after=0.15,
            block_after_first=True,
        )
        assert out["raised_type"] == "CancelledError"
        assert out["returned_normal"] is False
        assert "task_cancelled" in out["events"]
        assert "task_result" not in out["events"]
        assert "cancelled" in out["status_calls"]
        assert len(out["finalize_calls"]) == 0
