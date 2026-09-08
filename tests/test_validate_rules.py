"""F2 Validator 测试：R1–R10 规则矩阵 + 纯函数/无副作用 + finalizer status 边界。

R1/R4/R6/R7/R9 需要绕过 registry 守卫或 FK 的直接注入（验证器读端审计路径）——
sqlite 用独立连接（默认 foreign_keys=OFF）或 store 事务写入语义非法但 FK 合法的行。
PG 镜像见 tests/test_f2_postgres.py（R1/R4 的 FK-off 注入为 sqlite 专有，其余规则同路径）。
"""

import os
import sqlite3

import pytest

from app.research import provenance, registry
from app.research.registry import ResearchRunNotFoundError
from app.research.schemas import ClaimType
from app.research.validate import validate_run
from app.research.config import RESEARCH_DB_ENV
from tests._f2_helpers import add_web_evidence, make_run


def _raw_conn():
    return sqlite3.connect(os.environ[RESEARCH_DB_ENV])


def _claim(run_id, sqid, statement):
    return registry.create_claim(run_id, sqid, statement, ClaimType.FACT)


def _web(run_id, sqid, content="证据正文 ABC 123 连续片段", url="https://x.example/1"):
    return add_web_evidence(run_id, sqid, content, url)[2]


class TestBaseAndPurity:
    def test_clean_run_no_violations(self, research_sqlite):
        run_id, sqid = make_run()
        eid = _web(run_id, sqid)
        cid = _claim(run_id, sqid, "结论")
        registry.bind_claim_evidence(run_id, cid, eid)
        registry.create_citation(
            run_id, cid, eid, quote="证据正文 ABC", locator="sec:1"
        )
        report = validate_run(run_id)
        assert report.errors == []
        assert report.warnings == []
        assert report.counts == {
            "claims": 1,
            "unsupported": 0,
            "unreferenced": 0,
            "cited": 1,
        }
        assert report.cited_claim_ids == [cid]

    def test_validator_pure_no_side_effect(self, research_sqlite):
        run_id, sqid = make_run()
        eid = _web(run_id, sqid)
        cid = _claim(run_id, sqid, "纯函数验证")
        registry.bind_claim_evidence(run_id, cid, eid)
        registry.create_citation(
            run_id, cid, eid, quote="证据正文 ABC", locator="sec:1"
        )
        before_status = provenance.list_claims(run_id)[0].status

        r1 = validate_run(run_id)
        r2 = validate_run(run_id)
        assert r1.to_dict() == r2.to_dict()
        # claims.status 完全不变；无任何新行/新列被写
        claims = provenance.list_claims(run_id)
        assert all(c.status == before_status for c in claims)
        assert len(claims) == 1
        assert len(provenance.list_citations(run_id)) == 1
        conn = _raw_conn()
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        conn.close()
        assert "claims" in tables and "citations" in tables

    def test_apply_validation_outcome_single_write_entry(self, research_sqlite):
        run_id, sqid = make_run()
        eid = _web(run_id, sqid)
        cited = _claim(run_id, sqid, "被引用结论")
        unsupported = _claim(run_id, sqid, "无证据陈述")
        registry.bind_claim_evidence(run_id, cited, eid)
        registry.create_citation(run_id, cited, eid, quote="证据正文 ABC")
        report = validate_run(run_id)
        assert registry.apply_validation_outcome(run_id, report) == 1
        statuses = {c.claim_id: c.status for c in provenance.list_claims(run_id)}
        assert statuses[cited] == "validated"
        assert statuses[unsupported] == "drafted"  # unsupported 保持 drafted
        # 再次 apply 无变化（唯一写入口幂等）
        assert registry.apply_validation_outcome(run_id, report) == 0
        # report 与 run 不一致 → 拒绝
        other, _ = make_run("other")
        with pytest.raises(ValueError, match="不一致"):
            registry.apply_validation_outcome(other, report)
        with pytest.raises(ResearchRunNotFoundError):  # 占位确认守卫 API 存在
            registry.create_claim("no-run", "sq", "x", ClaimType.FACT)


