"""F9-P0 Batch 1 — Research State Projection PostgreSQL gate（RESEARCH_DSN_TEST 门控）。

PostgreSQL 是唯一生产基线与最终 Gate Backend；本文件在真实 PG16 上验证：
- cross-DB 逻辑等价：同一逻辑+物理 state（相同 id/时间戳）分别 seed 到 PG 与 sqlite →
  projection 输出逐字节一致（deterministic ordering / 时间戳归一 / genuine 归一 / JSON 载荷）；
- 语义抽查：best_verdict（多 succeeded / succeeded+failed / 仅 failed / 同刻 tie）、
  required_uncovered（无 ev 无 src / 有 src 无 ev / 有 ev / 非 required child）、
  conflicts（无 rec / GENUINE_CONTESTED / 缓解 outcome / failed rec）、independent_flag、
  fresh（注入 now）、domain 计数（含 unknown_domain 桶）、budget snapshot 只读、错误路径。

SQLite PASS ≠ PostgreSQL PASS；SQLite 语义单测在 test_research_projection.py。
"""

from __future__ import annotations

import datetime
import json
import os
import uuid

import pytest

from app.research import projection as f9p
from app.research import migrations, store as rstore
from tests import _research_helpers as h

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

FIXED_NOW = datetime.datetime(2026, 9, 10, 0, 0, 0, tzinfo=datetime.timezone.utc)


def _u() -> str:
    return uuid.uuid4().hex


def _budget(limits=None, counts=None):
    default_limits = {
        "llm_calls": 120,
        "tool_calls": 300,
        "search_calls": 40,
        "agent_steps": 200,
    }
    lim = dict(default_limits if limits is None else limits)
    cnt = dict(default_counts() if counts is None else counts)
    return {"limits": lim, "counts": cnt}


def default_counts():
    return {k: 0 for k in f9p.BUDGET_KINDS}


