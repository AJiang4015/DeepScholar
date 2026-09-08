"""F7 Research State Bridge — SQLite 全量测试（rev2 冻结契约）。

覆盖：Claim materialization（valid/invalid statement-type/missing anchor/cross-run/invalid quote/
quote substring/cap 12/binding cap 8）、Extractor semantic boundary（verdict 字段 → malformed；
extractor 从不产生 F3 verdict artifact）、F2–F6 orchestration（enabled → 既有 public API 产出
artifact；disabled → skipped_off/不伪造/not_computed）、finalization identity（同代复用；改
final_content / 改 evidence universe → 新 generation）、fail-open（extractor/stage/store 失败
不冒泡）、provenance（代际记录）。

F7 是 orchestration layer：不重新实现 F3/F4/F5/F6 算法（AC-Orch）。
"""

import json

from app.research import migrations, provenance, registry
from app.research.bridge import (
    FinalizeBudget,
    finalize_run,
    get_run_research_state,
    run_finalization_history,
)
from app.research.conflict import FakeDetector as F4Detector
from app.research.extractor import FakeExtractor
from app.research.normalize import canonical_key_for
from app.research.schemas import ClaimType
from app.research.verify import FakeVerifier as F3Fake
from tests._f2_helpers import make_run


def _claim(run_id, sqid, statement, ctype=ClaimType.FACT):
    return registry.create_claim(run_id, sqid, statement, ctype)


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


def _setup_evidence_run(run="f7"):
    """create run + root sqid + 两条独立 evidence；返回 (run_id, sqid, ev_a, ev_b)。"""
    run_id, sqid = make_run(run)
    ev_a = _web(run_id, sqid, "https://a.example/1", "标题 A", "content alpha body one")
    ev_b = _web(run_id, sqid, "https://b.example/2", "标题 B", "content beta body two")
    return run_id, sqid, ev_a, ev_b


def _cand(statement="X 存在", ctype="FACT", evidence=None):
    c = {"statement": statement, "claim_type": ctype}
    if evidence is not None:
        c["evidence"] = evidence
    return c


def _ok_extractor(*claims):
    return FakeExtractor(claims=list(claims))


class TestSchemaAndF1Metadata:
    def test_no_new_migration_and_run_metadata_writable(self, research_sqlite):
        """F7 不新增 migration；research_runs.metadata 可写 finalizations 键（不触碰其它键）。"""
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
        run_id, sqid = make_run("f7meta")
        ev = _web(run_id, sqid, "https://m.example/1", "M", "content m")
        cid = _claim(run_id, sqid, "meta claim")
        registry.bind_claim_evidence(run_id, cid, ev)
        # 写一个无关 metadata 键 + finalizations 键，确认互不覆盖
        with store.transaction() as tx:
            tx.execute(
                "UPDATE research_runs SET metadata=%s WHERE run_id=%s",
                (json.dumps({"other_key": 1}), run_id),
            )
        from app.research.bridge import record_finalization_generation

        record_finalization_generation(store, run_id, {"fingerprint": "fp1", "n": 1})
        row = store.execute(
            "SELECT metadata FROM research_runs WHERE run_id=%s", (run_id,)
        )[0]
        meta = (
            json.loads(row["metadata"])
            if isinstance(row["metadata"], str)
            else row["metadata"]
        )
        assert meta["other_key"] == 1
        assert meta["research_finalizations"][0]["fingerprint"] == "fp1"


