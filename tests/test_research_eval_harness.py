"""F9-P0 Batch 7 — eval harness / rubric 单测（deterministic，无真实 provider）。

覆盖：
- world：results_for（显式路由）与 results_any（补搜 fallback）语义；
- harness：baseline（run_deep_agent + scripted agent）与 adaptive
  （f9_orchestrator + scripted seam）各独立治理 execute；
- rubric：required/citation/sufficiency/unsupported/conflict/redundant/cost 的
  deterministic 分数与 verdict。

Frozen boundary：不改 F8/F1–F7/main_agent/orchestrator/Batch1–6；baseline 经真实
run_deep_agent 入口 + 进程内 get_main_agent 替换。
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

_TEST_TMP = Path(__file__).resolve().parent.parent / "_testtmp"


@pytest.fixture
def b7_tmp():
    d = _TEST_TMP / f"b7-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    import shutil

    try:
        shutil.rmtree(d)
    except OSError:
        pass


@pytest.fixture(autouse=True)
def _eval_env(monkeypatch):
    """占位外部凭据：本批只跑 deterministic scripted，不调用真实 provider。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-placeholder-batch7-eval")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://placeholder.invalid/v1")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-placeholder-batch7-eval")
    yield


class TestWorld:
    def test_results_for_explicit_route_only(self):
        from app.research.eval.world import Ev, World

        w = World(task="t", seed="s", query_to_evidence={"q": [Ev("q", "a", "c")]})
        assert len(w.results_for("q")) == 1
        assert w.results_for("unknown") == []  # 无路由 → 空（初始不足）
        assert w.results_any("unknown") == []  # 无 extra → 空

    def test_results_any_falls_back_to_extra(self):
        from app.research.eval.world import Ev, World

        w = World(
            task="t",
            seed="s",
            extra_evidence=[Ev("x", "a", "c")],
        )
        assert len(w.results_any("anything")) == 1
        assert w.results_for("anything") == []  # 初始检索无证据

    def test_deterministic_locator(self):
        from app.research.eval.world import make_locator

        assert make_locator("s", 1) == make_locator("s", 1)
        assert make_locator("s", 1) != make_locator("s", 2)


class TestHarnessBaseline:
    def test_baseline_deterministic_run(self, b7_tmp, monkeypatch):
        from app.research.eval import harness as H
        from app.research.eval import scenarios as SC

        sc = SC.get_scenario("s1_sufficient_no_followup")
        gov_store, ctl = H.make_governance(b7_tmp, owner="t-base")
        # baseline 独立 research db（b7_tmp 内再建）
        _fresh = _switch_research_db(b7_tmp / f"r-{uuid.uuid4().hex}")
        run = H.run_baseline(
            gov_store, ctl, task_query=sc.task, agent_factory=lambda s=sc: SC.make_baseline_agent(s)
        )
        gov_store.close()
        _fresh.close()
        assert run.error == ""
        assert run.final
        assert run.run_id


def _switch_research_db(d: Path):
    from app.research import config as rconfig
    from app.research import store as rstore

    d.mkdir(parents=True, exist_ok=True)
    os.environ[rconfig.RESEARCH_STORE_ENV] = "sqlite"
    os.environ[rconfig.RESEARCH_DB_ENV] = str(d / "research.sqlite")
    rstore.reset_store()
    return _ResetCtx()


class _ResetCtx:
    def close(self):
        from app.research import store as rstore

        rstore.reset_store()


class TestRubric:
    def test_rubric_shape_and_total(self, b7_tmp):
        from app.research.eval import harness as H
        from app.research.eval import rubric as RB
        from app.research.eval import scenarios as SC
        from app.research.eval import agents as AG

        sc = SC.get_scenario("s2_missing_required_targeted")
        gov_store, ctl = H.make_governance(b7_tmp, owner="t-rubric")

        # baseline
        ctx = _switch_research_db(b7_tmp / f"r-{uuid.uuid4().hex}")
        base = H.run_baseline(
            gov_store, ctl, task_query=sc.task,
            agent_factory=lambda s=sc: SC.make_baseline_agent(s),
        )
        ctx.close()

        # adaptive 独立 db
        ctx2 = _switch_research_db(b7_tmp / f"r-{uuid.uuid4().hex}")
        runner, _ = SC.make_adaptive_graph_runner_for(sc)
        tool = AG.make_scripted_search_tool(sc.world)
        ad = H.run_adaptive(
            gov_store, ctl, task_query=sc.task, graph_runner=runner,
            judge_model=AG.make_scripted_judge(), plan_model=AG.make_scripted_plan(),
            search_tool=tool, policy=sc.adaptive_policy,
        )
        ctx2.close()
        gov_store.close()

        rb_b = RB.evaluate_side(base, world=sc.world)
        rb_a = RB.evaluate_side(ad, world=sc.world)
        for rb in (rb_b, rb_a):
            for k in (
                "required_coverage",
                "citation_coverage",
                "evidence_sufficiency",
                "unsupported_score",
                "conflict_coverage",
                "redundant_score",
                "cost_compliance",
                "total",
            ):
                assert k in rb
        assert rb_a["required_coverage"] >= rb_b["required_coverage"]
        v = RB.verdict(rb_b, rb_a)
        assert set(v) == {"improved_dims", "total_delta", "gate_pass"}
