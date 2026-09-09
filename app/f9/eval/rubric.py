"""F9-P0 Batch 7 — deterministic / rubric-based evaluator（Gate 主判据）。

对每侧（baseline/adaptive）的 research_state + final answer 施加确定性规则，
产出可比较的 rubric 得分。无 real LLM。规则仅使用本仓库研究库的只读事实。

维度（D5）：
- required_coverage：required sq 是否达最小 evidence；
- citation_coverage：final answer 是否引用检索到的 source locator/标题（确定性子串）；
- evidence_sufficiency：存在 claim 时绑定证据是否充分（min 1 binding/claim）；
- unsupported_claim_penalty：存在无证据/无 succeeded verification 的 claim 时扣分；
- conflict_coverage：confirmed conflicts 是否已 reconciliation（unresolved 扣分/0 惩罚）；
- redundant_search：通过 world.calls 判重复 query（重复 → 惩罚）；
- cost_compliance：counters ≤ limits。

输出：dict[str, float]，全部越大越好（惩罚维度以 1-penalty 表达），并可合成总分。
"""
from __future__ import annotations

from typing import Any, Optional

from app.f9.eval.harness import (
    collect_cost,
    collect_research_metrics,
    required_coverage,
)


def evaluate_side(
    run: Any,
    world: Any = None,
    *,
    min_binding_per_claim: int = 1,
    required_sq_evidence: int = 1,
) -> dict[str, Any]:
    """对一个 EvalRun 计算 rubric（deterministic）。"""
    state = collect_research_metrics(run.run_id)
    cost = collect_cost(run.task_row)
    cov = required_coverage(run.run_id, required_sq_evidence)

    # 1) required coverage
    required = 1.0 if cov["all_covered"] else 0.0

    # 2) citation coverage：final 是否引用至少一个 source locator/标题
    locators = _locators(run.run_id)
    final = run.final or ""
    cited = sum(1 for tok in locators if tok and tok in final)
    citation = min(1.0, cited / max(1, len(locators))) if locators else 0.0

    # 3) evidence sufficiency（claim 绑定）
    claims = int(state.get("claim_count") or 0)
    bindings = int(state.get("bindings") or 0)
    sufficiency = (
        min(1.0, bindings / (claims * max(1, min_binding_per_claim)))
        if claims
        else 1.0  # 无 claim → 该项中性（不惩罚无 claim 的 plain baseline）
    )

    # 4) unsupported claim penalty：存在无 succeeded verification 的 claim
    ver_succeeded = int(state.get("verification_succeeded") or 0)
    unsupported = (
        min(1.0, max(0.0, claims - ver_succeeded) / claims) if claims else 0.0
    )
    unsupported_score = 1.0 - unsupported

    # 5) conflict coverage：confirmed 是否已 resolve
    confirmed = int(state.get("conflicts_confirmed") or 0)
    unresolved = int(state.get("conflicts_unresolved") or 0)
    conflict_score = 1.0 if confirmed == 0 else (
        0.0 if unresolved > 0 else 1.0
    )

    # 6) redundant search（world.calls 重复 query）
    redundant_score = 1.0
    if world is not None and getattr(world, "calls", None):
        n = len(world.calls)
        dup = n - len(set(world.calls))
        redundant_score = 0.0 if dup > 0 and n > 0 else 1.0

    # 7) cost compliance（counters ≤ limits；无 limits → 中性 1）
    counters = cost.get("counters") or {}
    limits = cost.get("limits") or {}
    compliant = True
    if limits:
        for k in ("llm_calls", "tool_calls", "search_calls", "agent_steps"):
            lim = limits.get(k)
            if lim is not None and int(counters.get(k) or 0) > int(lim):
                compliant = False
                break
    cost_score = 1.0 if compliant else 0.0

    total = (
        required
        + citation
        + sufficiency
        + unsupported_score
        + conflict_score
        + redundant_score
        + cost_score
    )
    return {
        "required_coverage": required,
        "citation_coverage": citation,
        "evidence_sufficiency": sufficiency,
        "unsupported_score": unsupported_score,
        "conflict_coverage": conflict_score,
        "redundant_score": redundant_score,
        "cost_compliance": cost_score,
        "total": total,
        "_raw": {
            "state": state,
            "cost": cost,
            "coverage": cov,
            "final_len": len(final or ""),
        },
    }


def _locators(run_id: Optional[str]) -> list[str]:
    if not run_id:
        return []
    from app.f9.eval.harness import _get_research_store

    store = _get_research_store()
    rows = store.execute(
        "SELECT locator FROM sources WHERE run_id=%s ORDER BY source_id", (run_id,)
    )
    return [str(r["locator"]) for r in rows]


def verdict(
    baseline_rubric: dict[str, Any],
    adaptive_rubric: dict[str, Any],
) -> dict[str, Any]:
    """Gate 判定：adaptive 在 ≥1 grounding 维度 ≥ baseline 且 total ≥ baseline
    （对照 baseline 的相对收益；negative-control 场景由 scenarios 另行断言）。"""
    dims = [
        "required_coverage",
        "citation_coverage",
        "evidence_sufficiency",
        "unsupported_score",
        "conflict_coverage",
    ]
    improved = [
        d
        for d in dims
        if adaptive_rubric[d] > baseline_rubric[d] + 1e-9
    ]
    total_delta = adaptive_rubric["total"] - baseline_rubric["total"]
    return {
        "improved_dims": improved,
        "total_delta": round(total_delta, 6),
        "gate_pass": bool(improved) and total_delta >= -1e-9,
    }
