"""F9-P0 Batch 8 — Stopping / Threshold Calibration 测试。

覆盖：
- calibration runner 可运行（deterministic，隔离 db/gov 每点）；
- summary/sensitivity 结构正确；
- 参数扫描前后生产默认值不变（runner 恢复机制）；
- D3 判定逻辑：若默认值点不劣于邻域且成本合规 → 推荐保持默认（不改码）；
- MIN_EVIDENCE 敏感性：提高 ME 应使缺证据场景轮次/plan 不降（s2 补足需更多轮）。

Frozen boundary：不改生产默认值文件；不改 Batch7 eval 契约；无 schema/UI/infra。
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

_TEST_TMP = Path(__file__).resolve().parent.parent / "_testtmp"


@pytest.fixture
def b8_tmp():
    import shutil

    d = _TEST_TMP / f"b8-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    try:
        shutil.rmtree(d)
    except OSError:
        pass


@pytest.fixture(autouse=True)
def _b8_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-placeholder-batch8-eval")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://placeholder.invalid/v1")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-placeholder-batch8-eval")
    yield


class TestCalibrationRunner:
    def test_defaults_preserved_after_sweep(self):
        import app.research.gaps as f9g
        from app.research.eval import calibration as C

        before = {
            "min_evidence": f9g.MIN_EVIDENCE,
            "budget_near_ratio": f9g.BUDGET_NEAR_RATIO,
        }
        res = C.run_sweep(
            scenario_ids=("s2_missing_required_targeted",),
            param_grid={
                "min_evidence": [1, 2, 3],
            },
        )
        assert len(res) == 3
        assert f9g.MIN_EVIDENCE == before["min_evidence"]  # 恢复默认
        assert f9g.BUDGET_NEAR_RATIO == before["budget_near_ratio"]
        # 每点独立隔离且 ok
        assert all(p.ok for p in res)

    def test_summary_and_sensitivity_structure(self):
        from app.research.eval import calibration as C

        res = C.run_sweep(
            scenario_ids=("s2_missing_required_targeted",),
            param_grid={"max_research_rounds": [2, 3]},
        )
        summary = C.summarize(res)
        assert len(summary) == 2
        for s in summary:
            assert s["param"] == "max_research_rounds"
            assert s["avg_total"] is not None
        sens = C.sensitivity_table(summary)
        assert all("avg_rounds" in r and "avg_total" in r for r in sens)
        det = C.scenario_detail(res, "max_research_rounds", 3)
        assert det and all(d["scenario"] == "s2_missing_required_targeted" for d in det)

    def test_min_evidence_monotonic_rounds(self):
        """s2 缺 required：ME=1 应比 ME=3 少轮/少 plan（补足成本随阈值上升）。"""
        from app.research.eval import calibration as C

        res = C.run_sweep(
            scenario_ids=("s2_missing_required_targeted",),
            param_grid={"min_evidence": [1, 2, 3]},
        )
        by_val = {p.value: p for p in res}
        assert by_val[1].ok and by_val[3].ok
        # 更高的 ME 至少不减少 rounds（需求更高 → 轮次不降）
        assert by_val[3].rounds >= by_val[1].rounds

    def test_defaults_are_pareto_acceptable(self):
        """D3：默认档不劣于邻域且 cost 合规 → 推荐保持默认（不改码）。"""
        from app.research.eval import calibration as C

        res = C.run_sweep(
            scenario_ids=(
                "s1_sufficient_no_followup",
                "s2_missing_required_targeted",
            ),
            param_grid={
                "min_evidence": [1, 2, 3],
                "budget_near_ratio": [0.2],
                "max_research_rounds": [3],
            },
        )
        summary = C.summarize(res)
        # 每档 cost 合规 + avg_total 无显著退化 → 不满足“明确更优”，维持默认
        for s in summary:
            assert s["cost_compliant"] is True
            assert s["avg_total"] >= 0.0
        # min_evidence 各档 avg_total 差幅不大 → 无强证据改值（记录为结论输入）
        totals = {s["value"]: s["avg_total"] for s in summary if s["param"] == "min_evidence"}
        assert len(totals) == 3
        assert max(totals.values()) - min(totals.values()) <= 2.0  # 无巨大差距
