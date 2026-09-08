"""F6 Conflict Reconciliation — SQLite 全量测试（rev2 冻结契约）。

覆盖：0006 schema/迁移、outcome 判定（SAME_ORIGIN/DETAIL/GENUINE）、
Unknown≠NotIndependent 硬红线（缺 corroboration / conflict 未覆盖 / cluster signal
missing-malformed / F5 派生信号与 cluster 交集矛盾 → failed）、claim register
（precedence / unverified / incomplete_input / verified_consistent）、optional review
（不改 outcome；失败→complete+review_failed）、fingerprint 幂等/升级、provenance
（F4/F5 signal 快照）、run_unresolved 只含 genuine_contested。

F6 explains/registers conflict state; it does not adjudicate truth.
"""

import json
import os
import sqlite3

from app.research import migrations, registry, verify as f3verify
from app.research.config import RESEARCH_DB_ENV
from app.research.conflict import FakeDetector as F4Detector
from app.research.conflict import detect_claim_conflicts
from app.research.corroboration import compute_claim_corroboration
from app.research.normalize import canonical_key_for
from app.research.reconciliation import (
    FakeReviewer,
    ReconciliationMethod,
    ReviewerRuntimeError,
    conflict_register,
    get_reconciliation,
    list_reconciliations,
    reconcile_claim_conflicts,
    reconcile_run_conflicts,
    run_unresolved,
)
from app.research.schemas import ClaimType
from app.research.verify import FakeVerifier as F3Fake
from tests._f2_helpers import make_run

_GENUINE_TYPES = {"CONTRADICTION", "INCONSISTENCY"}


def _claim(run_id, sqid, statement):
    return registry.create_claim(run_id, sqid, statement, ClaimType.FACT)


def _web(run_id, sqid, url, title, content):
    qid = registry.record_search_query(
        run_id, sqid, agent="network_search", tool="internet_search", query="q"
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
        run_id, sid, sqid, content=content, locator=url, extraction_method="web_result"
    )


def _bind_verify(run_id, cid, verdict_map):
    """绑定并 F3 verify：verdict_map: evidence_id → verdict（缺省 ABSTAIN）。"""
    for eid in verdict_map:
        registry.bind_claim_evidence(run_id, cid, eid)
    f3verify.semantic_verify_claim(
        cid,
        verifier=F3Fake(
            resolver=lambda p: verdict_map.get(p["evidence"]["evidence_id"], "ABSTAIN")
        ),
    )


def _confirm_pairs(cid, pairs, types):
    """F4 explicit confirm：pairs=[(a,b)...]，types=frozenset({a,b})→type。"""
    detector = F4Detector(
        resolver=lambda p: (
            {
                "genuine": (pair_type in _GENUINE_TYPES),
                "conflict_type": pair_type,
            }
            if (
                pair_type := types.get(
                    frozenset(
                        [p["evidence_a"]["evidence_id"], p["evidence_b"]["evidence_id"]]
                    )
                )
            )
            else {"genuine": False, "conflict_type": "NO_CONFLICT"}
        )
    )
    detect_claim_conflicts(cid, explicit_pairs=pairs, detector=detector)


def _confirmed(store, run_id, cid):
    return store.execute(
        "SELECT conflict_id, evidence_a_id, evidence_b_id, conflict_type "
        "FROM conflicts WHERE run_id=%s AND claim_id=%s AND status='confirmed' "
        "ORDER BY conflict_id",
        (run_id, cid),
    )


def _run_of(cid):
    from app.research import store as rstore

    rows = rstore.get_store().execute(
        "SELECT run_id FROM claims WHERE claim_id=%s", (cid,)
    )
    return rows[0]["run_id"] if rows else None


def _mutate_f5_entry(store, run_id, cid, mutate):
    """改写 corroboration.conflicts_independence 首条 entry（模拟损坏的 F5 数据）。"""
    rows = store.execute(
        "SELECT corroboration_id, conflicts_independence FROM corroborations "
        "WHERE run_id=%s AND claim_id=%s AND status='complete'",
        (run_id, cid),
    )
    assert rows, "需要 corroboration 才能做 malformed 测试"
    row = rows[0]
    payload = (
        json.loads(row["conflicts_independence"])
        if isinstance(row["conflicts_independence"], str)
        else row["conflicts_independence"]
    )
    mutate(payload[0])
    with store.transaction() as tx:
        tx.execute(
            "UPDATE corroborations SET conflicts_independence=%s "
            "WHERE corroboration_id=%s",
            (json.dumps(payload, ensure_ascii=False), row["corroboration_id"]),
        )


