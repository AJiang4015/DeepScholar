"""F9-P0 Batch 2 — Deterministic Gap Detection tests（sqlite/纯函数层）。

覆盖：每信号类型的边界（无/单/多/空·null）、required coverage、claim 结构缺口、
verification verdict 边界（INSUFFICIENT/UNVERIFIABLE/ABSTAIN/None）、conflict 信号、
independent/F5、freshness、budget_near 阈值与 F8 snapshot 只读、确定性（重复/乱序）、
bounded（截断+truncated 标记）、空 projection/契约违例、无副作用（DB 零写、counter 零改）、
与 Batch 1 project() 的整链集成（sqlite）。

SQLite PASS ≠ PostgreSQL PASS：跨后端语义门在 test_f9_gaps_postgres.py。
"""

from __future__ import annotations

import copy
import json
import uuid

import pytest

from app.f9 import gaps as f9g
from app.f9 import projection as f9p
from app.research import store as rstore
from tests import _f9_helpers as h

_DEFAULT_KINDS = ("llm_calls", "tool_calls", "search_calls", "agent_steps")


def _u() -> str:
    return uuid.uuid4().hex


def _budget(limits=None, counts=None):
    lim = {"llm_calls": 120, "tool_calls": 300, "search_calls": 40, "agent_steps": 200}
    lim.update(limits or {})
    cnt = {k: 0 for k in _DEFAULT_KINDS}
    cnt.update(counts or {})
    return {
        "limits": lim,
        "counts": cnt,
        "remaining": {k: lim[k] - cnt.get(k, 0) for k in _DEFAULT_KINDS},
    }


def _claim(
    cid=None,
    *,
    best_verdict=None,
    independent_flag=None,
    fresh=None,
    conflicts=None,
    evidence_refs=None,
    sub_question_id="sq-root",
    statement="s",
):
    cid = cid or _u()
    return {
        "claim_id": cid,
        "sub_question_id": sub_question_id,
        "statement": statement,
        "claim_type": "FACT",
        "status": "drafted",
        "created_at": "2026-09-02T12:00:00+00:00",
        "best_verdict": best_verdict,
        "independent_flag": independent_flag,
        "fresh": fresh,
        "conflicts": conflicts or [],
        "evidence_refs": evidence_refs if evidence_refs is not None else [],
    }


def _ev(eid=None):
    return {
        "evidence_id": eid or _u(),
        "created_at": "2026-09-02T10:00:00+00:00",
        "domain": "example.com",
    }


def _conf(cid=None, conflict_type="CONTRADICTION"):
    return {
        "conflict_id": cid or _u(),
        "conflict_type": conflict_type,
        "genuine": True,
        "status": "confirmed",
        "reconciliation_status": None,
        "reconciliation_outcome": None,
    }


