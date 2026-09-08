"""F2 ClaimEvidence binding 测试：M:N / 幂等 / 同 run 守卫 / cross-run 拒绝。"""

import pytest

from app.research import provenance
from app.research.registry import (
    ResearchRunNotFoundError,
    bind_claim_evidence,
    create_claim,
)
from app.research.schemas import ClaimType
from tests._f2_helpers import add_web_evidence, make_run, subq_of


class TestBindClaimEvidence:
    def test_many_to_many(self, research_sqlite):
        run_id, sqid = make_run()
        e1 = add_web_evidence(run_id, sqid, "body one")[2]
        e2 = add_web_evidence(run_id, sqid, "body two", url="https://x.example/2")[2]
        c1 = create_claim(run_id, sqid, "陈述甲", ClaimType.FACT)
        c2 = create_claim(run_id, sqid, "陈述乙", ClaimType.FACT)

        bind_claim_evidence(run_id, c1, e1)
        bind_claim_evidence(run_id, c1, e2)
        bind_claim_evidence(run_id, c2, e1)

        chain1 = provenance.get_claim_chain(c1)
        assert chain1 is not None
        assert {ev["evidence"]["evidence_id"] for ev in chain1["evidences"]} == {e1, e2}
        chain2 = provenance.get_claim_chain(c2)
        assert [ev["evidence"]["evidence_id"] for ev in chain2["evidences"]] == [e1]

    def test_pair_idempotent(self, research_sqlite):
        run_id, sqid = make_run()
        eid = add_web_evidence(run_id, sqid, "body")[2]
        cid = create_claim(run_id, sqid, "S", ClaimType.FACT)
        a = bind_claim_evidence(run_id, cid, eid)
        b = bind_claim_evidence(run_id, cid, eid)
        assert a == b

    def test_cross_run_rejected(self, research_sqlite):
        run1, sq1 = make_run("a")
        run2, _sq2 = make_run("b")
        e1 = add_web_evidence(run1, sq1, "body")[2]
        c2 = create_claim(run2, subq_of(run2), "S", ClaimType.FACT)
        with pytest.raises(ValueError, match="不属于该 run"):
            bind_claim_evidence(run2, c2, e1)  # claim 在 run2，evidence 在 run1

    def test_missing_entities_rejected(self, research_sqlite):
        run_id, sqid = make_run()
        eid = add_web_evidence(run_id, sqid, "body")[2]
        cid = create_claim(run_id, sqid, "S", ClaimType.FACT)
        with pytest.raises(ValueError, match="不存在"):
            bind_claim_evidence(run_id, "no-claim", eid)
        with pytest.raises(ValueError, match="不存在"):
            bind_claim_evidence(run_id, cid, "no-evidence")
        with pytest.raises(ResearchRunNotFoundError):
            bind_claim_evidence("no-run", cid, eid)
