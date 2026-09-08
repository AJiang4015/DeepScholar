"""F3 Semantic Verification — PostgreSQL 镜像（RESEARCH_DSN_TEST 门控，独立测试库）。"""

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
from app.research.verify import (  # noqa: E402
    DEFAULT_SPEC,
    FakeVerifier,
    VerifierRuntimeError,
    VerifierSpec,
    aggregate_claim_verdict,
    list_verifications,
    semantic_verify_run,
    verification_chain,
)


@needs_pg
class TestSemanticVerifyPostgres:
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

    def _run(self, prefix="vpg"):
        run_id = f"{prefix}-{uuid.uuid4().hex[:10]}"
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
            title="PG source",
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

    def test_migration_schema(self):
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
            rows = rstore.get_store().execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'verifications'"
            )
            names = {r["column_name"] for r in rows}
            assert {
                "verification_id",
                "run_id",
                "claim_id",
                "evidence_id",
                "verifier_spec",
                "verifier_fingerprint",
                "verdict",
                "status",
                "error",
                "confidence",
            } <= names
        finally:
            self._cleanup()

    def test_full_flow_conflict_aggregation(self):
        self._bind()
        try:
            run_id, sqid = self._run()
            e_sup = self._web(run_id, sqid, "PG 支持内容")
            e_con = self._web(run_id, sqid, "PG 反对内容")
            cid = registry.create_claim(run_id, sqid, "PG claim X", ClaimType.FACT)
            registry.bind_claim_evidence(run_id, cid, e_sup)
            registry.bind_claim_evidence(run_id, cid, e_con)

            def resolver(payload):
                return (
                    "SUPPORTS"
                    if payload["evidence"]["evidence_id"] == e_sup
                    else "CONTRADICTS"
                )

            summary = semantic_verify_run(
                run_id, verifier=FakeVerifier(resolver=resolver)
            )
            assert summary["succeeded"] == 2
            assert summary["failed"] == 0
            agg = aggregate_claim_verdict(cid)
            assert agg["aggregate"] == "contested"
            assert agg["contested"] is True
            rows = list_verifications(run_id)
            assert len(rows) == 2
            assert all(r["status"] == "succeeded" for r in rows)
            # provenance 链
            chain = verification_chain(cid)
            assert len(chain["verifications"]) == 2
            assert chain["verifications"][0]["verifier_spec"].get("provider") == "fake"
            # 清理（CASCADE）
            with rstore.get_store().transaction() as tx:
                tx.execute("DELETE FROM research_runs WHERE run_id = %s", (run_id,))
            assert list_verifications(run_id) == []
        finally:
            self._cleanup()

    def test_idempotency_and_failure(self):
        self._bind()
        try:
            run_id, sqid = self._run()
            eid = self._web(run_id, sqid, "PG body")
            cid = registry.create_claim(run_id, sqid, "PG claim", ClaimType.FACT)
            registry.bind_claim_evidence(run_id, cid, eid)
            semantic_verify_run(run_id, verifier=FakeVerifier())
            semantic_verify_run(run_id, verifier=FakeVerifier())  # 同 spec → 复用
            assert len(list_verifications(run_id)) == 1
            # spec 改变 → 新行
            spec2 = VerifierSpec(model="pg-model-2")
            semantic_verify_run(run_id, verifier=FakeVerifier(), spec=spec2)
            rows = list_verifications(run_id)
            assert len(rows) == 2
            assert {r["verifier_fingerprint"] for r in rows} == {
                DEFAULT_SPEC.fingerprint(),
                spec2.fingerprint(),
            }
            # 单条失败：verdict NULL
            e2 = self._web(run_id, sqid, "PG bad body")
            c2 = registry.create_claim(run_id, sqid, "PG bad claim", ClaimType.FACT)
            registry.bind_claim_evidence(run_id, c2, e2)

            class _Broken(FakeVerifier):
                def respond(self, payload, hint=""):
                    raise VerifierRuntimeError("provider", "down")

            semantic_verify_run(run_id, verifier=_Broken())
            bad = [r for r in list_verifications(run_id, claim_id=c2)]
            assert bad and bad[0]["status"] == "failed" and bad[0]["verdict"] is None
            # 清理
            with rstore.get_store().transaction() as tx:
                tx.execute("DELETE FROM research_runs WHERE run_id = %s", (run_id,))
        finally:
            self._cleanup()
