"""F9-P0 Batch 1 — Research State Projection（只读 · deterministic · bounded）。

Spec：docs/spec/2026-09-19-f9-p0-evidence-driven-research-loop.md Rev2 §3.1
Plan：docs/plan/2026-09-20-f9-p0-implementation-plan.md Rev2 §E

管道（Batch 1 范围）：

    Research Plane facts
      + F9 current_round（输入参数，不推断）
      + F8 BudgetCounter 只读 snapshot（输入参数，不维护 counter）
            ↓
        project(run_id, round=current_round, budget=snapshot)
            ↓
    deterministic bounded JSON（decision-oriented state，非 DB dump）

职责边界（本模块不做）：Gap Detection / Judge / Follow-up Plan / Query Dedup /
Targeted Research / Verification / Stopping / F7 / Agent orchestration（后续 Batch）。

不变量：
- 只读：只经 store.execute（SELECT），不进入 transaction，无任何写；
- deterministic：所有集合输出在 Python 内显式排序（时间戳先跨后端归一），不依赖
  PostgreSQL/SQLite 默认返回顺序；SQL 层不加 ORDER BY（Python 排序是唯一权威）；
- bounded：claims/evidence refs/conflicts/sub_questions 受 size_limits 上限截断，
  截断计数写入 size_limits["omitted"]（诚实标记，不把截断当全量）；
- no LLM / no side effects / 不修改 F1–F8 frozen schema / 不修改 F8 Runtime control
  semantics；
- budget 只消费调用方传入的 F8 read-only snapshot dict（形如 BudgetCounter.to_dict()：
  {"limits": {...}, "counts": {...}}），本模块不创建/递增/重置任何 counter。

实现层裁决（Spec §3.1 "实现层定"；本文件为唯一权威 schema）：
- claims[] 条目内嵌 claim-scoped 派生态：best_verdict / independent_flag / fresh /
  conflicts（未缓解冲突）/ evidence_refs（bounded 引用，无正文）；因为 F4/F6 冲突天然
  claim-scoped（conflicts.claim_id），逐 claim 自包含且不重复顶层列表；
- "required" 子问题 = root（parent_id IS NULL）——F1–F8 schema 无必答标记列，只读判定；
- best_verdict 直读 F3 succeeded verification 的 verdict 原值（SUPPORTS/CONTRADICTS/
  INSUFFICIENT/UNVERIFIABLE…），不翻译、不重映射 F3 语义；failed 不参与；无 succeeded
  → None；
- independent_flag 只读 F5 corroboration complete 行（逐 claim 取 computed_at 最新），
  映射 support.independent_count >= 2 → True；无 complete 行 → None；
- conflicts（未缓解）= F4 confirmed 冲突且不存在 F6 complete outcome ∈
  {SAME_ORIGIN_CONTRADICTION, DETAIL_INCONSISTENCY} 的缓解行（无/失败/GENUINE_CONTESTED
  reconciliation 均视为未缓解——不把"未计算"当已缓解，对齐 F7 "disabled ≠ reconciled"）；
- domain = source canonical_url host 的 registrable domain（eTLD+1 近似，配置表驱动，
  deterministic）；归一失败/无 URL → evidence_summary.by_domain["unknown_domain"] 桶。
"""

from __future__ import annotations

import datetime
import json
from typing import Any, Optional
from urllib.parse import urlsplit

from app.research import store as _store

#: FRESHNESS_WINDOW：Spec §3.1 默认 1 天（常量；测试注入 now 保证确定性）
FRESHNESS_WINDOW: datetime.timedelta = datetime.timedelta(days=1)

#: F8 BudgetCounter 的四种 kind（M-Spec frozen DEFAULT_LIMITS keys，只读镜像，用于校验）
BUDGET_KINDS: tuple[str, ...] = (
    "llm_calls",
    "tool_calls",
    "search_calls",
    "agent_steps",
)

#: budget=None 时的 fallback limits（镜像 F8 冻结默认值；真实执行由调用方传 snapshot）
_FALLBACK_LIMITS: dict[str, int] = {
    "llm_calls": 120,
    "tool_calls": 300,
    "search_calls": 40,
    "agent_steps": 200,
}

