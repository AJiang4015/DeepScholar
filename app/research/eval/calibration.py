"""F9-P0 Batch 8 — Stopping / Threshold Calibration runner。

目的：对 5 个策略常量做受控敏感性扫描，用 Batch7 deterministic eval 观测
stopping correctness / quality / cost，产出数据驱动结论（不预设改默认值）。

方法：
- 每参数独立扫描（2–3 档），在代表性场景上跑 adaptive（orchestrator 真实执行 +
  scripted world/judge/plan/search）；参数经运行期覆盖（MIN_EVIDENCE / BUDGET_NEAR_RATIO
  为 gaps 模块属性 monkeypatch；3 个 policy 值经 research_policy 注入），**不改生产
  默认值文件**；
- baseline 对照：代表性 positive 场景跑 baseline 一次作为质量基线；
- 观测：rounds / stopped_reason / executed_plans / rubric 各维 / cost counters；
- 汇总表 + 推荐（现状是否 Pareto）。

Frozen boundary：不改 F8/F1–F7/Batch1–6/orchestrator 语义；不改 Batch7 eval 契约；
real provider 不参与（deterministic Gate）。
"""
from __future__ import annotations

import dataclasses
import os
import uuid
from pathlib import Path
from typing import Any, Optional

import app.research.gaps as _f9g
from app.research.eval import agents as AG
from app.research.eval import harness as H
from app.research.eval import rubric as RB
from app.research.eval import scenarios as SC

# 生产默认值（只读镜像，用于恢复与对照）
DEFAULTS = {
    "min_evidence": getattr(_f9g, "MIN_EVIDENCE", 2),
    "budget_near_ratio": getattr(_f9g, "BUDGET_NEAR_RATIO", 0.2),
    "max_research_rounds": 3,
    "max_no_progress_rounds": 1,
    "repeated_gap_threshold": 2,
}

# 每参数扫描档位（值域保守，覆盖现状 ±）
SWEEP_GRID: dict[str, list[Any]] = {
    "min_evidence": [1, 2, 3],
    "budget_near_ratio": [0.1, 0.2, 0.3],
    "max_research_rounds": [2, 3, 4],
    "max_no_progress_rounds": [1, 2],
    "repeated_gap_threshold": [2, 3],
}

# 代表场景：positive(需补 required) / control(足够即停) / budget / fallback
SWEEP_SCENARIOS: tuple[str, ...] = (
    "s1_sufficient_no_followup",
    "s2_missing_required_targeted",
    "s6_diminishing_stop",
    "s7_near_budget",
    "s8_fallback_judge_failure",
)


@dataclasses.dataclass
class SweepPoint:
    param: str
    value: Any
    scenario: str
    rounds: int = 0
    stopped: Optional[str] = None
    plans: int = 0
    errors: list[str] = dataclasses.field(default_factory=list)
    rubric_total: float = 0.0
    required: float = 0.0
    citation: float = 0.0
    cost_compliance: float = 1.0
    counters: dict[str, Any] = dataclasses.field(default_factory=dict)
    ok: bool = False


def _fresh_research(tag: str) -> Path:
    from app.research import config as rconfig
    from app.research import store as rstore

    d = _test_tmp() / f"b8-{tag}-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=True)
    os.environ[rconfig.RESEARCH_STORE_ENV] = "sqlite"
    os.environ[rconfig.RESEARCH_DB_ENV] = str(d / "research.sqlite")
    rstore.reset_store()
    return d


def _test_tmp() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "_testtmp"


def _apply(param: str, value: Any) -> None:
    if param == "min_evidence":
        _f9g.MIN_EVIDENCE = int(value)
    elif param == "budget_near_ratio":
        _f9g.BUDGET_NEAR_RATIO = float(value)
    # 3 个 policy 值经 research_policy 注入（不在模块层改）


def _restore(param: str) -> None:
    if param == "min_evidence":
        _f9g.MIN_EVIDENCE = int(DEFAULTS["min_evidence"])
    elif param == "budget_near_ratio":
        _f9g.BUDGET_NEAR_RATIO = float(DEFAULTS["budget_near_ratio"])


def _policy_for(sc: SC.Scenario, param: str, value: Any) -> dict[str, Any]:
    policy = dict(sc.adaptive_policy)
    if param in ("max_research_rounds", "max_no_progress_rounds", "repeated_gap_threshold"):
        policy[param] = int(value)
    return policy


def _run_adaptive_once(
    sc: SC.Scenario, param: str, value: Any
) -> "tuple[Optional[H.EvalRun], list[str]]":
    d = _fresh_research(f"{sc.id}-{param}-{value}")
    gov_store, ctl = H.make_governance(d, owner=f"b8-{sc.id}")
    runner, _ = SC.make_adaptive_graph_runner_for(sc)
    tool = AG.make_scripted_search_tool(sc.world)
    judge = AG.make_scripted_judge(
        raise_error=(RuntimeError("judge down") if sc.judge_failure else None)
    )
    plan = AG.make_scripted_plan()
    try:
        _apply(param, value)
        ad = H.run_adaptive(
            gov_store, ctl, task_query=sc.task, graph_runner=runner,
            judge_model=judge, plan_model=plan, search_tool=tool,
            policy=_policy_for(sc, param, value),
        )
        return ad, []
    except Exception as exc:  # noqa: BLE001 — 记录失败点，不中断 sweep
        return None, [f"{type(exc).__name__}: {exc}"]
    finally:
        _restore(param)
        gov_store.close()