class TestClaimMaterialization:
    def test_valid_claim_with_quote_citation(self, research_sqlite):
        run_id, sqid, ev_a, ev_b = _setup_evidence_run("f7valid")
        quote = "content alpha body one"
        res = finalize_run(
            run_id,
            "报告断言 X 存在，引述来源。",
            extractor=_ok_extractor(
                _cand("X 存在", "FACT", [{"evidence_id": ev_a, "quote": quote}]),
                _cand("Y 存在", "STATISTIC", [{"evidence_id": ev_b}]),
            ),
        )
        assert res["ok"] is True
        state = res["state"]
        assert state["claims_total"] == 2
        assert state["claims_unanchored"] == 0
        assert state["pipeline"]["f2"]["status"] == "executed"
        assert state["pipeline"]["f3"]["status"] == "skipped_off"
        # 落库断言：binding + citation（quote 子串）
        chain = provenance.get_claim_chain(provenance.list_claims(run_id)[0].claim_id)
        assert chain["evidences"], "应有 binding 支撑的 evidence"
        assert chain["citations"], "quote 有效时应建 citation"

    def test_invalid_statement_or_type_skipped(self, research_sqlite):
        run_id, sqid = make_run("f7badtype")
        ev = _web(run_id, sqid, "https://x.example/1", "X", "content x body")
        # claim_type 非法 → FakeExtractor 校验即 malformed（extractor 边界），bridge 记录 extractor 失败
        bad_type = FakeExtractor(
            claims=[_cand("断言", "NOT_A_TYPE", [{"evidence_id": ev}])]
        )
        res = finalize_run(run_id, "text", extractor=bad_type)
        assert res["ok"] is True
        assert res["state"]["claims_total"] == 0
        assert res["generation"]["meta"]["extractor_ok"] is False
        # 空 statement（结构非法）同样 malformed
        bad_stmt = FakeExtractor(
            claims=[{"statement": "  ", "claim_type": "FACT", "evidence": []}]
        )
        res2 = finalize_run(run_id, "text2", extractor=bad_stmt)
        assert res2["state"]["claims_total"] == 0

    def test_missing_anchor_claim_unanchored(self, research_sqlite):
        run_id, sqid = make_run("f7nanchor")
        _web(run_id, sqid, "https://u.example/1", "U", "content u body")
        res = finalize_run(
            run_id,
            "final text",
            extractor=_ok_extractor(
                _cand("独立断言", "FACT", [{"evidence_id": "no-such-evidence"}])
            ),
        )
        assert res["ok"] is True
        state = res["state"]
        assert state["claims_total"] == 1
        assert state["claims_unanchored"] == 1
        claim = provenance.list_claims(run_id)[0]
        assert claim.metadata.get("unanchored") is True
        # 无 binding/citation
        assert provenance.get_claim_chain(claim.claim_id)["evidences"] == []
        assert provenance.get_claim_chain(claim.claim_id)["citations"] == []

    def test_cross_run_evidence_unanchored(self, research_sqlite):
        run_id, _sqid, _a, _b = _setup_evidence_run("f7cross-run")
        other_run, other_sq = make_run("f7other")
        ev_other = _web(
            other_run, other_sq, "https://o.example/1", "O", "other content"
        )
        res = finalize_run(
            run_id,
            "text",
            extractor=_ok_extractor(
                _cand("跨 run 断言", "FACT", [{"evidence_id": ev_other}])
            ),
        )
        assert res["ok"] is True
        assert res["state"]["claims_unanchored"] == 1
        # 未建跨 run binding
        claims = provenance.list_claims(run_id)
        assert claims[0].metadata.get("unanchored") is True
        assert provenance.get_claim_chain(claims[0].claim_id)["evidences"] == []

    def test_invalid_quote_not_binding_but_valid_quote_is(self, research_sqlite):
        run_id, sqid, ev_a, ev_b = _setup_evidence_run("f7quote")
        # ev_a quote 非法（非子串）→ 该 anchor 无效；ev_b quote 合法 → binding + citation
        res = finalize_run(
            run_id,
            "text",
            extractor=_ok_extractor(
                _cand(
                    "Q claim",
                    "FACT",
                    [
                        {"evidence_id": ev_a, "quote": "这不是子串"},
                        {"evidence_id": ev_b, "quote": "content beta body two"},
                    ],
                )
            ),
        )
        assert res["ok"] is True
        assert res["state"]["claims_total"] == 1
        assert res["state"]["claims_unanchored"] == 0
        claim = provenance.list_claims(run_id)[0]
        chain = provenance.get_claim_chain(claim.claim_id)
        assert [e["evidence"]["evidence_id"] for e in chain["evidences"]] == [ev_b]
        assert [c["evidence_id"] for c in chain["citations"]] == [ev_b]
        assert claim.metadata.get("unanchored") is None

    def test_claim_cap_and_binding_cap(self, research_sqlite):
        run_id, sqid, ev_a, ev_b = _setup_evidence_run("f7cap")
        _web(run_id, sqid, "https://c.example/3", "C", "content c body three")
        # 12 claims cap：budget.max_claims_per_run=2
        claims = [_cand(f"断言 {i}", "FACT", [{"evidence_id": ev_a}]) for i in range(4)]
        budget = FinalizeBudget(max_claims_per_run=2)
        res = finalize_run(
            run_id, "text", extractor=_ok_extractor(*claims), budget=budget
        )
        assert res["ok"] is True
        assert res["state"]["claims_total"] == 2
        assert res["generation"]["meta"]["truncated"] is True
        # binding cap=2：3 anchors → 仅前 2
        run2, sq2 = make_run("f7bcap")
        e1 = _web(run2, sq2, "https://b1.example/1", "B1", "content one")
        e2 = _web(run2, sq2, "https://b2.example/2", "B2", "content two")
        e3 = _web(run2, sq2, "https://b3.example/3", "B3", "content three")
        budget2 = FinalizeBudget(max_evidence_bindings=2)
        res2 = finalize_run(
            run2,
            "text",
            extractor=_ok_extractor(
                _cand(
                    "binding cap claim",
                    "FACT",
                    [
                        {"evidence_id": e1},
                        {"evidence_id": e2},
                        {"evidence_id": e3},
                    ],
                )
            ),
            budget=budget2,
        )
        assert res2["ok"] is True
        claim2 = provenance.list_claims(run2)[0]
        assert len(provenance.get_claim_chain(claim2.claim_id)["evidences"]) == 2


