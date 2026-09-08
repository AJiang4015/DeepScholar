"""F3 Semantic Verification — SQLite 全量测试（rev2 contract）。

覆盖：schema/状态机、Gate、候选与 coverage 优先级、allowed 覆盖、context 边界、
FakeVerifier 各 verdict/失败路径/retry、low-confidence→ABSTAIN policy、幂等、冲突标记、
聚合、batch partial、provenance。
"""

import os
import sqlite3

import pytest

from app.research import registry
from app.research.config import RESEARCH_DB_ENV
from app.research.schemas import ClaimType, SemanticVerdict, VerifyStatus
from app.research import migrations, verify
from app.research.verify import (
    FakeVerifier,
    VerificationGateError,
    VerifierRuntimeError,
    VerifyBudget,
    VerifierSpec,
    aggregate_claim_verdict,
    build_evidence_context,
    list_verifications,
    semantic_verify_claim,
    semantic_verify_run,
    verification_chain,
)
from tests._f2_helpers import add_web_evidence, make_run


def _claim(run_id, sqid, statement, ctype=ClaimType.FACT):
    return registry.create_claim(run_id, sqid, statement, ctype)


def _ev(run_id, sqid, content, url=None):
    url = url or f"https://x.example/{abs(hash(content)) % 100000}"
    return add_web_evidence(run_id, sqid, content, url)[2]


class TestSchemaAndStatus:
    def test_migration_and_table(self, research_sqlite):
        store = verify._get_store()
        assert sorted(migrations.applied_migration_versions(store)) == [
            "0001",
            "0002",
            "0003",
            "0004",
            "0005",
            "0006",
        ]
        conn = sqlite3.connect(os.environ[RESEARCH_DB_ENV])
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "verifications" in tables
        cols = {r[1] for r in conn.execute("PRAGMA table_info(verifications)")}
        conn.close()
        expected = {
            "verification_id",
            "run_id",
            "claim_id",
            "evidence_id",
            "verifier_spec",
            "verifier_fingerprint",
            "verdict",
            "rationale",
            "confidence",
            "status",
            "error",
            "created_at",
            "completed_at",
            "metadata",
        }
        assert expected <= cols

    def test_verdict_status_enums(self, research_sqlite):
        assert {v.value for v in SemanticVerdict} == {
            "SUPPORTS",
            "INSUFFICIENT",
            "CONTRADICTS",
            "UNVERIFIABLE",
            "ABSTAIN",
        }
        assert {v.value for v in VerifyStatus} == {"pending", "succeeded", "failed"}
        assert "partial" not in {v.value for v in VerifyStatus}


class TestVerdictPathsAndStatusInvariants:
    @pytest.mark.parametrize("verdict", [v.value for v in SemanticVerdict])
    def test_each_verdict_persisted_succeeded(self, research_sqlite, verdict):
        run_id, sqid = make_run()
        eid = _ev(run_id, sqid, "证据正文 ABC 支持内容")
        cid = _claim(run_id, sqid, "结论")
        registry.bind_claim_evidence(run_id, cid, eid)
        registry.create_citation(run_id, cid, eid, quote="证据正文 ABC")
        fake = FakeVerifier(default_verdict=verdict, default_confidence=0.9)
        result = semantic_verify_claim(cid, verifier=fake)
        rows = list_verifications(run_id)
        assert len(rows) == 1
        assert rows[0]["status"] == VerifyStatus.SUCCEEDED.value
        assert rows[0]["verdict"] == verdict
        assert result["aggregate"]["aggregate"] in (
            "supported",
            "partially_supported",
            "contested",
            "unverified",
        )

    def test_failed_has_null_verdict_and_no_partial(self, research_sqlite):
        run_id, sqid = make_run()
        eid = _ev(run_id, sqid, "body")
        cid = _claim(run_id, sqid, "结论")
        registry.bind_claim_evidence(run_id, cid, eid)
        fake = FakeVerifier(
            queue=[
                VerifierRuntimeError("provider", "boom"),
                VerifierRuntimeError("provider", "boom"),
            ]
        )
        semantic_verify_claim(cid, verifier=fake)
        rows = list_verifications(run_id)
        assert len(rows) == 1
        assert rows[0]["status"] == "failed"
        assert rows[0]["verdict"] is None
        assert rows[0]["error"]
        assert rows[0]["status"] not in {"partial"}


