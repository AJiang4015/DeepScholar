"""F9-P0 Batch 1 — Research State Projection tests（sqlite 快速单测层）。

覆盖：schema/字段集、determinism（重复运行 + 插入顺序无关）、稳定排序（同刻/异 id）、
best_verdict（多 succeeded / succeeded+failed / 仅 failed / 同刻不同 id）、
required_uncovered（无 ev 无 src / 有 src 无 ev / 有 ev / 非 required）、bounded（各上限截断
+ omitted 诚实计数）、budget（F8 snapshot 只读、projection×2 不增长 counter、非法 snapshot）、
fresh、independent_flag、conflicts（F4/F6 未缓解判定）、evidence_summary domain 计数、
ProjectionError 路径、JSON 可序列化。

SQLite PASS ≠ PostgreSQL PASS：跨后端语义门在 test_f9_projection_postgres.py。
"""

from __future__ import annotations

import datetime
import json
import uuid

import pytest

from app.f9 import projection as f9p
from app.research import store as rstore
from tests import _f9_helpers as h

FIXED_NOW = datetime.datetime(2026, 9, 10, 0, 0, 0, tzinfo=datetime.timezone.utc)
ISO = "2026-09-01T00:00:00+00:00"


def _u() -> str:
    return uuid.uuid4().hex


def _budget(limits=None, counts=None):
    default_limits = {
        "llm_calls": 120,
        "tool_calls": 300,
        "search_calls": 40,
        "agent_steps": 200,
    }
    default_counts = {k: 0 for k in default_limits}
    lim = dict(default_limits if limits is None else limits)
    cnt = dict(default_counts if counts is None else counts)
    return {"limits": lim, "counts": cnt}


def _fresh_run(question="q?"):
    """直接 seed 一个只有 run + root sub_question 的干净 run；返回 (run_id, root_sq_id)。"""
    run_id, sq_id = _u(), _u()
    h.insert_run(rstore.get_store(), run_id, question=question)
    h.insert_subq(
        rstore.get_store(),
        run_id,
        sq_id,
        position=0,
        question="root?",
        status="planned",
    )
    return run_id, sq_id


def _claim_chain(
    run_id,
    sq_id,
    *,
    statement=None,
    claim_id=None,
    evidence_count=1,
    url=None,
    evidence_created_at=None,
    claim_created_at="2026-09-02T12:00:00+00:00",
):
    """run 内加 claim + N 个 bound evidence（各自 query+source 链）；返回 (claim_id, [evidence_id])。"""
    store = rstore.get_store()
    cid = claim_id or _u()
    h.insert_claim(
        store,
        run_id,
        cid,
        sq_id,
        statement=statement or f"s-{cid}",
        created_at=claim_created_at,
    )
    evs = []
    for i in range(evidence_count):
        qid, sid, eid = _u(), _u(), _u()
        h.insert_query(store, run_id, qid, sq_id)
        url_i = f"{url}/v{i}" if url else None  # source canonical_key 须逐源唯一
        h.insert_source(store, run_id, sid, qid, url=url_i)
        h.insert_evidence(
            store,
            run_id,
            eid,
            sid,
            sq_id,
            content=f"c-{eid}",
            created_at=evidence_created_at or "2026-09-02T10:00:00+00:00",
        )
        h.insert_binding(store, run_id, _u(), cid, eid)
        evs.append(eid)
    return cid, evs