#: size caps（decision-oriented 输出上限；超限确定性截断并计入 omitted）
DEFAULT_SIZE_LIMITS: dict[str, int] = {
    "max_sub_questions": 64,
    "max_claims": 64,
    "max_evidence_refs_per_claim": 8,
    "max_conflicts_per_claim": 8,
}

#: 已知二级公共后缀（registrable domain 判定配置表；有限、deterministic，非完整 PSL）
_SECOND_LEVEL_PUBLIC_SUFFIXES: frozenset[str] = frozenset(
    {
        "co.uk",
        "org.uk",
        "ac.uk",
        "gov.uk",
        "me.uk",
        "net.uk",
        "com.au",
        "net.au",
        "org.au",
        "co.nz",
        "com.br",
        "com.cn",
        "net.cn",
        "org.cn",
        "gov.cn",
        "edu.cn",
        "co.jp",
        "or.jp",
        "ne.jp",
        "ac.jp",
        "co.kr",
        "or.kr",
        "com.hk",
        "com.tw",
        "com.sg",
        "com.mx",
        "co.in",
        "com.tr",
    }
)


class ProjectionError(Exception):
    """Projection 输入/读取错误（run 不存在 / store 不可用 / 参数非法 / snapshot 不一致）。

    F9 failure semantics（Spec §12）由调用方（后续 orchestrator）按 Projection failure
    处理：adaptive 不可用 → 同一 F8 execution 内 fallback baseline。
    """


def _ts(value: Any) -> Optional[datetime.datetime]:
    """把 TEXT / datetime 统一解析为 **UTC aware** datetime（表示无关时区）。

    - PG timestamptz 受 session timezone 影响返回 +08:00 等偏移（真实观测）；
      sqlite 存 TEXT 原样。两者表示同一时刻 → 统一 astimezone(UTC)，保证跨后端
      排序键与输出表示一致（deterministic，不依赖 DB session timezone）。
    - naive 文本按 UTC 处理；解析失败 → None。
    """
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        dt = value
    else:
        try:
            dt = datetime.datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    else:
        dt = dt.astimezone(datetime.timezone.utc)
    return dt


def _iso(value: Any) -> Optional[str]:
    """时间戳列双后端归一输出：一律 UTC ISO 字符串（PG datetime / sqlite TEXT 统一）。"""
    if value is None:
        return None
    dt = _ts(value)
    return dt.isoformat() if dt is not None else str(value)


def _maybe_json(value: Any) -> Any:
    """sqlite JSON 列存 TEXT、PG 返回 dict/list；统一为可序列化对象。"""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return value