class TestGateAndCandidates:
    def _inject_cross_binding(self, run_a, sq_a, run_b, sq_b):
        cid = _claim(run_a, sq_a, "gate claim")
        e_b = _ev(run_b, sq_b, "b body")
        store = verify._get_store()
        with store.transaction() as tx:
            tx.execute(
                "INSERT INTO claim_evidences (binding_id, run_id, claim_id, evidence_id,"
                " created_at, metadata) VALUES (%s,%s,%s,%s,%s,%s)",
                ("gate-cross", run_a, cid, e_b, "2026-01-01T00:00:00+00:00", "{}"),
            )
        return cid

    def test_f2_error_blocks(self, research_sqlite):
        run_a, sq_a = make_run("a")
        run_b, sq_b = make_run("b")
        cid = self._inject_cross_binding(run_a, sq_a, run_b, sq_b)
        with pytest.raises(VerificationGateError):
            semantic_verify_claim(cid, verifier=FakeVerifier())

    def test_r10_warning_does_not_block(self, research_sqlite):
        """bound 但未引用（R10 warning）→ 仍可验证。"""
        run_id, sqid = make_run()
        eid = _ev(run_id, sqid, "bound body")
        cid = _claim(run_id, sqid, "bound unreferenced")
        registry.bind_claim_evidence(run_id, cid, eid)
        result = semantic_verify_claim(cid, verifier=FakeVerifier())
        assert result["items"][0]["status"] == "succeeded"

    def test_r3_unsupported_no_candidates_no_error(self, research_sqlite):
        run_id, sqid = make_run()
        cid = _claim(run_id, sqid, "no evidence claim")
        result = semantic_verify_claim(cid, verifier=FakeVerifier())
        assert result["items"] == []

    def test_citation_coverage_prioritization(self, research_sqlite):
        """默认候选优先 cited evidence（coverage 是 budget 策略，非必要条件）。"""
        run_id, sqid = make_run()
        e1 = _ev(run_id, sqid, "uncited body", url="https://x.example/u")
        e2 = _ev(run_id, sqid, "cited body", url="https://x.example/c")
        cid = _claim(run_id, sqid, "结论")
        registry.bind_claim_evidence(run_id, cid, e1)
        registry.bind_claim_evidence(run_id, cid, e2)
        registry.create_citation(run_id, cid, e2, quote="cited body")
        fake = FakeVerifier(default_verdict="SUPPORTS")
        semantic_verify_claim(cid, verifier=fake)
        order = [c["evidence_id"] for c in fake.calls]
        assert order == [e2, e1]  # cited 优先

    def test_allowed_evidence_ids_override(self, research_sqlite):
        run_id, sqid = make_run()
        e1 = _ev(run_id, sqid, "one body", url="https://x.example/o1")
        e2 = _ev(run_id, sqid, "two body", url="https://x.example/o2")
        cid = _claim(run_id, sqid, "结论")
        registry.bind_claim_evidence(run_id, cid, e1)
        registry.bind_claim_evidence(run_id, cid, e2)
        fake = FakeVerifier()
        result = semantic_verify_claim(cid, verifier=fake, allowed_evidence_ids=[e1])
        assert [i["evidence_id"] for i in result["items"]] == [e1]
        with pytest.raises(ValueError, match="未 binding"):
            semantic_verify_claim(cid, verifier=fake, allowed_evidence_ids=["ghost"])