def _build_two_source_claim(run, ctype="CONTRADICTION", same_cluster=False):
    """支持/反对两侧证据 + F3 + F4(confirmed) + F5 corroboration。返回 (cid, ev_a, ev_b)。"""
    run_id, sqid = make_run(run)
    if same_cluster:
        title_a = title_b = f"同源标题 {run}"
    else:
        title_a, title_b = f"正面标题 {run}", f"负面标题 {run}"
    ev_a = _web(run_id, sqid, f"https://a-{run}.example/1", title_a, "a" * 30)
    ev_b = _web(run_id, sqid, f"https://b-{run}.example/2", title_b, "b" * 30)
    cid = _claim(run_id, sqid, f"F6 claim {run}")
    _bind_verify(run_id, cid, {ev_a: "SUPPORTS", ev_b: "CONTRADICTS"})
    _confirm_pairs(cid, [(ev_a, ev_b)], {frozenset([ev_a, ev_b]): ctype})
    compute_claim_corroboration(cid)
    return cid, ev_a, ev_b


class TestSchemaAndMigration:
    def test_migration_and_table(self, research_sqlite):
        from app.research import store as rstore

        store = rstore.get_store()
        assert sorted(migrations.applied_migration_versions(store)) == [
            "0001",
            "0002",
            "0003",
            "0004",
            "0005",
            "0006",
        ]
        migrations.ensure_schema(store)
        assert sorted(migrations.applied_migration_versions(store)) == [
            "0001",
            "0002",
            "0003",
            "0004",
            "0005",
            "0006",
        ]
        conn = sqlite3.connect(os.environ[RESEARCH_DB_ENV])
        cols = {r[1] for r in conn.execute("PRAGMA table_info(reconciliations)")}
        conn.close()
        assert {
            "reconciliation_id",
            "run_id",
            "claim_id",
            "conflict_id",
            "method_spec",
            "method_fingerprint",
            "status",
            "outcome",
            "detail",
            "error",
            "computed_at",
            "metadata",
        } <= cols

    def test_unique_fingerprint_constraint(self, research_sqlite):
        from app.research import store as rstore

        store = rstore.get_store()
        cid, _a, _b = _build_two_source_claim("f6uniq")
        reconcile_claim_conflicts(cid)
        reconcile_claim_conflicts(cid)
        run_id = _run_of(cid)
        rows = store.execute(
            "SELECT count(*) AS n FROM reconciliations WHERE run_id=%s AND claim_id=%s",
            (run_id, cid),
        )
        assert rows[0]["n"] == 1


class TestOutcomes:
    def test_same_cluster_same_origin(self, research_sqlite):
        """两侧同 cluster（转载跨侧同标题）→ SAME_ORIGIN_CONTRADICTION。"""
        cid, _a, _b = _build_two_source_claim("f6so", same_cluster=True)
        res = reconcile_claim_conflicts(cid)
        item = res["items"][0]
        assert item["status"] == "complete"
        assert item["outcome"] == "SAME_ORIGIN_CONTRADICTION"
        assert item["metadata"]["f5_signal"]["independent_between_sides"] is False
        assert res["register"]["register"] == "same_origin_contradiction_only"

    def test_independent_inconsistency_detail(self, research_sqlite):
        """独立 + F4 INCONSISTENCY → DETAIL_INCONSISTENCY（仅登记类别，不裁决影响）。"""
        cid, _a, _b = _build_two_source_claim("f6det", ctype="INCONSISTENCY")
        res = reconcile_claim_conflicts(cid)
        item = res["items"][0]
        assert item["status"] == "complete"
        assert item["outcome"] == "DETAIL_INCONSISTENCY"
        blob = json.dumps(item, ensure_ascii=False).lower()
        for banned in (
            "does_not_affect_claim",
            "minor_difference",
            "claim_remains_valid",
        ):
            assert banned not in blob
        assert res["register"]["register"] == "detail_inconsistency"

    def test_independent_contradiction_genuine(self, research_sqlite):
        """独立 + F4 CONTRADICTION → GENUINE_CONTESTED（保留 contested，不裁决 winner）。"""
        cid, _a, _b = _build_two_source_claim("f6gen")
        res = reconcile_claim_conflicts(cid)
        item = res["items"][0]
        assert item["status"] == "complete"
        assert item["outcome"] == "GENUINE_CONTESTED"
        assert item["metadata"]["f5_signal"]["independent_between_sides"] is True
        assert res["register"]["register"] == "genuine_contested"
        # run_unresolved 只含 genuine_contested claims + 对应 confirmed conflicts
        unresolved = run_unresolved(_run_of(cid))
        assert len(unresolved["unresolved_claims"]) == 1
        entry = unresolved["unresolved_claims"][0]
        assert entry["claim_id"] == cid
        assert entry["conflicts"][0]["reconciliation"]["outcome"] == "GENUINE_CONTESTED"


