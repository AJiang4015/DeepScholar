"""F6 Conflict Reconciliation — PostgreSQL 镜像（RESEARCH_DSN_TEST 门控，独立测试库）。

与 SQLite 套件同契约：0006 schema/幂等、三种 outcome、Unknown≠NotIndependent 硬红线
（缺 corroboration → failed；F5 派生信号与 cluster 交集矛盾 → failed）、register
（precedence / unverified / incomplete_input / verified_consistent）、review 不改
outcome 且失败→complete+review_failed、fingerprint 幂等/升级、run_unresolved。
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

from app.research import migrations, registry, store as rstore  # noqa: E402
from app.research.conflict import FakeDetector as F4Detector  # noqa: E402
from app.research.conflict import detect_claim_conflicts  # noqa: E402
from app.research.corroboration import compute_claim_corroboration  # noqa: E402
from app.research.normalize import canonical_key_for  # noqa: E402
from app.research.reconciliation import (  # noqa: E402
    FakeReviewer,
    ReconciliationMethod,
    ReviewerRuntimeError,
    conflict_register,
    list_reconciliations,
    reconcile_claim_conflicts,
    reconcile_run_conflicts,
    run_unresolved,
)
from app.research.schemas import ClaimType  # noqa: E402
from app.research.verify import FakeVerifier as F3Fake  # noqa: E402
from app.research import verify as f3verify  # noqa: E402

_GENUINE_TYPES = {"CONTRADICTION", "INCONSISTENCY"}


@needs_pg
class TestReconciliationPostgres:
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

    def _run(self, prefix="rpg"):
        run_id = f"{prefix}-{uuid.uuid4().hex[:10]}"
        run_id, sqid = registry.create_run_and_root(
            "pg-thread", f"{prefix} 问题?", run_id=run_id
        )
        return run_id, sqid

    def _web(self, run_id, sqid, url, title, content):
        qid = registry.record_search_query(
            run_id, sqid, agent="network_search", tool="s", query="q"
        )
        sid = registry.upsert_source(
            run_id,
            qid,
            source_type="web",
            agent="network_search",
            title=title,
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

    def _verify(self, run_id, cid, verdict_map):
        for eid in verdict_map:
            registry.bind_claim_evidence(run_id, cid, eid)
        f3verify.semantic_verify_claim(
            cid,
            verifier=F3Fake(
                resolver=lambda p: verdict_map.get(
                    p["evidence"]["evidence_id"], "ABSTAIN"
                )
            ),
        )

    def _confirm(self, cid, ev_a, ev_b, ctype):
        detect_claim_conflicts(
            cid,
            explicit_pairs=[(ev_a, ev_b)],
            detector=F4Detector(
                resolver=lambda p: {
                    "genuine": ctype in _GENUINE_TYPES,
                    "conflict_type": ctype,
                }
            ),
        )

    def _two(self, prefix, ctype="CONTRADICTION", same_cluster=False):
        run_id, sqid = self._run(prefix)
        if same_cluster:
            title_a = title_b = "PG 同源标题"
        else:
            title_a, title_b = "PG 正面标题", "PG 负面标题"
        ev_a = self._web(
            run_id, sqid, f"https://a-{prefix}.example/1", title_a, "a" * 30
        )
        ev_b = self._web(
            run_id, sqid, f"https://b-{prefix}.example/2", title_b, "b" * 30
        )
        cid = registry.create_claim(run_id, sqid, f"PG F6 {prefix}", ClaimType.FACT)
        self._verify(run_id, cid, {ev_a: "SUPPORTS", ev_b: "CONTRADICTS"})
        self._confirm(cid, ev_a, ev_b, ctype)
        compute_claim_corroboration(cid)
        return run_id, cid

    def test_migration_schema_and_outcomes(self):
        self._bind()
        try:
            assert sorted(
                migrations.applied_migration_versions(rstore.get_store())
            ) == ["0001", "0002", "0003", "0004", "0005", "0006"]
            rows = rstore.get_store().execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='reconciliations'"
            )
            names = {r["column_name"] for r in rows}
            assert {
                "reconciliation_id",
                "run_id",
                "claim_id",
                "conflict_id",
                "method_fingerprint",
                "status",
                "outcome",
                "detail",
                "error",
                "computed_at",
                "metadata",
            } <= names
            idx = rstore.get_store().execute(
                "SELECT indexdef FROM pg_indexes WHERE tablename='reconciliations'"
            )
            assert any("method_fingerprint" in i["indexdef"] for i in idx)

            # SAME_ORIGIN
            _run, cid = self._two("rpgso", same_cluster=True)
            res = reconcile_claim_conflicts(cid)
            assert res["items"][0]["outcome"] == "SAME_ORIGIN_CONTRADICTION"
            assert res["register"]["register"] == "same_origin_contradiction_only"
            # DETAIL
            _run2, cid2 = self._two("rpgdet", ctype="INCONSISTENCY")
            res2 = reconcile_claim_conflicts(cid2)
            assert res2["items"][0]["outcome"] == "DETAIL_INCONSISTENCY"
            assert res2["register"]["register"] == "detail_inconsistency"
            # GENUINE
            _run3, cid3 = self._two("rpggen")
            res3 = reconcile_claim_conflicts(cid3)
            assert res3["items"][0]["outcome"] == "GENUINE_CONTESTED"
            assert res3["register"]["register"] == "genuine_contested"
        finally:
            self._cleanup()

    def test_missing_corroboration_fails(self):
        """缺 corroboration → failed（Unknown ≠ Not Independent），PG 同语义。"""
        self._bind()
        try:
            run_id, sqid = self._run("rpgno")
            ev_a = self._web(run_id, sqid, "https://na-pg.example/1", "NA", "a" * 30)
            ev_b = self._web(run_id, sqid, "https://nb-pg.example/2", "NB", "b" * 30)
            cid = registry.create_claim(run_id, sqid, "PG no-corr", ClaimType.FACT)
            self._verify(run_id, cid, {ev_a: "SUPPORTS", ev_b: "CONTRADICTS"})
            self._confirm(cid, ev_a, ev_b, "CONTRADICTION")
            # 不跑 compute_claim_corroboration
            res = reconcile_claim_conflicts(cid)
            assert res["items"][0]["status"] == "failed"
            assert res["items"][0]["outcome"] is None
            assert "corroboration" in res["items"][0]["error"]
            assert res["register"]["register"] == "incomplete_input"
        finally:
            self._cleanup()

    def test_register_and_review_boundary(self):
        self._bind()
        try:
            # unverified：无 SUPPORTS
            run_id, sqid = self._run("rpgunv")
            ev = self._web(run_id, sqid, "https://un-pg.example/1", "U", "u" * 30)
            cid = registry.create_claim(run_id, sqid, "PG unverified", ClaimType.FACT)
            self._verify(run_id, cid, {ev: "ABSTAIN"})
            assert conflict_register(cid)["register"] == "unverified"

            # verified_consistent：SUPPORTS + 无 confirmed 冲突
            run2, sq2 = self._run("rpgvc")
            ev2 = self._web(run2, sq2, "https://vc-pg.example/1", "V", "v" * 30)
            cid2 = registry.create_claim(run2, sq2, "PG verified", ClaimType.FACT)
            self._verify(run2, cid2, {ev2: "SUPPORTS"})
            assert conflict_register(cid2)["register"] == "verified_consistent"

            # review 失败 → complete + review_failed；outcome 不变
            method = ReconciliationMethod(review_enabled=True, max_review_pairs=1)
            reviewer = FakeReviewer(
                queue=[
                    ReviewerRuntimeError("provider", "pg review outage"),
                    ReviewerRuntimeError("provider", "pg review outage"),
                ]
            )
            _run3, cid3 = self._two("rpgrv")
            res3 = reconcile_claim_conflicts(cid3, method=method, reviewer=reviewer)
            item3 = res3["items"][0]
            assert item3["status"] == "complete"
            assert item3["outcome"] == "GENUINE_CONTESTED"
            assert item3["metadata"]["review_failed"] is True
        finally:
            self._cleanup()

    def test_idempotency_and_run_unresolved(self):
        self._bind()
        try:
            run_id, cid = self._two("rpgidem")
            first = reconcile_claim_conflicts(cid)
            again = reconcile_claim_conflicts(cid)
            assert (
                again["items"][0]["reconciliation_id"]
                == first["items"][0]["reconciliation_id"]
            )
            rows = rstore.get_store().execute(
                "SELECT count(*) AS n FROM reconciliations WHERE run_id=%s AND claim_id=%s",
                (run_id, cid),
            )
            assert rows[0]["n"] == 1
            upgraded = reconcile_claim_conflicts(
                cid, method=ReconciliationMethod(max_conflicts_per_claim=10)
            )
            assert (
                upgraded["items"][0]["method_fingerprint"]
                != first["items"][0]["method_fingerprint"]
            )
            rows = rstore.get_store().execute(
                "SELECT count(*) AS n FROM reconciliations WHERE run_id=%s AND claim_id=%s",
                (run_id, cid),
            )
            assert rows[0]["n"] == 2
            # run_unresolved 只含 genuine_contested（run 级按默认 method 计算）
            summary = reconcile_run_conflicts(run_id)
            assert summary["genuine_contested"] == 1
            unresolved = run_unresolved(run_id)
            assert {c["claim_id"] for c in unresolved["unresolved_claims"]} == {cid}
            listed = list_reconciliations(run_id)
            assert len(listed) == 2
            assert (
                listed[0]["metadata"]["f4_signal"]["conflict_type"] == "CONTRADICTION"
            )
            assert (
                listed[0]["metadata"]["f5_signal"]["independent_between_sides"] is True
            )
        finally:
            self._cleanup()