class TestContextBoundary:
    def test_context_contains_only_claim_and_evidence(self, research_sqlite):
        run_id, sqid = make_run()
        eid = _ev(run_id, sqid, "真实证据正文内容")
        cid = _claim(run_id, sqid, "结论陈述")
        registry.bind_claim_evidence(run_id, cid, eid)
        seen: dict = {}

        def resolver(payload):
            seen.update(payload)
            return "SUPPORTS"

        semantic_verify_claim(cid, verifier=FakeVerifier(resolver=resolver))
        assert seen["claim"]["statement"] == "结论陈述"
        assert seen["evidence"]["content"] == "真实证据正文内容"
        assert seen["evidence"].get("quote") is None
        # 不允许 message history / 其他 claim / 会话
        assert "messages" not in seen
        assert "history" not in seen

    def test_source_meta_is_namespaced_and_not_evidentiary(self, research_sqlite):
        run_id, sqid = make_run()
        eid = _ev(run_id, sqid, "content body", url="https://example.com/a")
        cid = _claim(run_id, sqid, "S")
        registry.bind_claim_evidence(run_id, cid, eid)
        seen: dict = {}
        semantic_verify_claim(
            cid, verifier=FakeVerifier(resolver=lambda p: seen.update(p) or "SUPPORTS")
        )
        evidence = seen["evidence"]
        assert "source_meta" in evidence
        assert "title" not in evidence  # 不得平铺为证据字段
        assert evidence["source_meta"]["canonical_url"] == "https://example.com/a"

    def test_build_context_quote_used_when_present(self, research_sqlite):
        claim_row = {"claim_id": "c", "statement": "S", "claim_type": "FACT"}
        evidence_row = {
            "evidence_id": "e",
            "content": "ABC quote DEF",
            "extraction_method": "web",
        }
        ctx = build_evidence_context(claim_row, evidence_row, None, quote="quote DEF")
        assert ctx["evidence"]["quote"] == "quote DEF"


class TestFailureAndRetry:
    def test_malformed_then_ok_retries(self, research_sqlite):
        run_id, sqid = make_run()
        eid = _ev(run_id, sqid, "body")
        cid = _claim(run_id, sqid, "S")
        registry.bind_claim_evidence(run_id, cid, eid)
        fake = FakeVerifier(queue=["not-json"])  # 第 1 次 malformed，第 2 次回落正常
        result = semantic_verify_claim(cid, verifier=fake)
        assert result["items"][0]["status"] == "succeeded"

    def test_double_malformed_fails(self, research_sqlite):
        run_id, sqid = make_run()
        eid = _ev(run_id, sqid, "body")
        cid = _claim(run_id, sqid, "S")
        registry.bind_claim_evidence(run_id, cid, eid)
        fake = FakeVerifier(queue=["x", "y"])
        result = semantic_verify_claim(cid, verifier=fake)
        assert result["items"][0]["status"] == "failed"
        row = list_verifications(run_id)[0]
        assert row["verdict"] is None
        assert "malformed_output" in (row["error"] or "")

    def test_provider_error_then_ok(self, research_sqlite):
        run_id, sqid = make_run()
        eid = _ev(run_id, sqid, "body")
        cid = _claim(run_id, sqid, "S")
        registry.bind_claim_evidence(run_id, cid, eid)
        fake = FakeVerifier(queue=[VerifierRuntimeError("timeout", "slow")])
        result = semantic_verify_claim(cid, verifier=fake)
        assert result["items"][0]["status"] == "succeeded"

    def test_low_confidence_becomes_abstain_policy(self, research_sqlite):
        run_id, sqid = make_run()
        eid = _ev(run_id, sqid, "body")
        cid = _claim(run_id, sqid, "S")
        registry.bind_claim_evidence(run_id, cid, eid)
        fake = FakeVerifier(default_verdict="SUPPORTS", default_confidence=0.2)
        semantic_verify_claim(cid, verifier=fake)
        row = list_verifications(run_id)[0]
        assert row["verdict"] == "ABSTAIN"
        assert row["status"] == "succeeded"

    def test_threshold_none_keeps_verdict(self, research_sqlite):
        run_id, sqid = make_run()
        eid = _ev(run_id, sqid, "body")
        cid = _claim(run_id, sqid, "S")
        registry.bind_claim_evidence(run_id, cid, eid)
        fake = FakeVerifier(default_verdict="SUPPORTS", default_confidence=0.2)
        semantic_verify_claim(cid, verifier=fake, confidence_threshold=None)
        row = list_verifications(run_id)[0]
        assert row["verdict"] == "SUPPORTS"

    def test_partial_failure_isolated_and_batch_partial(self, research_sqlite):
        run_id, sqid = make_run()
        e1 = _ev(run_id, sqid, "good body", url="https://x.example/g")
        e2 = _ev(run_id, sqid, "bad body", url="https://x.example/b")
        c1 = _claim(run_id, sqid, "混合结论")
        c2 = _claim(run_id, sqid, "正常结论")
        registry.bind_claim_evidence(run_id, c1, e1)
        registry.bind_claim_evidence(run_id, c1, e2)
        registry.bind_claim_evidence(run_id, c2, e1)

        class _Fake(FakeVerifier):
            def respond(self, payload, hint=""):
                eid = payload["evidence"]["evidence_id"]
                if eid == e2:
                    raise VerifierRuntimeError("provider", "down")
                return super().respond(payload, hint)

        summary = semantic_verify_run(run_id, verifier=_Fake())
        assert summary["succeeded"] == 2  # c1-e1 + c2-e1
        assert summary["failed"] == 1  # c1-e2
        assert summary["partial"] is True  # partial 仅 batch 层
        statuses = {r["status"] for r in list_verifications(run_id)}
        assert statuses == {"succeeded", "failed"}
        assert "partial" not in statuses  # 单条无 partial


