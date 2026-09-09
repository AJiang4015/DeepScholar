"""F9-P0 Batch 5 — Targeted Research + Incremental Verification PostgreSQL Gate。

在真实 PG16（RESEARCH_DSN_TEST 独立库）验证核心持久化闭环：
  Targeted Research（fake tool ingest）→ Evidence ingest → snapshot diff → Claim–Evidence
  binding → 增量 F3 Verification；检查 run_id 一致 / 幂等（binding+F3 fingerprint）/
  ordering 与 cap / 无跨 run 污染。Fake search tool（无网络）经 registry/ctx seam 写入 PG。

SQLite PASS ≠ PostgreSQL PASS；语义单测在 test_f9_targeted.py。
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from langchain_core.tools import tool as lc_tool  # noqa: E402

from app.f9 import targeted as f9t
from app.research import context as research_ctx
from app.research import migrations, registry, store as rstore
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


def _u() -> str:
    return uuid.uuid4().hex


def _make_ingest_tool(ev_per_call=1):
    """同 sqlite 测试的 fake ingest tool（name=internet_search；research_ctx + registry）。"""
    state = {"calls": 0}

    @lc_tool(description="deterministic fake internet_search for PG gate")
    def internet_search(query: str) -> str:
        state["calls"] += 1
        run_id, sq = research_ctx.get_research_context()
        if not run_id or not sq:
            raise RuntimeError("no research context")
        for i in range(ev_per_call):
            eid = _u()
            url = f"https://pg-fake.example/{eid}"
            qid = registry.record_search_query(
                run_id,
                sq,
                agent="network_search",
                tool="internet_search",
                query=query,
                seq=i,
            )
            sid = registry.upsert_source(
                run_id,
                qid,
                source_type="web",
                agent="network_search",
                title=f"t-{eid}",
                locator=url,
                canonical_key=url,
                canonical_url=url,
            )
            registry.append_evidence(
                run_id,
                sid,
                sq,
                content=f"pg-evidence-{eid}",
                locator=url,
                extraction_method="web_result",
            )
        return f"OK:{query}"

    return internet_search, state


def _plan(run_id, sqid, claim_ids):
    return {
        "plan_id": f"plan-{_u()}",
        "target_sub_question_id": sqid,
        "objective": f"obj-{_u()}",
        "queries": [
            {
                "query": f"pg-q-{_u()}",
                "kind": "research_query",
                "query_identity": _u(),
                "dedup_identity": f"di-{_u()}",
                "status": "accepted",
                "attempt": 1,
            }
        ],
        "priority": "medium",
        "verification_target_claim_ids": claim_ids,
        "stop_condition": f"gap:{_u()}:resolved",
        "why": "why",
        "gap_refs": ["claim_no_evidence|claim|x"],
    }


@needs_pg
class TestTargetedPostgres:
    def _bind(self):
        store = rstore._PostgresStore(os.getenv(PG_TEST_DSN_ENV))
        migrations.ensure_schema(store)
        self._old = rstore._instance
        rstore._instance = store
        return store

    def _restore(self):
        rstore._instance = self._old

    def test_full_closed_loop_pg(self):
        store = self._bind()
        run_id = ""
        try:
            run_ids = registry.create_run_and_root(f"th-{_u()}", "batch5 pg?")
            assert run_ids is not None
            run_id, sqid = run_ids
            cid = registry.create_claim(run_id, sqid, f"claim-{_u()}", "FACT")
            assert cid
            tool, state = _make_ingest_tool(ev_per_call=1)
            plan = _plan(run_id, sqid, [cid])
            res = asyncio.run(
                f9t.execute_targeted_plan(
                    plan, run_id=run_id, search_tool=tool, store=store
                )
            )
            assert state["calls"] == 1
            assert res["executed_queries"][0]["status"] == "executed"
            assert res["executed_queries"][0]["evidence_added"] == 1
            assert len(res["added_evidence_ids"]) == 1
            assert res["verification"][0]["status"] == "verified"
            assert res["verification"][0]["candidates"] == 1
            row = res["verification"][0]["verdict_rows"][0]
            assert row["verdict"] == "SUPPORTS" and row["status"] == "succeeded"
            # 同一 run_id / 单个 ResearchRun
            runs = store.execute(
                "SELECT count(*) AS n FROM research_runs WHERE run_id=%s", (run_id,)
            )
            assert int(runs[0]["n"]) == 1
            # binding + verification fingerprint 幂等：第二轮无新 evidence → no_op、行数不变
            n_v_before = int(
                store.execute(
                    "SELECT count(*) AS n FROM verifications WHERE run_id=%s", (run_id,)
                )[0]["n"]
            )
            empty_tool, _ = _make_ingest_tool(ev_per_call=0)
            res2 = asyncio.run(
                f9t.execute_targeted_plan(
                    plan, run_id=run_id, search_tool=empty_tool, store=store
                )
            )
            assert res2["verification"][0]["status"] == "no_op"
            n_v_after = int(
                store.execute(
                    "SELECT count(*) AS n FROM verifications WHERE run_id=%s", (run_id,)
                )[0]["n"]
            )
            assert n_v_before == n_v_after == 1
            # binding 幂等重复（helper 再跑）→ 无新增
            new_again, _ = f9t.bind_new_evidence(
                run_id, cid, res["added_evidence_ids"], store=store
            )
            assert new_again == []
        finally:
            if run_id:
                h.purge_run(store, run_id)
            self._restore()

    def test_cap_and_no_cross_run_pollution_pg(self):
        store = self._bind()
        run_a, run_b = "", ""
        try:
            run_a, sq_a = registry.create_run_and_root("th-a", "q")
            assert run_a
            claim_a = registry.create_claim(run_a, sq_a, f"ca-{_u()}", "FACT")
            # 另一个 run B 的 claim（隔离哨兵）
            run_b, sq_b = registry.create_run_and_root("th-b", "q")
            registry.create_claim(run_b, sq_b, f"cb-{_u()}", "FACT")
            tool, _ = _make_ingest_tool(ev_per_call=10)
            plan = _plan(run_a, sq_a, [claim_a])
            res = asyncio.run(
                f9t.execute_targeted_plan(
                    plan, run_id=run_a, search_tool=tool, store=store
                )
            )
            entry = res["verification"][0]
            assert entry["candidates"] == 8  # D-B cap（≤8/claim）
            # 无跨 run 污染：run_b 无新增 evidences/verifications
            n_b = int(
                store.execute(
                    "SELECT count(*) AS n FROM evidences WHERE run_id=%s", (run_b,)
                )[0]["n"]
            )
            n_vb = int(
                store.execute(
                    "SELECT count(*) AS n FROM verifications WHERE run_id=%s", (run_b,)
                )[0]["n"]
            )
            assert n_b == 0 and n_vb == 0
        finally:
            if run_a:
                h.purge_run(store, run_a)
            if run_b:
                h.purge_run(store, run_b)
            self._restore()

    def test_pg_persistence_semantics_ordering_cap(self):
        """真实 PG 持久化语义：cap/ordering/fingerprint 幂等与 sqlite 同代码路径一致。"""
        store_pg = self._bind()
        run_pg = ""
        try:
            run_pg, sq_pg = registry.create_run_and_root("th-eq", "q")
            claim_pg = registry.create_claim(run_pg, sq_pg, f"ceq-{_u()}", "FACT")
            tool_pg, _ = _make_ingest_tool(ev_per_call=2)
            plan = _plan(run_pg, sq_pg, [claim_pg])
            res_pg = asyncio.run(
                f9t.execute_targeted_plan(
                    plan, run_id=run_pg, search_tool=tool_pg, store=store_pg
                )
            )
            assert res_pg["verification"][0]["candidates"] == 2
            # deterministic ordering：按 (created_at ASC, evidence_id ASC)（与 executor 同键）
            ids = res_pg["added_evidence_ids"]
            db_order = [
                r["evidence_id"]
                for r in store_pg.execute(
                    "SELECT evidence_id FROM evidences WHERE run_id=%s "
                    "ORDER BY created_at, evidence_id",
                    (run_pg,),
                )
            ]
            assert ids == db_order
            assert len(ids) == len(set(ids)) == 2
            rows_pg = int(
                store_pg.execute(
                    "SELECT count(*) AS n FROM verifications WHERE run_id=%s", (run_pg,)
                )[0]["n"]
            )
            assert rows_pg == 2  # fingerprint 幂等：恰 2 行
        finally:
            if run_pg:
                h.purge_run(store_pg, run_pg)
            self._restore()