class TestExtractorBoundary:
    def test_verdict_field_rejected_malformed(self, research_sqlite):
        """FakeExtractor 输出含 verdict → malformed；extractor 从不产生 F3 verdict artifact。"""
        run_id, sqid, ev_a, _b = _setup_evidence_run("f7verdict")
        bad = FakeExtractor(
            claims=[
                {
                    "statement": "X 存在",
                    "claim_type": "FACT",
                    "verdict": "SUPPORTS",
                    "evidence": [{"evidence_id": ev_a}],
                }
            ]
        )
        with_bad = finalize_run(run_id, "text", extractor=bad)
        assert with_bad["ok"] is True
        assert with_bad["state"]["claims_total"] == 0
        assert with_bad["generation"]["meta"]["extractor_ok"] is False
        assert "被禁止" in with_bad["generation"]["meta"]["extractor_error"]
        # 没有任何 verification（verdict 只能由 F3 产生，F3 disabled）
        from app.research import store as rstore

        rows = rstore.get_store().execute(
            "SELECT count(*) AS n FROM verifications WHERE run_id=%s", (run_id,)
        )
        assert rows[0]["n"] == 0

    def test_fake_extractor_never_sets_verdict(self, research_sqlite):
        """OK extractor 输出经校验后仅含 statement/claim_type/evidence 候选。"""
        run_id, sqid, ev_a, _b = _setup_evidence_run("f7noverdict")
        ex = _ok_extractor(
            _cand("正常断言", "FACT", [{"evidence_id": ev_a}]),
            _cand("统计断言", "STATISTIC"),
        )
        res = finalize_run(run_id, "text", extractor=ex)
        assert (
            res["state"]["claims_total"] == 2
        )  # 两条都落库：一条 anchored + 一条 unanchored
        assert res["state"]["claims_unanchored"] == 1


