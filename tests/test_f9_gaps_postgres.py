"""F9-P0 Batch 2 — Deterministic Gap Detection PostgreSQL gate（RESEARCH_DSN_TEST 门控）。

Batch 2 为消费 Batch 1 Projection 的纯函数（不直接访问 research DB）；因此跨 DB 一致性 =
"同一逻辑+物理 state 在 PG 与 sqlite 上分别 Projection → detect_gaps 输出一致"。
本文件在真实 PG16 上验证整链（seed → project → detect）语义等价与关键信号边界。

SQLite PASS ≠ PostgreSQL PASS：语义单测在 test_f9_gaps.py。
"""

from __future__ import annotations

import datetime
import json
import os
import uuid

import pytest

from app.f9 import gaps as f9g
from app.f9 import projection as f9p
from app.research import migrations, store as rstore
from tests import _f9_helpers as h

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

FIXED_NOW = datetime.datetime(2026, 9, 2, 20, 0, 0, tzinfo=datetime.timezone.utc)
TS_STR = "2026-09-02T10:00:00+00:00"


def _u() -> str:
    return uuid.uuid4().hex


def _budget(counts=None):
    limits = {
        "llm_calls": 120,
        "tool_calls": 300,
        "search_calls": 40,
        "agent_steps": 200,
    }
    cnt = {k: 0 for k in limits}
    cnt.update(counts or {})
    return {
        "limits": limits,
        "counts": cnt,
        "remaining": {k: limits[k] - cnt[k] for k in limits},
    }