# ---------------------------------------------------------------------------
# schema / determinism / ordering
# ---------------------------------------------------------------------------
class TestSchemaAndDeterminism:
    def test_top_level_fields_and_defaults(self, research_sqlite):
        run_id, sq_id = _fresh_run()
        p = f9p.project(run_id, round=3, budget=_budget())
        assert list(p.keys()) == [
            "run_id",
            "round",
            "budget",
            "sub_questions",
            "required_uncovered",
            "claims",
            "evidence_summary",
            "gap_signals",
            "open_questions",
            "size_limits",
        ]
        assert p["run_id"] == run_id
        assert p["round"] == 3
        assert p["gap_signals"] == {}
        assert p["open_questions"] == []
        assert p["required_uncovered"] == [sq_id]  # root 无 evidence 无 source
        assert p["claims"] == []
        assert p["evidence_summary"] == {"count": 0, "by_domain": {}}
        # size_limits：caps + omitted 全 0
        sl = p["size_limits"]
        for key in (
            "max_sub_questions",
            "max_claims",
            "max_evidence_refs_per_claim",
            "max_conflicts_per_claim",
        ):
            assert key in sl
        assert sl["omitted"] == {
            "sub_questions": 0,
            "claims": 0,
            "evidence_refs": 0,
            "conflicts": 0,
        }

    def test_repeat_projections_identical(self, research_sqlite):
        run_id, sq_id = _fresh_run()
        _claim_chain(run_id, sq_id)
        p1 = f9p.project(run_id, round=2, budget=_budget())
        p2 = f9p.project(run_id, round=2, budget=_budget())
        assert p1 == p2
        assert json.dumps(p1, sort_keys=True) == json.dumps(p2, sort_keys=True)

    def test_insertion_order_independent(self, research_tmp):
        """同一逻辑 state、两种插入顺序 → projection 完全一致。"""
        from app.research import migrations

        path_a = research_tmp / "a.sqlite"
        path_b = research_tmp / "b.sqlite"
        store_a = rstore._SqliteStore(str(path_a))
        store_b = rstore._SqliteStore(str(path_b))
        try:
            migrations.ensure_schema(store_a)
            migrations.ensure_schema(store_b)
            run_id = "run-order-x"
            h.seed_rich_scenario(store_a, run_id, reverse=False)
            h.seed_rich_scenario(store_b, run_id, reverse=True)
            pa = f9p.project(run_id, round=0, budget=_budget(), store=store_a)
            pb = f9p.project(run_id, round=0, budget=_budget(), store=store_b)
            assert pa == pb
            assert json.dumps(pa, sort_keys=True) == json.dumps(pb, sort_keys=True)
        finally:
            store_a.close()
            store_b.close()

    def test_stable_ordering_same_timestamp_different_ids(self, research_sqlite):
        """人为同刻 + 不同 id + 不同插入顺序 → 顺序由 (ts, id) 显式键决定。"""
        store = rstore.get_store()
        run_id, _ = _fresh_run()
        sq_id = _root_of(run_id)
        c_late, _ = _claim_chain(
            run_id,
            sq_id,
            claim_id="zzz-claim",
            statement="late",
            claim_created_at="2026-09-02T12:00:00+00:00",
        )
        c_early, _ = _claim_chain(
            run_id,
            sq_id,
            claim_id="aaa-claim",
            statement="early",
            claim_created_at="2026-09-02T12:00:00+00:00",
        )
        del c_late, c_early, store
        claims = f9p.project(run_id)["claims"]
        assert [c["claim_id"] for c in claims] == ["aaa-claim", "zzz-claim"]


def _root_of(run_id: str) -> str:
    from app.research import provenance

    subs = provenance.list_sub_questions(run_id)
    assert subs
    return subs[0].sub_question_id


