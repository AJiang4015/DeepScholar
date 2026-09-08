"""F4 Conflict Detection — SQLite 全量测试（rev2 冻结契约）。

覆盖：0004 schema/迁移、integrity invariants、候选（SUPPORTS×CONTRADICTS）、explicit_pairs
controlled 语义、detector 类型矩阵与 invariant、retry/failure、幂等、SET NULL + signal 快照、
detector SHALL NOT re-evaluate、聚合、provenance、budget/candidate 残留。
"""

import os
import sqlite3

import pytest

from app.research import migrations, registry, verify as f3verify
from app.research.config import RESEARCH_DB_ENV
from app.research.conflict import (
    ConflictBudget,
    ConflictIntegrityError,
    DetectorRuntimeError,
    FakeDetector,
    VerifierSpec,
    claim_conflict_summary,
    conflict_chain,
    detect_claim_conflicts,
    detect_run_conflicts,
    list_conflicts,
    _guard_verification,
    _normalize_pair,
)
from app.research.schemas import ClaimType
from app.research.verify import FakeVerifier as F3Fake
from tests._f2_helpers import add_web_evidence, make_run


def _claim(run_id, sqid, statement):
    return registry.create_claim(run_id, sqid, statement, ClaimType.FACT)


def _ev(run_id, sqid, content, url):
    return add_web_evidence(run_id, sqid, content, url)[2]


def _run_f3_verify(run_id, sqid, cid, verdict_map):
    """对 claim 的各证据按 verdict_map 运行 F3 verification。"""
    fake = F3Fake(
        resolver=lambda payload: verdict_map.get(
            payload["evidence"]["evidence_id"], "ABSTAIN"
        )
    )
    f3verify.semantic_verify_claim(cid, verifier=fake)


def _setup_opposed_pair():
    run_id, sqid = make_run()
    ev_a = _ev(run_id, sqid, "支持内容 A", "https://x.example/sup")
    ev_b = _ev(run_id, sqid, "反对内容 B", "https://x.example/con")
    cid = _claim(run_id, sqid, "X 存在")
    registry.bind_claim_evidence(run_id, cid, ev_a)
    registry.bind_claim_evidence(run_id, cid, ev_b)
    _run_f3_verify(run_id, sqid, cid, {ev_a: "SUPPORTS", ev_b: "CONTRADICTS"})
    return run_id, sqid, cid, ev_a, ev_b


class TestSchemaAndIntegrity:
    def test_migration_table(self, research_sqlite):
        from app.research import store as rstore

        migrations.ensure_schema(rstore.get_store())
        conn = sqlite3.connect(os.environ[RESEARCH_DB_ENV])
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        cols = {r[1] for r in conn.execute("PRAGMA table_info(conflicts)")}
        conn.close()
        assert "conflicts" in tables
        assert {
            "conflict_id",
            "run_id",
            "claim_id",
            "evidence_a_id",
            "evidence_b_id",
            "verification_a_id",
            "verification_b_id",
            "detector_spec",
            "detector_fingerprint",
            "candidate_source",
            "status",
            "conflict_type",
            "genuine",
            "rationale",
            "error",
            "metadata",
        } <= cols
        from app.research import store as rstore

        assert sorted(migrations.applied_migration_versions(rstore.get_store())) == [
            "0001",
            "0002",
            "0003",
            "0004",
            "0005",
            "0006",
        ]

    def test_normalize_pair(self):
        assert _normalize_pair("z", "a") == ("a", "z")
        with pytest.raises(ConflictIntegrityError):
            _normalize_pair("same", "same")
        with pytest.raises(ConflictIntegrityError):
            _normalize_pair("", "b")

    def test_explicit_must_be_bound(self, research_sqlite):
        run_id, sqid = make_run()
        ev = _ev(run_id, sqid, "body", "https://x.example/u")
        cid = _claim(run_id, sqid, "claim")
        registry.bind_claim_evidence(run_id, cid, ev)
        other = _ev(run_id, sqid, "other", "https://x.example/v")  # 未 binding
        with pytest.raises(ConflictIntegrityError):
            detect_claim_conflicts(cid, explicit_pairs=[(ev, other)])

    def test_cross_run_rejected(self, research_sqlite):
        run_a, sq_a = make_run("a")
        run_b, sq_b = make_run("b")
        ev_a = _ev(run_a, sq_a, "A body", "https://x.example/a")
        ev_b = _ev(run_b, sq_b, "B body", "https://x.example/b")
        cid = _claim(run_a, sq_a, "claim")
        registry.bind_claim_evidence(run_a, cid, ev_a)
        with pytest.raises(ConflictIntegrityError):
            detect_claim_conflicts(cid, explicit_pairs=[(ev_a, ev_b)])

    def test_verification_alignment_guard(self, research_sqlite):
        run_id, sqid, cid, ev_a, ev_b = _setup_opposed_pair()
        from app.research import store as rstore

        store = rstore.get_store()
        other_ver = store.execute(
            "SELECT verification_id FROM verifications WHERE run_id=%s AND claim_id=%s "
            "AND evidence_id=%s LIMIT 1",
            (run_id, cid, ev_a),
        )[0]["verification_id"]
        with pytest.raises(ConflictIntegrityError):
            _guard_verification(store, run_id, cid, ev_b, other_ver)  # 错位 evidence


