"""F5 Independent Evidence Corroboration — PostgreSQL 镜像（RESEARCH_DSN_TEST 门控，独立测试库）。

与 SQLite 套件同契约：global clustering（R-DOMAIN/R-TITLE/R-SHINGLE、union-find chaining）、
计数（4 sources/3 independent）、cross-side independence、reviewer 失败→complete+review_failed、
gate 失败→failed、fingerprint 幂等、source_profile 无 score 字段。
"""

import json
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
from app.research.corroboration import (  # noqa: E402
    BaseReviewer,
    CorroborationMethod,
    FakeReviewer,
    ReviewerRuntimeError,
    compute_claim_corroboration,
    get_corroboration,
)
from app.research.normalize import canonical_key_for  # noqa: E402
from app.research.schemas import ClaimType  # noqa: E402
from app.research.verify import FakeVerifier as F3Fake  # noqa: E402
from app.research import verify as f3verify  # noqa: E402


@needs_pg
class TestCorroborationPostgres:
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

    def _run(self, prefix="cpg"):
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

    def test_migration_schema_and_zero_alter(self):
        self._bind()
        try:
            assert sorted(
                migrations.applied_migration_versions(rstore.get_store())
            ) == ["0001", "0002", "0003", "0004", "0005", "0006"]
            rows = rstore.get_store().execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='corroborations'"
            )
            names = {r["column_name"] for r in rows}
            assert {
                "corroboration_id",
                "run_id",
                "claim_id",
                "method_spec",
                "method_fingerprint",
                "status",
                "error",
                "computed_at",
                "metadata",
                "global_clusters",
                "support",
                "contradict",
                "conflicts_independence",
                "source_profile",
            } <= names
            # UNIQUE (run_id, claim_id, method_fingerprint) 约束存在
            idx = rstore.get_store().execute(
                "SELECT indexdef FROM pg_indexes WHERE tablename='corroborations'"
            )
            assert any("method_fingerprint" in i["indexdef"] for i in idx)
        finally:
            self._cleanup()

    def test_counting_4_sources_2_shared_and_reprint(self):
        self._bind()
        try:
            run_id, sqid = self._run("cpgcnt")
            ev_a = self._web(
                run_id, sqid, "https://pa-pg.example/2026/a", "PG 同标题 T", "x" * 30
            )
            ev_b = self._web(
                run_id, sqid, "https://pb-pg.example/2026/b", "PG 同标题 T", "x" * 30
            )
            ev_c = self._web(
                run_id, sqid, "https://pc-pg.example/1", "PG 标题 C 独立", "y" * 30
            )
            ev_d = self._web(
                run_id, sqid, "https://pd-pg.example/1", "PG 标题 D 独立", "z" * 30
            )
            cid = registry.create_claim(
                run_id, sqid, "PG 4 源计数 claim", ClaimType.FACT
            )
            self._verify(
                run_id,
                cid,
                {
                    ev_a: "SUPPORTS",
                    ev_b: "SUPPORTS",
                    ev_c: "SUPPORTS",
                    ev_d: "SUPPORTS",
                },
            )
            res = compute_claim_corroboration(cid)
            assert res["status"] == "complete"
            assert res["support"]["source_count"] == 4
            assert res["support"]["independent_count"] == 3
            assert len(res["contradict"]["cluster_ids"]) == 0
            assert res["metadata"] == {}
        finally:
            self._cleanup()

    def test_shingle_reprint_and_chaining(self):
        """R-SHINGLE 转载聚类 + A≈B、B≈C、A≉C union-find chaining（PG 同语义）。"""
        self._bind()
        try:
            run_id, sqid = self._run("cpgsh")
            body = "同文转载正文 shingle 判定。同文转载正文 shingle 判定。同文转载正文 shingle 判定。"
            ev_x = self._web(run_id, sqid, "https://x-pg.example/a", "标题 X", body)
            ev_y = self._web(run_id, sqid, "https://y-pg.example/b", "标题 Y", body)
            cid = registry.create_claim(run_id, sqid, "PG 转载 claim", ClaimType.FACT)
            self._verify(run_id, cid, {ev_x: "SUPPORTS", ev_y: "SUPPORTS"})
            res = compute_claim_corroboration(cid)
            assert len(res["global_clusters"]) == 1
            assert len(res["global_clusters"][0]["member_source_ids"]) == 2

            # chaining：A/B 同域名同 path 前缀；B/C 同标题（不同域）
            run2, sq2 = self._run("cpgch")
            ev_a = self._web(
                run2,
                sq2,
                "https://news-pg.example/sec/2026/01/a",
                "标题 Alpha",
                "1" * 30,
            )
            ev_b = self._web(
                run2,
                sq2,
                "https://news-pg.example/sec/2026/01/b",
                "转载标题 Gamma",
                "2" * 30,
            )
            ev_c = self._web(
                run2,
                sq2,
                "https://mirror-pg.example/2026/c",
                "转载标题 Gamma",
                "3" * 30,
            )
            cid2 = registry.create_claim(run2, sq2, "PG chain claim", ClaimType.FACT)
            self._verify(
                run2,
                cid2,
                {ev_a: "SUPPORTS", ev_b: "SUPPORTS", ev_c: "SUPPORTS"},
            )
            res2 = compute_claim_corroboration(cid2)
            assert len(res2["global_clusters"]) == 1
            assert len(res2["global_clusters"][0]["member_source_ids"]) == 3
        finally:
            self._cleanup()

    def test_cross_side_independence_and_shared_cluster(self):
        """转载跨两侧（same cluster）+ F4 confirmed 冲突 → independent_between_sides 语义。"""
        self._bind()
        try:
            from app.research.conflict import FakeDetector, detect_claim_conflicts

            # 同簇（转载标题相同）：independent=false
            run1, sq1 = self._run("cpgx1")
            ev_a = self._web(
                run1, sq1, "https://pos-pg.example/a", "转载标题 Z", "p" * 20
            )
            ev_b = self._web(
                run1, sq1, "https://neg-pg.example/b", "转载标题 Z", "n" * 20
            )
            cid = registry.create_claim(run1, sq1, "PG 同簇冲突", ClaimType.FACT)
            self._verify(run1, cid, {ev_a: "SUPPORTS", ev_b: "CONTRADICTS"})
            detect_claim_conflicts(
                cid, detector=FakeDetector(conflict_type="CONTRADICTION")
            )
            res = compute_claim_corroboration(cid)
            assert res["global_clusters"][0]["member_side"] == "both"
            item = res["conflicts_independence"][0]
            assert item["independent_between_sides"] is False
            assert item["side_a"]["cluster_ids"] == item["side_b"]["cluster_ids"]

            # 独立簇：independent=true
            run2, sq2 = self._run("cpgx2")
            ev_c = self._web(
                run2, sq2, "https://pos2-pg.example/a", "正面标题 2", "p2" * 20
            )
            ev_d = self._web(
                run2, sq2, "https://neg2-pg.example/b", "负面标题 2", "n2" * 20
            )
            cid2 = registry.create_claim(run2, sq2, "PG 独立簇冲突", ClaimType.FACT)
            self._verify(run2, cid2, {ev_c: "SUPPORTS", ev_d: "CONTRADICTS"})
            detect_claim_conflicts(
                cid2, detector=FakeDetector(conflict_type="CONTRADICTION")
            )
            res2 = compute_claim_corroboration(cid2)
            assert len(res2["global_clusters"]) == 2
            assert (
                res2["conflicts_independence"][0]["independent_between_sides"] is True
            )
        finally:
            self._cleanup()

    def test_review_failure_and_fingerprint_idempotency(self):
        self._bind()
        try:
            run_id, sqid = self._run("cpgrev")
            ev_a = self._web(
                run_id, sqid, "https://a-pg.example/1", "A 独立标题 pg", "a" * 20
            )
            ev_b = self._web(
                run_id, sqid, "https://b-pg.example/2", "B 独立标题 pg", "b" * 20
            )
            cid = registry.create_claim(run_id, sqid, "PG review claim", ClaimType.FACT)
            self._verify(run_id, cid, {ev_a: "SUPPORTS", ev_b: "SUPPORTS"})

            method = CorroborationMethod(review_enabled=True, max_review_pairs=5)

            class _Broken(BaseReviewer):
                def respond(self, payload, hint=""):
                    raise ReviewerRuntimeError("provider", "pg review outage")

            res = compute_claim_corroboration(cid, method=method, reviewer=_Broken())
            assert res["status"] == "complete"
            assert res["metadata"]["review_failed"] is True
            assert res["support"]["independent_count"] == 2

            # 同指纹幂等：第二次调用返回同一 artifact，无新行
            # （reviewer 非 identity 成分：即使换成 same=True 也不产生新行）
            again = compute_claim_corroboration(cid, method=method, reviewer=_Broken())
            assert again["corroboration_id"] == res["corroboration_id"]
            again_ok = compute_claim_corroboration(
                cid, method=method, reviewer=FakeReviewer(same=True)
            )
            assert again_ok["corroboration_id"] == res["corroboration_id"]

            # review merged 路径（新 claim，同方法指纹）：确定性未归并的跨域对经 review 合并
            run2, sq2 = self._run("cpgmv")
            ev_c = self._web(
                run2, sq2, "https://c-pg.example/1", "C 独立标题 pg", "c" * 20
            )
            ev_d = self._web(
                run2, sq2, "https://d-pg.example/2", "D 独立标题 pg", "d" * 20
            )
            cid2 = registry.create_claim(run2, sq2, "PG merge claim", ClaimType.FACT)
            self._verify(run2, cid2, {ev_c: "SUPPORTS", ev_d: "SUPPORTS"})
            merged = compute_claim_corroboration(
                cid2, method=method, reviewer=FakeReviewer(same=True)
            )
            assert merged["corroboration_id"] != res["corroboration_id"]
            assert merged["status"] == "complete"
            assert merged["metadata"]["review_merged"] >= 1
            assert len(merged["global_clusters"]) == 1
            # 只读回读需带同一 method 指纹；默认指纹未计算 → None
            assert get_corroboration(cid, method=method) is not None
            assert get_corroboration(cid) is None
        finally:
            self._cleanup()

    def test_gate_error_failed_then_complete_and_no_score_fields(self):
        self._bind()
        try:
            store = rstore.get_store()
            run_a, sq_a = self._run("cpg-a")
            run_b, sq_b = self._run("cpg-b")
            ev = self._web(run_a, sq_a, "https://g-pg.example/1", "G 标题", "g" * 20)
            cid = registry.create_claim(run_a, sq_a, "PG gate claim", ClaimType.FACT)
            self._verify(run_a, cid, {ev: "SUPPORTS"})
            e_b = self._web(run_b, sq_b, "https://h-pg.example/1", "H 标题", "h" * 20)
            with store.transaction() as tx:
                tx.execute(
                    "INSERT INTO claim_evidences (binding_id, run_id, claim_id, evidence_id,"
                    " created_at, metadata) VALUES (%s,%s,%s,%s,%s,%s)",
                    (
                        "cpg-gate-cross",
                        run_a,
                        cid,
                        e_b,
                        "2026-01-01T00:00:00+00:00",
                        "{}",
                    ),
                )
            failed = compute_claim_corroboration(cid)
            assert failed["status"] == "failed"
            assert failed["error"] and "gate" in failed["error"]
            with store.transaction() as tx:
                tx.execute(
                    "DELETE FROM claim_evidences WHERE binding_id=%s",
                    ("cpg-gate-cross",),
                )
            recovered = compute_claim_corroboration(cid)
            assert recovered["status"] == "complete"
            assert recovered["corroboration_id"] == failed["corroboration_id"]
            # source_profile 仅 descriptive：无 authority/reliability/confidence 等字段
            blob = json.dumps(recovered, ensure_ascii=False).lower()
            for token in (
                "authority",
                "reliability",
                "confidence",
                "truth_probability",
            ):
                assert token not in blob
            assert "winner" not in blob
            assert '"score"' not in blob
        finally:
            self._cleanup()