@needs_pg
class TestF9ProjectionPostgres:
    def _pg_store(self):
        store = rstore._PostgresStore(os.getenv(PG_TEST_DSN_ENV))
        migrations.ensure_schema(store)
        return store

    def _sqlite_store(self, tmp_path):
        store = rstore._SqliteStore(str(tmp_path / f"rs-{uuid.uuid4().hex}.sqlite"))
        migrations.ensure_schema(store)
        return store

    def _seed_and_purge(self, store, run_id, seed_fn):
        try:
            seed_fn(store, run_id)
        except Exception:
            h.purge_run(store, run_id)
            raise

    def test_cross_db_logical_equivalence(self, research_tmp):
        """相同 id/时间戳 state：PG 与 sqlite 的 projection 完全一致（顺序/类型/载荷）。"""
        run_id = f"run-f9-eq-{uuid.uuid4().hex[:8]}"
        pg = self._pg_store()
        sq = self._sqlite_store(research_tmp)
        try:
            h.seed_rich_scenario(pg, run_id)
            h.seed_rich_scenario(sq, run_id)
            pa = f9p.project(run_id, round=2, budget=_budget(), store=pg)
            pb = f9p.project(run_id, round=2, budget=_budget(), store=sq)
            assert pa == pb
            assert json.dumps(pa, sort_keys=True) == json.dumps(pb, sort_keys=True)
            # 语义抽查（PG 侧与 sqlite 等价即为双方一致）
            claims = {c["claim_id"]: c for c in pa["claims"]}
            assert list(claims) == ["c-1", "c-2"]  # (created_at, claim_id) 稳定序
            assert claims["c-1"]["best_verdict"] == "SUPPORTS"  # failed 不参与 latest
            assert (
                claims["c-2"]["best_verdict"] == "INSUFFICIENT"
            )  # 同刻按 verification_id 取末
            assert claims["c-1"]["independent_flag"] is True
            c1_conflicts = {c["conflict_id"] for c in claims["c-1"]["conflicts"]}
            assert c1_conflicts == {"f-1", "f-2"}  # f-3（DETAIL_INCONSISTENCY）缓解排除
            assert pa["required_uncovered"] == []  # root/child 均有 evidence 覆盖
            es = pa["evidence_summary"]
            assert es["count"] == 4
            assert es["by_domain"] == {"bbc.co.uk": 1, "example.com": 3}
            assert pa["size_limits"]["omitted"] == {
                "sub_questions": 0,
                "claims": 0,
                "evidence_refs": 0,
                "conflicts": 0,
            }
        finally:
            h.purge_run(pg, run_id)
            sq.close()

    def test_cross_db_insertion_order_independent(self, research_tmp):
        """reverse 插入顺序的同一 state：PG projection 与 sqlite（正序）完全一致。"""
        run_id = f"run-f9-ri-{uuid.uuid4().hex[:8]}"
        pg = self._pg_store()
        sq = self._sqlite_store(research_tmp)
        try:
            h.seed_rich_scenario(pg, run_id, reverse=True)
            h.seed_rich_scenario(sq, run_id, reverse=False)
            pa = f9p.project(run_id, budget=_budget(), store=pg)
            pb = f9p.project(run_id, budget=_budget(), store=sq)
            assert pa == pb
        finally:
            h.purge_run(pg, run_id)
            sq.close()

    def test_best_verdict_cases(self):
        run_id = f"run-f9-bv-{uuid.uuid4().hex[:8]}"
        pg = self._pg_store()
        try:
            h.insert_run(pg, run_id)
            sq_id = _u()
            h.insert_subq(pg, run_id, sq_id, position=0, question="root")

            def claim(cid, statement, ev_id):
                h.insert_claim(
                    pg,
                    run_id,
                    cid,
                    sq_id,
                    statement=statement,
                    created_at="2026-09-02T12:00:00+00:00",
                )
                qid, sid, eid = _u(), _u(), _u()
                h.insert_query(pg, run_id, qid, sq_id)
                h.insert_source(pg, run_id, sid, qid, url=f"https://example.com/{cid}")
                h.insert_evidence(
                    pg,
                    run_id,
                    eid,
                    sid,
                    sq_id,
                    content=ev_id,
                    created_at="2026-09-02T10:00:00+00:00",
                )
                h.insert_binding(pg, run_id, _u(), cid, eid)
                return eid

            # A：succeeded+failed → SUPPORTS（failed 排除）
            e_a = claim("c-a", "A", "ev-a")
            h.insert_verification(
                pg,
                run_id,
                "v-a1",
                "c-a",
                e_a,
                verdict="SUPPORTS",
                status="succeeded",
                created_at="2026-09-02T13:00:00+00:00",
            )
            h.insert_verification(
                pg,
                run_id,
                "v-a2",
                "c-a",
                e_a,
                verdict="CONTRADICTS",
                status="failed",
                created_at="2026-09-02T14:00:00+00:00",
            )
            # B：仅 failed → None
            e_b = claim("c-b", "B", "ev-b")
            h.insert_verification(
                pg,
                run_id,
                "v-b1",
                "c-b",
                e_b,
                verdict="CONTRADICTS",
                status="failed",
                created_at="2026-09-02T13:00:00+00:00",
            )
            # C：同刻双 succeeded → INSUFFICIENT（id 较大者）
            e_c = claim("c-c", "C", "ev-c")
            h.insert_verification(
                pg,
                run_id,
                "v-caa",
                "c-c",
                e_c,
                verdict="SUPPORTS",
                status="succeeded",
                created_at="2026-09-02T13:30:00+00:00",
            )
            h.insert_verification(
                pg,
                run_id,
                "v-czz",
                "c-c",
                e_c,
                verdict="INSUFFICIENT",
                status="succeeded",
                created_at="2026-09-02T13:30:00+00:00",
            )
            p = f9p.project(run_id, budget=_budget(), store=pg)
            claims = {c["claim_id"]: c for c in p["claims"]}
            assert claims["c-a"]["best_verdict"] == "SUPPORTS"
            assert claims["c-b"]["best_verdict"] is None
            assert claims["c-c"]["best_verdict"] == "INSUFFICIENT"
        finally:
            h.purge_run(pg, run_id)

    def test_required_uncovered_semantics(self):
        run_id = f"run-f9-ru-{uuid.uuid4().hex[:8]}"
        pg = self._pg_store()
        try:
            h.insert_run(pg, run_id)
            sq_root = "sq-root-x"
            sq_child = "sq-child-x"
            h.insert_subq(pg, run_id, sq_root, position=0, question="root")
            h.insert_subq(
                pg, run_id, sq_child, position=1, question="child", parent_id=sq_root
            )
            # child：有 source 无 evidence（覆盖 → 不进 required_uncovered，且非 required）
            qid, sid = _u(), _u()
            h.insert_query(pg, run_id, qid, sq_child)
            h.insert_source(pg, run_id, sid, qid, url="https://example.com/s")
            # root：无 evidence 无 source → uncovered
            p = f9p.project(run_id, budget=_budget(), store=pg)
            assert p["required_uncovered"] == [sq_root]
            by_id = {s["sub_question_id"]: s for s in p["sub_questions"]}
            assert by_id[sq_root]["required"] is True
            assert by_id[sq_child]["required"] is False
            assert by_id[sq_child]["source_count"] == 1
            assert by_id[sq_child]["evidence_count"] == 0
        finally:
            h.purge_run(pg, run_id)

    def test_conflicts_unresolved_and_reconciliation(self):
        run_id = f"run-f9-cf-{uuid.uuid4().hex[:8]}"
        pg = self._pg_store()
        try:
            h.insert_run(pg, run_id)
            sq_id = _u()
            h.insert_subq(pg, run_id, sq_id, position=0, question="root")
            cid = "c-conf"
            h.insert_claim(pg, run_id, cid, sq_id, statement="conf", created_at=ISO())
            evs = []
            for i in range(3):
                qid, sid, eid = _u(), _u(), _u()
                h.insert_query(pg, run_id, qid, sq_id)
                h.insert_source(pg, run_id, sid, qid, url=f"https://example.com/{i}")
                h.insert_evidence(
                    pg, run_id, eid, sid, sq_id, content=f"e-{i}", created_at=ISO()
                )
                h.insert_binding(pg, run_id, _u(), cid, eid)
                evs.append(eid)
            h.insert_conflict(
                pg,
                run_id,
                "f-1",
                cid,
                evs[0],
                evs[1],
                conflict_type="CONTRADICTION",
                genuine=True,
            )
            h.insert_conflict(
                pg,
                run_id,
                "f-2",
                cid,
                evs[0],
                evs[2],
                conflict_type="CONTRADICTION",
                genuine=True,
            )
            h.insert_reconciliation(
                pg,
                run_id,
                "r-2",
                cid,
                "f-2",
                status="complete",
                outcome="GENUINE_CONTESTED",
            )
            h.insert_conflict(
                pg,
                run_id,
                "f-3",
                cid,
                evs[1],
                evs[2],
                conflict_type="INCONSISTENCY",
                genuine=True,
            )
            h.insert_reconciliation(
                pg,
                run_id,
                "r-3",
                cid,
                "f-3",
                status="complete",
                outcome="SAME_ORIGIN_CONTRADICTION",
            )
            p = f9p.project(run_id, budget=_budget(), store=pg)
            claim = {c["claim_id"]: c for c in p["claims"]}[cid]
            got = {c["conflict_id"]: c for c in claim["conflicts"]}
            assert set(got) == {"f-1", "f-2"}
            assert got["f-1"]["reconciliation_status"] is None
            assert got["f-2"]["reconciliation_outcome"] == "GENUINE_CONTESTED"
            assert all(c["genuine"] is True for c in claim["conflicts"])
        finally:
            h.purge_run(pg, run_id)

    def test_independent_flag_and_fresh(self):
        run_id = f"run-f9-if-{uuid.uuid4().hex[:8]}"
        pg = self._pg_store()
        try:
            h.insert_run(pg, run_id)
            sq_id = _u()
            h.insert_subq(pg, run_id, sq_id, position=0, question="root")

            def claim(cid, statement, ev_created_at, indep_count):
                h.insert_claim(
                    pg, run_id, cid, sq_id, statement=statement, created_at=ISO()
                )
                qid, sid, eid = _u(), _u(), _u()
                h.insert_query(pg, run_id, qid, sq_id)
                h.insert_source(pg, run_id, sid, qid, url=f"https://example.com/{cid}")
                h.insert_evidence(
                    pg, run_id, eid, sid, sq_id, content=eid, created_at=ev_created_at
                )
                h.insert_binding(pg, run_id, _u(), cid, eid)
                if indep_count is not None:
                    h.insert_corroboration(
                        pg,
                        run_id,
                        _u(),
                        cid,
                        status="complete",
                        support={
                            "source_count": indep_count,
                            "independent_count": indep_count,
                        },
                    )
                return cid

            stale = claim(
                "c-stale", "stale", "2026-09-08T00:00:00+00:00", indep_count=1
            )
            fresh_ind = claim(
                "c-fresh", "fresh", "2026-09-09T23:30:00+00:00", indep_count=2
            )
            no_corr = claim(
                "c-nocorr", "nocorr", "2026-09-09T23:30:00+00:00", indep_count=None
            )
            p = f9p.project(run_id, round=1, budget=_budget(), store=pg, now=FIXED_NOW)
            claims = {c["claim_id"]: c for c in p["claims"]}
            assert claims[stale]["fresh"] is False
            assert claims[stale]["independent_flag"] is False  # independent_count=1
            assert claims[fresh_ind]["fresh"] is True
            assert claims[fresh_ind]["independent_flag"] is True
            assert claims[no_corr]["fresh"] is True
            assert claims[no_corr]["independent_flag"] is None
        finally:
            h.purge_run(pg, run_id)

    def test_budget_snapshot_readonly_and_errors(self):
        run_id = f"run-f9-bg-{uuid.uuid4().hex[:8]}"
        pg = self._pg_store()
        try:
            h.insert_run(pg, run_id)
            sq_id = _u()
            h.insert_subq(pg, run_id, sq_id, position=0, question="root")
            snap = _budget(
                limits={
                    "llm_calls": 10,
                    "tool_calls": 20,
                    "search_calls": 5,
                    "agent_steps": 8,
                },
                counts={
                    "llm_calls": 2,
                    "tool_calls": 1,
                    "search_calls": 1,
                    "agent_steps": 1,
                },
            )
            p1 = f9p.project(run_id, budget=snap, store=pg)
            p2 = f9p.project(run_id, budget=snap, store=pg)
            assert snap["counts"] == {
                "llm_calls": 2,
                "tool_calls": 1,
                "search_calls": 1,
                "agent_steps": 1,
            }  # 输入未被修改
            assert p1["budget"] == p2["budget"]
            assert p1["budget"]["remaining"] == {
                "llm_calls": 8,
                "tool_calls": 19,
                "search_calls": 4,
                "agent_steps": 7,
            }
            with pytest.raises(f9p.ProjectionError):
                f9p.project(run_id, budget={"limits": {}, "counts": {}}, store=pg)
            with pytest.raises(f9p.ProjectionError):
                f9p.project("no-such-run-pg", store=pg)
            with pytest.raises(f9p.ProjectionError):
                f9p.project(run_id, round=-2, store=pg)
        finally:
            h.purge_run(pg, run_id)

    def test_evidence_summary_domain_buckets(self):
        run_id = f"run-f9-es-{uuid.uuid4().hex[:8]}"
        pg = self._pg_store()
        try:
            h.insert_run(pg, run_id)
            sq_id = _u()
            h.insert_subq(pg, run_id, sq_id, position=0, question="root")
            for url in (
                "https://www.example.com/a",
                "https://sub.example.com/b",
                "https://www.bbc.co.uk/news/1",
                "https://docs.bbc.co.uk/x",
            ):
                qid, sid, eid = _u(), _u(), _u()
                h.insert_query(pg, run_id, qid, sq_id)
                h.insert_source(pg, run_id, sid, qid, url=url)
                h.insert_evidence(
                    pg, run_id, eid, sid, sq_id, content=eid, created_at=ISO()
                )
            # ragflow 无 canonical_url → unknown_domain
            qid, sid, eid = _u(), _u(), _u()
            h.insert_query(pg, run_id, qid, sq_id)
            h.insert_source(pg, run_id, sid, qid, source_type="ragflow", url=None)
            h.insert_evidence(
                pg, run_id, eid, sid, sq_id, content="rag", created_at=ISO()
            )
            es = f9p.project(run_id, store=pg)["evidence_summary"]
            assert es["count"] == 5
            assert es["by_domain"] == {
                "bbc.co.uk": 2,
                "example.com": 2,
                "unknown_domain": 1,
            }
        finally:
            h.purge_run(pg, run_id)


def ISO() -> str:
    return "2026-09-01T00:00:00+00:00"