class TestDetectionDomain:
    def test_only_supports_x_contradicts(self, research_sqlite):
        run_id, sqid, cid, ev_a, ev_b = _setup_opposed_pair()
        ev_c = _ev(run_id, sqid, "中性不足内容 C", "https://x.example/insuf")
        registry.bind_claim_evidence(run_id, cid, ev_c)
        _run_f3_verify(
            run_id,
            sqid,
            cid,
            {ev_a: "SUPPORTS", ev_b: "CONTRADICTS", ev_c: "INSUFFICIENT"},
        )
        detect_claim_conflicts(
            cid, detector=FakeDetector(conflict_type="CONTRADICTION")
        )
        rows = list_conflicts(run_id)
        pairs = {(r["evidence_a_id"], r["evidence_b_id"]) for r in rows}
        assert (min(ev_a, ev_b), max(ev_a, ev_b)) in pairs
        assert len(rows) == 1  # C(INSUFFICIENT) 不产生候选

    def test_no_verification_no_default_candidates(self, research_sqlite):
        run_id, sqid = make_run()
        ev_a = _ev(run_id, sqid, "body", "https://x.example/a")
        cid = _claim(run_id, sqid, "claim")
        registry.bind_claim_evidence(run_id, cid, ev_a)
        result = detect_claim_conflicts(cid, detector=FakeDetector())
        assert result["items"] == []

    def test_claim_budget_candidate_leftover(self, research_sqlite):
        run_id, sqid, cid, ev_a, _ = _setup_opposed_pair()
        ev_b2 = _ev(run_id, sqid, "反对2", "https://x.example/c2")
        ev_b3 = _ev(run_id, sqid, "反对3", "https://x.example/c3")
        registry.bind_claim_evidence(run_id, cid, ev_b2)
        registry.bind_claim_evidence(run_id, cid, ev_b3)
        _run_f3_verify(
            run_id,
            sqid,
            cid,
            {ev_a: "SUPPORTS", ev_b2: "CONTRADICTS", ev_b3: "CONTRADICTS"},
        )
        result = detect_claim_conflicts(
            cid,
            detector=FakeDetector(conflict_type="CONTRADICTION"),
            budget=ConflictBudget(max_semantic_calls=1),
        )
        assert len(result["items"]) == 1
        statuses = [r["status"] for r in list_conflicts(run_id)]
        assert "candidate" in statuses  # 预算截断保留 candidate 残留
        assert statuses.count("confirmed") == 1


class TestExplicitPairs:
    def test_explicit_bypasses_verdict_but_not_integrity(self, research_sqlite):
        run_id, sqid = make_run()
        ev_a = _ev(run_id, sqid, "同向 A", "https://x.example/s1")
        ev_b = _ev(run_id, sqid, "同向 B", "https://x.example/s2")
        cid = _claim(run_id, sqid, "claim")
        registry.bind_claim_evidence(run_id, cid, ev_a)
        registry.bind_claim_evidence(run_id, cid, ev_b)
        # 默认域：都 SUPPORTS → 无候选
        _run_f3_verify(run_id, sqid, cid, {ev_a: "SUPPORTS", ev_b: "SUPPORTS"})
        default = detect_claim_conflicts(cid, detector=FakeDetector())
        assert default["items"] == []
        # explicit 可绕过 verdict 候选，但必须已 binding
        result = detect_claim_conflicts(
            cid,
            detector=FakeDetector(conflict_type="INCONSISTENCY"),
            explicit_pairs=[(ev_b, ev_a)],  # 反序 → 归一化 a<b
        )
        assert len(result["items"]) == 1
        assert result["items"][0]["status"] == "confirmed"
        row = list_conflicts(run_id)[0]
        assert (row["evidence_a_id"], row["evidence_b_id"]) == (
            min(ev_a, ev_b),
            max(ev_a, ev_b),
        )


