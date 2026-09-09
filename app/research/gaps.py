"""F9-P0 Batch 2 — Deterministic Gap Detection（纯函数；无 LLM · 无副作用 · 确定性）。

Spec：docs/spec/2026-09-19-f9-p0-evidence-driven-research-loop.md Rev2 §5/§3.1
Plan：docs/plan/2026-09-20-f9-p0-implementation-plan.md Rev2 §F/D5
上游：F9-P0 Batch 1 Research State Projection（app/research/projection.py，PASS/FROZEN）

管道（Batch 2 范围）：

    Research State Projection（Batch 1 输出：含 current_round 与 F8 budget snapshot）
            ↓
        detect_gaps(projection)
            ↓
    Deterministic Gap Signals（typed / rule-id / bounded / stable order）

核心原则：只做"从已有事实确定性地发现研究缺口"的**规则判定**；不判断缺口的语义价值、
不制定后续研究计划、不生成 query —— 那些属于 Batch 3 Semantic Judge 与后续 Batch。

Deterministic boundary（本模块只允许）：
- required coverage 缺失 / evidence-source 缺失 / claim-evidence-verification 结构缺口 /
  unresolved-conflict 信号 / freshness 规则 / budget snapshot 阈值 —— 全部基于 Batch 1
  Projection 已提供的**事实字段**做显式规则求值；
- 禁止：LLM / Semantic Judge / 自然语言语义判断 / 新 Agent / Planner / Verification Agent /
  Targeted Research / Follow-up Plan / Query generation / 新 Runtime·Controller·BudgetCounter /
  修改 F8 / 修改 F1–F8 schema / F7 finalization / Orchestrator / 自动下一轮研究。

每信号必须满足：Input Facts → Explicit Rule(rule id) → Deterministic Signal，
并回答：输入事实、判断规则、输出、边界条件、SQLite/PG 是否同结果（本模块消费 Batch 1
已跨后端归一的 projection → 跨 DB 一致由 projection 保证，见测试）。

Budget boundary（F9-P0 Rev2 冻结约束）：
- F8 BudgetCounter 是唯一预算权威。本模块**只读取** projection["budget"]
  （Batch 1 构建时写入的 F8 read-only snapshot 内容：counts/limits/remaining）；
- 不维护任何 counter、不修改任何 counter、不按搜索次数另算预算；
- `budget_near` 只基于该 snapshot 的 remaining/limits 判定；
- F9 round 只是 projection 里的编排计数（透传输出），不是 runtime execution。

Batch 3 边界：本模块输出"存在一个可确定的结构性/规则性缺口"；
Batch 3 Judge 才判断"这个缺口是否值得追查、为什么、优先级、关注什么"。

Known compatibility gap（Blocked by frozen Batch 1 contract，非本批缺陷）：
- "citation coverage 不足" 与 "query/source 重复率过高"（Spec §5 列举）所需事实
  （citation 计数、query 去重统计）**不在 Batch 1 Projection 契约内** →
  本批不实现、不伪造（不把 not-computed 当 no-gap）；留待 Batch 4 dedup / 未来 additive
  projection 扩展（需用户裁决，不解冻 Batch 1）。文档见 Batch 2 report。
"""

from __future__ import annotations

from typing import Any, Optional

#: 必答子问题 evidence 数量下限（D5 threshold；Eval 校准前初始值；缺口=有 evidence 但 < 该值）
MIN_EVIDENCE: int = 2

#: budget_near 判定：remaining/limit <= 该比例（D5 threshold；Eval 校准前初始值）
BUDGET_NEAR_RATIO: float = 0.2

#: 输出信号条数上限（bounded；超限确定性截断并如实标记 truncated）
MAX_SIGNALS: int = 256

#: F8 四种 budget kind（read-only 镜像；与 projection.BUDGET_KINDS 一致）
_BUDGET_KINDS: tuple[str, ...] = (
    "llm_calls",
    "tool_calls",
    "search_calls",
    "agent_steps",
)