class TestIdempotency:
    def test_same_fingerprint_reuses(self, research_sqlite):
        run_id, sqid = make_run()
        eid = _ev(run_id, sqid, "body")
        cid = _claim(run_id, sqid, "S")
        registry.bind_claim_evidence(run_id, cid, eid)
        fake1 = FakeVerifier()
        semantic_verify_claim(cid, verifier=fake1)
        fake2 = FakeVerifier()
        result2 = semantic_verify_claim(cid, verifier=fake2)
        assert result2["items"][0]["reused"] is True
        assert len(list_verifications(run_id)) == 1

    def test_spec_change_new_artifact(self, research_sqlite):
        run_id, sqid = make_run()
        eid = _ev(run_id, sqid, "body")
        cid = _claim(run_id, sqid, "S")
        registry.bind_claim_evidence(run_id, cid, eid)
        semantic_verify_claim(cid, verifier=FakeVerifier())
        spec2 = VerifierSpec(provider="fake", model="other-model")
        semantic_verify_claim(cid, verifier=FakeVerifier(), spec=spec2)
        rows = list_verifications(run_id)
        assert len(rows) == 2
        assert rows[0]["verifier_fingerprint"] != rows[1]["verifier_fingerprint"]

    def test_failed_same_spec_retry_updates_same_artifact(self, research_sqlite):
        run_id, sqid = make_run()
        eid = _ev(run_id, sqid, "body")
        cid = _claim(run_id, sqid, "S")
        registry.bind_claim_evidence(run_id, cid, eid)
        semantic_verify_claim(
            cid,
            verifier=FakeVerifier(
                queue=[
                    VerifierRuntimeError("provider", "x"),
                    VerifierRuntimeError("provider", "y"),
                ]
            ),
        )
        first = list_verifications(run_id)[0]
        assert first["status"] == "failed"
        semantic_verify_claim(cid, verifier=FakeVerifier())  # 同 spec 重试
        rows = list_verifications(run_id)
        assert len(rows) == 1
        assert rows[0]["verification_id"] == first["verification_id"]
        assert rows[0]["status"] == "succeeded"