class TestInvariantAndTypes:
    @pytest.mark.parametrize(
        ("ctype", "expected_status", "expected_genuine"),
        [
            ("CONTRADICTION", "confirmed", True),
            ("INCONSISTENCY", "confirmed", True),
            ("CONTEXTUAL_DIFFERENCE", "rejected", False),
            ("TEMPORAL_DIFFERENCE", "rejected", False),
            ("SCOPE_DIFFERENCE", "rejected", False),
            ("NO_CONFLICT", "rejected", False),
        ],
    )
    def test_type_status_matrix(
        self, research_sqlite, ctype, expected_status, expected_genuine
    ):
        run_id, sqid, cid, _, _ = _setup_opposed_pair()
        detect_claim_conflicts(cid, detector=FakeDetector(conflict_type=ctype))
        row = list_conflicts(run_id)[0]
        assert row["status"] == expected_status
        assert row["genuine"] == expected_genuine
        assert row["conflict_type"] == ctype

    def test_genuine_type_contradiction_is_malformed(self, research_sqlite):
        run_id, sqid, cid, _, _ = _setup_opposed_pair()
        fake = FakeDetector(
            resolver=lambda p: {
                "genuine": True,
                "conflict_type": "SCOPE_DIFFERENCE",
                "rationale": "x",
            }
        )
        detect_claim_conflicts(cid, detector=fake)
        row = list_conflicts(run_id)[0]
        assert row["status"] == "failed"
        assert row["conflict_type"] is None and row["genuine"] is None
        assert "malformed" in (row["error"] or "")


class TestFailureRetryIdempotency:
    def test_malformed_then_ok(self, research_sqlite):
        run_id, sqid, cid, _, _ = _setup_opposed_pair()
        detect_claim_conflicts(cid, detector=FakeDetector(queue=["not-json"]))
        assert list_conflicts(run_id)[0]["status"] == "confirmed"

    def test_double_malformed_fails(self, research_sqlite):
        run_id, sqid, cid, _, _ = _setup_opposed_pair()
        detect_claim_conflicts(cid, detector=FakeDetector(queue=["a", "b"]))
        row = list_conflicts(run_id)[0]
        assert row["status"] == "failed"
        assert row["conflict_type"] is None and row["genuine"] is None

    def test_provider_error_then_ok(self, research_sqlite):
        run_id, sqid, cid, _, _ = _setup_opposed_pair()
        detect_claim_conflicts(
            cid, detector=FakeDetector(queue=[DetectorRuntimeError("timeout", "slow")])
        )
        assert list_conflicts(run_id)[0]["status"] == "confirmed"

    def test_idempotent_same_fingerprint(self, research_sqlite):
        run_id, sqid, cid, _, _ = _setup_opposed_pair()
        detect_claim_conflicts(cid, detector=FakeDetector())
        detect_claim_conflicts(cid, detector=FakeDetector())
        assert len(list_conflicts(run_id)) == 1
        assert list_conflicts(run_id)[0]["status"] == "confirmed"

    def test_spec_change_new_artifact(self, research_sqlite):
        run_id, sqid, cid, _, _ = _setup_opposed_pair()
        detect_claim_conflicts(cid, detector=FakeDetector())
        detect_claim_conflicts(
            cid,
            detector=FakeDetector(),
            spec=VerifierSpec(model="det2", prompt_version="v2"),
        )
        rows = list_conflicts(run_id)
        assert len(rows) == 2
        assert rows[0]["detector_fingerprint"] != rows[1]["detector_fingerprint"]

    def test_failed_retry_updates_same_artifact(self, research_sqlite):
        run_id, sqid, cid, _, _ = _setup_opposed_pair()
        detect_claim_conflicts(
            cid,
            detector=FakeDetector(
                queue=[
                    DetectorRuntimeError("provider", "x"),
                    DetectorRuntimeError("provider", "y"),
                ]
            ),
        )
        first = list_conflicts(run_id)[0]
        assert first["status"] == "failed"
        detect_claim_conflicts(cid, detector=FakeDetector())  # 同 spec 重试
        rows = list_conflicts(run_id)
        assert len(rows) == 1
        assert rows[0]["conflict_id"] == first["conflict_id"]
        assert rows[0]["status"] == "confirmed"