_INSUFFICIENT_VERDICTS: frozenset[str] = frozenset({"INSUFFICIENT", "UNVERIFIABLE"})

_REQUIRED_PROJECTION_KEYS: dict[str, type] = {
    "run_id": str,
    "round": int,
    "budget": dict,
    "sub_questions": list,
    "required_uncovered": list,
    "claims": list,
    "evidence_summary": dict,
    "gap_signals": dict,
    "open_questions": list,
    "size_limits": dict,
}


class GapDetectionError(Exception):
    """Projection 输入不符合 Batch 1 契约（缺键/类型错）→ 显式失败，不静默猜测。

    供后续 orchestrator 按 F9 failure semantics（adaptive 不可用 → 同 F8 run 内 baseline）
    处理；本模块不自行降级。
    """


def _validate_projection(projection: dict[str, Any]) -> None:
    if not isinstance(projection, dict):
        raise GapDetectionError("projection 必须是 dict（Batch 1 project() 输出）")
    for key, kind in _REQUIRED_PROJECTION_KEYS.items():
        if key not in projection:
            raise GapDetectionError(f"projection 缺少键: {key}（Batch 1 契约）")
        if not isinstance(projection[key], kind):
            raise GapDetectionError(
                f"projection.{key} 类型错误: {type(projection[key]).__name__}"
            )
    # budget 快照子键（Batch 1 契约内）
    budget = projection["budget"]
    for sub in ("limits", "counts", "remaining"):
        if sub not in budget or not isinstance(budget[sub], dict):
            raise GapDetectionError(
                f"projection.budget 缺少 {sub} 子键（Batch 1 契约）"
            )