class TestUnknownNotIndependent:
    def test_missing_corroboration_fails(self, research_sqlite):
        """F4 confirmed 但 F5 corroboration 缺失 → failed（绝不当作同源）。"""
        run_id, sqid = make_run("f6nocorr")
        ev_a = _web(run_id, sqid, "https://na.example/1", "标题 NA", "a" * 30)
        ev_b = _web(run_id, sqid, "https://nb.example/2", "标题 NB", "b" * 30)
        cid = _claim(run_id, sqid, "no-corroboration claim")
        _bind_verify(run_id, cid, {ev_a: "SUPPORTS", ev_b: "CONTRADICTS"})
        _confirm_pairs(cid, [(ev_a, ev_b)], {frozenset([ev_a, ev_b]): "CONTRADICTION"})
        # 不跑 compute_claim_corroboration
        res = reconcile_claim_conflicts(cid)
        item = res["items"][0]
        assert item["status"] == "failed"
        assert item["outcome"] is None
        assert item["error"] and "corroboration" in item["error"]
        assert res["register"]["register"] == "incomplete_input"

    def test_conflict_not_covered_by_f5_fails(self, research_sqlite):
        """corroboration 已算但该 conflict 在其后新增 → not covered → failed。"""
        run_id, sqid = make_run("f6cov")
        ev_a = _web(run_id, sqid, "https://ca.example/1", "标题 CA", "a" * 30)
        ev_b = _web(run_id, sqid, "https://cb.example/2", "标题 CB", "b" * 30)
        cid = _claim(run_id, sqid, "coverage claim")
        _bind_verify(run_id, cid, {ev_a: "SUPPORTS", ev_b: "CONTRADICTS"})
        _confirm_pairs(cid, [(ev_a, ev_b)], {frozenset([ev_a, ev_b]): "CONTRADICTION"})
        compute_claim_corroboration(cid)
        # 之后再加一个反对方证据 → 新 confirmed pair (a,c) 不在 corroboration 覆盖内
        ev_c = _web(run_id, sqid, "https://cc.example/3", "标题 CC", "c" * 30)
        registry.bind_claim_evidence(run_id, cid, ev_c)
        f3verify.semantic_verify_claim(
            cid,
            verifier=F3Fake(
                resolver=lambda p: {
                    ev_a: "SUPPORTS",
                    ev_b: "CONTRADICTS",
                    ev_c: "CONTRADICTS",
                }.get(p["evidence"]["evidence_id"], "ABSTAIN")
            ),
        )
        detect_claim_conflicts(cid, detector=F4Detector(conflict_type="CONTRADICTION"))
        res = reconcile_claim_conflicts(cid)
        outcomes = {(i["outcome"], i["status"]) for i in res["items"]}
        failed = [i for i in res["items"] if i["status"] == "failed"]
        assert failed, outcomes
        assert "conflicts_independence" in failed[0]["error"]
        assert res["register"]["register"] == "incomplete_input"

    def test_missing_cluster_ids_malformed_fails(self, research_sqlite):
        """side_a.cluster_ids 缺失 → failed（Unknown ≠ Not Independent）。"""
        from app.research import store as rstore

        store = rstore.get_store()
        cid, _a, _b = _build_two_source_claim("f6mal")
        run_id = _run_of(cid)
        _mutate_f5_entry(store, run_id, cid, lambda e: e["side_a"].pop("cluster_ids"))
        res = reconcile_claim_conflicts(cid)
        item = res["items"][0]
        assert item["status"] == "failed"
        assert item["outcome"] is None
        assert "cluster_ids" in item["error"]

    def test_malformed_cluster_ids_type_fails(self, research_sqlite):
        from app.research import store as rstore

        store = rstore.get_store()
        cid, _a, _b = _build_two_source_claim("f6mal2")
        run_id = _run_of(cid)
        _mutate_f5_entry(
            store, run_id, cid, lambda e: e["side_b"].update({"cluster_ids": "x"})
        )
        res = reconcile_claim_conflicts(cid)
        item = res["items"][0]
        assert item["status"] == "failed"
        assert item["outcome"] is None

    def test_independent_false_without_intersection_fails(self, research_sqlite):
        """independent_between_sides=false 但 cluster 无交集 → 不一致 → failed（不直接 SAME_ORIGIN）。"""
        from app.research import store as rstore

        store = rstore.get_store()
        cid, _a, _b = _build_two_source_claim("f6bogus")
        run_id = _run_of(cid)
        _mutate_f5_entry(
            store, run_id, cid, lambda e: e.update({"independent_between_sides": False})
        )
        res = reconcile_claim_conflicts(cid)
        item = res["items"][0]
        assert item["status"] == "failed"
        assert item["outcome"] is None  # 绝不产出 SAME_ORIGIN
        assert "independent_between_sides" in item["error"]

    def test_independent_true_with_intersection_fails(self, research_sqlite):
        """同簇但 F5 误标 independent=true → 与交集矛盾 → failed（signal malformed）。"""
        from app.research import store as rstore

        store = rstore.get_store()
        cid, _a, _b = _build_two_source_claim("f6bogus2", same_cluster=True)
        run_id = _run_of(cid)
        _mutate_f5_entry(
            store, run_id, cid, lambda e: e.update({"independent_between_sides": True})
        )
        res = reconcile_claim_conflicts(cid)
        item = res["items"][0]
        assert item["status"] == "failed"
        assert item["outcome"] is None
        assert "independent_between_sides" in item["error"]


