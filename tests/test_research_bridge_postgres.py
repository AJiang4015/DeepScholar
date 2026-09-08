"""F7 Research State Bridge — PostgreSQL 镜像（RESEARCH_DSN_TEST 门控，独立测试库）。

与 SQLite 套件同契约：materialization（含 quote citation / unanchored）、F2–F6 enabled 全链
走既有 public API、disabled → skipped_off 无伪 artifact、finalization identity（同代复用 /
改 final_content / 改 evidence universe → 新 generation）、run.metadata.finalizations 只写该键。
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

from app.research import migrations, provenance, registry, store as rstore  # noqa: E402
from app.research.bridge import (  # noqa: E402
    finalize_run,
    get_run_research_state,
    run_finalization_history,
)
from app.research.conflict import FakeDetector as F4Detector  # noqa: E402
from app.research.extractor import FakeExtractor  # noqa: E402
from app.research.normalize import canonical_key_for  # noqa: E402
from app.research.verify import FakeVerifier as F3Fake  # noqa: E402


@needs_pg
class TestResearchBridgePostgres:
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

    def _run(self, prefix="bpg"):
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

    def _ok(self, *claims):
        return FakeExtractor(claims=list(claims))

    def _cand(self, statement, ctype="FACT", evidence=None):
        c = {"statement": statement, "claim_type": ctype}
        if evidence is not None:
            c["evidence"] = evidence
        return c

    def test_no_new_migration_and_materialization(self):
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
            run_id, sqid = self._run("bpgmat")
            ev_a = self._web(
                run_id, sqid, "https://a-pg.example/1", "PA", "content alpha pg"
            )
            ev_b = self._web(
                run_id, sqid, "https://b-pg.example/2", "PB", "content beta pg"
            )
            res = finalize_run(
                run_id,
                "PG final text",
                extractor=self._ok(
                    self._cand(
                        "PG 断言 A",
                        "FACT",
                        [{"evidence_id": ev_a, "quote": "content alpha pg"}],
                    ),
                    self._cand("PG 断言 B", "STATISTIC", [{"evidence_id": ev_b}]),
                    self._cand("PG 无锚断言", "FACT", [{"evidence_id": "missing-ev"}]),
                ),
            )
            assert res["ok"] is True
            state = res["state"]
            assert state["claims_total"] == 3
            assert state["claims_unanchored"] == 1
            assert state["pipeline"]["f2"]["status"] == "executed"
            assert state["pipeline"]["f3"]["status"] == "skipped_off"
            # quote 子串 → citation 建立
            claims = provenance.list_claims(run_id)
            by_statement = {c.statement: c for c in claims}
            chain_a = provenance.get_claim_chain(by_statement["PG 断言 A"].claim_id)
            assert (
                chain_a["citations"]
                and chain_a["citations"][0]["quote"] == "content alpha pg"
            )
            # run.metadata.finalizations 只写该键
            row = rstore.get_store().execute(
                "SELECT metadata FROM research_runs WHERE run_id=%s", (run_id,)
            )[0]
            meta = (
                row["metadata"]
                if isinstance(row["metadata"], dict)
                else json.loads(row["metadata"])
            )
            assert "research_finalizations" in meta
            assert len(meta["research_finalizations"]) == 1
        finally:
            self._cleanup()

    def test_enabled_full_chain_and_identity(self):
        self._bind()
        try:
            run_id, sqid = self._run("bpgchain")
            ev_a = self._web(
                run_id, sqid, "https://ca-pg.example/1", "CA", "content ca pg"
            )
            ev_b = self._web(
                run_id, sqid, "https://cb-pg.example/2", "CB", "content cb pg"
            )
            ex = self._ok(
                self._cand(
                    "PG 争点断言",
                    "FACT",
                    [{"evidence_id": ev_a}, {"evidence_id": ev_b}],
                )
            )
            r1 = finalize_run(
                run_id,
                "PG same content",
                extractor=ex,
                f3_enabled=True,
                f3_verifier=F3Fake(
                    resolver=lambda p: {
                        ev_a: "SUPPORTS",
                        ev_b: "CONTRADICTS",
                    }.get(p["evidence"]["evidence_id"], "ABSTAIN")
                ),
                f4_enabled=True,
                f4_detector=F4Detector(conflict_type="CONTRADICTION"),
                f5_enabled=True,
                f6_enabled=True,
            )
            assert r1["ok"] is True
            state = r1["state"]
            for stage in ("f2", "f3", "f4", "f5", "f6"):
                assert state["pipeline"][stage]["status"] == "executed"
            assert state["contested_claims"] == 1
            assert state["unresolved_claims"] == 1
            # 同代复用
            r2 = finalize_run(
                run_id,
                "PG same content",
                extractor=ex,
                f3_enabled=True,
                f4_enabled=True,
                f5_enabled=True,
                f6_enabled=True,
            )
            assert r2["state"].get("reused_generation") is True
            assert len(run_finalization_history(run_id)) == 1
            # 改 final_content → 新 generation
            r3 = finalize_run(
                run_id,
                "PG changed content!!",
                extractor=ex,
                f3_enabled=True,
                f4_enabled=True,
                f5_enabled=True,
                f6_enabled=True,
            )
            assert r3["generation"]["fingerprint"] != r1["generation"]["fingerprint"]
            assert len(run_finalization_history(run_id)) == 2
            # 改 evidence universe → 新 generation
            self._web(
                run_id, sqid, "https://cc-pg.example/3", "CC", "content cc pg extra"
            )
            r4 = finalize_run(
                run_id,
                "PG changed content!!",
                extractor=ex,
                f3_enabled=True,
                f4_enabled=True,
                f5_enabled=True,
                f6_enabled=True,
            )
            assert r4["generation"]["fingerprint"] != r3["generation"]["fingerprint"]
        finally:
            self._cleanup()

    def test_disabled_skipped_and_verdict_malformed(self):
        self._bind()
        try:
            run_id, sqid = self._run("bpgsk")
            ev_a = self._web(
                run_id, sqid, "https://s-pg.example/1", "S", "content s pg"
            )
            res = finalize_run(
                run_id,
                "text",
                extractor=self._ok(
                    self._cand("PG 断言", "FACT", [{"evidence_id": ev_a}])
                ),
            )
            state = res["state"]
            assert state["claims_total"] == 1
            for stage in ("f3", "f4", "f5", "f6"):
                assert state["pipeline"][stage]["status"] == "skipped_off"
            assert state["verified_claims"] is None
            assert state["unresolved_claims"] is None
            for table in (
                "verifications",
                "conflicts",
                "corroborations",
                "reconciliations",
            ):
                n = rstore.get_store().execute(
                    f"SELECT count(*) AS n FROM {table} WHERE run_id=%s", (run_id,)
                )[0]["n"]
                assert n == 0
            # verdict 键 → malformed（extractor 边界）
            run2, sq2 = self._run("bpgext")
            ev_b = self._web(run2, sq2, "https://e-pg.example/1", "E", "content e pg")
            bad = FakeExtractor(
                claims=[
                    {
                        "statement": "X",
                        "claim_type": "FACT",
                        "verdict": "SUPPORTS",
                        "evidence": [{"evidence_id": ev_b}],
                    }
                ]
            )
            r2 = finalize_run(run2, "text", extractor=bad)
            assert r2["state"]["claims_total"] == 0
            assert r2["generation"]["meta"]["extractor_ok"] is False
        finally:
            self._cleanup()

    def test_invalid_quote_and_store_metadata_preserved(self):
        self._bind()
        try:
            run_id, sqid = self._run("bpgquote")
            ev_a = self._web(
                run_id, sqid, "https://q-pg.example/1", "Q", "content q pg"
            )
            res = finalize_run(
                run_id,
                "text",
                extractor=self._ok(
                    self._cand(
                        "PG quote 断言",
                        "FACT",
                        [{"evidence_id": ev_a, "quote": "not a substring"}],
                    )
                ),
            )
            assert res["state"]["claims_unanchored"] == 1
            claim = provenance.list_claims(run_id)[0]
            assert claim.metadata.get("unanchored") is True
            assert provenance.get_claim_chain(claim.claim_id)["evidences"] == []
            # metadata 其它键保留
            with rstore.get_store().transaction() as tx:
                tx.execute(
                    "UPDATE research_runs SET metadata=%s WHERE run_id=%s",
                    (json.dumps({"keep_me": True}), run_id),
                )
            from app.research.bridge import record_finalization_generation

            record_finalization_generation(
                rstore.get_store(), run_id, {"fingerprint": "g1"}
            )
            row = rstore.get_store().execute(
                "SELECT metadata FROM research_runs WHERE run_id=%s", (run_id,)
            )[0]
            meta = (
                row["metadata"]
                if isinstance(row["metadata"], dict)
                else json.loads(row["metadata"])
            )
            assert meta["keep_me"] is True
            assert meta["research_finalizations"][0]["fingerprint"] == "g1"
            assert get_run_research_state(run_id)["run_id"] == run_id
            assert get_run_research_state("no-such-run") is None
        finally:
            self._cleanup()