class TestOrchestration:
    def test_enabled_full_chain_uses_existing_apis(self, research_sqlite):
        """enabled 时 F2→F3→F4→F5→F6 全链走既有 public API 并产出真实 artifact。"""
        from app.research import store as rstore

        store = rstore.get_store()
        run_id, sqid, ev_a, ev_b = _setup_evidence_run("f7chain")
        res = finalize_run(
            run_id,
            "结论：X 受支持亦受反驳。",
            extractor=_ok_extractor(
                _cand(
                    "X 为真",
                    "FACT",
                    [
                        {"evidence_id": ev_a, "quote": "content alpha body one"},
                        {"evidence_id": ev_b, "quote": "content beta body two"},
                    ],
                )
            ),
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
        assert res["ok"] is True
        state = res["state"]
        assert state["pipeline"]["f2"]["status"] == "executed"
        assert state["pipeline"]["f3"]["status"] == "executed"
        assert state["pipeline"]["f4"]["status"] == "executed"
        assert state["pipeline"]["f5"]["status"] == "executed"
        assert state["pipeline"]["f6"]["status"] == "executed"
        # F3 verification 真实产出（同一 claim 双证据 SUPPORTS + CONTRADICTS）
        v = store.execute(
            "SELECT verdict FROM verifications WHERE run_id=%s AND status='succeeded'",
            (run_id,),
        )
        assert {r["verdict"] for r in v} == {"SUPPORTS", "CONTRADICTS"}
        # F4 confirmed → F5 corroboration → F6 reconciliation 产物
        conflicts = store.execute(
            "SELECT conflict_id FROM conflicts WHERE run_id=%s AND status='confirmed'",
            (run_id,),
        )
        assert conflicts
        corr = store.execute(
            "SELECT corroboration_id FROM corroborations WHERE run_id=%s", (run_id,)
        )
        assert corr
        rec = store.execute(
            "SELECT reconciliation_id FROM reconciliations WHERE run_id=%s", (run_id,)
        )
        assert rec
        # F3 aggregate：该 claim contested（SUPPORTS+CONTRADICTS）→ contested_claims=1
        assert state["contested_claims"] == 1
        assert state["verified_claims"] == 0
        # F6 unresolved：register=genuine_contested → unresolved_claims=1
        assert state["unresolved_claims"] == 1
        # AC-Orch：产物全部来自既有 F3–F6 表，bridge 未自行实现算法
        assert state["pipeline"]["f5"]["complete"] >= 1

    def test_disabled_stages_skipped_no_fake_artifact(self, research_sqlite):
        """disabled → skipped_off；无伪 artifact；计数 not_computed/null（绝不写成 0 结论）。"""
        run_id, sqid, ev_a, ev_b = _setup_evidence_run("f7off")
        res = finalize_run(
            run_id,
            "text",
            extractor=_ok_extractor(
                _cand("A 为真", "FACT", [{"evidence_id": ev_a}]),
                _cand("B 为真", "FACT", [{"evidence_id": ev_b}]),
            ),
            f3_enabled=False,
            f4_enabled=False,
            f5_enabled=False,
            f6_enabled=False,
        )
        state = res["state"]
        assert state["pipeline"]["f3"]["status"] == "skipped_off"
        assert state["pipeline"]["f4"]["status"] == "skipped_off"
        assert state["pipeline"]["f5"]["status"] == "skipped_off"
        assert state["pipeline"]["f6"]["status"] == "skipped_off"
        assert state["verified_claims"] is None  # not_computed
        assert state["contested_claims"] is None
        assert state["unresolved_claims"] is None
        from app.research import store as rstore

        store = rstore.get_store()
        for table in (
            "verifications",
            "conflicts",
            "corroborations",
            "reconciliations",
        ):
            n = store.execute(
                f"SELECT count(*) AS n FROM {table} WHERE run_id=%s", (run_id,)
            )[0]["n"]
            assert n == 0, f"{table} 不应有伪 artifact（实际 {n}）"
        # claims 仍在（F2 物化不依赖 semantic 阶段）
        assert state["claims_total"] == 2


class TestFinalizationIdentity:
    def test_same_identity_reuse(self, research_sqlite):
        run_id, _sqid, ev_a, _b = _setup_evidence_run("f7same")
        ex = _ok_extractor(_cand("稳定断言", "FACT", [{"evidence_id": ev_a}]))
        r1 = finalize_run(run_id, "same content", extractor=ex)
        r2 = finalize_run(run_id, "same content", extractor=ex)
        assert r1["generation"]["fingerprint"] == r2["generation"]["fingerprint"]
        assert r2["state"].get("reused_generation") is True
        assert len(run_finalization_history(run_id)) == 1
        # claims 不重复物化
        assert r2["state"]["claims_total"] == 1

    def test_changed_final_content_new_generation(self, research_sqlite):
        run_id, _sqid, ev_a, _b = _setup_evidence_run("f7content")
        ex = _ok_extractor(_cand("断言 v1", "FACT", [{"evidence_id": ev_a}]))
        r1 = finalize_run(run_id, "content v1", extractor=ex)
        ex2 = _ok_extractor(_cand("断言 v2", "FACT", [{"evidence_id": ev_a}]))
        r2 = finalize_run(run_id, "content v2 changed", extractor=ex2)
        assert r1["generation"]["fingerprint"] != r2["generation"]["fingerprint"]
        assert r2["state"].get("reused_generation") is None
        assert len(run_finalization_history(run_id)) == 2
        # 新断言物化 → claims_total=2（旧断言保留，不删除）
        assert r2["state"]["claims_total"] == 2

    def test_changed_evidence_universe_new_generation(self, research_sqlite):
        run_id, sqid, ev_a, _b = _setup_evidence_run("f7universe")
        ex = _ok_extractor(_cand("断言", "FACT", [{"evidence_id": ev_a}]))
        r1 = finalize_run(run_id, "same content", extractor=ex)
        # 加入新 evidence → universe hash 变化
        _web(run_id, sqid, "https://new.example/9", "NEW", "brand new content nine")
        r2 = finalize_run(run_id, "same content", extractor=ex)
        assert r1["generation"]["fingerprint"] != r2["generation"]["fingerprint"]
        assert r2["state"].get("reused_generation") is None
        assert len(run_finalization_history(run_id)) == 2

    def test_changed_extractor_spec_new_generation(self, research_sqlite):
        run_id, _sqid, ev_a, _b = _setup_evidence_run("f7extspec")
        ex1 = _ok_extractor(_cand("A", "FACT", [{"evidence_id": ev_a}]))
        ex2 = FakeExtractor(
            claims=[_cand("B", "FACT", [{"evidence_id": ev_a}])], name="fake.v2"
        )
        r1 = finalize_run(run_id, "same content", extractor=ex1)
        r2 = finalize_run(run_id, "same content", extractor=ex2)
        assert r1["generation"]["fingerprint"] != r2["generation"]["fingerprint"]


class TestFailOpen:
    def test_extractor_raises_does_not_break(self, research_sqlite):
        run_id, sqid, ev_a, _b = _setup_evidence_run("f7boom")

        class _BoomExtractor(FakeExtractor):
            def extract(self, final_content, evidence_envelope):
                raise RuntimeError("provider down")

        res = finalize_run(
            run_id, "text", extractor=_BoomExtractor(claims=[_cand("X")])
        )
        assert res["ok"] is True  # 不冒泡
        assert res["state"]["claims_total"] == 0
        assert res["generation"]["meta"]["extractor_ok"] is False

    def test_f2_validation_error_recorded_not_blocked(self, research_sqlite):
        """F2 报告有 error（非法跨 run binding）→ pipeline.f2 记录 errors，后续仍完成。"""
        from app.research import store as rstore

        store = rstore.get_store()
        run_a, sq_a = make_run("f7err-a")
        run_b, sq_b = make_run("f7err-b")
        ev = _web(run_a, sq_a, "https://e.example/1", "E", "content e body")
        _web(run_b, sq_b, "https://o.example/2", "O", "content o")
        r1 = finalize_run(
            run_a,
            "text",
            extractor=_ok_extractor(_cand("正常断言", "FACT", [{"evidence_id": ev}])),
        )
        assert r1["ok"] is True
        claim_id = provenance.list_claims(run_a)[0].claim_id
        # 注入跨 run binding → R6 deterministic error
        ev_other = _web(run_b, sq_b, "https://o2.example/3", "O2", "other content o2")
        with store.transaction() as tx:
            tx.execute(
                "INSERT INTO claim_evidences (binding_id, run_id, claim_id, evidence_id,"
                " created_at, metadata) VALUES (%s,%s,%s,%s,%s,%s)",
                (
                    "f7-cross",
                    run_a,
                    claim_id,
                    ev_other,
                    "2026-01-01T00:00:00+00:00",
                    "{}",
                ),
            )
        # 改内容 → 新 generation → 重跑 F2 → errors 被记录，但 finalize 不冒泡、仍完成
        ex2 = _ok_extractor(_cand("另一断言", "FACT", [{"evidence_id": ev}]))
        r2 = finalize_run(run_a, "changed text", extractor=ex2)
        assert r2["ok"] is True
        assert r2["generation"]["pipeline"]["f2"]["status"] == "executed"
        assert r2["generation"]["pipeline"]["f2"]["errors"] >= 1

    def test_store_unavailable_noop(self, research_sqlite, monkeypatch):
        """store disabled → finalize 返回 noop，不抛错。"""
        from app.research import config as rconfig
        from app.research import store as rstore

        monkeypatch.setenv(rconfig.RESEARCH_STORE_ENV, "disabled")
        rstore.reset_store()
        res = finalize_run("any-run", "text", extractor=_ok_extractor(_cand("X")))
        assert res["ok"] is False
        assert "research store" in res["reason"]


class TestStateRead:
    def test_get_state_and_history(self, research_sqlite):
        run_id, sqid, ev_a, _b = _setup_evidence_run("f7read")
        _claim(run_id, sqid, "已有 claim")
        ex = _ok_extractor(_cand("断言", "FACT", [{"evidence_id": ev_a}]))
        finalize_run(run_id, "text", extractor=ex)
        state = get_run_research_state(run_id)
        assert state["run_id"] == run_id
        assert state["finalization_fingerprint"]
        assert state["materialized_at"]
        assert state["extractor"]["provider"] == "fake"
        assert state["claims_total"] == 2
        hist = run_finalization_history(run_id)
        assert len(hist) == 1
        assert hist[0]["pipeline"]["f2"]["status"] == "executed"
        assert get_run_research_state("no-such-run") is None