class TestClaimRegister:
    def test_precedence_genuine_over_detail(self, research_sqlite):
        """同 claim 多冲突：GENUINE > DETAIL（deterministic 唯一终态）。"""
        from app.research import store as rstore

        store = rstore.get_store()
        run_id, sqid = make_run("f6prec")
        s1 = _web(run_id, sqid, "https://p1.example/1", "标题 P1", "a" * 30)
        c1 = _web(run_id, sqid, "https://q1.example/1", "标题 Q1", "b" * 30)
        s2 = _web(run_id, sqid, "https://p2.example/1", "标题 P2", "c" * 30)
        c2 = _web(run_id, sqid, "https://q2.example/1", "标题 Q2", "d" * 30)
        cid = _claim(run_id, sqid, "precedence claim")
        _bind_verify(
            run_id,
            cid,
            {s1: "SUPPORTS", c1: "CONTRADICTS", s2: "SUPPORTS", c2: "CONTRADICTS"},
        )
        pairs = [(s1, c1), (s2, c2)]
        _confirm_pairs(
            cid,
            pairs,
            {
                frozenset([s1, c1]): "CONTRADICTION",
                frozenset([s2, c2]): "INCONSISTENCY",
            },
        )
        compute_claim_corroboration(cid)
        conflicts = _confirmed(store, run_id, cid)
        assert {c["conflict_type"] for c in conflicts} == {
            "CONTRADICTION",
            "INCONSISTENCY",
        }
        res = reconcile_claim_conflicts(cid)
        outcomes = {i["outcome"] for i in res["items"]}
        assert outcomes == {"GENUINE_CONTESTED", "DETAIL_INCONSISTENCY"}
        assert res["register"]["register"] == "genuine_contested"

    def test_precedence_detail_over_same_origin(self, research_sqlite):
        """多冲突：DETAIL > SAME_ORIGIN。"""
        run_id, sqid = make_run("f6prec2")
        # pair1：同标题（同簇）→ SAME_ORIGIN；pair2：独立 INCONSISTENCY → DETAIL
        s1 = _web(run_id, sqid, "https://r1.example/1", "共享标题 W", "a" * 30)
        c1 = _web(run_id, sqid, "https://r2.example/1", "共享标题 W", "b" * 30)
        s2 = _web(run_id, sqid, "https://r3.example/1", "标题 P3", "c" * 30)
        c2 = _web(run_id, sqid, "https://r4.example/1", "标题 Q3", "d" * 30)
        cid = _claim(run_id, sqid, "precedence2 claim")
        _bind_verify(
            run_id,
            cid,
            {s1: "SUPPORTS", c1: "CONTRADICTS", s2: "SUPPORTS", c2: "CONTRADICTS"},
        )
        pairs = [(s1, c1), (s2, c2)]
        _confirm_pairs(
            cid,
            pairs,
            {
                frozenset([s1, c1]): "CONTRADICTION",
                frozenset([s2, c2]): "INCONSISTENCY",
            },
        )
        compute_claim_corroboration(cid)
        res = reconcile_claim_conflicts(cid)
        assert {i["outcome"] for i in res["items"]} == {
            "SAME_ORIGIN_CONTRADICTION",
            "DETAIL_INCONSISTENCY",
        }
        assert res["register"]["register"] == "detail_inconsistency"

    def test_no_supports_unverified(self, research_sqlite):
        """无 SUPPORTS（即使无 contradiction）→ unverified。"""
        run_id, sqid = make_run("f6unv")
        ev = _web(run_id, sqid, "https://un.example/1", "标题 U", "u" * 30)
        cid = _claim(run_id, sqid, "unverified claim")
        _bind_verify(run_id, cid, {ev: "ABSTAIN"})
        register = conflict_register(cid)
        assert register["register"] == "unverified"

    def test_supports_no_conflict_verified_consistent(self, research_sqlite):
        """SUPPORTS + 无 confirmed 冲突 + inputs 可解析 → verified_consistent（非因"无矛盾"）。"""
        run_id, sqid = make_run("f6vc")
        ev = _web(run_id, sqid, "https://vc.example/1", "标题 V", "v" * 30)
        cid = _claim(run_id, sqid, "verified-consistent claim")
        _bind_verify(run_id, cid, {ev: "SUPPORTS"})
        register = conflict_register(cid)
        assert register["register"] == "verified_consistent"

    def test_incomplete_input_without_reconciliation(self, research_sqlite):
        """有 confirmed 冲突但未 reconcile → incomplete_input（不静默降级）。"""
        run_id, sqid = make_run("f6inc")
        ev_a = _web(run_id, sqid, "https://ia.example/1", "标题 IA", "a" * 30)
        ev_b = _web(run_id, sqid, "https://ib.example/2", "标题 IB", "b" * 30)
        cid = _claim(run_id, sqid, "incomplete claim")
        _bind_verify(run_id, cid, {ev_a: "SUPPORTS", ev_b: "CONTRADICTS"})
        _confirm_pairs(cid, [(ev_a, ev_b)], {frozenset([ev_a, ev_b]): "CONTRADICTION"})
        compute_claim_corroboration(cid)
        register = conflict_register(cid)
        assert register["register"] == "incomplete_input"
        assert register["missing"][0]["reason"] == "no_reconciliation"