def run_sweep(
    scenario_ids: Optional[tuple[str, ...]] = None,
    param_grid: Optional[dict[str, list[Any]]] = None,
) -> list[SweepPoint]:
    """执行确定性参数扫描。返回每个 (param,value,scenario) 的观测点。

    注意：每次 run_adaptive 独立 research db + governance（隔离，无跨点污染）；
    同一场景/参数点不同 value 之间无状态残留。
    """
    scenarios = scenario_ids or SWEEP_SCENARIOS
    grid = param_grid or SWEEP_GRID
    points: list[SweepPoint] = []
    for param, values in grid.items():
        for value in values:
            for sid in scenarios:
                sc = SC.get_scenario(sid)
                if param in ("max_no_progress_rounds", "repeated_gap_threshold") and sid in (
                    "s1_sufficient_no_followup",
                ):
                    continue  # no-gap 场景不触发 no-progress/repeated 路径（无意义点）
                ad, errs = _run_adaptive_once(sc, param, value)
                pt = SweepPoint(param=param, value=value, scenario=sid, errors=errs)
                if ad is None:
                    points.append(pt)
                    continue
                pt.rounds = int(ad.summary.get("rounds") or 0)
                pt.stopped = ad.summary.get("stopped_reason")
                pt.plans = int(ad.summary.get("executed_plans") or 0)
                pt.errors = list(ad.summary.get("errors") or []) + (
                    [ad.error] if ad.error else []
                )
                rb = RB.evaluate_side(ad, world=sc.world)
                pt.rubric_total = round(float(rb["total"]), 4)
                pt.required = round(float(rb["required_coverage"]), 4)
                pt.citation = round(float(rb["citation_coverage"]), 4)
                pt.cost_compliance = round(float(rb["cost_compliance"]), 4)
                pt.counters = dict(ad.summary)
                pt.ok = ad.error == ""
                points.append(pt)
    return points


def summarize(points: list[SweepPoint]) -> list[dict[str, Any]]:
    """按 (param,value) 汇总各场景观测 → 便于判定 Pareto。"""
    out: list[dict[str, Any]] = []
    by = {}
    for p in points:
        key = (p.param, p.value)
        by.setdefault(key, []).append(p)
    for (param, value), pts in sorted(by.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        ok = [p for p in pts if p.ok]
        out.append(
            {
                "param": param,
                "value": value,
                "runs": len(pts),
                "ok_runs": len(ok),
                "avg_rounds": round(sum(p.rounds for p in ok) / len(ok), 2) if ok else None,
                "avg_total": round(sum(p.rubric_total for p in ok) / len(ok), 4) if ok else None,
                "avg_required": round(sum(p.required for p in ok) / len(ok), 4) if ok else None,
                "avg_citation": round(sum(p.citation for p in ok) / len(ok), 4) if ok else None,
                "cost_compliant": all(p.cost_compliance == 1.0 for p in ok) if ok else None,
                "stopped_set": sorted({p.stopped for p in ok}),
                "errors": sorted({e for p in pts for e in p.errors})[:5],
            }
        )
    return out


def run_calibration() -> dict[str, Any]:
    """一键校准入口（供测试/报告调用）：返回 points + summary。"""
    points = run_sweep()
    summary = summarize(points)
    return {"points": points, "summary": summary, "defaults": dict(DEFAULTS)}


def scenario_detail(points: list[SweepPoint], param: str, value: Any) -> list[dict[str, Any]]:
    """单个 (param,value) 的逐场景明细（判断哪类场景对参数敏感）。"""
    out = []
    for p in points:
        if p.param == param and p.value == value:
            out.append(
                {
                    "scenario": p.scenario,
                    "ok": p.ok,
                    "rounds": p.rounds,
                    "stopped": p.stopped,
                    "plans": p.plans,
                    "total": p.rubric_total,
                    "required": p.required,
                    "citation": p.citation,
                    "cost_ok": p.cost_compliance == 1.0,
                    "errors": p.errors,
                }
            )
    return out


def sensitivity_table(summary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 summary 转成简洁敏感性表（判定参数是否有可测影响）。"""
    rows = []
    for s in summary:
        rows.append(
            {
                "param": s["param"],
                "value": s["value"],
                "avg_rounds": s["avg_rounds"],
                "avg_total": s["avg_total"],
                "avg_required": s["avg_required"],
                "avg_citation": s["avg_citation"],
                "cost_compliant": s["cost_compliant"],
            }
        )
    return rows
