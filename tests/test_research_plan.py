"""F9-P0 Batch 4 — Follow-up Plan + Validation + Dedup tests（纯函数 + F8 seam）。

覆盖 §15：canonicalization（URL/非 URL）、identity 层级与确定性、time-window NONE/显式、
candidate selection（P5）、plan validation fail-closed（未知/缺失/越界/非法引用/cross-run）、
proposal parsing、plan_id determinism、dedup/retry/re-query/re_verify 分类（P1 ledger 语义）、
plan-proposal LLM（P2：callback accounting、provider/malformed/budget、无裸调）、
F8 seam（同 execute 身份与计费、失败→baseline），以及 Batch1–3 回归另行全绿。

无 DB 集成（P1 in-memory ledger；无 PG 测试文件，理由同 Batch3 §4）。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from pathlib import Path

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.research import gaps as f9g
from app.research import judge as f9j
from app.research import plan as f9p
from app.runtime.governance import migrations as gov_migrations
from app.runtime.governance import store as gov_store
from app.runtime.governance.context import (
    GovernanceExecution,
    enter_governance_execution,
)
from app.runtime.governance.controller import GovernanceController
from app.runtime.governance.counters import BudgetCounter, GovernanceLimitExceeded
from app.runtime.governance.models import TaskStatus

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST_TMP = _REPO_ROOT / "_testtmp"

DEFAULT_LIMITS = {
    "llm_calls": 120,
    "tool_calls": 300,
    "search_calls": 40,
    "agent_steps": 200,
}


def _proj(**over):
    p = {
        "run_id": "run-x",
        "round": 0,
        "budget": {
            "limits": dict(DEFAULT_LIMITS),
            "counts": {k: 0 for k in DEFAULT_LIMITS},
            "remaining": dict(DEFAULT_LIMITS),
        },
        "sub_questions": [
            {
                "sub_question_id": "sq-root",
                "parent_id": None,
                "position": 0,
                "question": "root question?",
                "status": "planned",
                "required": True,
                "evidence_count": 0,
                "source_count": 0,
            }
        ],
        "required_uncovered": ["sq-root"],
        "claims": [
            {
                "claim_id": "c-1",
                "sub_question_id": "sq-root",
                "statement": "claim one statement",
                "claim_type": "FACT",
                "status": "drafted",
                "created_at": "2026-09-02T12:00:00+00:00",
                "best_verdict": None,
                "independent_flag": None,
                "fresh": None,
                "conflicts": [],
                "evidence_refs": [],
            },
            {
                "claim_id": "c-2",
                "sub_question_id": "sq-root",
                "statement": "claim two statement",
                "claim_type": "FACT",
                "status": "drafted",
                "created_at": "2026-09-02T12:00:00+00:00",
                "best_verdict": None,
                "independent_flag": None,
                "fresh": None,
                "conflicts": [],
                "evidence_refs": [],
            },
        ],
        "evidence_summary": {"count": 0, "by_domain": {}},
        "gap_signals": {},
        "open_questions": [],
        "size_limits": {
            "omitted": {
                "sub_questions": 0,
                "claims": 0,
                "evidence_refs": 0,
                "conflicts": 0,
            }
        },
    }
    for k, v in over.items():
        p[k] = v
    return p


def _scenario():
    """real pipe：projection fixture → detect_gaps → 3 signals（RU1 + CE1×2）。"""
    projection = _proj()
    gaps = f9g.detect_gaps(projection)
    assert {s["type"] for s in gaps["signals"]} == {
        "required_uncovered",
        "claim_no_evidence",
    }
    return projection, gaps


def _gap_ids(gaps):
    return f9j.signal_gap_ids(gaps)


def _judgments(gaps, *, importance=None, priorities=None):
    """按 gap 顺序生成完整 judgments（默认全部 important=True priority medium）。"""
    ids = _gap_ids(gaps)
    rows = []
    for i, gid in enumerate(ids):
        rows.append(
            {
                "gap_id": gid,
                "important": bool(importance[i]) if importance is not None else True,
                "reason": f"reason-{i}",
                "priority": (priorities or ["medium"] * len(ids))[i],
                "semantic_need": f"need-{i}",
            }
        )
    return {"round": 0, "judged": True, "judgments": rows}


def _proposal_payload(gap_ids, **over):
    plans = []
    for i, gid in enumerate(gap_ids):
        item = {
            "gap_id": gid,
            "objective": f"establish-{i}",
            "queries": [f"query-{i}-a", f"query-{i}-b"],
        }
        for key, val in over.get(i, {}).items():
            item[key] = val
        plans.append(item)
    return json.dumps({"plans": plans})


def _build_result(proj, gaps, judgments, proposal_text):
    ids = [c["gap_id"] for c in f9p.select_candidates(gaps, judgments)]
    proposals = f9p.parse_proposals(proposal_text, ids)
    plans = [
        p
        for p in (
            f9p.build_plan(proj, gaps, judgments, proposal) for proposal in proposals
        )
        if p is not None
    ]
    return f9p.apply_dedup(plans, kind="research_query")


# ---------------------------------------------------------------------------
# Canonicalization / Identity（P3/P4/§7）
# ---------------------------------------------------------------------------
class TestCanonicalizationAndIdentity:
    def test_non_url_query_canonical(self):
        assert f9p.canonicalize_query("  Hello   World  ") == "hello world"
        assert f9p.canonicalize_query("DeepSearch?  AGENTS ") == "deepsearch? agents"

    def test_url_query_uses_normalize(self):
        # normalize.canonicalize_url：host 小写、去 tracking 参数、query 排序；path 大小写保留
        url = "https://Example.com/A?utm_source=x&b=2&a=1"
        assert f9p.canonicalize_query(url) == "https://example.com/A?a=1&b=2"

    def test_scheme_less_not_treated_as_url(self):
        assert f9p.canonicalize_query("Example.com/Path") == "example.com/path"

    def test_empty_query_rejected(self):
        with pytest.raises(f9p.PlanValidationError):
            f9p.canonicalize_query("   ")

    def test_objective_identity_binding(self):
        a = f9p.objective_identity_of("sq-a", "  Establish X?  ")
        b = f9p.objective_identity_of("sq-a", "establish x?")
        c = f9p.objective_identity_of("sq-b", "establish x?")
        assert a == b  # normalize（case/whitespace 无关）
        assert a != c  # target 绑定

    def test_source_universe_stable_no_secret(self):
        a = f9p.source_universe_identity_of("internet_search", "network_search")
        b = f9p.source_universe_identity_of("internet_search", "network_search")
        c = f9p.source_universe_identity_of("internet_search", "db_agent")
        assert a == b and a != c
        canon = f9p.canonical_source_universe("internet_search", "network_search")
        assert "key" not in canon and "token" not in canon

    def test_time_window_identity(self):
        assert f9p.time_window_identity_of("NONE") == "NONE"
        assert (
            f9p.time_window_identity_of("2026-09-01/2026-09-10")
            == "2026-09-01/2026-09-10"
        )
        for bad in ("2026-09-01", "today/now", "2026/09/01-2026/09/10"):
            with pytest.raises(f9p.PlanValidationError):
                f9p.time_window_identity_of(bad)

    def test_identity_hierarchy_separate(self):
        kw = dict(
            query="what is x?",
            target_sub_question_id="sq-root",
            objective="establish x",
            tool="internet_search",
            agent="network_search",
            time_window="NONE",
        )
        qid = f9p.query_identity_of(kw["query"])
        dedup = f9p.dedup_identity_of(**kw)
        assert dedup != qid  # dedup_identity != query_identity
        assert len(qid) == len(dedup) == 64

    def test_dedup_sensitive_to_every_component(self):
        base = dict(
            query="q",
            target_sub_question_id="sq-root",
            objective="o",
            tool="t",
            agent="a",
            time_window="NONE",
        )
        baseline = f9p.dedup_identity_of(**base)
        for field, value in (
            ("query", "q2"),
            ("objective", "o2"),
            ("tool", "t2"),
            ("agent", "a2"),
            ("time_window", "2026-09-01/2026-09-10"),
        ):
            kw = dict(base)
            kw[field] = value
            assert f9p.dedup_identity_of(**kw) != baseline

    def test_plan_id_deterministic_and_not_dedup(self):
        proj, gaps = _scenario()
        judgments = _judgments(gaps)
        out = _build_result(proj, gaps, judgments, _proposal_payload(_gap_ids(gaps)))
        plan = out["plans"][0]
        assert f9p.plan_id_of(plan) == plan["plan_id"]
        q0 = plan["queries"][0]
        assert plan["plan_id"] != q0["dedup_identity"]
        # canonical 稳定性：重建（相同内容）→ 相同 plan_id
        rebuilt = f9p.build_plan(
            proj,
            gaps,
            judgments,
            {
                "gap_id": plan["gap_refs"][0],
                "objective": plan["objective"],
                "queries": [q["query"] for q in plan["queries"]],
                "why": plan["why"],
                "stop_condition": plan["stop_condition"],
            },
        )
        assert rebuilt["plan_id"] == plan["plan_id"]


# ---------------------------------------------------------------------------
# Candidate selection（P5）
# ---------------------------------------------------------------------------
class TestCandidateSelection:
    def test_only_important_and_priority_order(self):
        _, gaps = _scenario()
        ids = _gap_ids(
            gaps
        )  # [claim_no_evidence|c-1, claim_no_evidence|c-2, required_uncovered|sq-root]? 实际以 detect 排序为准
        assert len(ids) == 3
        # c-1 high、c-2 low、root medium → 候选排序：c-1(high) → root(medium) → c-2(low)
        importance = [True, True, True]
        by_id = {
            next(s_id for s_id in ids if s_id.endswith("|c-1")): "high",
            next(s_id for s_id in ids if s_id.endswith("|c-2")): "low",
            next(s_id for s_id in ids if s_id.endswith("|sq-root")): "medium",
        }
        j = _judgments(
            gaps, importance=importance, priorities=[by_id[gid] for gid in ids]
        )
        cand = f9p.select_candidates(gaps, j)
        got = [f9p.PRIORITY_ORDER[c["priority"]] for c in cand]
        assert got == sorted(got)  # priority 单调（high<medium<low 数值序）
        assert [c["gap_id"] for c in cand] == sorted(
            ids, key=lambda gid: (f9p.PRIORITY_ORDER[by_id[gid]], ids.index(gid))
        )

    def test_unimportant_excluded_and_order_stable(self):
        _, gaps = _scenario()
        ids = _gap_ids(gaps)
        importance = [False, True, True]
        # c-1 unimportant（排除）；c-2 high、root low → 候选序 c-2(high) 先于 root(low)
        c2 = next(i for i, g in enumerate(ids) if g.endswith("|c-2"))
        root = next(i for i, g in enumerate(ids) if g.endswith("|sq-root"))
        pri = [None] * len(ids)
        pri[c2], pri[root] = "high", "low"
        j = _judgments(gaps, importance=importance, priorities=pri)
        cand = f9p.select_candidates(gaps, j)
        assert [c["gap_id"] for c in cand] == [ids[c2], ids[root]]

    def test_cross_run_reference_rejected(self):
        _, gaps = _scenario()
        j = {
            "judgments": [
                {
                    "gap_id": "claim_no_evidence|claim|other-run-id",
                    "important": True,
                    "reason": "x",
                    "priority": "high",
                    "semantic_need": "y",
                }
            ]
        }
        with pytest.raises(f9p.PlanValidationError):
            f9p.select_candidates(gaps, j)

    def test_max_candidates_cap(self):
        proj = _proj()
        claims = []
        for i in range(20):
            claims.append(
                {
                    "claim_id": f"c-{i}",
                    "sub_question_id": "sq-root",
                    "statement": f"s{i}",
                    "claim_type": "FACT",
                    "status": "drafted",
                    "created_at": "2026-09-02T12:00:00+00:00",
                    "best_verdict": None,
                    "independent_flag": None,
                    "fresh": None,
                    "conflicts": [],
                    "evidence_refs": [],
                }
            )
        proj["claims"] = claims
        gaps = f9g.detect_gaps(proj)
        ids = _gap_ids(gaps)
        j = _judgments(
            gaps, importance=[True] * len(ids), priorities=["high"] * len(ids)
        )
        cand = f9p.select_candidates(gaps, j)
        assert len(cand) <= f9p.MAX_PLAN_CANDIDATES


# ---------------------------------------------------------------------------
# build_plan / plan contract
# ---------------------------------------------------------------------------
class TestBuildPlan:
    def test_valid_plan_contract_fields(self):
        proj, gaps = _scenario()
        judgments = _judgments(gaps)
        ids = [c["gap_id"] for c in f9p.select_candidates(gaps, judgments)]
        proposals = f9p.parse_proposals(_proposal_payload(ids), ids)
        plan = f9p.build_plan(proj, gaps, judgments, proposals[0])
        assert set(plan) == {
            "plan_id",
            "target_sub_question_id",
            "objective",
            "queries",
            "priority",
            "verification_target_claim_ids",
            "stop_condition",
            "why",
            "gap_refs",
        }
        assert plan["target_sub_question_id"] == "sq-root"
        q0 = plan["queries"][0]
        assert set(q0) == {
            "query",
            "kind",
            "query_identity",
            "dedup_identity",
            "status",
            "attempt",
        }
        assert q0["status"] == "accepted" and q0["attempt"] == 1
        assert plan["priority"] == "medium"
        assert plan["gap_refs"] == [proposals[0]["gap_id"]]
        # 首个候选 = claim_no_evidence|claim|c-1 → verification target = 该 claim 自身
        assert proposals[0]["gap_id"].endswith("|c-1")
        assert plan["verification_target_claim_ids"] == ["c-1"]

    def test_claim_gap_targets_own_claim(self):
        proj, gaps = _scenario()
        # claim_no_evidence subject c-1 的 gap → verification target = [c-1]
        claim_gap = next(
            s
            for s in gaps["signals"]
            if s["type"] == "claim_no_evidence" and s["subject_id"] == "c-1"
        )
        gid = f9j.gap_id_of(claim_gap)
        judgments = {
            "judgments": [
                {
                    "gap_id": gid,
                    "important": True,
                    "reason": "r",
                    "priority": "high",
                    "semantic_need": "n",
                }
            ]
        }
        plan = f9p.build_plan(
            proj,
            gaps,
            judgments,
            {"gap_id": gid, "objective": "obj", "queries": ["q1"]},
        )
        assert plan["verification_target_claim_ids"] == ["c-1"]
        assert plan["priority"] == "high"

    def test_unimportant_or_missing_judgment_rejected(self):
        proj, gaps = _scenario()
        ids = _gap_ids(gaps)
        j_off = _judgments(gaps, importance=[False, True, True])
        with pytest.raises(f9p.PlanValidationError):
            f9p.build_plan(
                proj,
                gaps,
                j_off,
                {"gap_id": ids[0], "objective": "o", "queries": ["q"]},
            )

    def test_no_researchable_target_returns_none(self):
        proj = _proj(sub_questions=[], required_uncovered=[], claims=[])
        gaps = {
            "signals": [
                {
                    "type": "budget_near",
                    "rule": "BU1",
                    "subject_type": "budget_kind",
                    "subject_id": "llm_calls",
                    "reason": "x",
                }
            ]
        }
        gid = f9j.gap_id_of(gaps["signals"][0])
        judgments = {
            "judgments": [
                {
                    "gap_id": gid,
                    "important": True,
                    "reason": "r",
                    "priority": "low",
                    "semantic_need": "n",
                }
            ]
        }
        assert (
            f9p.build_plan(
                proj,
                gaps,
                judgments,
                {"gap_id": gid, "objective": "o", "queries": ["q"]},
            )
            is None
        )

    def test_unknown_gap_in_proposal_rejected(self):
        proj, gaps = _scenario()
        judgments = _judgments(gaps)
        with pytest.raises(f9p.PlanValidationError):
            f9p.build_plan(
                proj,
                gaps,
                judgments,
                {"gap_id": "other|claim|z", "objective": "o", "queries": ["q"]},
            )

    def test_unknown_fields_bounds_rejected(self):
        proj, gaps = _scenario()
        judgments = _judgments(gaps)
        gid = f9j.gap_id_of(gaps["signals"][0])
        bad = {
            "gap_id": gid,
            "objective": "o" * (f9p.MAX_OBJECTIVE + 1),
            "queries": ["q"],
        }
        with pytest.raises(f9p.PlanValidationError):
            f9p.build_plan(proj, gaps, judgments, bad)
        too_many = {
            "gap_id": gid,
            "objective": "o",
            "queries": [f"q{i}" for i in range(f9p.MAX_QUERIES_PER_GAP + 1)],
        }
        with pytest.raises(f9p.PlanValidationError):
            f9p.build_plan(proj, gaps, judgments, too_many)
        unknown = {
            "gap_id": gid,
            "objective": "o",
            "queries": ["q"],
            "tool_selection": "hack",
        }
        with pytest.raises(f9p.PlanValidationError):
            f9p.build_plan(proj, gaps, judgments, unknown)


# ---------------------------------------------------------------------------
# Dedup / Retry / Re-query / Re-verify（P1 ledger 语义）
# ---------------------------------------------------------------------------
class TestDedupSemantics:
    def _plan_query(self, ident_extra=None, kind="research_query"):
        return {
            "query": "q",
            "kind": kind,
            "query_identity": "qid",
            "dedup_identity": ident_extra or "di-1",
            "status": "accepted",
            "attempt": 1,
        }

    def test_accepted_retry_duplicate(self):
        q = self._plan_query("di-1")
        assert f9p.classify_query(q, [], kind="research_query") == "accepted"
        failed_history = [
            {
                "dedup_identity": "di-1",
                "kind": "research_query",
                "outcome": "failed",
                "sources_produced": False,
                "attempt": 1,
            }
        ]
        assert f9p.classify_query(q, failed_history, kind="research_query") == "retry"
        ok_history = [
            {
                "dedup_identity": "di-1",
                "kind": "research_query",
                "outcome": "succeeded",
                "sources_produced": True,
                "attempt": 1,
            }
        ]
        assert f9p.classify_query(q, ok_history, kind="research_query") == "duplicate"

    def test_re_verify_namespace_independent(self):
        q = self._plan_query("di-1", kind="re_verify")
        history_rq = [
            {
                "dedup_identity": "di-1",
                "kind": "research_query",
                "outcome": "succeeded",
                "sources_produced": True,
                "attempt": 1,
            }
        ]
        assert f9p.classify_query(q, history_rq, kind="re_verify") == "accepted"
        history_rv = [
            {
                "dedup_identity": "di-1",
                "kind": "re_verify",
                "outcome": "succeeded",
                "sources_produced": True,
                "attempt": 1,
            }
        ]
        assert f9p.classify_query(q, history_rv, kind="re_verify") == "duplicate"

    def test_requery_new_identity(self):
        kw = dict(
            query="q",
            target_sub_question_id="sq-root",
            objective="o",
            tool="t",
            agent="a",
            time_window="NONE",
        )
        old = f9p.dedup_identity_of(**kw)
        kw["objective"] = "o2"
        new = f9p.dedup_identity_of(**kw)
        assert new != old
        assert (
            f9p.classify_query(self._plan_query(old), [], kind="research_query")
            == "accepted"
        )
        assert (
            f9p.classify_query(self._plan_query(new), [], kind="research_query")
            == "accepted"
        )

    def test_apply_dedup_keeps_order_and_rejects(self):
        proj, gaps = _scenario()
        judgments = _judgments(gaps)
        ids = [c["gap_id"] for c in f9p.select_candidates(gaps, judgments)]
        proposals = f9p.parse_proposals(_proposal_payload(ids), ids)
        plans = [
            p
            for p in (f9p.build_plan(proj, gaps, judgments, pr) for pr in proposals)
            if p is not None
        ]
        first_q = plans[0]["queries"][0]
        history = [
            {
                "dedup_identity": first_q["dedup_identity"],
                "kind": "research_query",
                "outcome": "failed",
                "sources_produced": False,
                "attempt": 1,
            }
        ]
        out = f9p.apply_dedup(plans, history, kind="research_query")
        assert len(out["plans"]) == len(plans)
        assert out["plans"][0]["queries"][0]["status"] == "retry"
        assert out["plans"][0]["queries"][0]["attempt"] == 2
        ok_history = [
            {
                "dedup_identity": first_q["dedup_identity"],
                "kind": "research_query",
                "outcome": "succeeded",
                "sources_produced": True,
                "attempt": 1,
            }
        ]
        out2 = f9p.apply_dedup(plans, ok_history, kind="research_query")
        assert len(out2["rejected_duplicates"]) == 1
        assert (
            out2["rejected_duplicates"][0]["dedup_identity"]
            == first_q["dedup_identity"]
        )


# ---------------------------------------------------------------------------
# Proposal parsing / LLM（P2）
# ---------------------------------------------------------------------------
def _make_plan_model(payload=None, raise_error=None, hang=False):
    class FakePlanModel(BaseChatModel):
        def __init__(self):
            super().__init__()
            object.__setattr__(self, "_state", {"calls": 0})
            object.__setattr__(self, "_payload", payload)
            object.__setattr__(self, "_raise_error", raise_error)
            object.__setattr__(self, "_hang", hang)

        @property
        def _llm_type(self) -> str:
            return "fake-plan"

        @property
        def calls(self) -> int:
            return self._state["calls"]

        def _bump(self):
            self._state["calls"] += 1

        def _resolve(self):
            if self._raise_error is not None:
                raise self._raise_error
            if self._payload is not None:
                return self._payload
            raise RuntimeError("fake plan model: no payload")

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self._bump()
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content=self._resolve()))]
            )

        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            self._bump()
            if self._hang:
                await asyncio.Event().wait()
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content=self._resolve()))]
            )

    return FakePlanModel()


def _default_ctx(*, limits=None):
    return GovernanceExecution(
        task_id="task-p", counter=BudgetCounter(limits=limits), run_id="run-p"
    )


async def _run_propose(proj, gaps, judgments, model, ctx=None, **kw):
    ctx = ctx or _default_ctx()
    with enter_governance_execution(ctx):
        result = await f9p.propose_followup_plans(proj, gaps, judgments, model, **kw)
    return result, ctx


class TestProposalParsingAndLLM:
    def test_parse_valid_and_order(self):
        ids = ["a|claim|x", "b|claim|y"]
        out = f9p.parse_proposals(_proposal_payload(ids), ids)
        assert [o["gap_id"] for o in out] == ids

    def test_parse_failures(self):
        ids = ["a|claim|x"]
        cases = [
            "not json{{",
            json.dumps({"other": []}),
            _proposal_payload(["b|claim|z"]),  # 未知 gap
            _proposal_payload(
                [],
            ),  # 空 plans（缺覆盖）
            json.dumps(
                {
                    "plans": [
                        {
                            "gap_id": "a|claim|x",
                            "objective": "o",
                            "queries": [],
                        }
                    ]
                }
            ),  # queries 空
            json.dumps(
                {
                    "plans": [
                        {
                            "gap_id": "a|claim|x",
                            "objective": "o",
                            "queries": ["q"],
                            "tool_selection": "x",
                        }
                    ]
                }
            ),
        ]
        for text in cases:
            with pytest.raises(f9p.PlanProposalFailure):
                f9p.parse_proposals(text, ids)

    def test_p2_valid_proposal_accounting(self):
        proj, gaps = _scenario()
        judgments = _judgments(gaps)
        ids = [c["gap_id"] for c in f9p.select_candidates(gaps, judgments)]
        model = _make_plan_model(payload=_proposal_payload(ids))
        result, ctx = _run(_run_propose(proj, gaps, judgments, model))
        assert len(result["plans"]) == len(ids)
        assert result["round"] == 0
        assert ctx.counter.count("llm_calls") == 1
        assert ctx.counter.count("agent_steps") == 0
        assert model.calls == 1
        assert any(
            d.get("event") == "llm_classification_unresolved" for d in ctx.diagnostics
        )

    def test_p2_no_important_candidates_no_llm(self):
        proj, gaps = _scenario()
        judgments = _judgments(gaps, importance=[False, False, False])
        model = _make_plan_model(payload="{}")
        result, _ = _run(_run_propose(proj, gaps, judgments, model))
        assert result["plans"] == [] and model.calls == 0

    def test_p2_malformed_and_provider_failure(self):
        proj, gaps = _scenario()
        judgments = _judgments(gaps)
        bad = _make_plan_model(payload="not json{{")
        with pytest.raises(f9p.PlanProposalFailure):
            _run(_run_propose(proj, gaps, judgments, bad))
        boom = _make_plan_model(raise_error=RuntimeError("provider down"))
        with pytest.raises(f9p.PlanProposalFailure):
            _run(_run_propose(proj, gaps, judgments, boom))

    def test_p2_budget_not_swallowed(self):
        proj, gaps = _scenario()
        judgments = _judgments(gaps)
        ctx = _default_ctx(
            limits={
                "llm_calls": 0,
                "tool_calls": 10,
                "search_calls": 5,
                "agent_steps": 10,
            }
        )
        model = _make_plan_model(payload="{}")
        with pytest.raises(GovernanceLimitExceeded):
            _run(_run_propose(proj, gaps, judgments, model, ctx=ctx))
        assert model.calls == 0

    def test_requires_governance_context(self):
        proj, gaps = _scenario()
        judgments = _judgments(gaps)
        model = _make_plan_model(payload="{}")
        with pytest.raises(f9p.PlanProposalFailure):
            asyncio.run(f9p.propose_followup_plans(proj, gaps, judgments, model))


@pytest.fixture
def gov_tmp():
    d = _TEST_TMP / f"plan-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    try:
        shutil.rmtree(d)
    except OSError:
        pass


def _make_controller(gov_tmp):
    db = str(gov_tmp / f"gov-{uuid.uuid4().hex}.sqlite")
    store = gov_store._GovernanceSqliteStore(db)
    gov_migrations.ensure_schema(store)
    ctl = GovernanceController(
        store, owner_instance="inst-plan", retry_delays=(0.01, 0.02)
    )
    ctl.watchdog_tick = 0.02
    return store, ctl


class TestGovernanceSeam:
    def test_same_execute_accounting_and_completed(self, gov_tmp):
        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-p1", run_id="run-p1")
            proj, gaps = _scenario()
            judgments = _judgments(gaps)
            ids = [c["gap_id"] for c in f9p.select_candidates(gaps, judgments)]
            model = _make_plan_model(payload=_proposal_payload(ids))

            async def shim():
                return await f9p.propose_followup_plans(proj, gaps, judgments, model)

            result = await ctl.execute(rec.task_id, shim(), policy={})
            row = gov_store.get_task(store, rec.task_id)
            assert row.status == TaskStatus.COMPLETED.value
            assert row.version == 1
            assert len(result["plans"]) == len(ids)
            assert row.counters_snapshot["llm_calls"] == 1
            assert row.counters_snapshot["agent_steps"] == 0
            assert model.calls == 1
            store.close()

        asyncio.run(scenario())

    def test_proposal_failure_fallback_same_execute_completed(self, gov_tmp):
        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-p2")
            proj, gaps = _scenario()
            judgments = _judgments(gaps)
            model = _make_plan_model(raise_error=RuntimeError("proposal down"))
            baseline = {"n": 0}

            async def shim():
                try:
                    await f9p.propose_followup_plans(proj, gaps, judgments, model)
                except f9p.PlanProposalFailure:
                    baseline["n"] += 1
                    return "BASELINE"
                raise AssertionError("proposal 应失败")

            out = await ctl.execute(rec.task_id, shim(), policy={})
            row = gov_store.get_task(store, rec.task_id)
            assert out == "BASELINE" and baseline["n"] == 1
            assert row.status == TaskStatus.COMPLETED.value
            assert row.version == 1
            assert row.counters_snapshot["llm_calls"] == 1
            store.close()

        asyncio.run(scenario())

    def test_timeout_during_plan_llm_timed_out(self, gov_tmp):
        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-p3")
            proj, gaps = _scenario()
            judgments = _judgments(gaps)
            model = _make_plan_model(hang=True)

            async def shim():
                return await f9p.propose_followup_plans(proj, gaps, judgments, model)

            task = asyncio.create_task(
                ctl.execute(rec.task_id, shim(), policy={"wall_clock_timeout": 0.25})
            )
            with pytest.raises(asyncio.CancelledError):
                await task
            row = gov_store.get_task(store, rec.task_id)
            assert row.status == TaskStatus.TIMED_OUT.value
            assert row.counters_snapshot["llm_calls"] == 1
            store.close()

        asyncio.run(scenario())


def _run(coro):
    return asyncio.run(coro)