# ---------------------------------------------------------------------------
# best_verdict
# ---------------------------------------------------------------------------
class TestBestVerdict:
    def _verdict_run(self, specs):
        """specs: list of (verification_id, verdict, status, created_at)。"""
        store = rstore.get_store()
        run_id, _ = _fresh_run()
        sq_id = _root_of(run_id)
        cid, evs = _claim_chain(run_id, sq_id)
        for vid, verdict, status, created_at in specs:
            h.insert_verification(
                store,
                run_id,
                vid,
                cid,
                evs[0],
                verdict=verdict,
                status=status,
                created_at=created_at,
            )
        return run_id, cid

    def test_multiple_succeeded_take_latest(self, research_sqlite):
        run_id, cid = self._verdict_run(
            [
                ("v-1", "SUPPORTS", "succeeded", "2026-09-02T13:00:00+00:00"),
                ("v-2", "CONTRADICTS", "succeeded", "2026-09-02T14:00:00+00:00"),
            ]
        )
        claim = _claim_of(run_id, cid)
        assert claim["best_verdict"] == "CONTRADICTS"

    def test_succeeded_plus_failed_failed_excluded(self, research_sqlite):
        run_id, cid = self._verdict_run(
            [
                ("v-1", "SUPPORTS", "succeeded", "2026-09-02T13:00:00+00:00"),
                ("v-2", "CONTRADICTS", "failed", "2026-09-02T14:00:00+00:00"),
            ]
        )
        claim = _claim_of(run_id, cid)
        assert claim["best_verdict"] == "SUPPORTS"

    def test_only_failed_is_null(self, research_sqlite):
        run_id, cid = self._verdict_run(
            [("v-1", "CONTRADICTS", "failed", "2026-09-02T13:00:00+00:00")]
        )
        claim = _claim_of(run_id, cid)
        assert claim["best_verdict"] is None

    def test_same_created_at_tie_by_verification_id(self, research_sqlite):
        run_id, cid = self._verdict_run(
            [
                ("v-aaa", "SUPPORTS", "succeeded", "2026-09-02T13:30:00+00:00"),
                ("v-zzz", "INSUFFICIENT", "succeeded", "2026-09-02T13:30:00+00:00"),
            ]
        )
        claim = _claim_of(run_id, cid)
        assert claim["best_verdict"] == "INSUFFICIENT"  # (ts, id) 升序取末

    def test_no_verifications_is_null(self, research_sqlite):
        run_id, _ = _fresh_run()
        cid, _ = _claim_chain(run_id, _root_of(run_id))
        assert _claim_of(run_id, cid)["best_verdict"] is None


def _claim_of(run_id_or_projection, claim_id: str):
    """从 run_id 或已计算的 projection dict 中取 claim 条目。"""
    claims = (
        run_id_or_projection["claims"]
        if isinstance(run_id_or_projection, dict)
        else f9p.project(run_id_or_projection)["claims"]
    )
    for c in claims:
        if c["claim_id"] == claim_id:
            return c
    raise AssertionError(f"claim {claim_id} 不在 projection")


# ---------------------------------------------------------------------------
# required_uncovered
# ---------------------------------------------------------------------------
class TestRequiredUncovered:
    def test_root_no_evidence_no_source_uncovered(self, research_sqlite):
        run_id, sq_id = _fresh_run()
        assert f9p.project(run_id)["required_uncovered"] == [sq_id]

    def test_root_with_source_but_no_evidence_covered(self, research_sqlite):
        store = rstore.get_store()
        run_id, sq_id = _fresh_run()
        qid, sid, _ = _u(), _u(), _u()
        h.insert_query(store, run_id, qid, sq_id)
        h.insert_source(store, run_id, sid, qid, url="https://example.com/p")
        assert f9p.project(run_id)["required_uncovered"] == []

    def test_root_with_evidence_covered(self, research_sqlite):
        run_id, sq_id = _fresh_run()
        _claim_chain(run_id, sq_id, evidence_count=1)
        assert f9p.project(run_id)["required_uncovered"] == []

    def test_non_required_child_not_uncovered(self, research_sqlite):
        store = rstore.get_store()
        run_id, sq_id = _fresh_run()
        child = _u()
        h.insert_subq(
            store, run_id, child, position=1, question="child", parent_id=sq_id
        )
        p = f9p.project(run_id)
        assert child not in p["required_uncovered"]
        assert p["required_uncovered"] == [sq_id]  # root 仍缺失覆盖
        by_id = {s["sub_question_id"]: s for s in p["sub_questions"]}
        assert by_id[sq_id]["required"] is True
        assert by_id[child]["required"] is False


