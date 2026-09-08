"""F4 Conflict Detection — PostgreSQL 镜像（RESEARCH_DSN_TEST 门控，独立测试库）。"""

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

from app.research import migrations, registry, store as rstore  # noqa: E402
from app.research.normalize import canonical_key_for  # noqa: E402
from app.research.schemas import ClaimType  # noqa: E402
from app.research.conflict import (  # noqa: E402
    ConflictIntegrityError,
    FakeDetector,
    VerifierSpec,
    claim_conflict_summary,
    conflict_chain,
    detect_claim_conflicts,
    list_conflicts,
)
from app.research.verify import FakeVerifier as F3Fake  # noqa: E402
from app.research import verify as f3verify  # noqa: E402


@needs_pg
class TestConflictPostgres:
    def _bind(self):
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

    def _run(self, prefix="cfpg"):
        run_id = f"{prefix}-{uuid.uuid4().hex[:10]}"
        run_id, sqid = registry.create_run_and_root(
            "pg-thread", f"{prefix} 问题?", run_id=run_id
        )
        return run_id, sqid

    def _web(self, run_id, sqid, content, url):
        qid = registry.record_search_query(
            run_id, sqid, agent="network_search", tool="s", query="q"
        )
        sid = registry.upsert_source(
            run_id,
            qid,
            source_type="web",
            agent="network_search",
            title="PG src",
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

    def test_migration_schema_and_flow(self):
        self._bind()
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
            cols = rstore.get_store().execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name='conflicts'"
            )
            names = {r["column_name"] for r in cols}
            assert {
                "conflict_id",
                "evidence_a_id",
                "evidence_b_id",
                "verification_a_id",
                "status",
                "conflict_type",
                "genuine",
                "detector_fingerprint",
            } <= names

            run_id, sqid = self._run()
            ev_a = self._web(run_id, sqid, "PG 支持", "https://pg.example/a")
            ev_b = self._web(run_id, sqid, "PG 反对", "https://pg.example/b")
            cid = registry.create_claim(run_id, sqid, "PG claim X", ClaimType.FACT)
            registry.bind_claim_evidence(run_id, cid, ev_a)
            registry.bind_claim_evidence(run_id, cid, ev_b)
            f3verify.semantic_verify_claim(
                cid,
                verifier=F3Fake(
                    resolver=lambda p: (
                        "SUPPORTS"
                        if p["evidence"]["evidence_id"] == ev_a
                        else "CONTRADICTS"
                    )
                ),
            )
            result = detect_claim_conflicts(
                cid, detector=FakeDetector(conflict_type="CONTRADICTION")
            )
            assert result["items"][0]["status"] == "confirmed"
            rows = list_conflicts(run_id)
            assert len(rows) == 1
            assert rows[0]["conflict_type"] == "CONTRADICTION"
            assert rows[0]["genuine"] is True
            summary = claim_conflict_summary(cid)
            assert summary["has_confirmed_conflict"] is True
            chain = conflict_chain(rows[0]["conflict_id"])
            assert chain["claim"]["claim_id"] == cid
            assert chain["detector_spec"]["provider"] == "fake"
            # 清理 CASCADE
            with rstore.get_store().transaction() as tx:
                tx.execute("DELETE FROM research_runs WHERE run_id = %s", (run_id,))
            assert list_conflicts(run_id) == []
        finally:
            self._cleanup()

    def test_integrity_and_idempotency(self):
        self._bind()
        try:
            run_id, sqid = self._run()
            ev_a = self._web(run_id, sqid, "body A", "https://pg.example/ca")
            cid = registry.create_claim(run_id, sqid, "PG claim", ClaimType.FACT)
            registry.bind_claim_evidence(run_id, cid, ev_a)
            other_run, other_sq = self._run("other")
            ev_b = self._web(other_run, other_sq, "body B", "https://pg.example/cb")
            with pytest.raises(ConflictIntegrityError):
                detect_claim_conflicts(cid, explicit_pairs=[(ev_a, ev_b)])

            # 完整幂等
            run2, sq2 = self._run("idem")
            e1 = self._web(run2, sq2, "s", "https://pg.example/i1")
            e2 = self._web(run2, sq2, "c", "https://pg.example/i2")
            c2 = registry.create_claim(run2, sq2, "PG idem", ClaimType.FACT)
            registry.bind_claim_evidence(run2, c2, e1)
            registry.bind_claim_evidence(run2, c2, e2)
            f3verify.semantic_verify_claim(
                c2,
                verifier=F3Fake(
                    resolver=lambda p: (
                        "SUPPORTS"
                        if p["evidence"]["evidence_id"] == e1
                        else "CONTRADICTS"
                    )
                ),
            )
            detect_claim_conflicts(c2, detector=FakeDetector())
            detect_claim_conflicts(c2, detector=FakeDetector())
            assert len(list_conflicts(run2)) == 1
            detect_claim_conflicts(
                c2,
                detector=FakeDetector(),
                spec=VerifierSpec(model="pg-det2", prompt_version="v2"),
            )
            assert len(list_conflicts(run2)) == 2
            with rstore.get_store().transaction() as tx:
                tx.execute(
                    "DELETE FROM research_runs WHERE run_id IN (%s, %s, %s)",
                    (run_id, run2, other_run),
                )
        finally:
            self._cleanup()
