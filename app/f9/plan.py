"""F9-P0 Batch 4 — Follow-up Plan + Validation + Dedup（纯函数 + plan-proposal LLM；不执行研究）。

裁决记录（用户 Readiness P1–P6 ACCEPT，2026-09-26）：
- P1 in-run in-memory ledger：本批**不建表/migration**；dedup/retry 分类只对传入 ledger 判定
  （ledger 由未来 Batch6 orchestrator 在一个 execution 内持有）；跨 execution/crash 持久化属
  future additive Gate。
- P2 plan-proposal LLM（若启用）复用 F8 GovernanceExecution：`ctx.make_handler()` +
  `ainvoke(config={"callbacks":[handler]})`，计费 +1 llm_call / +0 agent_step（unclassified 冻结
  分类）；provider/malformed/schema/未知字段/非法引用/bounds/deterministic 失败 →
  `PlanProposalFailure`（orchestrator 按 R2-1 在同 execute 内 fallback baseline）；
  GovernanceLimitExceeded / CancelledError / F8 timeout **不吞**，原样传播。
- P3 identity 表示：objective = bounded normalized text + 显式 target sub-question；
  source_universe_identity = SHA256(tool + agent + CONFIG_VERSION)（无 secret）；
  time_window ∈ {"NONE"} ∪ {YYYY-MM-DD/YYYY-MM-DD}，禁止 wall-clock 自动生成。
- P4 canonicalize_query：可解析为 http(s) URL → 复用 app/research/normalize.canonicalize_url；
  否则 lowercase + whitespace→单空格 + strip（不强制自然语言走 URL normalization）。
- P5 候选 = important==True 的 judged gap；排序 priority high>medium>low，同 priority 按
  Batch3 输入序；bounded 常量（Batch8 校准）。
- P6 plan_id = SHA256(canonical_validated_plan)（显式排序/稳定序列化；不用 UUID）。

Identity 层级（概念分离，禁止合并）：
    query_identity = SHA256(canonicalize_query(query))
    objective_identity = SHA256(canonical(target_sq_id + objective))
    source_universe_identity = SHA256(tool + agent + CONFIG_VERSION)
    time_window_identity = canonical window（"NONE" 或 "YYYY-MM-DD/YYYY-MM-DD"）
    dedup_identity = SHA256(deterministic tuple(query_identity, objective_identity,
                            source_universe_identity, time_window_identity))   # ≠ query_identity
    plan_id = SHA256(canonical_validated_plan)                                 # ≠ dedup_identity

Dedup/Retry/Re-query/Re-verify（§11，严格区分）：
    - duplicate：同一 dedup_identity 且上次成功产生 source → 拒绝；
    - retry：同一 dedup_identity 且上次失败未产生新 source → 保留，attempt metadata +1，
      identity 不变；
    - re-query：query/objective/source_universe/time_window 任一 identity component 改变 →
      新 dedup_identity（新研究 query，非 retry）；
    - re_verify：独立 namespace（kind="re_verify"），不参与 research_query 的 dedup。

本模块只产出**validated 结构性研究意图**，不执行 Targeted Research / Verification / F7 /
Orchestrator / Runtime 控制。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.research.normalize import canonicalize_url
from app.runtime.governance.callbacks import GovernanceCallbackHandler
from app.runtime.governance.context import GovernanceExecution, get_governance_execution
from app.runtime.governance.counters import GovernanceLimitExceeded

# ---------------------------------------------------------------------------
# 常量（bounded；Batch 8 Eval 校准前不视为冻结协议值）
# ---------------------------------------------------------------------------
CONFIG_VERSION: str = "f9-plan-config-v1"  # source universe 配置指纹（无 secret）

MAX_OBJECTIVE: int = 600
MAX_QUERY: int = 800
MAX_QUERIES_PER_GAP: int = 3
MAX_PLAN_CANDIDATES: int = 16
MAX_WHY: int = 600
MAX_STOP_CONDITION: int = 400
MAX_TARGET_CLAIMS: int = 8
CONTEXT_CHAR_CAP: int = 16000

PRIORITY_ORDER: dict[str, int] = {"high": 0, "medium": 1, "low": 2}

_KIND_RESEARCH_QUERY = "research_query"
_KIND_RE_VERIFY = "re_verify"
ALLOWED_KINDS: frozenset[str] = frozenset({_KIND_RESEARCH_QUERY, _KIND_RE_VERIFY})

_TIME_WINDOW_RE = re.compile(r"^\d{4}-\d{2}-\d{2}/\d{4}-\d{2}-\d{2}$")

#: 单次 proposal LLM 输入/输出大小常量
_SNIPPET_MAX = 400
_PROPOSAL_KEYS = frozenset({"gap_id", "objective", "queries", "why", "stop_condition"})


class PlanError(Exception):
    """Follow-up Plan 层错误基类。"""


class PlanValidationError(PlanError):
    """deterministic validation 失败（纯函数路径；非法输入/引用/越界/bounds/identity 输入）。"""


class PlanProposalFailure(PlanError):
    """plan-proposal LLM 失败（provider/malformed/JSON/schema/未知字段/引用/bounds）。

    由调用方（未来 orchestrator）按 R2-1 丢弃 partial → 同 F8 execute 内 baseline fallback。
    GovernanceLimitExceeded / CancelledError 属 F8 control，不包装为本异常。
    """


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fail(message: str) -> None:
    raise PlanValidationError(message)


def _is_absolute_http_url(text: str) -> bool:
    """query 是否可明确解析为 http(s) URL（P4 判定边界；无 scheme 不算 URL）。"""
    from urllib.parse import urlsplit  # noqa: PLC0415

    raw = (text or "").strip()
    if "://" not in raw:
        return False
    try:
        parts = urlsplit(raw)
    except ValueError:
        return False
    return (parts.scheme or "").lower() in {"http", "https"} and bool(parts.netloc)


# ---------------------------------------------------------------------------
# Canonicalization（P4 / P3；deterministic，无 wall-clock / 随机）
# ---------------------------------------------------------------------------
def canonicalize_query(query: str) -> str:
    """URL query → normalize.canonicalize_url；非 URL query → lowercase+单空格 strip。"""
    if not isinstance(query, str):
        _fail("query 必须为 str")
    text = query.strip()
    if not text:
        _fail("query 不能为空")
    if _is_absolute_http_url(text):
        canonical = canonicalize_url(text)
        if canonical is not None:
            return canonical
        # URL 归一失败（异常输入）→ 走纯文本归一（deterministic fallback）
    return re.sub(r"\s+", " ", text).strip().lower()


def canonicalize_objective(objective: str) -> str:
    if not isinstance(objective, str):
        _fail("objective 必须为 str")
    text = re.sub(r"\s+", " ", objective).strip()
    if not text:
        _fail("objective 不能为空")
    return text.lower()


def canonicalize_time_window(time_window: str) -> str:
    if time_window == "NONE":
        return "NONE"
    if not isinstance(time_window, str) or not _TIME_WINDOW_RE.match(time_window):
        _fail(
            "time_window 只允许 'NONE' 或 'YYYY-MM-DD/YYYY-MM-DD' 格式"
            f"（禁止 wall-clock 自动生成）：{time_window!r}"
        )
    return time_window


def canonical_source_universe(tool: str, agent: str) -> str:
    if not tool or not agent:
        _fail("source universe 需要 tool 与 agent（非空）")
    return f"{str(tool).strip().lower()}\x1f{str(agent).strip().lower()}\x1f{CONFIG_VERSION}"


# ---------------------------------------------------------------------------
# Identity 生成（§7 层级；概念分离）
# ---------------------------------------------------------------------------
def query_identity_of(query: str) -> str:
    return _sha256(canonicalize_query(query))


def objective_identity_of(target_sub_question_id: str, objective: str) -> str:
    canonical = f"{target_sub_question_id}\x1f{canonicalize_objective(objective)}"
    return _sha256(canonical)


def source_universe_identity_of(tool: str, agent: str) -> str:
    return _sha256(canonical_source_universe(tool, agent))


def time_window_identity_of(time_window: str) -> str:
    """= canonical explicit window / NONE（§7：identity 即规范化窗口字符串）。"""
    return canonicalize_time_window(time_window)


def dedup_identity_of(
    *,
    query: str,
    target_sub_question_id: str,
    objective: str,
    tool: str,
    agent: str,
    time_window: str,
) -> str:
    """dedup_identity = SHA256(deterministic tuple)（≠ query_identity）。

    Plan D4 允许"或等价 deterministic tuple"；用 \\x1f 连接（record separator 不出现于输入）。
    """
    parts = (
        query_identity_of(query),
        objective_identity_of(target_sub_question_id, objective),
        source_universe_identity_of(tool, agent),
        time_window_identity_of(time_window),
    )
    return _sha256("\x1f".join(parts))


def _canonical_plan_dict(plan: dict[str, Any]) -> dict[str, Any]:
    """plan_id 的 canonical 表示：显式键序 + 稳定 JSON（public 字段 + 内部 identities）。"""
    queries_canon = []
    for q in plan.get("queries", []):
        queries_canon.append(
            {
                "query": q["query"],
                "query_identity": q["query_identity"],
                "dedup_identity": q["dedup_identity"],
                "kind": q["kind"],
            }
        )
    return {
        "target_sub_question_id": plan["target_sub_question_id"],
        "objective": plan["objective"],
        "queries": queries_canon,
        "priority": plan["priority"],
        "verification_target_claim_ids": plan["verification_target_claim_ids"],
        "stop_condition": plan["stop_condition"],
        "why": plan["why"],
        "gap_refs": plan["gap_refs"],
    }


def plan_id_of(plan: dict[str, Any]) -> str:
    """plan_id = SHA256(canonical_validated_plan)（P6；不用 UUID；≠ dedup_identity）。"""
    canonical = json.dumps(
        _canonical_plan_dict(plan),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return _sha256(canonical)


# ---------------------------------------------------------------------------
# Candidate selection（P5；deterministic）
# ---------------------------------------------------------------------------
def select_candidates(
    gap_signals: dict[str, Any],
    judgments: dict[str, Any],
) -> list[dict[str, Any]]:
    """候选 = important==True 的 judged gap；排序 priority(high>medium>low)+Batch3 输入序。

    引用校验：judgments 必须引用当前 gap_signals（同 run）；缺失/未知/cross-run → PlanValidationError。
    """
    signal_map: dict[str, dict[str, Any]] = {}
    for s in gap_signals.get("signals") or []:
        from app.f9.judge import gap_id_of  # noqa: PLC0415

        gid = gap_id_of(s)
        if gid in signal_map:
            _fail(f"gap signals 含重复 gap_id: {gid}")
        signal_map[gid] = s

    from app.f9.judge import signal_gap_ids  # noqa: PLC0415

    expected = signal_gap_ids(gap_signals)
    judged: list[tuple[dict[str, Any], int]] = []  # (judgment, input_order)
    for j in judgments.get("judgments") or []:
        gid = j["gap_id"]
        if gid not in signal_map:
            _fail(f"judgment 引用不存在的 gap: {gid!r}")
        if j.get("important") is True:
            judged.append((j, expected.index(gid)))
    judged.sort(
        key=lambda pair: (
            PRIORITY_ORDER.get(pair[0].get("priority", "low"), 9),
            pair[1],
        )
    )
    # bounded：候选上限（P5 常量；Batch8 Eval 校准）
    return [j for j, _ in judged[:MAX_PLAN_CANDIDATES]]


# ---------------------------------------------------------------------------
# Plan validation（fail-closed；纯函数）
# ---------------------------------------------------------------------------
def _resolve_target_sq(
    projection: dict[str, Any], gap: dict[str, Any]
) -> Optional[str]:
    """target_sub_question_id：claim subject → 其 sq；sq subject → 自身；否则 root required。"""
    subj_type = gap.get("subject_type")
    subj_id = gap.get("subject_id")
    sq_by_id = {s["sub_question_id"]: s for s in projection.get("sub_questions") or []}
    claim_by_id = {c["claim_id"]: c for c in projection.get("claims") or []}
    if subj_type == "claim":
        claim = claim_by_id.get(subj_id)
        if claim is not None:
            return claim["sub_question_id"]
    if subj_type == "sub_question" and subj_id in sq_by_id:
        return subj_id
    # fallback：root required
    roots = [s["sub_question_id"] for s in sq_by_id.values() if s.get("required")]
    roots.sort()
    return roots[0] if roots else None


def _target_claims(
    projection: dict[str, Any], gap: dict[str, Any], target_sq: Optional[str]
) -> list[str]:
    """verification_target_claim_ids：claim subject → 自身；否则该 sq 的 claims（sorted，cap）。"""
    claims = projection.get("claims") or []
    if gap.get("subject_type") == "claim":
        cid = gap.get("subject_id")
        if any(c["claim_id"] == cid for c in claims):
            return [cid]
    if target_sq is None:
        return []
    owned = sorted((c["claim_id"] for c in claims if c["sub_question_id"] == target_sq))
    return owned[:MAX_TARGET_CLAIMS]


def _validate_proposal_shape(p: dict[str, Any]) -> None:
    if not isinstance(p, dict):
        _fail("proposal 必须为 object")
    unknown = set(p) - _PROPOSAL_KEYS
    if unknown:
        _fail(f"proposal 含未知字段（禁扩展协议）: {sorted(unknown)}")
    objective = p.get("objective")
    if not isinstance(objective, str) or not objective.strip():
        _fail("proposal 缺少非空 objective")
    if len(objective) > MAX_OBJECTIVE:
        _fail(f"objective 超长 >{MAX_OBJECTIVE}")
    queries = p.get("queries")
    if not isinstance(queries, list) or not queries:
        _fail("proposal 缺少 queries（非空 list）")
    if len(queries) > MAX_QUERIES_PER_GAP:
        _fail(f"queries 超限 >{MAX_QUERIES_PER_GAP}/gap")
    seen_q: set[str] = set()
    for q in queries:
        if not isinstance(q, str):
            _fail("query 必须为 str")
        qn = q.strip()
        if not qn:
            _fail("query 不能为空串")
        if len(qn) > MAX_QUERY:
            _fail(f"query 超长 >{MAX_QUERY}")
        if qn in seen_q:
            _fail(f"proposal 内重复 query: {qn[:60]}")
        seen_q.add(qn)
    for key, limit in (("why", MAX_WHY), ("stop_condition", MAX_STOP_CONDITION)):
        value = p.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > limit):
            _fail(f"{key} 非法或超长 >{limit}")


def build_plan(
    projection: dict[str, Any],
    gap_signals: dict[str, Any],
    judgments: dict[str, Any],
    proposal: dict[str, Any],
    *,
    tool: str = "internet_search",
    agent: str = "network_search",
    time_window: str = "NONE",
    kind: str = _KIND_RESEARCH_QUERY,
) -> Optional[dict[str, Any]]:
    """把单个（已校验的）proposal 组装为 validated Follow-up Plan（纯函数、fail-closed）。

    返回 None 表示该 gap 无 researchable target（budget/无 root 等 → 不产 plan，确定性跳过）。
    """
    if kind not in ALLOWED_KINDS:
        _fail(f"kind 非法: {kind!r}")
    _validate_proposal_shape(proposal)
    gid = proposal["gap_id"]
    if not isinstance(gid, str):
        _fail("proposal gap_id 必须为 str")
    from app.f9.judge import gap_id_of  # noqa: PLC0415

    signal_map = {gap_id_of(s): s for s in (gap_signals.get("signals") or [])}
    gap = signal_map.get(gid)
    if gap is None:
        _fail(f"proposal 引用不存在的 gap: {gid!r}")
    j_map = {j["gap_id"]: j for j in (judgments.get("judgments") or [])}
    judgment = j_map.get(gid)
    if judgment is None or judgment.get("important") is not True:
        _fail(f"proposal gap 缺失 important judgment: {gid!r}")
    if judgment.get("priority") not in ("high", "medium", "low"):
        _fail(f"judgment priority 非法: {judgment.get('priority')!r}")

    target_sq = _resolve_target_sq(projection, gap)
    if target_sq is None:
        return None  # budget_kind 且无 root required → 无可研究目标（确定性跳过）

    objective = proposal["objective"].strip()
    queries_out = []
    for q in proposal["queries"]:
        qn = q.strip()
        queries_out.append(
            {
                "query": qn,
                "kind": kind,
                "query_identity": query_identity_of(qn),
                "dedup_identity": dedup_identity_of(
                    query=qn,
                    target_sub_question_id=target_sq,
                    objective=objective,
                    tool=tool,
                    agent=agent,
                    time_window=time_window,
                ),
                "status": "accepted",
                "attempt": 1,
            }
        )
    why = proposal.get("why")
    if why is None or not str(why).strip():
        why = str(judgment.get("reason") or "")
    stop_condition = proposal.get("stop_condition")
    if stop_condition is None or not str(stop_condition).strip():
        stop_condition = f"gap:{gid}:resolved"
    plan: dict[str, Any] = {
        "target_sub_question_id": target_sq,
        "objective": objective,
        "queries": queries_out,
        "priority": judgment["priority"],
        "verification_target_claim_ids": _target_claims(projection, gap, target_sq),
        "stop_condition": str(stop_condition)[:MAX_STOP_CONDITION],
        "why": str(why)[:MAX_WHY],
        "gap_refs": [gid],
    }
    plan["plan_id"] = plan_id_of(plan)
    return plan


# ---------------------------------------------------------------------------
# Dedup / Retry / Re-verify 分类（P1/P2 语义；ledger 由 Batch6 在 execution 内持有）
# ---------------------------------------------------------------------------
def classify_query(
    query_item: dict[str, Any],
    history: list[dict[str, Any]],
    *,
    kind: str,
) -> str:
    """按 dedup_identity(+kind namespace) 对历史判定：accepted | retry | duplicate。

    history 条目（executor 回填）：{dedup_identity, kind, outcome: succeeded|failed,
    sources_produced: bool, attempt: int}；同 identity 取最近（attempt 最大/列表序靠后）。
    """
    if kind not in ALLOWED_KINDS:
        _fail(f"kind 非法: {kind!r}")
    ident = query_item["dedup_identity"]
    matches = [
        e for e in history if e.get("dedup_identity") == ident and e.get("kind") == kind
    ]
    if not matches:
        return "accepted"
    latest = max(matches, key=lambda e: (int(e.get("attempt", 0)),))
    if latest.get("outcome") == "failed" and not latest.get("sources_produced"):
        return "retry"
    return "duplicate"  # 成功产出 source / outcome unknown 保守视为已有结果 → duplicate


def apply_dedup(
    plans: list[dict[str, Any]],
    history: Optional[list[dict[str, Any]]] = None,
    *,
    kind: str = _KIND_RESEARCH_QUERY,
) -> dict[str, Any]:
    """对组装后的 plans 逐 query 做 dedup 分类（duplicate 移出 plan、retry 保留 attempt+1）。

    返回 {plans, rejected_duplicates}；确定性顺序（plans 输入序保持）。
    """
    history = list(history or [])
    kept_plans: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for plan in plans:
        accepted_queries = []
        for q in plan["queries"]:
            status = classify_query(q, history, kind=q["kind"])
            if status == "duplicate":
                rejected.append(
                    {
                        "query": q["query"],
                        "kind": q["kind"],
                        "dedup_identity": q["dedup_identity"],
                        "reason": "duplicate（相同 dedup_identity 且已有成功结果）",
                    }
                )
                continue
            if status == "retry":
                prev = [
                    e
                    for e in history
                    if e.get("dedup_identity") == q["dedup_identity"]
                    and e.get("kind") == q["kind"]
                ]
                attempt = max((int(e.get("attempt", 0)) for e in prev), default=0) + 1
                q = dict(q)
                q["status"] = "retry"
                q["attempt"] = attempt
            accepted_queries.append(q)
        plan = dict(plan)
        plan["queries"] = accepted_queries
        if accepted_queries:
            kept_plans.append(plan)
    return {"plans": kept_plans, "rejected_duplicates": rejected}


# ---------------------------------------------------------------------------
# Plan-proposal LLM（P2；显式 callback 计费；失败 → PlanProposalFailure）
# ---------------------------------------------------------------------------
def _build_proposal_messages(
    projection: dict[str, Any],
    gap_signals: dict[str, Any],
    judgments: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> list[Any]:
    """bounded prompt：仅 candidates（important gaps）+ 各自 semantic_need/reason + 主体上下文。"""
    from app.f9.judge import gap_id_of  # noqa: PLC0415

    claim_by_id = {c["claim_id"]: c for c in (projection.get("claims") or [])}
    sq_by_id = {
        s["sub_question_id"]: s for s in (projection.get("sub_questions") or [])
    }
    signal_map = {gap_id_of(s): s for s in (gap_signals.get("signals") or [])}
    lines: list[str] = []
    for idx, j in enumerate(candidates):
        gid = j["gap_id"]
        gap = signal_map.get(gid, {})
        extra = ""
        if gap.get("subject_type") == "claim":
            c = claim_by_id.get(gap.get("subject_id"))
            if c is not None:
                extra = f" claim_statement={str(c.get('statement'))[:_SNIPPET_MAX]}"
        elif gap.get("subject_type") == "sub_question":
            s = sq_by_id.get(gap.get("subject_id"))
            if s is not None:
                extra = f" question={str(s.get('question'))[:_SNIPPET_MAX]}"
        lines.append(
            f"[{idx}] gap_id={gid} type={gap.get('type')} subject_type={gap.get('subject_type')} "
            f"subject_id={gap.get('subject_id')}{extra} "
            f"reason={str(j.get('reason'))[:200]} "
            f"semantic_need={str(j.get('semantic_need'))[:300]}"
        )
    context = "\n".join(lines)
    if len(context) > CONTEXT_CHAR_CAP:
        context = context[:CONTEXT_CHAR_CAP] + "\n…(truncated)"
    system = (
        "You are a research follow-up planner. For EVERY provided important gap_id, propose a "
        "follow-up research intent: an objective (what the follow-up must establish), "
        "queries (1-3 concrete search queries, plain text — do NOT include URLs unless the "
        "query itself is a URL fetch intent), optional why (rationale) and optional "
        "stop_condition. Semantic judgment only: do NOT emit tools, dedup ids, targeted "
        "research tasks, or verification execution. Reply with exactly one JSON object "
        '{"plans":[{"gap_id","objective","queries":[...],"why"?,"stop_condition"?}]} covering '
        "EVERY provided gap_id exactly once and nothing else. objective <= 600 chars, "
        "each query <= 800 chars, why <= 600, stop_condition <= 400."
    )
    user = (
        "Projection round: {round}\n"
        "required_uncovered ids: {ru}\n\n"
        "IMPORTANT GAPS (semantic judgments):\n{context}".format(
            round=projection.get("round"),
            ru=",".join(projection.get("required_uncovered") or []),
            context=context,
        )
    )
    return [SystemMessage(content=system), HumanMessage(content=user)]


def parse_proposals(
    text: str,
    expected_gap_ids: list[str],
) -> list[dict[str, Any]]:
    """LLM proposal JSON → deterministic validation（顺序 = candidates 输入序）。

    失败（任一）→ PlanProposalFailure：非 JSON / 无 {"plans":[]} wrapper / 未知字段 /
    缺 objective 或 queries / bounds / 引用未知 gap / 漏判或重复。
    """
    try:
        payload = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise PlanProposalFailure(f"plan proposal 非合法 JSON: {exc}") from exc
    if not isinstance(payload, dict) or "plans" not in payload:
        raise PlanProposalFailure('plan proposal 缺少 {"plans": [...]} wrapper')
    rows = payload["plans"]
    if not isinstance(rows, list):
        raise PlanProposalFailure("plans 必须为 list")
    expected_set = set(expected_gap_ids)
    if len(expected_set) != len(expected_gap_ids):
        raise PlanProposalFailure("expected gap ids 重复（内部错误）")
    try:
        seen: dict[str, dict[str, Any]] = {}
        for raw in rows:
            _validate_proposal_shape(raw)
            gid = raw["gap_id"]
            if not isinstance(gid, str):
                raise PlanValidationError("proposal gap_id 必须为 str")
            if gid not in expected_set:
                raise PlanValidationError(f"proposal 引用不存在的 gap: {gid!r}")
            if gid in seen:
                raise PlanValidationError(f"gap 被重复规划: {gid}")
            seen[gid] = raw
        missing = sorted(expected_set - set(seen))
        if missing:
            raise PlanValidationError(f"存在未规划的 gap: {missing}")
    except PlanValidationError as exc:
        raise PlanProposalFailure(str(exc)) from exc
    return [seen[gid] for gid in expected_gap_ids]


async def _call_plan_model(
    model: Any,
    messages: list[Any],
    handler: GovernanceCallbackHandler,
) -> str:
    response = await model.ainvoke(messages, config={"callbacks": [handler]})
    if not isinstance(response, AIMessage):
        raise PlanProposalFailure("plan model 未返回 AIMessage")
    content = getattr(response, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise PlanProposalFailure("plan model 返回空 content")
    return content.strip()


async def propose_followup_plans(
    projection: dict[str, Any],
    gap_signals: dict[str, Any],
    judgments: dict[str, Any],
    plan_model: Any,
    *,
    tool: str = "internet_search",
    agent: str = "network_search",
    time_window: str = "NONE",
    kind: str = _KIND_RESEARCH_QUERY,
    history: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Batch 4 主入口（须在 F8 governed execution 内）：候选→LLM 提议→validation→build→dedup。

    无 important candidate → 不调 LLM（与 A2 同纪律）。返回
    {"round", "plans": [ValidatedPlan...], "rejected_duplicates": [...]}。
    失败语义：proposal/validation 失败 → PlanProposalFailure；F8 control 原样传播。
    """
    execution: Optional[GovernanceExecution] = get_governance_execution()
    if execution is None:
        raise PlanProposalFailure(
            "propose_followup_plans 必须在 F8 governed execution（GovernanceContext）内调用"
        )
    canonicalize_time_window(time_window)  # 早期校验输入（含 NONE / 显式窗口）
    if kind not in ALLOWED_KINDS:
        raise PlanProposalFailure(f"kind 非法: {kind!r}")
    candidates = select_candidates(gap_signals, judgments)
    if not candidates:
        return {
            "round": int(projection.get("round", 0)),
            "plans": [],
            "rejected_duplicates": [],
        }
    messages = _build_proposal_messages(projection, gap_signals, judgments, candidates)
    handler = execution.make_handler()
    try:
        content = await _call_plan_model(plan_model, messages, handler)
    except GovernanceLimitExceeded:
        raise  # F8 control：budget_exceeded
    except asyncio.CancelledError:
        raise  # F8 control：cancel/timeout
    except Exception as exc:  # noqa: BLE001 — provider/框架异常 → PlanProposalFailure
        raise PlanProposalFailure(
            f"plan proposal provider failure: {type(exc).__name__}: {exc}"
        ) from exc

    proposals = parse_proposals(content, [c["gap_id"] for c in candidates])
    plans: list[dict[str, Any]] = []
    for proposal in proposals:
        plan = build_plan(
            projection,
            gap_signals,
            judgments,
            proposal,
            tool=tool,
            agent=agent,
            time_window=time_window,
            kind=kind,
        )
        if plan is not None:
            plans.append(plan)
    deduped = apply_dedup(plans, history, kind=kind)
    return {
        "round": int(projection.get("round", 0)),
        "plans": deduped["plans"],
        "rejected_duplicates": deduped["rejected_duplicates"],
    }


__all__ = [
    "PlanError",
    "PlanValidationError",
    "PlanProposalFailure",
    "CONFIG_VERSION",
    "PRIORITY_ORDER",
    "MAX_OBJECTIVE",
    "MAX_QUERY",
    "MAX_QUERIES_PER_GAP",
    "MAX_PLAN_CANDIDATES",
    "ALLOWED_KINDS",
    "canonicalize_query",
    "canonicalize_objective",
    "canonicalize_time_window",
    "canonical_source_universe",
    "query_identity_of",
    "objective_identity_of",
    "source_universe_identity_of",
    "time_window_identity_of",
    "dedup_identity_of",
    "plan_id_of",
    "select_candidates",
    "build_plan",
    "classify_query",
    "apply_dedup",
    "parse_proposals",
    "propose_followup_plans",
]
