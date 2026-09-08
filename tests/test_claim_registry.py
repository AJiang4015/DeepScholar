"""F2 Claim registry 测试：创建 / 内容级幂等 / 枚举 / 守卫 / 截断。"""

import pytest

from app.research import provenance
from app.research.registry import (
    CLAIM_STATEMENT_MAX,
    ResearchRunNotFoundError,
    create_claim,
    sha256_hex,
)
from app.research.schemas import ClaimStatus, ClaimType
from tests._f2_helpers import make_run, subq_of


class TestCreateClaim:
    def test_create_basic(self, research_sqlite):
        run_id, sqid = make_run()
        cid = create_claim(run_id, sqid, "行业 2026 增长显著", ClaimType.FACT)
        assert cid
        claims = provenance.list_claims(run_id)
        assert len(claims) == 1
        c = claims[0]
        assert c.claim_id == cid
        assert c.status == ClaimStatus.DRAFTED.value
        assert c.claim_type == ClaimType.FACT.value
        assert c.statement_sha == sha256_hex("行业 2026 增长显著")

    def test_content_idempotent_same_run(self, research_sqlite):
        run_id, sqid = make_run()
        a = create_claim(run_id, sqid, "同样陈述", ClaimType.FACT)
        b = create_claim(run_id, sqid, "同样陈述", ClaimType.OPINION)  # type 不同仍收敛
        assert a == b
        assert len(provenance.list_claims(run_id)) == 1

    def test_same_statement_different_run_distinct(self, research_sqlite):
        run1, sq1 = make_run("r1")
        run2, sq2 = make_run("r2")
        assert create_claim(run1, sq1, "S", ClaimType.FACT) != create_claim(
            run2, sq2, "S", ClaimType.FACT
        )

    def test_invalid_claim_type_rejected(self, research_sqlite):
        run_id, sqid = make_run()
        with pytest.raises(ValueError, match="非法 claim_type"):
            create_claim(run_id, sqid, "S", "GUESS")

    def test_empty_statement_rejected(self, research_sqlite):
        run_id, sqid = make_run()
        with pytest.raises(ValueError, match="不能为空"):
            create_claim(run_id, sqid, "   ", ClaimType.FACT)

    def test_missing_run_rejected(self, research_sqlite):
        with pytest.raises(ResearchRunNotFoundError):
            create_claim("nope", "nope", "S", ClaimType.FACT)

    def test_subquestion_cross_run_rejected(self, research_sqlite):
        run_id, _sq = make_run("a")
        other_sq = subq_of(make_run("b")[0])
        with pytest.raises(ValueError, match="不属于该 run"):
            create_claim(run_id, other_sq, "S", ClaimType.FACT)

    def test_statement_truncation(self, research_sqlite):
        run_id, sqid = make_run()
        long = "x" * (CLAIM_STATEMENT_MAX + 100)
        cid = create_claim(run_id, sqid, long, ClaimType.STATISTIC)
        claims = provenance.list_claims(run_id)
        assert len(claims[0].statement) == CLAIM_STATEMENT_MAX
        assert claims[0].statement_sha == sha256_hex(long[:CLAIM_STATEMENT_MAX])
        assert cid == claims[0].claim_id
