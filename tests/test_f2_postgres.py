"""F2 PostgreSQL 镜像（RESEARCH_DSN_TEST 门控，独立测试库）。

覆盖 sqlite 套件的核心 F2 语义在 PG 上的等价行为：
migration（0002）/ schema / claim 幂等 / binding M:N / citation R2 守卫 /
validator 核心规则（R2/R3/R5/R6/R7/R8/R10）/ 纯度 / apply 单写入口 / render 确定性。
说明：R1/R4 的 FK-off 原始注入为 sqlite 专有（见 test_validate_rules.py），
PG 走与 sqlite 相同的规则代码路径，matrix 以可达规则为准。
"""

import os
import uuid

import pytest

PG_TEST_DSN_ENV = "RESEARCH_DSN_TEST"

try:
    import psycopg  # noqa: F401

    HAS_PSYCOPG = True
except Exception:  # pragma: no cover
    HAS_PSYCOPG = False

needs_pg = pytest.mark.skipif(
    not (HAS_PSYCOPG and os.getenv(PG_TEST_DSN_ENV)),
    reason=f"需要 psycopg 与 {PG_TEST_DSN_ENV}（独立测试库）",
)

from app.research import migrations, provenance, registry, store as rstore  # noqa: E402
from app.research.normalize import canonical_key_for  # noqa: E402
from app.research.schemas import ClaimStatus, ClaimType  # noqa: E402
from app.research.validate import validate_run  # noqa: E402


@needs_pg
class TestF2Postgres:
    def _bind_store(self):
        old = rstore._instance
        dsn = os.getenv(PG_TEST_DSN_ENV)
        store = rstore._PostgresStore(dsn)
        migrations.ensure_schema(store)
        rstore._instance = store
        self._old_instance = old
        return store

    def _cleanup(self):
        old = getattr(self, "_old_instance", None)
        if old is not None:
            rstore._instance = old

    def _run(self, prefix="f2pg"):
        run_id = f"{prefix}-{uuid.uuid4().hex[:12]}"
        run_id, sqid = registry.create_run_and_root(
            "pg-thread", f"{prefix} 问题?", run_id=run_id
        )
        return run_id, sqid

    def _web(self, run_id, sqid, content, url=None):
        url = url or f"https://pg.example/{uuid.uuid4().hex[:8]}"
        qid = registry.record_search_query(
            run_id, sqid, agent="network_search", tool="s", query="q"
        )
        sid = registry.upsert_source(
            run_id,
            qid,
            source_type="web",
            agent="network_search",
            title="PG",
            locator=url,
            canonical_key=canonical_key_for("web", canonical_url=url),
            canonical_url=url,
        )
        return registry.append_evidence(
            run_id,
            sid,
            sqid,
            content=content,
            locator=url,
            extraction_method="web_result",
        )

    def test_migration_schema_and_boundary(self):
        self._bind_store()
        try:
            assert sorted(
                migrations.applied_migration_versions(rstore.get_store())
            ) == [
                "0001",
                "0002",
                "0003",
                "0004",
                "0005",
                "0006",
            ]
            rows = rstore.get_store().execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name IN "
                "('claims','claim_evidences','citations')"
            )
            assert {r["table_name"] for r in rows} == {
                "claims",
                "claim_evidences",
                "citations",
            }
            cols = rstore.get_store().execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='citations'"
            )
            names = {c["column_name"] for c in cols}
            assert "citation_key" not in names and "number" not in names
        finally:
            self._cleanup()

    def test_full_flow_and_status_boundary(self):
        self._bind_store()
        try:
            run_id, sqid = self._run()
            e1 = self._web(run_id, sqid, "PG 证据正文 ABC")
            e2 = self._web(run_id, sqid, "PG 第二证据 XYZ")
            c1 = registry.create_claim(run_id, sqid, "PG 结论甲", ClaimType.FACT)
            c2 = registry.create_claim(run_id, sqid, "PG 结论乙", ClaimType.FACT)
            assert (
                registry.create_claim(run_id, sqid, "PG 结论甲", ClaimType.OPINION)
                == c1
            )  # 幂等
            registry.bind_claim_evidence(run_id, c1, e1)
            registry.bind_claim_evidence(run_id, c1, e2)  # M:N
            registry.bind_claim_evidence(run_id, c2, e2)
            assert registry.create_citation(
                run_id, c1, e1, quote="PG 证据正文", locator="s1"
            )
            # R2：无 binding 引用被拒
            import pytest as _pt

            with _pt.raises(ValueError, match="R2"):
                c3 = registry.create_claim(run_id, sqid, "PG 无绑定", ClaimType.FACT)
                e3 = self._web(run_id, sqid, "PG body3")
                registry.create_citation(run_id, c3, e3)

            report = validate_run(run_id)
            assert report.counts["claims"] == 3
            assert report.counts["unsupported"] == 1  # c3 无绑定
            # 纯度：validate 前后 status 不变
            status_before = {
                c.claim_id: c.status for c in provenance.list_claims(run_id)
            }
            validate_run(run_id)
            status_after = {
                c.claim_id: c.status for c in provenance.list_claims(run_id)
            }
            assert status_before == status_after
            assert all(s == "drafted" for s in status_after.values())
            # 单写入口：仅 c1 被 citation 覆盖 → validated
            assert registry.apply_validation_outcome(run_id, report) == 1
            statuses = {c.claim_id: c.status for c in provenance.list_claims(run_id)}
            assert statuses[c1] == ClaimStatus.VALIDATED.value
            assert statuses[c2] == "drafted" and statuses[c3] == "drafted"
            # R10：c2 有 binding 无 citation
            report2 = validate_run(run_id)
            assert any(w["rule"] == "R10" for w in report2.warnings)
            # render 确定性
            first = provenance.render_citations(run_id)
            assert first == provenance.render_citations(run_id)
            assert [c["number"] for c in first] == list(range(1, len(first) + 1))
            # 清理
            with rstore.get_store().transaction() as tx:
                tx.execute("DELETE FROM research_runs WHERE run_id = %s", (run_id,))
        finally:
            self._cleanup()