def _proj(
    *, round_no=0, sub_questions=None, claims=None, budget=None, required_uncovered=None
):
    return {
        "run_id": "run-x",
        "round": round_no,
        "budget": budget if budget is not None else _budget(),
        "sub_questions": sub_questions if sub_questions is not None else [],
        "required_uncovered": required_uncovered or [],
        "claims": claims or [],
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


def _sq(sq_id=None, *, required=True, evidence_count=0, source_count=0, position=0):
    return {
        "sub_question_id": sq_id or "sq-root",
        "parent_id": None if required else "sq-root",
        "position": position,
        "question": "q",
        "status": "planned",
        "required": required,
        "evidence_count": evidence_count,
        "source_count": source_count,
    }


def _types(result) -> dict:
    return result["counts"]


class TestEmptyAndNull:
    def test_empty_projection_no_signals(self):
        r = f9g.detect_gaps(_proj())
        assert r["signals"] == []
        assert r["counts"] == {}
        assert r["total_signals"] == 0
        assert r["truncated"] is False
        assert r["round"] == 0

    def test_round_passthrough(self):
        assert f9g.detect_gaps(_proj(round_no=3))["round"] == 3

    def test_contract_violation_raises(self):
        with pytest.raises(f9g.GapDetectionError):
            f9g.detect_gaps({})
        p = _proj()
        p["claims"] = {}  # 契约要求 list
        with pytest.raises(f9g.GapDetectionError):
            f9g.detect_gaps(p)
        p2 = _proj()
        del p2["budget"]["limits"]
        with pytest.raises(f9g.GapDetectionError):
            f9g.detect_gaps(p2)

    def test_no_claims_no_evidence_no_signals(self):
        r = f9g.detect_gaps(_proj(sub_questions=[_sq(required=False)]))
        assert r["signals"] == []


class TestRequiredCoverage:
    def test_required_uncovered_signal(self):
        sq = _sq(evidence_count=0, source_count=0)
        r = f9g.detect_gaps(_proj(sub_questions=[sq], required_uncovered=["sq-root"]))
        assert _types(r) == {"required_uncovered": 1}
        s = r["signals"][0]
        assert (s["type"], s["rule"], s["subject_id"]) == (
            "required_uncovered",
            "RU1",
            "sq-root",
        )

    def test_uncovered_duplicate_not_double_counted(self):
        # required_uncovered 在 sub_questions 缺失 required 集合之外 → 只按列表发一次
        sq_other = _sq(
            sq_id="sq-other", required=True, evidence_count=0, source_count=0
        )
        r = f9g.detect_gaps(
            _proj(sub_questions=[sq_other], required_uncovered=["sq-other", "sq-ghost"])
        )
        # sq-ghost 不在 required 集合内 → 不发 RU1（Batch1 required_uncovered 已只含 required）
        assert _types(r) == {"required_uncovered": 1}

    def test_required_low_evidence_below_min(self):
        sq = _sq(evidence_count=1, source_count=1)
        r = f9g.detect_gaps(_proj(sub_questions=[sq]))
        assert _types(r) == {"required_low_evidence": 1}
        s = r["signals"][0]
        assert (s["type"], s["rule"]) == ("required_low_evidence", "RE1")
        assert s["detail"]["min_evidence"] == f9g.MIN_EVIDENCE

    def test_required_evidence_at_min_no_signal(self):
        r = f9g.detect_gaps(
            _proj(sub_questions=[_sq(evidence_count=f9g.MIN_EVIDENCE, source_count=1)])
        )
        assert r["signals"] == []

    def test_non_required_never_signal(self):
        sq = _sq(required=False, evidence_count=0, source_count=0)
        r = f9g.detect_gaps(_proj(sub_questions=[sq]))
        assert r["signals"] == []


class TestClaimStructuralGaps:
    def test_claim_no_evidence(self):
        c = _claim()
        r = f9g.detect_gaps(_proj(claims=[c]))
        assert _types(r) == {"claim_no_evidence": 1}
        assert r["signals"][0]["rule"] == "CE1"

    def test_claim_with_evidence_no_verdict_unverified(self):
        c = _claim(evidence_refs=[_ev()], best_verdict=None)
        r = f9g.detect_gaps(_proj(claims=[c]))
        assert _types(r) == {"claim_unverified": 1}

    def test_claim_insufficient_and_unverifiable(self):
        for verdict in ("INSUFFICIENT", "UNVERIFIABLE"):
            c = _claim(evidence_refs=[_ev()], best_verdict=verdict)
            r = f9g.detect_gaps(_proj(claims=[c]))
            assert _types(r) == {"claim_verdict_insufficient": 1}
            assert r["signals"][0]["detail"]["best_verdict"] == verdict

    def test_claim_supports_abstain_not_signal(self):
        # ABSTAIN/CONTRADICTS 不在 Spec §5 列举的确定性信号集 → 不发 CV2
        for verdict in ("SUPPORTS", "ABSTAIN", "CONTRADICTS"):
            c = _claim(evidence_refs=[_ev()], best_verdict=verdict)
            r = f9g.detect_gaps(_proj(claims=[c]))
            assert r["signals"] == []

    def test_claim_no_evidence_skips_other_claim_signals(self):
        c = _claim(independent_flag=False, fresh=False)
        r = f9g.detect_gaps(_proj(claims=[c]))
        assert _types(r) == {"claim_no_evidence": 1}  # 只 CE1，不叠加 IS1/FR1

    def test_independent_insufficient_only_when_f5_false(self):
        c_false = _claim(
            evidence_refs=[_ev()], best_verdict="SUPPORTS", independent_flag=False
        )
        c_none = _claim(
            evidence_refs=[_ev()], best_verdict="SUPPORTS", independent_flag=None
        )
        c_true = _claim(
            evidence_refs=[_ev()], best_verdict="SUPPORTS", independent_flag=True
        )
        r = f9g.detect_gaps(_proj(claims=[c_false, c_none, c_true]))
        assert _types(r) == {"independent_sources_insufficient": 1}

    def test_stale_evidence_only_when_fresh_false(self):
        c_stale = _claim(evidence_refs=[_ev()], best_verdict="SUPPORTS", fresh=False)
        c_ok = _claim(evidence_refs=[_ev()], best_verdict="SUPPORTS", fresh=True)
        r = f9g.detect_gaps(_proj(claims=[c_stale, c_ok]))
        assert _types(r) == {"stale_evidence": 1}

    def test_unresolved_conflict_signal(self):
        cf = _conf("cf-1")
        c = _claim(evidence_refs=[_ev()], best_verdict="SUPPORTS", conflicts=[cf])
        r = f9g.detect_gaps(_proj(claims=[c]))
        assert _types(r) == {"unresolved_conflict": 1}
        s = r["signals"][0]
        assert s["detail"]["conflict_ids"] == ["cf-1"]
        assert s["detail"]["count"] == 1

    def test_multiple_signals_one_claim(self):
        cf1, cf2 = _conf("cf-a"), _conf("cf-b", conflict_type="INCONSISTENCY")
        c = _claim(
            evidence_refs=[_ev(), _ev()],
            best_verdict="INSUFFICIENT",
            independent_flag=False,
            fresh=False,
            conflicts=[cf1, cf2],
        )
        r = f9g.detect_gaps(_proj(claims=[c]))
        assert _types(r) == {
            "claim_verdict_insufficient": 1,
            "independent_sources_insufficient": 1,
            "stale_evidence": 1,
            "unresolved_conflict": 1,
        }


class TestBudgetNear:
    def test_budget_near_threshold(self):
        # budget_near = remaining/limit <= 0.2（剩余接近上限；消费高则剩余低）
        # search counts=32 → remaining 8/40=0.2 → near；llm counts=36 → remaining 84/120=0.7
        p = _proj(budget=_budget(counts={"llm_calls": 36, "search_calls": 32}))
        r = f9g.detect_gaps(p)
        kinds = [s["subject_id"] for s in r["signals"] if s["type"] == "budget_near"]
        assert kinds == ["search_calls"]
        assert "tool_calls" not in kinds
        assert "agent_steps" not in kinds

    def test_budget_near_exact_ratio_inclusive(self):
        # search counts=32 → remaining 8/40=0.2 → near（<= 含边界）；
        # llm counts=30 → remaining 90/120=0.75 → 不 near
        p = _proj(budget=_budget(counts={"llm_calls": 30, "search_calls": 32}))
        r = f9g.detect_gaps(p)
        kinds = [s["subject_id"] for s in r["signals"] if s["type"] == "budget_near"]
        assert kinds == ["search_calls"]

    def test_budget_exhausted_is_near(self):
        p = _proj(budget=_budget(counts={"llm_calls": 120}))
        r = f9g.detect_gaps(p)
        kinds = [s["subject_id"] for s in r["signals"] if s["type"] == "budget_near"]
        assert kinds == ["llm_calls"]

    def test_budget_zero_limit_exhausted_domain(self):
        p = _proj(budget=_budget(limits={"agent_steps": 0}, counts={}))
        r = f9g.detect_gaps(p)
        kinds = {s["subject_id"] for s in r["signals"] if s["type"] == "budget_near"}
        assert "agent_steps" in kinds  # limit=0 → remaining=0 → 视为已到顶（确定性）
        assert "llm_calls" not in kinds

    def test_snapshot_not_mutated(self):
        b = _budget(counts={"search_calls": 8})
        before = copy.deepcopy(b)
        r1 = f9g.detect_gaps(_proj(budget=b))
        r2 = f9g.detect_gaps(_proj(budget=b))
        assert b == before  # 输入未被修改
        assert r1 == r2


class TestDeterminismOrderingAndBounds:
    def test_repeat_identical(self):
        claims = [
            _claim(best_verdict="INSUFFICIENT", evidence_refs=[_ev()]),
            _claim(independent_flag=False, evidence_refs=[_ev()]),
        ]
        p = _proj(claims=claims)
        r1 = f9g.detect_gaps(p)
        r2 = f9g.detect_gaps(p)
        assert r1 == r2
        assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)

    def test_stable_order_scrambled_build(self):
        def build(claims, budget_counts):
            return f9g.detect_gaps(
                _proj(
                    claims=claims,
                    budget=_budget(counts=budget_counts),
                    sub_questions=[_sq(evidence_count=1, source_count=1)],
                )
            )["signals"]

        claims_a = [
            _claim("c-z", best_verdict="UNVERIFIABLE", evidence_refs=[_ev()]),
            _claim("c-a", evidence_refs=[_ev()], independent_flag=False),
            _claim("c-m", evidence_refs=[]),
        ]
        claims_b = list(reversed(claims_a))
        sa = build(claims_a, {"search_calls": 8})
        sb = build(claims_b, {"search_calls": 8})
        assert sa == sb
        keys = [(s["type"], s["subject_type"], s["subject_id"]) for s in sa]
        assert keys == sorted(keys)

    def test_truncation_flag_and_cap(self, monkeypatch):
        monkeypatch.setattr(f9g, "MAX_SIGNALS", 5)
        claims = [_claim(evidence_refs=[]) for _ in range(8)]
        r = f9g.detect_gaps(_proj(claims=claims))
        assert r["truncated"] is True
        assert r["total_signals"] == 8
        assert len(r["signals"]) == 5
        assert r["counts"]["claim_no_evidence"] == 5