# ---------------------------------------------------------------------------
# evidence_summary / domain
# ---------------------------------------------------------------------------
class TestEvidenceSummary:
    def test_domain_counts_deterministic(self, research_sqlite):
        store = rstore.get_store()
        run_id, sq_id = _fresh_run()
        # 2 × example.com（www.example.com/a, www.example.com/b）+ 1 × bbc.co.uk
        for url in ("https://www.example.com/a", "https://www.example.com/b"):
            _claim_chain(run_id, sq_id, url=url, evidence_count=1)
        qid, sid, eid = _u(), _u(), _u()
        h.insert_query(store, run_id, qid, sq_id)
        h.insert_source(store, run_id, sid, qid, url="https://news.bbc.co.uk/x")
        h.insert_evidence(store, run_id, eid, sid, sq_id, content="bbc", created_at=ISO)
        # ragflow（无 canonical_url）→ unknown_domain 桶
        qid2, sid2, eid2 = _u(), _u(), _u()
        h.insert_query(store, run_id, qid2, sq_id)
        h.insert_source(store, run_id, sid2, qid2, source_type="ragflow", url=None)
        h.insert_evidence(
            store, run_id, eid2, sid2, sq_id, content="rag", created_at=ISO
        )
        es = f9p.project(run_id)["evidence_summary"]
        assert es["count"] == 4
        assert es["by_domain"] == {
            "bbc.co.uk": 1,
            "example.com": 2,
            "unknown_domain": 1,
        }


# ---------------------------------------------------------------------------
# fresh
# ---------------------------------------------------------------------------
class TestFresh:
    def test_fresh_window(self, research_sqlite):
        run_id, sq_id = _fresh_run()
        # 老 evidence（2 天前）→ fresh False
        old_cid, _ = _claim_chain(
            run_id,
            sq_id,
            evidence_count=1,
            url="https://example.com/old",
            evidence_created_at="2026-09-08T00:00:00+00:00",
        )
        # 新 evidence（1 小时内）→ fresh True
        fresh_cid, _ = _claim_chain(
            run_id,
            sq_id,
            evidence_count=1,
            url="https://example.com/new",
            evidence_created_at="2026-09-09T23:30:00+00:00",
        )
        p = f9p.project(run_id, now=FIXED_NOW)
        assert _claim_of(p, fresh_cid)["fresh"] is True
        assert _claim_of(p, old_cid)["fresh"] is False

    def test_fresh_no_evidence_is_null(self, research_sqlite):
        store = rstore.get_store()
        run_id, sq_id = _fresh_run()
        cid = _u()
        h.insert_claim(store, run_id, cid, sq_id, statement="naked", created_at=ISO)
        claim = _claim_of(run_id, cid)
        assert claim["fresh"] is None

    def test_boundary_exactly_window_is_fresh(self, research_sqlite):
        run_id, sq_id = _fresh_run()
        cid, _ = _claim_chain(
            run_id,
            sq_id,
            evidence_count=1,
            url="https://example.com/b",
            evidence_created_at="2026-09-09T00:00:00+00:00",  # 恰 1 天
        )
        assert _claim_of(f9p.project(run_id, now=FIXED_NOW), cid)["fresh"] is True


# ---------------------------------------------------------------------------
# independent_flag
# ---------------------------------------------------------------------------
class TestIndependentFlag:
    def test_no_corroboration_is_null(self, research_sqlite):
        run_id, sq_id = _fresh_run()
        cid, _ = _claim_chain(run_id, sq_id)
        assert _claim_of(run_id, cid)["independent_flag"] is None

    def test_complete_independent_count_ge2_true(self, research_sqlite):
        store = rstore.get_store()
        run_id, sq_id = _fresh_run()
        cid, _ = _claim_chain(
            run_id, sq_id, evidence_count=2, url="https://example.com/x"
        )
        h.insert_corroboration(
            store,
            run_id,
            _u(),
            cid,
            status="complete",
            support={"source_count": 2, "independent_count": 2},
        )
        assert _claim_of(run_id, cid)["independent_flag"] is True

    def test_single_cluster_false(self, research_sqlite):
        store = rstore.get_store()
        run_id, sq_id = _fresh_run()
        cid, _ = _claim_chain(run_id, sq_id)
        h.insert_corroboration(
            store,
            run_id,
            _u(),
            cid,
            status="complete",
            support={"source_count": 1, "independent_count": 1},
        )
        assert _claim_of(run_id, cid)["independent_flag"] is False

    def test_failed_corroboration_ignored_null(self, research_sqlite):
        store = rstore.get_store()
        run_id, sq_id = _fresh_run()
        cid, _ = _claim_chain(run_id, sq_id)
        h.insert_corroboration(
            store,
            run_id,
            _u(),
            cid,
            status="failed",
            support={"source_count": 0, "independent_count": 0},
        )
        assert _claim_of(run_id, cid)["independent_flag"] is None