@needs_pg
class TestF9GapsPostgres:
    def _pg_store(self):
        store = rstore._PostgresStore(os.getenv(PG_TEST_DSN_ENV))
        migrations.ensure_schema(store)
        return store

    def _sqlite_store(self, tmp_path):
        store = rstore._SqliteStore(str(tmp_path / f"rs-{uuid.uuid4().hex}.sqlite"))
        migrations.ensure_schema(store)
        return store

    def test_cross_db_pipeline_equivalence(self, research_tmp):
        """rich scenario：PG 与 sqlite 各自 project → detect 输出完全一致。"""
        run_id = f"run-f9g-eq-{uuid.uuid4().hex[:8]}"
        pg = self._pg_store()
        sq = self._sqlite_store(research_tmp)
        try:
            h.seed_rich_scenario(pg, run_id)
            h.seed_rich_scenario(sq, run_id)
            snap = _budget(counts={"search_calls": 32})  # 32/40 → near
            pa = f9p.project(run_id, round=1, budget=snap, store=pg, now=FIXED_NOW)
            pb = f9p.project(run_id, round=1, budget=snap, store=sq, now=FIXED_NOW)
            ga = f9g.detect_gaps(pa)
            gb = f9g.detect_gaps(pb)
            assert ga == gb
            assert json.dumps(ga, sort_keys=True) == json.dumps(gb, sort_keys=True)
            types = set(ga["counts"])
            # rich scenario：c-2 最新 succeeded=INSUFFICIENT；c-1 有未缓解冲突
            assert "claim_verdict_insufficient" in types
            assert "unresolved_conflict" in types
            assert "budget_near" in types
            assert ga["round"] == 1
        finally:
            h.purge_run(pg, run_id)
            sq.close()

    def test_cross_db_reversed_insertion_order(self, research_tmp):
        run_id = f"run-f9g-ri-{uuid.uuid4().hex[:8]}"
        pg = self._pg_store()
        sq = self._sqlite_store(research_tmp)
        try:
            h.seed_rich_scenario(pg, run_id, reverse=True)
            h.seed_rich_scenario(sq, run_id, reverse=False)
            pa = f9p.project(run_id, store=pg, now=FIXED_NOW)
            pb = f9p.project(run_id, store=sq, now=FIXED_NOW)
            assert f9g.detect_gaps(pa) == f9g.detect_gaps(pb)
        finally:
            h.purge_run(pg, run_id)
            sq.close()

    def test_required_coverage_semantics(self):
        run_id = f"run-f9g-ru-{uuid.uuid4().hex[:8]}"
        pg = self._pg_store()
        try:
            h.insert_run(pg, run_id)
            sq_id = "sq-root-g"
            h.insert_subq(pg, run_id, sq_id, position=0, question="root")
            # 无 ev/src → RU1
            r1 = f9g.detect_gaps(f9p.project(run_id, store=pg))
            assert r1["counts"] == {"required_uncovered": 1}
            # 1 条 evidence（<MIN_EVIDENCE=2）→ RE1
            _add_evidence_chain(pg, run_id, sq_id)
            r2 = f9g.detect_gaps(f9p.project(run_id, store=pg))
            assert r2["counts"] == {"required_low_evidence": 1}
            # 第 2 条 evidence 补齐 → 无 required 信号（无 claim）
            _add_evidence_chain(pg, run_id, sq_id)
            r3 = f9g.detect_gaps(f9p.project(run_id, store=pg))
            assert r3["signals"] == []
        finally:
            h.purge_run(pg, run_id)

    def test_claim_signal_boundaries(self):
        run_id = f"run-f9g-cs-{uuid.uuid4().hex[:8]}"
        pg = self._pg_store()
        try:
            h.insert_run(pg, run_id)
            sq_id = _u()
            h.insert_subq(pg, run_id, sq_id, position=0, question="root")
            # root 补齐 evidence（避免 required 信号干扰）
            _add_evidence_chain(pg, run_id, sq_id)
            _add_evidence_chain(pg, run_id, sq_id)
            cid = "c-insuff"
            h.insert_claim(
                pg, run_id, cid, sq_id, statement="insuff", created_at=TS_STR
            )
            evs = []
            for _ in range(2):
                evs.append(_add_evidence_chain(pg, run_id, sq_id))
            for i, eid in enumerate(evs):
                h.insert_binding(pg, run_id, _u(), cid, eid)
                h.insert_verification(
                    pg,
                    run_id,
                    f"v-{i}",
                    cid,
                    eid,
                    verdict=("SUPPORTS" if i == 0 else "INSUFFICIENT"),
                    status=("succeeded" if i == 0 else "failed"),
                    created_at=f"2026-09-02T1{i}:00:00+00:00",
                )
            h.insert_corroboration(
                pg,
                run_id,
                _u(),
                cid,
                status="complete",
                support={"source_count": 2, "independent_count": 1},
                computed_at="2026-09-02T18:00:00+00:00",
            )
            cf_id = "cf-unresolved"
            h.insert_conflict(
                pg,
                run_id,
                cf_id,
                cid,
                evs[0],
                evs[1],
                conflict_type="CONTRADICTION",
                genuine=True,
            )
            r = f9g.detect_gaps(f9p.project(run_id, store=pg, now=FIXED_NOW))
            types = r["counts"]
            # best_verdict = 最新 succeeded(SUPPORTS)；v-1(failed) 不参与 → 无 CV2/CV1。
            # F5 independent_count=1 → IS1；未缓解冲突 → CF1
            assert types.get("independent_sources_insufficient") == 1
            assert types.get("unresolved_conflict") == 1
            cf_signals = [s for s in r["signals"] if s["type"] == "unresolved_conflict"]
            assert cf_signals[0]["detail"]["conflict_ids"] == [cf_id]
            assert "claim_verdict_insufficient" not in types
            assert "claim_unverified" not in types
        finally:
            h.purge_run(pg, run_id)

    def test_budget_near_readonly_no_db_write(self):
        run_id = f"run-f9g-bg-{uuid.uuid4().hex[:8]}"
        pg = self._pg_store()
        try:
            h.insert_run(pg, run_id)
            sq_id = _u()
            h.insert_subq(pg, run_id, sq_id, position=0, question="root")
            _add_evidence_chain(pg, run_id, sq_id)
            _add_evidence_chain(pg, run_id, sq_id)
            snap = _budget(counts={"search_calls": 32, "llm_calls": 30})
            before = {k: dict(v) for k, v in snap.items()}
            rows_before = _count_rows(pg, run_id)
            p = f9p.project(run_id, round=0, budget=snap, store=pg)
            r1 = f9g.detect_gaps(p)
            r2 = f9g.detect_gaps(p)
            assert snap == before  # snapshot 未被修改
            assert r1 == r2
            assert [
                s["subject_id"] for s in r1["signals"] if s["type"] == "budget_near"
            ] == ["search_calls"]
            assert _count_rows(pg, run_id) == rows_before  # detect 零写
        finally:
            h.purge_run(pg, run_id)


def _add_evidence_chain(pg, run_id, sq_id) -> str:
    """query + source(web, unique url) + evidence；返回 evidence_id。"""
    qid, sid, eid = _u(), _u(), _u()
    h.insert_query(pg, run_id, qid, sq_id)
    h.insert_source(pg, run_id, sid, qid, url=f"https://example.com/{eid}")
    h.insert_evidence(
        pg, run_id, eid, sid, sq_id, content=f"c-{eid}", created_at=TS_STR
    )
    return eid


def _count_rows(pg, run_id: str) -> int:
    rows = pg.execute(
        "SELECT (SELECT COUNT(*) FROM evidences WHERE run_id=%s) + "
        "(SELECT COUNT(*) FROM claims WHERE run_id=%s) AS n",
        (run_id, run_id),
    )
    return int(rows[0]["n"])