def registrable_domain(url: Optional[str]) -> Optional[str]:
    """URL host → registrable domain（deterministic；Spec §3.1 "eTLD+1 或配置表"）。

    - scheme 缺失自动补 https；host 小写；
    - IPv4/IPv6 字面量 / 单标签 host → 原样返回（无 public suffix 语义）；
    - 末两标签命中配置表（co.uk/com.cn/…）→ 取末三标签；否则取末两标签。
    """
    if not url or not isinstance(url, str):
        return None
    raw = url.strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "https://" + raw
    try:
        host = urlsplit(raw).hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.lower()
    if ":" in host:  # IPv6 literal（hostname 已去 brackets）
        return host
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    if all(part.isdigit() for part in labels):  # IPv4 literal
        return host
    if ".".join(labels[-2:]) in _SECOND_LEVEL_PUBLIC_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _normalize_budget(budget: Optional[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """校验并把调用方传入的 F8 snapshot dict 归一为 {limits, counts}（只读，不写 counter）。

    接受形如 BudgetCounter.to_dict() 的 dict：{"limits": {...}, "counts": {...}}；
    budget=None → counts 全 0 + fallback limits（"数据不足时按上限值"，Spec §3.1）。
    """
    limits: dict[str, int] = {}
    counts: dict[str, int] = {}
    if budget is None:
        for kind in BUDGET_KINDS:
            limits[kind] = _FALLBACK_LIMITS[kind]
            counts[kind] = 0
        return {"limits": limits, "counts": counts}
    if not isinstance(budget, dict):
        raise ProjectionError(
            "budget 必须是 dict（形如 BudgetCounter.to_dict()）或 None"
        )
    raw_limits = budget.get("limits")
    raw_counts = budget.get("counts")
    if not isinstance(raw_limits, dict) or not isinstance(raw_counts, dict):
        raise ProjectionError(
            "budget 必须含 limits 与 counts 两个 dict（BudgetCounter.to_dict() 形）"
        )
    missing = [
        kind
        for kind in BUDGET_KINDS
        if kind not in raw_limits or kind not in raw_counts
    ]
    if missing:
        raise ProjectionError(f"budget snapshot 缺少 kind：{sorted(missing)}")

    def _int_field(value: Any, kind: str, field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ProjectionError(f"budget.{field}[{kind}] 必须为 int：{value!r}")
        return value

    for kind in BUDGET_KINDS:
        limit = _int_field(raw_limits[kind], kind, "limits")
        count = _int_field(raw_counts[kind], kind, "counts")
        if limit < 0 or count < 0:
            raise ProjectionError(
                f"budget 不允许负值：{kind} limit={limit} count={count}"
            )
        if count > limit:
            raise ProjectionError(
                f"budget snapshot 不一致：{kind} count={count} > limit={limit}"
            )
        limits[kind] = limit
        counts[kind] = count
    return {"limits": limits, "counts": counts}


# ---------------------------------------------------------------------------
# 只读事实读取（全部 SELECT，无写）
# ---------------------------------------------------------------------------
def _row_maps(store: Any, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in store.execute(sql, params)]


def _select_sub_questions(store: Any, run_id: str) -> list[dict[str, Any]]:
    return _row_maps(
        store,
        "SELECT sub_question_id, parent_id, position, question, status "
        "FROM sub_questions WHERE run_id = %s",
        (run_id,),
    )


def _evidence_counts_by_subq(store: Any, run_id: str) -> dict[str, int]:
    rows = _row_maps(
        store,
        "SELECT sub_question_id, COUNT(*) AS c FROM evidences "
        "WHERE run_id = %s GROUP BY sub_question_id",
        (run_id,),
    )
    return {r["sub_question_id"]: int(r["c"]) for r in rows}


def _source_counts_by_subq(store: Any, run_id: str) -> dict[str, int]:
    """每个 sub_question 的 source 数：source 经 search_queries 归属子问题（distinct）。"""
    rows = _row_maps(
        store,
        "SELECT q.sub_question_id, COUNT(DISTINCT s.source_id) AS c "
        "FROM sources s JOIN search_queries q "
        "ON q.query_id = s.query_id AND q.run_id = s.run_id "
        "WHERE s.run_id = %s GROUP BY q.sub_question_id",
        (run_id,),
    )
    return {r["sub_question_id"]: int(r["c"]) for r in rows}


def _select_claims(store: Any, run_id: str) -> list[dict[str, Any]]:
    return _row_maps(
        store,
        "SELECT claim_id, sub_question_id, statement, claim_type, status, created_at "
        "FROM claims WHERE run_id = %s",
        (run_id,),
    )


def _select_bindings_with_evidence(store: Any, run_id: str) -> list[dict[str, Any]]:
    """claim→bound evidence（含 source canonical_url 供 domain 判定）；只读。"""
    return _row_maps(
        store,
        "SELECT ce.claim_id, e.evidence_id, e.created_at, s.canonical_url "
        "FROM claim_evidences ce "
        "JOIN evidences e ON e.evidence_id = ce.evidence_id "
        "LEFT JOIN sources s ON s.source_id = e.source_id "
        "WHERE ce.run_id = %s",
        (run_id,),
    )


def _select_verifications(store: Any, run_id: str) -> list[dict[str, Any]]:
    return _row_maps(
        store,
        "SELECT claim_id, verification_id, verdict, status, created_at "
        "FROM verifications WHERE run_id = %s",
        (run_id,),
    )


def _select_corroborations(store: Any, run_id: str) -> list[dict[str, Any]]:
    return _row_maps(
        store,
        "SELECT claim_id, corroboration_id, status, computed_at, support "
        "FROM corroborations WHERE run_id = %s AND status = 'complete'",
        (run_id,),
    )


def _select_conflicts(store: Any, run_id: str) -> list[dict[str, Any]]:
    return _row_maps(
        store,
        "SELECT conflict_id, claim_id, conflict_type, genuine, status, created_at "
        "FROM conflicts WHERE run_id = %s AND status = 'confirmed'",
        (run_id,),
    )


def _select_reconciliations(store: Any, run_id: str) -> list[dict[str, Any]]:
    return _row_maps(
        store,
        "SELECT conflict_id, reconciliation_id, status, outcome, computed_at "
        "FROM reconciliations WHERE run_id = %s",
        (run_id,),
    )


def _select_evidence_urls(store: Any, run_id: str) -> list[dict[str, Any]]:
    """run 全部 evidence 的 source URL（evidence_summary domain 计数用）。"""
    return _row_maps(
        store,
        "SELECT e.evidence_id, s.canonical_url "
        "FROM evidences e LEFT JOIN sources s ON s.source_id = e.source_id "
        "WHERE e.run_id = %s",
        (run_id,),
    )


# ---------------------------------------------------------------------------
# 派生计算（deterministic）
# ---------------------------------------------------------------------------
_MITIGATING_OUTCOMES = frozenset({"SAME_ORIGIN_CONTRADICTION", "DETAIL_INCONSISTENCY"})


def _latest_succeeded_verdict(
    rows: list[dict[str, Any]], claim_id: str
) -> Optional[str]:
    """best_verdict：该 claim succeeded verification（verdict 非空）按
    (created_at ASC, verification_id ASC) 取末；failed 不参与；无 → None。"""
    candidates = [
        r
        for r in rows
        if r["claim_id"] == claim_id
        and r["status"] == "succeeded"
        and r.get("verdict") is not None
    ]
    if not candidates:
        return None
    ordered = sorted(
        candidates,
        key=lambda r: (
            _ts(r["created_at"])
            or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc),
            r["verification_id"],
        ),
    )
    return ordered[-1]["verdict"]


def _latest_complete_corroboration(
    rows: list[dict[str, Any]], claim_id: str
) -> Optional[dict[str, Any]]:
    """逐 claim 取 computed_at 最新的 complete corroboration 行（同刻按 corroboration_id）。"""
    candidates = [r for r in rows if r["claim_id"] == claim_id]
    if not candidates:
        return None
    ordered = sorted(
        candidates,
        key=lambda r: (
            _ts(r["computed_at"])
            or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc),
            r["corroboration_id"],
        ),
    )
    return ordered[-1]


def _reconciliation_of_conflict(
    rec_rows: list[dict[str, Any]], conflict_id: str
) -> Optional[dict[str, Any]]:
    """某 conflict 的最新 reconciliation（(computed_at, reconciliation_id) 降序取首）。"""
    rows = [r for r in rec_rows if r["conflict_id"] == conflict_id]
    if not rows:
        return None
    ordered = sorted(
        rows,
        key=lambda r: (
            _ts(r["computed_at"])
            or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc),
            r["reconciliation_id"],
        ),
        reverse=True,
    )
    return ordered[0]