# ---------------------------------------------------------------------------
# conflicts（F4/F6 未缓解）
# ---------------------------------------------------------------------------
class TestConflicts:
    def _conflict_run(self):
        run_id, sq_id = _fresh_run()
        cid, evs = _claim_chain(
            run_id, sq_id, evidence_count=3, url="https://example.com/c"
        )
        return run_id, cid, evs

    def test_unresolved_conflicts_selection(self, research_sqlite):
        store = rstore.get_store()
        run_id, cid, evs = self._conflict_run()
        # f1 无 reconciliation → 未缓解
        h.insert_conflict(
            store,
            run_id,
            "f1",
            cid,
            evs[0],
            evs[1],
            conflict_type="CONTRADICTION",
            genuine=True,
        )
        # f2 GENUINE_CONTESTED → 未缓解
        h.insert_conflict(
            store,
            run_id,
            "f2",
            cid,
            evs[0],
            evs[2],
            conflict_type="CONTRADICTION",
            genuine=True,
        )
        h.insert_reconciliation(
            store,
            run_id,
            "r2",
            cid,
            "f2",
            status="complete",
            outcome="GENUINE_CONTESTED",
        )
        # f3 DETAIL_INCONSISTENCY → 缓解（排除）
        h.insert_conflict(
            store,
            run_id,
            "f3",
            cid,
            evs[1],
            evs[2],
            conflict_type="INCONSISTENCY",
            genuine=True,
        )
        h.insert_reconciliation(
            store,
            run_id,
            "r3",
            cid,
            "f3",
            status="complete",
            outcome="DETAIL_INCONSISTENCY",
        )
        # f4 failed reconciliation → 未缓解
        h.insert_conflict(
            store,
            run_id,
            "f4",
            cid,
            evs[0],
            evs[1],
            conflict_type="CONTRADICTION",
            genuine=True,
            fingerprint="fp-x",
        )
        h.insert_reconciliation(
            store, run_id, "r4", cid, "f4", status="failed", outcome=None
        )
        claim = _claim_of(run_id, cid)
        got = {c["conflict_id"]: c for c in claim["conflicts"]}
        assert set(got) == {"f1", "f2", "f4"}
        assert got["f1"]["reconciliation_status"] is None
        assert got["f2"]["reconciliation_outcome"] == "GENUINE_CONTESTED"
        assert got["f4"]["reconciliation_status"] == "failed"
        assert all(c["status"] == "confirmed" for c in claim["conflicts"])
        assert all(c["genuine"] is True for c in claim["conflicts"])

    def test_same_origin_mitigated_excluded(self, research_sqlite):
        store = rstore.get_store()
        run_id, cid, evs = self._conflict_run()
        h.insert_conflict(
            store,
            run_id,
            "f5",
            cid,
            evs[0],
            evs[1],
            conflict_type="CONTRADICTION",
            genuine=False,
        )
        h.insert_reconciliation(
            store,
            run_id,
            "r5",
            cid,
            "f5",
            status="complete",
            outcome="SAME_ORIGIN_CONTRADICTION",
        )
        assert _claim_of(run_id, cid)["conflicts"] == []

    def test_conflict_order_by_created_at_then_id(self, research_sqlite):
        store = rstore.get_store()
        run_id, cid, evs = self._conflict_run()
        h.insert_conflict(
            store,
            run_id,
            "f-z",
            cid,
            evs[0],
            evs[1],
            conflict_type="CONTRADICTION",
            genuine=True,
            created_at="2026-09-02T15:00:00+00:00",
        )
        h.insert_conflict(
            store,
            run_id,
            "f-a",
            cid,
            evs[0],
            evs[2],
            conflict_type="CONTRADICTION",
            genuine=True,
            created_at="2026-09-02T14:00:00+00:00",
        )
        ids = [c["conflict_id"] for c in _claim_of(run_id, cid)["conflicts"]]
        assert ids == ["f-a", "f-z"]


