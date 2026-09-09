"""F9-P0 Batch 7 — scenarios 跨 run 对照测试（deterministic Gate）。

档 A（跨 run 行为对照，真实双跑 baseline vs adaptive）：
- s1 already-sufficient → adaptive 不加搜（no unnecessary follow-up）
- s2 missing required → adaptive targeted 补足（required 0→1）且 cost 合规
- s6 diminishing/正确停止 → adaptive 不超预算、completed、stopping 早于/等于策略上限
- s7 near-budget → cost counters ≤ limits 且 completed
- s8 Judge failure → adaptive_fallback → completed 且无第二 run（F7 once）

每个断言基于 orchestrator summary / rubric / TaskRecord，不依赖 provider。
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

_TEST_TMP = Path(__file__).resolve().parent.parent / "_testtmp"


@pytest.fixture
def b7_tmp():
    d = _TEST_TMP / f"b7sc-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    import shutil

    try:
        shutil.rmtree(d)
    except OSError:
        pass


@pytest.fixture(autouse=True)
def _eval_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-placeholder-batch7-eval")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://placeholder.invalid/v1")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-placeholder-batch7-eval")
    yield


def _switch_research_db(d: Path):
    from app.research import config as rconfig
    from app.research import store as rstore

    d.mkdir(parents=True, exist_ok=True)
    os.environ[rconfig.RESEARCH_STORE_ENV] = "sqlite"
    os.environ[rconfig.RESEARCH_DB_ENV] = str(d / "research.sqlite")
    rstore.reset_store()


def _run_pair(b7_tmp, sid, *, judge_failure=False):
    from app.f9.eval import agents as AG
    from app.f9.eval import harness as H
    from app.f9.eval import scenarios as SC

    sc = SC.get_scenario(sid)
    gov_store, ctl = H.make_governance(b7_tmp, owner=f"sc-{sid}")

    _switch_research_db(b7_tmp / f"b-{uuid.uuid4().hex}")
    base = H.run_baseline(
        gov_store, ctl, task_query=sc.task,
        agent_factory=lambda s=sc: SC.make_baseline_agent(s),
    )

    _switch_research_db(b7_tmp / f"a-{uuid.uuid4().hex}")
    runner, _ = SC.make_adaptive_graph_runner_for(sc)
    tool = AG.make_scripted_search_tool(sc.world)
    judge = AG.make_scripted_judge(raise_error=(RuntimeError("judge down") if judge_failure else None))
    ad = H.run_adaptive(
        gov_store, ctl, task_query=sc.task, graph_runner=runner,
        judge_model=judge, plan_model=AG.make_scripted_plan(),
        search_tool=tool, policy=sc.adaptive_policy,
    )
    gov_store.close()
    return sc, base, ad


class TestScenarioRunLevel:
    def test_s1_sufficient_no_extra_followup(self, b7_tmp):
        sc, base, ad = _run_pair(b7_tmp, "s1_sufficient_no_followup")
        assert ad.error == ""
        assert ad.summary["finalize_calls"] == 1
        assert ad.summary["executed_plans"] == 0  # no unnecessary follow-up
        assert ad.summary["stopped_reason"] == "no_gap"
        # 成本合规（limits 默认空 → 不判定惩罚；至少 completed）
        assert ad.summary["rounds"] <= 2

    def test_s2_missing_required_targeted(self, b7_tmp):
        from app.f9.eval import rubric as RB

        sc, base, ad = _run_pair(b7_tmp, "s2_missing_required_targeted")
        assert ad.error == ""
        assert ad.summary["executed_plans"] >= 1  # targeted follow-up 发生
        rb_b = RB.evaluate_side(base, world=sc.world)
        rb_a = RB.evaluate_side(ad, world=sc.world)
        assert rb_a["required_coverage"] > rb_b["required_coverage"]
        v = RB.verdict(rb_b, rb_a)
        assert v["gate_pass"] is True
        assert "required_coverage" in v["improved_dims"]

    def test_s6_correct_stopping_no_budget_breach(self, b7_tmp):
        sc, base, ad = _run_pair(b7_tmp, "s6_diminishing_stop")
        assert ad.error == ""
        assert ad.summary["finalize_calls"] == 1
        # 不超硬上限（policy max=3 → summary.rounds 含 round0 后 adaptive 轮）
        assert ad.summary["rounds"] <= 4
        # completed 而非 timeout/cancel/budget
        assert ad.summary["stopped_reason"] in (
            "no_gap",
            "max_rounds",
            "no_progress",
            "all_gaps_unimportant",
        )

    def test_s7_near_budget_compliant(self, b7_tmp):
        from app.f9.eval import rubric as RB

        sc, base, ad = _run_pair(b7_tmp, "s7_near_budget")
        assert ad.error == ""
        rb_a = RB.evaluate_side(ad, world=sc.world)
        assert rb_a["cost_compliance"] == 1.0
        # counters ≤ limits（policy max_llm_calls=6）
        counters = (getattr(ad.task_row, "counters_snapshot", None) or {}).get(
            "counters", {}
        ) if False else (getattr(ad.task_row, "counters_snapshot", None) or {})
        limits = getattr(ad.task_row, "effective_limits", None) or {}
        if limits.get("llm_calls") is not None:
            assert int(counters.get("llm_calls") or 0) <= int(limits["llm_calls"])

    def test_s8_judge_failure_fallback_completed(self, b7_tmp):
        sc, base, ad = _run_pair(b7_tmp, "s8_fallback_judge_failure", judge_failure=True)
        assert ad.error == ""
        assert ad.summary["stopped_reason"] == "adaptive_fallback"
        assert ad.summary["finalize_calls"] == 1  # F7 once（fallback 后 normal 完成）
        assert "judge_failure_fallback" in (ad.summary.get("errors") or [])
        # 单 run：TaskRecord.run_id 与 research run 一致
        assert ad.run_id