def _is_unresolved_conflict(
    conflict: dict[str, Any], rec_rows: list[dict[str, Any]]
) -> bool:
    """未缓解冲突：F4 confirmed 且不存在 F6 complete + 缓解 outcome 的行。

    缓解 outcome ∈ {SAME_ORIGIN_CONTRADICTION, DETAIL_INCONSISTENCY}；无 reconciliation /
    failed / GENUINE_CONTESTED → 未缓解（不把"未计算"当已缓解）。
    """
    latest = _reconciliation_of_conflict(rec_rows, conflict["conflict_id"])
    if latest is None:
        return True
    if latest["status"] != "complete":
        return True
    return (latest.get("outcome") or "") not in _MITIGATING_OUTCOMES


def project(
    run_id: str,
    round: int = 0,
    budget: Optional[dict[str, Any]] = None,
    *,
    store: Any = None,
    now: Optional[datetime.datetime] = None,
) -> dict[str, Any]:
    """Research State Projection 主入口。

    round：F9 Orchestrator current_round（输入；绝不从 created_at/evidence 时间/行数推断）。
    budget：F8 BudgetCounter 只读 snapshot dict（BudgetCounter.to_dict() 形）；None → 上限值。
    store：测试注入用（默认进程级 research store）；只读。
    now：fresh 判定的参考时钟（默认 UTC now；测试注入保证确定性）。
    """
    if not isinstance(run_id, str) or not run_id:
        raise ProjectionError("run_id 必须为非空 str")
    if isinstance(round, bool) or not isinstance(round, int) or round < 0:
        raise ProjectionError(f"round 必须为非负 int：{round!r}")

    effective_store = _store.get_store() if store is None else store
    if effective_store is None:
        raise ProjectionError(
            "research store 不可用（Projection 只读层拒绝伪造空状态）"
        )
    exists = effective_store.execute(
        "SELECT run_id FROM research_runs WHERE run_id = %s", (run_id,)
    )
    if not exists:
        raise ProjectionError(f"research run 不存在: {run_id}")

    norm_budget = _normalize_budget(budget)
    limits = norm_budget["limits"]
    counts = norm_budget["counts"]
    budget_out = {
        "limits": {k: limits[k] for k in BUDGET_KINDS},
        "counts": {k: counts[k] for k in BUDGET_KINDS},
        "remaining": {k: limits[k] - counts[k] for k in BUDGET_KINDS},
    }

    # -- sub_questions + required_uncovered --------------------------------
    sq_rows = _select_sub_questions(effective_store, run_id)
    sq_rows.sort(key=lambda r: (int(r["position"]), str(r["sub_question_id"])))
    ev_counts = _evidence_counts_by_subq(effective_store, run_id)
    src_counts = _source_counts_by_subq(effective_store, run_id)

    size_limits = dict(DEFAULT_SIZE_LIMITS)
    omitted: dict[str, int] = {
        "sub_questions": max(0, len(sq_rows) - size_limits["max_sub_questions"]),
        "claims": 0,
        "evidence_refs": 0,
        "conflicts": 0,
    }
    sq_selected = sq_rows[: size_limits["max_sub_questions"]]

    sub_questions: list[dict[str, Any]] = []
    required_uncovered: list[str] = []
    for r in sq_selected:
        sq_id = r["sub_question_id"]
        required = r.get("parent_id") is None
        ev_n = int(ev_counts.get(sq_id, 0))
        src_n = int(src_counts.get(sq_id, 0))
        sub_questions.append(
            {
                "sub_question_id": sq_id,
                "parent_id": r.get("parent_id"),
                "position": int(r["position"]),
                "question": str(r["question"]),
                "status": str(r["status"]),
                "required": bool(required),
                "evidence_count": ev_n,
                "source_count": src_n,
            }
        )
        if required and ev_n == 0 and src_n == 0:
            required_uncovered.append(sq_id)
    # required_uncovered 排序与 sub_questions 同键（position, sub_question_id）
    required_uncovered.sort(
        key=lambda sid: next(
            (
                (int(s["position"]), str(s["sub_question_id"]))
                for s in sub_questions
                if s["sub_question_id"] == sid
            ),
            (0, str(sid)),
        )
    )

    # -- claims + 逐 claim 派生态 ------------------------------------------
    claim_rows = _select_claims(effective_store, run_id)
    claim_rows.sort(
        key=lambda r: (
            _ts(r["created_at"])
            or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc),
            str(r["claim_id"]),
        )
    )
    omitted["claims"] = max(0, len(claim_rows) - size_limits["max_claims"])
    claim_selected = claim_rows[: size_limits["max_claims"]]
    selected_ids = {r["claim_id"] for r in claim_selected}

    binding_rows = _select_bindings_with_evidence(effective_store, run_id)
    verif_rows = _select_verifications(effective_store, run_id)
    corrob_rows = _select_corroborations(effective_store, run_id)
    conflict_rows = _select_conflicts(effective_store, run_id)
    rec_rows = _select_reconciliations(effective_store, run_id)

    # conflicts 未缓解集合（只关心 selected claims）：
    # 排序键 (created_at ASC, conflict_id ASC)；截断在条目构建前完成（保留最早 N 条）。
    unresolved_by_claim: dict[str, list[dict[str, Any]]] = {
        cid: [] for cid in selected_ids
    }
    for cf in conflict_rows:
        cid = cf["claim_id"]
        if cid in selected_ids and _is_unresolved_conflict(cf, rec_rows):
            unresolved_by_claim[cid].append(cf)
    for cid in selected_ids:
        rows = unresolved_by_claim[cid]
        rows.sort(
            key=lambda r: (
                _ts(r["created_at"])
                or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc),
                str(r["conflict_id"]),
            )
        )
        cap = size_limits["max_conflicts_per_claim"]
        if len(rows) > cap:
            omitted["conflicts"] += len(rows) - cap
            rows = rows[:cap]
        built: list[dict[str, Any]] = []
        for cf in rows:
            latest_rec = _reconciliation_of_conflict(rec_rows, cf["conflict_id"])
            built.append(
                {
                    "conflict_id": cf["conflict_id"],
                    "conflict_type": cf.get("conflict_type"),
                    "genuine": bool(cf.get("genuine")),
                    "status": cf["status"],
                    "reconciliation_status": (
                        latest_rec["status"] if latest_rec is not None else None
                    ),
                    "reconciliation_outcome": (
                        latest_rec.get("outcome") if latest_rec is not None else None
                    ),
                }
            )
        unresolved_by_claim[cid] = built

    ev_by_claim: dict[str, list[dict[str, Any]]] = {cid: [] for cid in selected_ids}
    for b in binding_rows:
        cid = b["claim_id"]
        if cid not in selected_ids:
            continue
        ev_by_claim[cid].append(
            {
                "evidence_id": b["evidence_id"],
                "created_at": _iso(b.get("created_at")),
                "domain": registrable_domain(b.get("canonical_url")),
            }
        )
    for cid in selected_ids:
        ev_by_claim[cid].sort(
            key=lambda e: (
                _ts(e["created_at"])
                or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc),
                e["evidence_id"],
            )
        )
        cap = size_limits["max_evidence_refs_per_claim"]
        if len(ev_by_claim[cid]) > cap:
            omitted["evidence_refs"] += len(ev_by_claim[cid]) - cap
            ev_by_claim[cid] = ev_by_claim[cid][-cap:]  # 保留最新 N 条

    reference_now = (
        now if now is not None else datetime.datetime.now(datetime.timezone.utc)
    )
    if reference_now.tzinfo is None:
        reference_now = reference_now.replace(tzinfo=datetime.timezone.utc)

    claims_out: list[dict[str, Any]] = []
    for c in claim_selected:
        cid = c["claim_id"]
        refs = ev_by_claim[cid]
        newest_ts = (
            max(
                _ts(r["created_at"])
                or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
                for r in refs
            )
            if refs
            else None
        )
        corrob = _latest_complete_corroboration(corrob_rows, cid)
        independent_flag: Optional[bool] = None
        if corrob is not None:
            support = _maybe_json(corrob.get("support")) or {}
            independent_flag = int(support.get("independent_count") or 0) >= 2
        fresh: Optional[bool] = None
        if newest_ts is not None:
            fresh = (reference_now - newest_ts) <= FRESHNESS_WINDOW
        claims_out.append(
            {
                "claim_id": cid,
                "sub_question_id": c["sub_question_id"],
                "statement": c["statement"],
                "claim_type": c["claim_type"],
                "status": c["status"],
                "created_at": _iso(c["created_at"]),
                "best_verdict": _latest_succeeded_verdict(verif_rows, cid),
                "independent_flag": independent_flag,
                "fresh": fresh,
                "conflicts": unresolved_by_claim[cid],
                "evidence_refs": refs,
            }
        )

    # -- evidence_summary（count + domain counts；无 authority/reliability）--
    domain_counts: dict[str, int] = {}
    for row in _select_evidence_urls(effective_store, run_id):
        d = registrable_domain(row.get("canonical_url"))
        bucket = d if d is not None else "unknown_domain"
        domain_counts[bucket] = domain_counts.get(bucket, 0) + 1
    total_evidence = sum(domain_counts.values())
    evidence_summary = {
        "count": total_evidence,
        "by_domain": {
            k: domain_counts[k]
            for k in sorted(domain_counts)  # deterministic 键序
        },
    }

    return {
        "run_id": run_id,
        "round": round,
        "budget": budget_out,
        "sub_questions": sub_questions,
        "required_uncovered": required_uncovered,
        "claims": claims_out,
        "evidence_summary": evidence_summary,
        "gap_signals": {},
        "open_questions": [],
        "size_limits": {**size_limits, "omitted": omitted},
    }