# ---------------------------------------------------------------------------
# bounded
# ---------------------------------------------------------------------------
class TestBounded:
    def test_claims_over_cap_truncated_and_omitted(self, research_sqlite):
        store = rstore.get_store()
        run_id, sq_id = _fresh_run()
        total = f9p.DEFAULT_SIZE_LIMITS["max_claims"] + 6
        for i in range(total):
            h.insert_claim(
                store,
                run_id,
                _u(),
                sq_id,
                statement=f"stmt-{i:03d}",
                created_at="2026-09-02T12:00:00+00:00",
            )
        p = f9p.project(run_id)
        assert len(p["claims"]) == f9p.DEFAULT_SIZE_LIMITS["max_claims"]
        assert p["size_limits"]["omitted"]["claims"] == 6

    def test_evidence_refs_over_cap_truncated_and_omitted(self, research_sqlite):
        run_id, sq_id = _fresh_run()
        cid, evs = _claim_chain(
            run_id, sq_id, evidence_count=12, url="https://example.com/e"
        )
        del evs
        claim = _claim_of(run_id, cid)
        assert (
            len(claim["evidence_refs"])
            == f9p.DEFAULT_SIZE_LIMITS["max_evidence_refs_per_claim"]
        )
        p = f9p.project(run_id)
        assert p["size_limits"]["omitted"]["evidence_refs"] == 4

    def test_conflicts_over_cap_truncated_and_omitted(self, research_sqlite):
        store = rstore.get_store()
        run_id, cid, evs = TestConflicts()._conflict_run()
        cap = f9p.DEFAULT_SIZE_LIMITS["max_conflicts_per_claim"]
        for i in range(cap + 3):
            h.insert_conflict(
                store,
                run_id,
                f"f-{i:02d}",
                cid,
                evs[0],
                evs[1],
                conflict_type="CONTRADICTION",
                genuine=True,
                fingerprint=f"fp-{i}",
            )
        claim = _claim_of(run_id, cid)
        assert len(claim["conflicts"]) == cap
        assert f9p.project(run_id)["size_limits"]["omitted"]["conflicts"] == 3

    def test_sub_questions_over_cap_truncated_and_omitted(self, research_sqlite):
        store = rstore.get_store()
        run_id, sq_id = _fresh_run()
        cap = f9p.DEFAULT_SIZE_LIMITS["max_sub_questions"]
        for i in range(1, cap + 2):
            h.insert_subq(
                store, run_id, _u(), position=i, question=f"sq-{i}", parent_id=sq_id
            )
        p = f9p.project(run_id)
        assert len(p["sub_questions"]) == cap
        assert p["size_limits"]["omitted"]["sub_questions"] == 2


