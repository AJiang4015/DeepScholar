"""F2 provenance 链 + render_citations 确定性（[n] 不落库、identity 与编号解耦）。"""

import os
import sqlite3

from app.research import provenance, registry
from app.research.config import RESEARCH_DB_ENV
from app.research.schemas import ClaimType
from tests._f2_helpers import add_web_evidence, make_run


def _setup_two(run_id, sqid, t0: str = "2026-01-01T00:00:00+00:00"):
    """两个 claim、两条 evidence、两条 citation（显式递增时间序，render 确定性测试用）。"""
    e1 = add_web_evidence(run_id, sqid, "证据一 内容 AA", url="https://x.example/1")[2]
    e2 = add_web_evidence(run_id, sqid, "证据二 内容 BB", url="https://x.example/2")[2]
    c1 = registry.create_claim(run_id, sqid, "结论一", ClaimType.FACT)
    c2 = registry.create_claim(run_id, sqid, "结论二", ClaimType.STATISTIC)
    registry.bind_claim_evidence(run_id, c1, e1)
    registry.bind_claim_evidence(run_id, c1, e2)  # M:N
    registry.bind_claim_evidence(run_id, c2, e2)
    t1 = t0
    t2 = _add_seconds(t0, 1)
    cit1 = registry.create_citation(
        run_id, c1, e1, quote="证据一 内容", locator="sec:1", created_at=t1
    )
    cit2 = registry.create_citation(
        run_id, c2, e2, quote="证据二 内容", locator="sec:2", created_at=t2
    )
    return {"e1": e1, "e2": e2, "c1": c1, "c2": c2, "cit1": cit1, "cit2": cit2}


def _add_seconds(iso: str, seconds: int) -> str:
    from datetime import datetime, timedelta, timezone

    dt = datetime.fromisoformat(iso).astimezone(timezone.utc)
    return (dt + timedelta(seconds=seconds)).isoformat()


class TestProvenanceChain:
    def test_claim_chain(self, research_sqlite):
        run_id, sqid = make_run()
        ids = _setup_two(run_id, sqid)
        chain = provenance.get_claim_chain(ids["c1"])
        assert chain is not None
        assert chain["claim"]["statement"] == "结论一"
        evs = {ev["evidence"]["evidence_id"] for ev in chain["evidences"]}
        assert evs == {ids["e1"], ids["e2"]}
        for ev in chain["evidences"]:
            assert ev["source"]["run_id"] == run_id
        cits = [c["citation_id"] for c in chain["citations"]]
        assert ids["cit1"] in cits
        assert provenance.get_claim_chain("missing") is None

    def test_lists(self, research_sqlite):
        run_id, sqid = make_run()
        _setup_two(run_id, sqid)
        claims = provenance.list_claims(run_id)
        cits = provenance.list_citations(run_id)
        assert len(claims) == 2
        assert len(cits) == 2
        assert {c.claim_type for c in claims} == {"FACT", "STATISTIC"}


class TestRenderCitations:
    def test_deterministic_and_stable(self, research_sqlite):
        run_id, sqid = make_run()
        ids = _setup_two(run_id, sqid)
        first = provenance.render_citations(run_id)
        second = provenance.render_citations(run_id)
        assert first == second
        assert [c["number"] for c in first] == [1, 2]
        assert {c["citation_id"] for c in first} == {ids["cit1"], ids["cit2"]}
        # 显式时间序（t0 < t0+1s）→ cit1 在前、cit2 在后；uuid 序不参与排序
        assert first[0]["citation_id"] == ids["cit1"]
        assert first[1]["citation_id"] == ids["cit2"]

    def test_add_citation_appends_without_reorder(self, research_sqlite):
        run_id, sqid = make_run()
        _setup_two(run_id, sqid, t0="2026-01-01T00:00:00+00:00")
        before = provenance.render_citations(run_id)
        # 新增第三条（时间更晚 → 编号 3，前两条编号不变）
        e3 = add_web_evidence(
            run_id, sqid, "证据三 内容 CC", url="https://x.example/3"
        )[2]
        c3 = registry.create_claim(run_id, sqid, "结论三", ClaimType.INFERENCE)
        registry.bind_claim_evidence(run_id, c3, e3)
        cit3 = registry.create_citation(
            run_id, c3, e3, quote="证据三 内容", created_at="2026-01-01T00:00:02+00:00"
        )
        after = provenance.render_citations(run_id)
        assert len(after) == 3
        assert [c["number"] for c in after[:2]] == [1, 2]
        assert after[2]["citation_id"] == cit3 and after[2]["number"] == 3
        assert [c["number"] for c in before] == [1, 2]

    def test_identity_decoupled_from_number(self, research_sqlite):
        """[n] 与 citation_id 完全解耦；渲染不写回 citations 表。"""
        run_id, sqid = make_run()
        _setup_two(run_id, sqid)
        conn = sqlite3.connect(os.environ[RESEARCH_DB_ENV])
        before = conn.execute("SELECT count(*) FROM citations").fetchone()[0]
        rendered = provenance.render_citations(run_id)
        after = conn.execute("SELECT count(*) FROM citations").fetchone()[0]
        conn.close()
        assert before == after
        assert {r["number"] for r in rendered} == {1, 2}