class TestFailureAndIdempotency:
    def test_gate_error_failed_then_recover(self, research_sqlite):
        """run F2 error → gate → failed；修复后同指纹收敛 complete（同行 UPDATE）。"""
        from app.research import store as rstore

        store = rstore.get_store()
        run_a, sq_a = make_run("f6gate-a")
        run_b, sq_b = make_run("f6gate-b")
        ev_a = _web(run_a, sq_a, "https://ga.example/1", "标题 GA", "a" * 30)
        ev_b = _web(run_a, sq_a, "https://gb.example/2", "标题 GB", "b" * 30)
        cid = _claim(run_a, sq_a, "gate claim")
        _bind_verify(run_a, cid, {ev_a: "SUPPORTS", ev_b: "CONTRADICTS"})
        _confirm_pairs(cid, [(ev_a, ev_b)], {frozenset([ev_a, ev_b]): "CONTRADICTION"})
        compute_claim_corroboration(cid)
        # 注入跨 run binding → R6 deterministic error
        e_other = _web(run_b, sq_b, "https://go.example/1", "标题 GO", "o" * 30)
        with store.transaction() as tx:
            tx.execute(
                "INSERT INTO claim_evidences (binding_id, run_id, claim_id, evidence_id,"
                " created_at, metadata) VALUES (%s,%s,%s,%s,%s,%s)",
                (
                    "f6-gate-cross",
                    run_a,
                    cid,
                    e_other,
                    "2026-01-01T00:00:00+00:00",
                    "{}",
                ),
            )
        failed = reconcile_claim_conflicts(cid)
        assert failed["items"][0]["status"] == "failed"
        assert "gate" in failed["items"][0]["error"]
        assert failed["register"]["register"] == "incomplete_input"
        with store.transaction() as tx:
            tx.execute(
                "DELETE FROM claim_evidences WHERE binding_id=%s", ("f6-gate-cross",)
            )
        recovered = reconcile_claim_conflicts(cid)
        item = recovered["items"][0]
        assert item["status"] == "complete"
        assert item["outcome"] == "GENUINE_CONTESTED"
        assert item["reconciliation_id"] == failed["items"][0]["reconciliation_id"]
        rows = store.execute(
            "SELECT count(*) AS n FROM reconciliations WHERE run_id=%s AND claim_id=%s",
            (run_a, cid),
        )
        assert rows[0]["n"] == 1

    def test_idempotent_single_row_and_method_upgrade(self, research_sqlite):
        from app.research import store as rstore

        store = rstore.get_store()
        cid, _a, _b = _build_two_source_claim("f6idem")
        run_id = _run_of(cid)
        first = reconcile_claim_conflicts(cid)
        again = reconcile_claim_conflicts(cid)
        assert (
            again["items"][0]["reconciliation_id"]
            == first["items"][0]["reconciliation_id"]
        )
        rows = store.execute(
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
        assert (
            upgraded["items"][0]["reconciliation_id"]
            != first["items"][0]["reconciliation_id"]
        )
        rows = store.execute(
            "SELECT count(*) AS n FROM reconciliations WHERE run_id=%s AND claim_id=%s",
            (run_id, cid),
        )
        assert rows[0]["n"] == 2

    def test_review_failure_keeps_complete_and_outcome(self, research_sqlite):
        """review 失败 → complete + metadata.review_failed；deterministic outcome 不变。"""
        method = ReconciliationMethod(review_enabled=True, max_review_pairs=1)
        reviewer = FakeReviewer(
            queue=[
                ReviewerRuntimeError("provider", "review outage"),
                ReviewerRuntimeError("provider", "review outage"),
            ]
        )
        cid, _a, _b = _build_two_source_claim("f6rvf")
        res = reconcile_claim_conflicts(cid, method=method, reviewer=reviewer)
        item = res["items"][0]
        assert item["status"] == "complete"
        assert item["outcome"] == "GENUINE_CONTESTED"
        assert item["metadata"]["review_failed"] is True
        assert "review outage" in item["metadata"]["review_failed_reason"]
        assert res["register"]["register"] == "genuine_contested"

    def test_review_cannot_change_outcome_family(self, research_sqlite):
        """reviewer 正常输出 detail/rationale → outcome 仍 GENUINE（不得新增 outcome family）。"""
        method = ReconciliationMethod(review_enabled=True, max_review_pairs=1)
        reviewer = FakeReviewer()
        cid, _a, _b = _build_two_source_claim("f6rvok")
        res = reconcile_claim_conflicts(cid, method=method, reviewer=reviewer)
        item = res["items"][0]
        assert item["status"] == "complete"
        assert item["outcome"] == "GENUINE_CONTESTED"
        assert item["metadata"]["rationale"]
        assert res["register"]["register"] == "genuine_contested"


class TestApis:
    def test_run_level_and_read_apis(self, research_sqlite):
        run_id, sqid = make_run("f6run")
        ev_a = _web(run_id, sqid, "https://ra.example/1", "标题 RA", "a" * 30)
        ev_b = _web(run_id, sqid, "https://rb.example/2", "标题 RB", "b" * 30)
        cid = _claim(run_id, sqid, "run-level claim")
        _bind_verify(run_id, cid, {ev_a: "SUPPORTS", ev_b: "CONTRADICTS"})
        _confirm_pairs(cid, [(ev_a, ev_b)], {frozenset([ev_a, ev_b]): "CONTRADICTION"})
        compute_claim_corroboration(cid)
        summary = reconcile_run_conflicts(run_id)
        assert summary["run_id"] == run_id
        assert summary["conflicts"] == 1
        assert summary["complete"] == 1
        assert summary["genuine_contested"] == 1
        # 只读 API
        got = get_reconciliation(cid)
        assert got["outcome"] == "GENUINE_CONTESTED"
        listed = list_reconciliations(run_id)
        assert len(listed) == 1
        assert listed[0]["method_spec"]["name"] == "reconciliation.v1"
        assert get_reconciliation("nope") is None
        # provenance：F4/F5 signal 快照在 metadata
        assert listed[0]["metadata"]["f4_signal"]["conflict_type"] == "CONTRADICTION"
        assert listed[0]["metadata"]["f5_signal"]["independent_between_sides"] is True