class TestSetNullSnapshotAndBoundaries:
    def test_verification_delete_set_null_keeps_snapshot(self, research_sqlite):
        run_id, sqid, cid, ev_a, ev_b = _setup_opposed_pair()
        detect_claim_conflicts(
            cid, detector=FakeDetector(conflict_type="CONTRADICTION")
        )
        row = list_conflicts(run_id)[0]
        assert row["verification_a_id"] and row["verification_b_id"]
        snap_a = row["metadata"].get("signal_snapshot_a")
        snap_b = row["metadata"].get("signal_snapshot_b")
        assert {snap_a["verdict"], snap_b["verdict"]} == {"SUPPORTS", "CONTRADICTS"}
        from app.research import store as rstore

        store = rstore.get_store()
        with store.transaction() as tx:
            tx.execute(
                "DELETE FROM verifications WHERE verification_id IN (%s, %s)",
                (row["verification_a_id"], row["verification_b_id"]),
            )
        after = list_conflicts(run_id)[0]
        assert after["verification_a_id"] is None
        assert after["verification_b_id"] is None
        after_snaps = [
            after["metadata"].get("signal_snapshot_a"),
            after["metadata"].get("signal_snapshot_b"),
        ]
        assert {s["verdict"] for s in after_snaps if s} == {"SUPPORTS", "CONTRADICTS"}

    def test_detector_shall_not_reevaluate_support(self, research_sqlite):
        """detector 输出含 verdict 字段也必须被忽略（不改判 support）。"""
        run_id, sqid, cid, _, _ = _setup_opposed_pair()

        def resolver(payload):
            return {
                "genuine": False,
                "conflict_type": "NO_CONFLICT",
                "rationale": "x",
                "verdict": "SUPPORTS",
            }

        detect_claim_conflicts(cid, detector=FakeDetector(resolver=resolver))
        row = list_conflicts(run_id)[0]
        assert row["status"] == "rejected"
        assert row["conflict_type"] == "NO_CONFLICT"

    def test_context_envelope(self, research_sqlite):
        seen: dict = {}

        def resolver(payload):
            seen.update(payload)
            return {"genuine": True, "conflict_type": "CONTRADICTION", "rationale": "r"}

        run_id, sqid, cid, ev_a, ev_b = _setup_opposed_pair()
        detect_claim_conflicts(cid, detector=FakeDetector(resolver=resolver))
        assert seen["claim"]["claim_id"] == cid
        assert seen["evidence_a"]["content"]
        assert seen["evidence_b"]["content"]
        assert "source_meta" in seen["evidence_a"]
        assert "messages" not in seen and "history" not in seen
        signals = {
            seen["evidence_a"].get("signal", {}).get("verdict"),
            seen["evidence_b"].get("signal", {}).get("verdict"),
        }
        assert signals == {"SUPPORTS", "CONTRADICTS"}


class TestAggregationAndProvenance:
    def test_summary_confirmed(self, research_sqlite):
        run_id, sqid, cid, _, _ = _setup_opposed_pair()
        detect_claim_conflicts(
            cid, detector=FakeDetector(conflict_type="CONTRADICTION")
        )
        summary = claim_conflict_summary(cid)
        assert summary["has_confirmed_conflict"] is True
        assert summary["counts"]["CONTRADICTION"] == 1

    def test_summary_rejected_only(self, research_sqlite):
        run_id, sqid, cid, _, _ = _setup_opposed_pair()
        detect_claim_conflicts(cid, detector=FakeDetector(conflict_type="NO_CONFLICT"))
        summary = claim_conflict_summary(cid)
        assert summary["has_confirmed_conflict"] is False

    def test_provenance_chain(self, research_sqlite):
        run_id, sqid, cid, ev_a, ev_b = _setup_opposed_pair()
        detect_claim_conflicts(cid, detector=FakeDetector())
        row = list_conflicts(run_id)[0]
        chain = conflict_chain(row["conflict_id"])
        assert chain["claim"]["claim_id"] == cid
        assert chain["evidence_a"]["evidence_id"] in {ev_a, ev_b}
        assert chain["detector_spec"]["provider"] == "fake"
        assert chain["verification_a"] is not None

    def test_run_level_summary(self, research_sqlite):
        run_id, sqid, cid, _, _ = _setup_opposed_pair()
        summary = detect_run_conflicts(run_id, detector=FakeDetector())
        assert summary["confirmed"] == 1
        assert summary["partial"] is False
        assert summary["claims"][0]["claim_id"] == cid