class TestRuleMatrix:
    def _rule_flags(self, run_id):
        report = validate_run(run_id)
        return {e["rule"] for e in report.errors}, {w["rule"] for w in report.warnings}

    def test_r1_dangling_citation(self, research_sqlite):
        run_id, sqid = make_run()
        _claim(run_id, sqid, "孤儿引用")
        conn = _raw_conn()
        conn.execute(
            "INSERT INTO citations (citation_id, run_id, claim_id, evidence_id, "
            "quote, locator, metadata, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                "orphan-c1",
                run_id,
                "ghost-claim",
                "ghost-evidence",
                None,
                None,
                "{}",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        conn.commit()
        conn.close()
        errors, _ = self._rule_flags(run_id)
        assert "R1" in errors

    def test_r2_citation_without_binding(self, research_sqlite):
        run_id, sqid = make_run()
        eid = _web(run_id, sqid)
        cid = _claim(run_id, sqid, "未绑定即引用")
        conn = _raw_conn()
        conn.execute(
            "INSERT INTO citations (citation_id, run_id, claim_id, evidence_id, "
            "quote, locator, metadata, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                "no-bind-c1",
                run_id,
                cid,
                eid,
                None,
                None,
                "{}",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        conn.commit()
        conn.close()
        errors, _ = self._rule_flags(run_id)
        assert "R2" in errors

    def test_r3_unsupported_no_binding(self, research_sqlite):
        run_id, sqid = make_run()
        cid = _claim(run_id, sqid, "无任何证据的陈述")
        report = validate_run(run_id)
        assert {"R3"} == {w["rule"] for w in report.warnings}
        assert report.counts["unsupported"] == 1
        assert cid in report.warnings[0]["claim_id"]

    def test_r4_invalid_evidence_source(self, research_sqlite):
        run_id, sqid = make_run()
        _claim(run_id, sqid, "孤儿证据的陈述")
        conn = _raw_conn()
        conn.execute(
            "INSERT INTO evidences (evidence_id, run_id, source_id, sub_question_id, "
            "content, locator, extraction_method, metadata, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "ghost-ev",
                run_id,
                "ghost-source",
                sqid,
                "body",
                "l",
                "web_result",
                "{}",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        conn.commit()
        conn.close()
        errors, _ = self._rule_flags(run_id)
        assert "R4" in errors

    def test_r5_evidence_source_cross_run(self, research_sqlite):
        run_id, sqid = make_run()
        run2, sq2 = make_run("r2")
        _q2, s2, _e2 = add_web_evidence(run2, sq2, "r2 body", url="https://x.example/2")
        _claim(run_id, sqid, "跨 run 证据")
        # 用 store 直接写入证据：evidence 归 run1，source 归 run2（FK 存在、run 不等）
        from app.research import store as rstore

        store = rstore.get_store()
        eid = "cross-src-ev"
        with store.transaction() as tx:
            tx.execute(
                "INSERT INTO evidences (evidence_id, run_id, source_id, sub_question_id, "
                "content, locator, extraction_method, metadata, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    eid,
                    run_id,
                    s2,
                    sqid,
                    "body",
                    "l",
                    "web_result",
                    "{}",
                    "2026-01-01T00:00:00+00:00",
                ),
            )
        errors, _ = self._rule_flags(run_id)
        assert "R5" in errors
        # run2 的 e2 本身健康（不因 run1 的跨引用被污染）
        errors2, _ = self._rule_flags(run2)
        assert "R5" not in errors2

    def test_r6_claim_evidence_cross_run(self, research_sqlite):
        run1, sq1 = make_run("a")
        run2, sq2 = make_run("b")
        e_local = _web(run1, sq1, "local body")
        cid = _claim(run1, sq1, "跨 run 绑定")
        registry.bind_claim_evidence(run1, cid, e_local)
        registry.create_citation(run1, cid, e_local, quote="local body", locator="s")
        # 注入跨 run binding：claim(run1) ↔ evidence(run2)
        e2 = _web(run2, sq2, "b body", url="https://x.example/2")
        from app.research import store as rstore

        store = rstore.get_store()
        with store.transaction() as tx:
            tx.execute(
                "INSERT INTO claim_evidences (binding_id, run_id, claim_id, evidence_id, "
                "created_at, metadata) VALUES (%s, %s, %s, %s, %s, %s)",
                ("cross-b", run1, cid, e2, "2026-01-01T00:00:00+00:00", "{}"),
            )
        errors, warnings = self._rule_flags(run1)
        assert "R6" in errors
        assert "R10" not in warnings  # 该 claim 有合法 citation 覆盖

    def test_r7_locator_invalid(self, research_sqlite):
        run_id, sqid = make_run()
        eid = _web(run_id, sqid, "body")
        cid = _claim(run_id, sqid, "locator 陈述")
        registry.bind_claim_evidence(run_id, cid, eid)
        # registry 会拒绝，因此直接注入坏 locator
        from app.research import store as rstore

        store = rstore.get_store()
        with store.transaction() as tx:
            tx.execute(
                "INSERT INTO citations (citation_id, run_id, claim_id, evidence_id, "
                "quote, locator, metadata, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    "bad-loc",
                    run_id,
                    cid,
                    eid,
                    None,
                    "bad\nloc",
                    "{}",
                    "2026-01-01T00:00:00+00:00",
                ),
            )
        errors, _ = self._rule_flags(run_id)
        assert "R7" in errors

    def test_r8_quote_not_substring(self, research_sqlite):
        run_id, sqid = make_run()
        eid = _web(run_id, sqid, "证据正文 ABC 123 连续片段")
        cid = _claim(run_id, sqid, "quote 陈述")
        registry.bind_claim_evidence(run_id, cid, eid)
        registry.create_citation(
            run_id, cid, eid, quote="根本不在正文里的片段", locator="s"
        )
        errors, warnings = self._rule_flags(run_id)
        assert "R8" in warnings
        assert errors == set()

    def test_r10_unreferenced_bound_claim(self, research_sqlite):
        run_id, sqid = make_run()
        eid = _web(run_id, sqid, "bound body")
        cid = _claim(run_id, sqid, "已绑定但未进报告")
        registry.bind_claim_evidence(run_id, cid, eid)
        report = validate_run(run_id)
        rules = {w["rule"] for w in report.warnings}
        assert "R10" in rules
        assert "R3" not in rules  # 有 binding → 不是 unsupported
        assert report.counts["unreferenced"] == 1
        assert report.counts["unsupported"] == 0

    def test_r9_write_layer_and_no_false_positive(self, research_sqlite):
        run_id, sqid = make_run()
        cid = _claim(run_id, sqid, "幂等陈述")
        assert registry.create_claim(run_id, sqid, "幂等陈述", ClaimType.OPINION) == cid
        report = validate_run(run_id)
        assert "R9" not in {e["rule"] for e in report.errors}
        # 唯一键在 DB 层强制：直接注入重复 sha 必须失败
        conn = _raw_conn()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO claims (claim_id, run_id, sub_question_id, statement, "
                "statement_sha, claim_type, status, created_at, metadata) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    "dup-c",
                    run_id,
                    sqid,
                    "幂等陈述",
                    registry.sha256_hex("幂等陈述"),
                    "FACT",
                    "drafted",
                    "2026-01-01T00:00:00+00:00",
                    "{}",
                ),
            )
        conn.close()