class TestIntegrationPipelineSqlite:
    def _store(self, research_sqlite):
        return rstore.get_store()

    def test_empty_run_pipeline(self, research_sqlite):
        store = self._store(research_sqlite)
        run_id, sq_id = _u(), _u()
        h.insert_run(store, run_id)
        h.insert_subq(store, run_id, sq_id, position=0, question="root")
        p = f9p.project(run_id)
        r = f9g.detect_gaps(p)
        assert r["counts"] == {"required_uncovered": 1}
        assert r["signals"][0]["subject_id"] == sq_id

    def test_no_side_effect_on_research_plane(self, research_sqlite):
        store = self._store(research_sqlite)
        run_id, sq_id = _u(), _u()
        h.insert_run(store, run_id)
        h.insert_subq(store, run_id, sq_id, position=0, question="root")
        before = store.execute(
            "SELECT (SELECT COUNT(*) FROM research_runs) + "
            "(SELECT COUNT(*) FROM sub_questions) + "
            "(SELECT COUNT(*) FROM claims) AS n"
        )[0]["n"]
        p = f9p.project(run_id)
        for _ in range(3):
            f9g.detect_gaps(p)
        after = store.execute(
            "SELECT (SELECT COUNT(*) FROM research_runs) + "
            "(SELECT COUNT(*) FROM sub_questions) + "
            "(SELECT COUNT(*) FROM claims) AS n"
        )[0]["n"]
        assert after == before

    def test_real_budget_counter_readonly_pipeline(self, research_sqlite):
        from app.runtime.governance.counters import BudgetCounter

        store = self._store(research_sqlite)
        run_id, _ = _u(), _u()
        h.insert_run(store, run_id)
        h.insert_subq(store, run_id, _u(), position=0, question="root")
        counter = BudgetCounter(
            limits={
                "llm_calls": 10,
                "tool_calls": 20,
                "search_calls": 4,
                "agent_steps": 8,
            }
        )
        for _ in range(8):
            assert counter.acquire_llm_call()  # llm 8/10 → remaining 2/10 = 0.2 → near
        assert counter.acquire_search_call()  # search 1/4 → remaining 3/4 → 不 near
        before = counter.to_dict()
        p = f9p.project(run_id, round=2, budget=counter.to_dict())
        r1 = f9g.detect_gaps(p)
        r2 = f9g.detect_gaps(p)
        assert counter.to_dict() == before  # detect 不修改 F8 counter
        assert r1 == r2
        near = {s["subject_id"] for s in r1["signals"] if s["type"] == "budget_near"}
        assert near == {"llm_calls"}