class TestConflictAndAggregation:
    def test_contested_marking_no_winner(self, research_sqlite):
        run_id, sqid = make_run()
        e1 = _ev(run_id, sqid, "支持内容 A", url="https://x.example/sup")
        e2 = _ev(run_id, sqid, "反对内容 B", url="https://x.example/contra")
        cid = _claim(run_id, sqid, "X 存在")
        registry.bind_claim_evidence(run_id, cid, e1)
        registry.bind_claim_evidence(run_id, cid, e2)

        def resolver(payload):
            return (
                "SUPPORTS"
                if payload["evidence"]["evidence_id"] == e1
                else "CONTRADICTS"
            )

        semantic_verify_claim(cid, verifier=FakeVerifier(resolver=resolver))
        agg = aggregate_claim_verdict(cid)
        assert agg["aggregate"] == "contested"
        assert agg["contested"] is True
        assert sorted(agg["counts"].keys())

    def test_supported_and_partial(self, research_sqlite):
        run_id, sqid = make_run()
        e1 = _ev(run_id, sqid, "A body", url="https://x.example/a")
        e2 = _ev(run_id, sqid, "B body", url="https://x.example/b")
        c_sup = _claim(run_id, sqid, "sup claim")
        c_part = _claim(run_id, sqid, "part claim")
        registry.bind_claim_evidence(run_id, c_sup, e1)
        registry.bind_claim_evidence(run_id, c_part, e1)
        registry.bind_claim_evidence(run_id, c_part, e2)

        def resolver(payload):
            if payload["claim"]["claim_id"] == c_sup:
                return "SUPPORTS"
            return (
                "SUPPORTS"
                if payload["evidence"]["evidence_id"] == e1
                else "INSUFFICIENT"
            )

        semantic_verify_run(run_id, verifier=FakeVerifier(resolver=resolver))
        assert aggregate_claim_verdict(c_sup)["aggregate"] == "supported"
        assert aggregate_claim_verdict(c_part)["aggregate"] == "partially_supported"
        assert aggregate_claim_verdict(c_part)["contested"] is False

    def test_unverified_when_no_rows(self, research_sqlite):
        run_id, sqid = make_run()
        cid = _claim(run_id, sqid, "no verification yet")
        assert aggregate_claim_verdict(cid)["aggregate"] == "unverified"


class TestProvenanceAndBudget:
    def test_verification_chain_contains_spec(self, research_sqlite):
        run_id, sqid = make_run()
        eid = _ev(run_id, sqid, "body")
        cid = _claim(run_id, sqid, "S")
        registry.bind_claim_evidence(run_id, cid, eid)
        spec = VerifierSpec(model="m2", prompt_version="v9")
        semantic_verify_claim(cid, verifier=FakeVerifier(), spec=spec)
        chain = verification_chain(cid)
        assert chain["claim_id"] == cid
        v = chain["verifications"][0]
        assert v["verifier_spec"]["model"] == "m2"
        assert v["verifier_spec"]["prompt_version"] == "v9"
        assert v["verifier_fingerprint"] == spec.fingerprint()

    def test_list_verifications_filters(self, research_sqlite):
        run_id, sqid = make_run()
        e1 = _ev(run_id, sqid, "A body", url="https://x.example/a")
        e2 = _ev(run_id, sqid, "B body", url="https://x.example/b")
        cid = _claim(run_id, sqid, "S")
        registry.bind_claim_evidence(run_id, cid, e1)
        registry.bind_claim_evidence(run_id, cid, e2)

        def resolver(payload):
            return "SUPPORTS" if payload["evidence"]["evidence_id"] == e1 else "ABSTAIN"

        semantic_verify_claim(cid, verifier=FakeVerifier(resolver=resolver))
        assert len(list_verifications(run_id, verdict="SUPPORTS")) == 1
        assert len(list_verifications(run_id, claim_id=cid)) == 2

    def test_budget_exhausted_stops(self, research_sqlite):
        run_id, sqid = make_run()
        e1 = _ev(run_id, sqid, "one", url="https://x.example/1")
        e2 = _ev(run_id, sqid, "two", url="https://x.example/2")
        cid = _claim(run_id, sqid, "S")
        registry.bind_claim_evidence(run_id, cid, e1)
        registry.bind_claim_evidence(run_id, cid, e2)
        budget = VerifyBudget(max_llm_calls=1)
        summary = semantic_verify_run(run_id, verifier=FakeVerifier(), budget=budget)
        assert summary["budget_exhausted"] is True
        assert summary["succeeded"] == 1  # 第二个候选被 budget 截断