def _signal(
    signal_type: str,
    rule: str,
    subject_type: str,
    subject_id: str,
    reason: str,
    detail: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return {
        "type": signal_type,
        "rule": rule,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "reason": reason,
        "detail": dict(detail or {}),
    }


def detect_gaps(projection: dict[str, Any]) -> dict[str, Any]:
    """Deterministic Gap Detection 主入口（纯函数；consumes Batch 1 Projection）。

    返回 GapSignals：
    {
      "round": int,                       # 透传 projection.round（F9 编排计数，不推断）
      "signals": [Signal...],             # 确定性排序 (type, subject_type, subject_id)
      "counts": {type: count},            # 已输出信号计数（truncated 后）
      "total_signals": int,               # 截断前总数（honest）
      "truncated": bool,                  # 是否因 MAX_SIGNALS 截断
    }
    Signal = {type, rule, subject_type, subject_id, reason, detail}（detail 仅受控小事实，
    无 evidence 正文 / raw history / tool payload）。
    """
    _validate_projection(projection)
    round_no: int = projection["round"]
    signals: list[dict[str, Any]] = []

    # ---- required coverage（Spec §5: 必答未覆盖；有 evidence 但 < MIN_EVIDENCE）----
    required_ids = {
        s["sub_question_id"] for s in projection["sub_questions"] if s.get("required")
    }
    covered_low: set[str] = set()
    for sq in projection["sub_questions"]:
        if not sq.get("required"):
            continue
        ev_n = int(sq.get("evidence_count") or 0)
        if ev_n == 0:
            continue  # 无 evidence 无 source 属 required_uncovered（RU1）；此处避免重复
        if ev_n < MIN_EVIDENCE:
            covered_low.add(sq["sub_question_id"])
            signals.append(
                _signal(
                    "required_low_evidence",
                    "RE1",
                    "sub_question",
                    sq["sub_question_id"],
                    f"必答子问题 evidence 数低于下限 MIN_EVIDENCE={MIN_EVIDENCE}",
                    {"evidence_count": ev_n, "min_evidence": MIN_EVIDENCE},
                )
            )
    for sid in projection["required_uncovered"]:
        if sid in required_ids:
            signals.append(
                _signal(
                    "required_uncovered",
                    "RU1",
                    "sub_question",
                    sid,
                    "必答子问题无 evidence 且无 source（缺失覆盖集）",
                    {"evidence_count": 0, "source_count": 0},
                )
            )

    # ---- claim 级结构缺口（Spec §5）----
    for claim in projection["claims"]:
        cid = claim["claim_id"]
        refs = claim.get("evidence_refs") or []
        has_evidence = len(refs) > 0
        best = claim.get("best_verdict")

        if not has_evidence:
            signals.append(
                _signal(
                    "claim_no_evidence",
                    "CE1",
                    "claim",
                    cid,
                    "claim 没有任何 bound evidence（不可被 F3 验证）",
                    {"evidence_refs": 0},
                )
            )
            continue  # 无 evidence → 其余 claim 级信号不适用
        if best is None:
            signals.append(
                _signal(
                    "claim_unverified",
                    "CV1",
                    "claim",
                    cid,
                    "claim 有 bound evidence 但无 succeeded verification（best_verdict=None）",
                    {"evidence_refs": len(refs)},
                )
            )
        elif best in _INSUFFICIENT_VERDICTS:
            signals.append(
                _signal(
                    "claim_verdict_insufficient",
                    "CV2",
                    "claim",
                    cid,
                    f"claim 最新 succeeded verification verdict={best}（结构性不足）",
                    {"best_verdict": best},
                )
            )
        if claim.get("independent_flag") is False:
            signals.append(
                _signal(
                    "independent_sources_insufficient",
                    "IS1",
                    "claim",
                    cid,
                    "F5 显示支持侧独立来源簇数 < 2（仅 F5 已计算时判定；None=未计算不入信号）",
                    {},
                )
            )
        if claim.get("fresh") is False:
            signals.append(
                _signal(
                    "stale_evidence",
                    "FR1",
                    "claim",
                    cid,
                    "claim 最新 bound evidence 超出 FRESHNESS_WINDOW（fresh=False）",
                    {},
                )
            )
        conflicts = claim.get("conflicts") or []
        if conflicts:
            signals.append(
                _signal(
                    "unresolved_conflict",
                    "CF1",
                    "claim",
                    cid,
                    "存在 F6 未缓解的 confirmed conflict（含 reconciliation_state 详情）",
                    {
                        "conflict_ids": [c["conflict_id"] for c in conflicts],
                        "count": len(conflicts),
                    },
                )
            )

    # ---- budget_near（F8 snapshot 只读阈值；remaining/limit <= BUDGET_NEAR_RATIO）----
    budget = projection["budget"]
    limits: dict[str, int] = budget["limits"]
    counts: dict[str, int] = budget["counts"]
    remaining: dict[str, int] = budget["remaining"]
    for kind in _BUDGET_KINDS:
        limit = int(limits.get(kind) or 0)
        count = int(counts.get(kind) or 0)
        remain = int(remaining.get(kind, max(0, limit - count)))
        near = remain == 0 or (limit > 0 and (remain / limit) <= BUDGET_NEAR_RATIO)
        if near:
            signals.append(
                _signal(
                    "budget_near",
                    "BU1",
                    "budget_kind",
                    kind,
                    f"F8 预算剩余接近上限（remaining/limit <= {BUDGET_NEAR_RATIO:.0%}）",
                    {"limit": limit, "count": count, "remaining": remain},
                )
            )

    # ---- deterministic ordering + bounded（超限截断并如实标记）----
    signals.sort(key=lambda s: (s["type"], s["subject_type"], s["subject_id"]))
    total = len(signals)
    truncated = total > MAX_SIGNALS
    kept = signals[:MAX_SIGNALS]
    counts: dict[str, int] = {}
    for s in kept:
        counts[s["type"]] = counts.get(s["type"], 0) + 1

    return {
        "round": int(round_no),
        "signals": kept,
        "counts": counts,
        "total_signals": total,
        "truncated": truncated,
    }
