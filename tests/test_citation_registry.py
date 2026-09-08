"""F2 Citation registry 测试：创建 / 无 binding 拒绝（R2）/ 幂等 / quote / locator / 无编号列。"""

import pytest

from app.research import provenance
from app.research.registry import (
    LOCATOR_MAX,
    QUOTE_MAX,
    ResearchRunNotFoundError,
    bind_claim_evidence,
    create_citation,
    create_claim,
)
from app.research.schemas import ClaimType
from tests._f2_helpers import add_web_evidence, make_run, subq_of


def _setup(run_id, sqid, content="证据正文 ABC 123 连续片段"):
    eid = add_web_evidence(run_id, sqid, content)[2]
    cid = create_claim(run_id, sqid, "结论陈述", ClaimType.FACT)
    bid = bind_claim_evidence(run_id, cid, eid)
    return cid, eid, bid


class TestCreateCitation:
    def test_create_after_binding(self, research_sqlite):
        run_id, sqid = make_run()
        cid, eid, _ = _setup(run_id, sqid)
        cit = create_citation(run_id, cid, eid, quote="证据正文 ABC", locator="sec:2")
        assert cit
        citations = provenance.list_citations(run_id)
        assert len(citations) == 1
        assert citations[0].quote == "证据正文 ABC"
        assert citations[0].locator == "sec:2"

    def test_missing_binding_rejected(self, research_sqlite):
        """R2：无 claim_evidences binding 时引用被拒（写前守卫）。"""
        run_id, sqid = make_run()
        cid = create_claim(run_id, sqid, "无绑定结论", ClaimType.FACT)
        eid = add_web_evidence(run_id, sqid, "body")[2]
        with pytest.raises(ValueError, match="R2"):
            create_citation(run_id, cid, eid)

    def test_idempotent_pair(self, research_sqlite):
        run_id, sqid = make_run()
        cid, eid, _ = _setup(run_id, sqid)
        a = create_citation(run_id, cid, eid, quote="A")
        b = create_citation(run_id, cid, eid, quote="B")  # 二次调用收敛
        assert a == b
        assert len(provenance.list_citations(run_id)) == 1
        assert provenance.list_citations(run_id)[0].quote == "A"  # 首写生效

    def test_guards(self, research_sqlite):
        run_id, sqid = make_run()
        cid, eid, _ = _setup(run_id, sqid)
        with pytest.raises(ResearchRunNotFoundError):
            create_citation("no-run", cid, eid)
        with pytest.raises(ValueError, match="claim 不存在"):
            create_citation(run_id, "no-claim", eid)
        with pytest.raises(ValueError, match="evidence 不存在"):
            create_citation(run_id, cid, "no-evidence")

    def test_cross_run_rejected(self, research_sqlite):
        run1, sq1 = make_run("a")
        run2, _ = make_run("b")
        c1, e1, _ = _setup(run1, sq1)
        c2 = create_claim(run2, subq_of(run2), "b 结论", ClaimType.FACT)
        # claim 在 run2、evidence 在 run1 → cross-run 拒绝（写前守卫）
        with pytest.raises(ValueError, match="不属于该 run"):
            create_citation(run2, c2, e1)
        _ = c1  # 保持引用清晰

    def test_quote_truncated_to_500(self, research_sqlite):
        run_id, sqid = make_run()
        content = "x" * 900
        cid, eid, _ = _setup(run_id, sqid, content=content)
        create_citation(run_id, cid, eid, quote=content)
        cit = provenance.list_citations(run_id)[0]
        assert cit.quote == content[:QUOTE_MAX]

    def test_locator_validation(self, research_sqlite):
        run_id, sqid = make_run()
        cid, eid, _ = _setup(run_id, sqid)
        with pytest.raises(ValueError, match="R7"):
            create_citation(run_id, cid, eid, locator="z" * (LOCATOR_MAX + 1))
        with pytest.raises(ValueError, match="R7"):
            create_citation(run_id, cid, eid, locator="ok\nbad")

    def test_no_presentation_number_column(self, research_sqlite):
        """citations 表不得含 citation_key / 编号列（rev2 红线）。"""
        import sqlite3

        from app.research import config as rconfig
        from app.research.store import get_store

        get_store()
        import os

        db = os.environ[rconfig.RESEARCH_DB_ENV]
        conn = sqlite3.connect(db)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(citations)")}
        conn.close()
        assert "citation_key" not in cols
        assert "number" not in cols