# ---------------------------------------------------------------------------
# budget（F8 snapshot 只读）
# ---------------------------------------------------------------------------
class TestBudget:
    def test_snapshot_to_projection_no_counter_mutation(self, research_sqlite):
        """真实 BudgetCounter：projection×2 不增长任何 counter。"""
        from app.runtime.governance.counters import BudgetCounter

        run_id, _ = _fresh_run()
        _claim_chain(run_id, _root_of(run_id))
        counter = BudgetCounter(
            limits={
                "llm_calls": 10,
                "tool_calls": 20,
                "search_calls": 5,
                "agent_steps": 8,
            }
        )
        assert counter.acquire_llm_call()
        assert counter.acquire_search_call()
        before = counter.to_dict()
        p1 = f9p.project(run_id, round=1, budget=counter.to_dict())
        p2 = f9p.project(run_id, round=1, budget=counter.to_dict())
        after = counter.to_dict()
        assert before == after  # projection 未修改 counter
        assert p1["budget"] == p2["budget"]
        b = p1["budget"]
        assert b["counts"] == {
            "llm_calls": 1,
            "tool_calls": 1,
            "search_calls": 1,
            "agent_steps": 0,
        }
        assert b["limits"] == {
            "llm_calls": 10,
            "tool_calls": 20,
            "search_calls": 5,
            "agent_steps": 8,
        }
        assert b["remaining"] == {
            "llm_calls": 9,
            "tool_calls": 19,
            "search_calls": 4,
            "agent_steps": 8,
        }

    def test_budget_none_defaults_to_limits(self, research_sqlite):
        run_id, _ = _fresh_run()
        b = f9p.project(run_id)["budget"]
        assert b["counts"] == {k: 0 for k in f9p.BUDGET_KINDS}
        assert b["remaining"] == b["limits"]

    def test_invalid_snapshots_rejected(self, research_sqlite):
        run_id, _ = _fresh_run()
        with pytest.raises(f9p.ProjectionError):
            f9p.project(run_id, budget={"limits": {}, "counts": {}})
        with pytest.raises(f9p.ProjectionError):
            f9p.project(run_id, budget=_budget(counts={"llm_calls": 999}))
        with pytest.raises(f9p.ProjectionError):
            f9p.project(run_id, budget=_budget(counts={"llm_calls": 1.5}))
        with pytest.raises(f9p.ProjectionError):
            f9p.project(run_id, budget={"limits": 1, "counts": 2})


# ---------------------------------------------------------------------------
# 错误路径 / 其它
# ---------------------------------------------------------------------------
class TestErrorsAndMisc:
    def test_missing_run_raises(self, research_sqlite):
        with pytest.raises(f9p.ProjectionError):
            f9p.project("no-such-run")

    def test_invalid_round_raises(self, research_sqlite):
        run_id, _ = _fresh_run()
        with pytest.raises(f9p.ProjectionError):
            f9p.project(run_id, round=-1)
        with pytest.raises(f9p.ProjectionError):
            f9p.project(run_id, round=True)

    def test_store_disabled_raises(self, research_sqlite, monkeypatch):
        from app.research import config as rconfig

        monkeypatch.setenv(rconfig.RESEARCH_STORE_ENV, "disabled")
        rstore.reset_store()
        try:
            with pytest.raises(f9p.ProjectionError):
                f9p.project("whatever")
        finally:
            rstore.reset_store()

    def test_output_json_serializable(self, research_sqlite):
        run_id, _ = _fresh_run()
        sq_id = _root_of(run_id)
        for url in ("https://example.com/1", "https://www.bbc.co.uk/2"):
            _claim_chain(run_id, sq_id, url=url)
        p = f9p.project(run_id, budget=_budget())
        text = json.dumps(p, sort_keys=True, ensure_ascii=False)
        assert isinstance(text, str)
        assert p["round"] == 0  # round 缺省 0 且与 DB 状态无关

    def test_sub_question_evidence_and_source_counts(self, research_sqlite):
        store = rstore.get_store()
        run_id, sq_id = _fresh_run()
        _claim_chain(run_id, sq_id, evidence_count=2, url="https://example.com/a")
        qid, sid = _u(), _u()
        h.insert_query(store, run_id, qid, sq_id)
        h.insert_source(store, run_id, sid, qid, url="https://example.com/b")
        sq = {s["sub_question_id"]: s for s in f9p.project(run_id)["sub_questions"]}[
            sq_id
        ]
        assert sq["evidence_count"] == 2
        # 2（claim chain 的 query→source）+ 1（仅 source 无 evidence）→ source_count 3
        assert sq["source_count"] == 3
