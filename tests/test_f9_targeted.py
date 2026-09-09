"""F9-P0 Batch 5 — Targeted Research + Incremental F3 Verification tests（sqlite + F8 seam）。

覆盖：targeted（valid/multi/duplicate-skip/retry/re_verify-skip/failure/同 run_id/确定性/
bounded）、snapshot-diff vs binding 语义（new Evidence ≠ new Binding；预绑定幂等去重）、
incremental F3（0/1/>8 candidates、deterministic ordering、5 verdict、幂等、failed 保留）、
D-C′ GovernedRealVerifier accounting（+1 llm / +0 agent_step、无裸调）、F8 seam
（normal/search budget）、regression（Batch1–4 + F1–F8 sqlite 另行全绿）。

真实 PostgreSQL 闭环 Gate 在 tests/test_f9_targeted_postgres.py。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from pathlib import Path

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool as lc_tool

from app.f9 import targeted as f9t
from app.research import context as research_ctx
from app.research import registry
from app.research import store as rstore
from app.research.verify import FakeVerifier, VerifierRuntimeError
from tests._f2_helpers import add_web_evidence, make_run

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST_TMP = _REPO_ROOT / "_testtmp"


def _u() -> str:
    return uuid.uuid4().hex


def _setup_claims(claim_count=1, evidence_count=1):
    """创建 run+root sq+claims（registry）；返回 (run_id, sq_id, claim_ids)。"""
    run_id, sqid = make_run()
    claim_ids = []
    for i in range(claim_count):
        cid = registry.create_claim(run_id, sqid, f"claim-{i}-{_u()}", "FACT")
        assert cid
        claim_ids.append(cid)
    evs = []
    for i in range(evidence_count):
        evs.append(add_web_evidence(run_id, sqid, f"pre evidence {i} {_u()}")[2])
    return run_id, sqid, claim_ids, evs


def _plan(run_id, sqid, claim_ids, query_specs=None, kind="research_query"):
    """构造 Batch4 ValidatedPlan 形状。query_specs: [(query, status, kind)]。"""
    specs = query_specs or [("q-target-1", "accepted", kind)]
    queries = []
    for q, status, k in specs:
        qid = _u()
        queries.append(
            {
                "query": q,
                "kind": k,
                "query_identity": qid,
                "dedup_identity": f"di-{qid}",
                "status": status,
                "attempt": 1,
            }
        )
    return {
        "plan_id": f"plan-{_u()}",
        "target_sub_question_id": sqid,
        "objective": f"obj-{_u()}",
        "queries": queries,
        "priority": "medium",
        "verification_target_claim_ids": claim_ids,
        "stop_condition": f"gap:{_u()}:resolved",
        "why": "why",
        "gap_refs": ["claim_no_evidence|claim|x"],
    }


def _fake_search_tool(ev_per_call=1, fail=False, calls=None):
    """LangChain @tool（name=internet_search，触发 F8 search/tool 计费）；research_ctx 驱动
    registry ingest（镜像真实 internet_search 语义）。"""
    state = {"calls": 0}
    if calls is not None:
        calls["n"] = 0

    @lc_tool(
        description="deterministic fake internet_search with research ingest mirror"
    )
    def internet_search(query: str) -> str:
        state["calls"] += 1
        if calls is not None:
            calls["n"] += 1
        run_id, sq = research_ctx.get_research_context()
        if not run_id or not sq:
            raise RuntimeError("no research context")
        if fail:
            raise RuntimeError("search provider boom")
        for i in range(ev_per_call):
            eid = _u()
            url = f"https://fake.example/{eid}"
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
                content=f"targeted-evidence-{eid}",
                locator=url,
                extraction_method="web_result",
            )
        return f"OK:{query}"

    return internet_search, state


class _FakeChatModel(BaseChatModel):
    def __init__(self, payload=None):
        super().__init__()
        object.__setattr__(self, "_state", {"calls": 0})
        object.__setattr__(
            self,
            "_payload",
            payload
            or json.dumps({"verdict": "SUPPORTS", "rationale": "r", "confidence": 0.9}),
        )

    @property
    def _llm_type(self) -> str:
        return "fake-targeted-llm"

    @property
    def calls(self) -> int:
        return self._state["calls"]

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self._state["calls"] += 1
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=self._payload))]
        )


def _run(coro):
    return asyncio.run(coro)


def _claims_in_store(run_id, sqid, claim_ids, want_evidence):
    """校验本轮 binding 与 evidence 落库数量。"""
    store = rstore.get_store()
    evs = store.execute("SELECT evidence_id FROM evidences WHERE run_id=%s", (run_id,))
    binds = store.execute(
        "SELECT count(*) AS n FROM claim_evidences WHERE claim_id=%s "
        "AND evidence_id IN (SELECT evidence_id FROM evidences WHERE run_id=%s)",
        (claim_ids[0], run_id),
    )
    return len(evs), int(binds[0]["n"]) if binds else 0


class TestTargetedResearch:
    def test_valid_query_ingests_and_verifies(self, research_sqlite):
        run_id, sqid, claim_ids, _ = _setup_claims()
        tool, state = _fake_search_tool(ev_per_call=1)
        plan = _plan(run_id, sqid, claim_ids)
        res = _run(f9t.execute_targeted_plan(plan, run_id=run_id, search_tool=tool))
        assert state["calls"] == 1
        assert res["executed_queries"][0]["status"] == "executed"
        assert res["executed_queries"][0]["evidence_added"] == 1
        assert len(res["added_evidence_ids"]) == 1
        assert res["verification"][0]["status"] == "verified"
        assert res["verification"][0]["candidates"] == 1
        assert res["verification"][0]["verdict_rows"][0]["verdict"] == "SUPPORTS"
        # 同一 run：research_runs 仍只有 1 行（未创建第二个 ResearchRun）
        runs = rstore.get_store().execute(
            "SELECT count(*) AS n FROM research_runs WHERE run_id=%s", (run_id,)
        )
        assert int(runs[0]["n"]) == 1

    def test_multiple_queries_deterministic_order(self, research_sqlite):
        run_id, sqid, claim_ids, _ = _setup_claims()
        specs = [(f"q-{i}", "accepted", "research_query") for i in range(3)]
        tool, state = _fake_search_tool(ev_per_call=1)
        plan = _plan(run_id, sqid, claim_ids, query_specs=specs)
        res = _run(f9t.execute_targeted_plan(plan, run_id=run_id, search_tool=tool))
        assert state["calls"] == 3
        executed = [q["query"] for q in res["executed_queries"]]
        assert executed == ["q-0", "q-1", "q-2"]
        assert [q["status"] for q in res["executed_queries"]] == ["executed"] * 3

    def test_duplicate_and_reverify_skipped(self, research_sqlite):
        run_id, sqid, claim_ids, _ = _setup_claims()
        specs = [
            ("q-dup", "duplicate", "research_query"),
            ("q-rv", "accepted", "re_verify"),
            ("q-ok", "accepted", "research_query"),
        ]
        tool, state = _fake_search_tool(ev_per_call=1)
        plan = _plan(run_id, sqid, claim_ids, query_specs=specs)
        res = _run(f9t.execute_targeted_plan(plan, run_id=run_id, search_tool=tool))
        statuses = [q["status"] for q in res["executed_queries"]]
        assert statuses == ["skipped_duplicate", "skipped_duplicate", "executed"]
        assert state["calls"] == 1  # 只执行 research_query accepted

    def test_retry_runs_and_failure_continues(self, research_sqlite):
        run_id, sqid, claim_ids, _ = _setup_claims()
        specs = [
            ("q-fail", "retry", "research_query"),
            ("q-ok", "accepted", "research_query"),
        ]
        tool, state = _fake_search_tool(ev_per_call=1, fail=True)
        # fail 只对第一条生效 → 用 fail 后重置？简化：两条都失败则验证普通失败记录
        plan = _plan(run_id, sqid, claim_ids, query_specs=specs)
        res = _run(f9t.execute_targeted_plan(plan, run_id=run_id, search_tool=tool))
        assert state["calls"] == 2
        assert all(q["status"] == "failed" for q in res["executed_queries"])
        assert res["verification"][0]["status"] == "no_op"

    def test_output_json_serializable_and_bounded(self, research_sqlite):
        run_id, sqid, claim_ids, _ = _setup_claims(claim_count=2, evidence_count=1)
        specs = [("q-1", "accepted", "research_query") for _ in range(5)]
        tool, _ = _fake_search_tool(ev_per_call=1)
        plan = _plan(run_id, sqid, claim_ids, query_specs=specs)
        res = _run(f9t.execute_targeted_plan(plan, run_id=run_id, search_tool=tool))
        text = json.dumps(res, sort_keys=True, ensure_ascii=False)
        assert isinstance(text, str)
        assert res["size"]["max_candidates_per_claim"] == 8
        assert sorted(q["dedup_identity"] for q in res["executed_queries"]) == sorted(
            q["dedup_identity"] for q in plan["queries"]
        )


class TestIncrementalVerification:
    def test_zero_candidate_noop(self, research_sqlite):
        run_id, sqid, claim_ids, _ = _setup_claims()
        # 无查询（queries 空不允许 → 只放一条 duplicate 全部跳过 → 无新增 binding）
        specs = [("q-x", "duplicate", "research_query")]
        tool, _ = _fake_search_tool(ev_per_call=1)
        plan = _plan(run_id, sqid, claim_ids, query_specs=specs)
        res = _run(f9t.execute_targeted_plan(plan, run_id=run_id, search_tool=tool))
        assert res["verification"][0]["status"] == "no_op"

    def test_binding_semantics_not_all_new_evidence(self, research_sqlite):
        """new Evidence ≠ new Binding：候选只来自 executor 实际新增 binding。

        场景（closure §二）：E1 已绑定目标 claim A、E2 未绑定、E3 已绑定其它 claim B。
        A 的候选只含本 executor 新增（E1 重复绑定幂等排除）；B 候选不含 E3（已绑 B）。
        """
        run_id, sqid, claim_ids, _ = _setup_claims(claim_count=1)
        claim_a = claim_ids[0]
        claim_b = registry.create_claim(run_id, sqid, f"other-{_u()}", "FACT")
        evs = []
        for i in range(3):
            evs.append(add_web_evidence(run_id, sqid, f"new-ev-{i}-{_u()}")[2])
        e1, e2, e3 = evs
        registry.bind_claim_evidence(run_id, claim_a, e1)  # E1 已绑定目标 claim A
        registry.bind_claim_evidence(run_id, claim_b, e3)  # E3 已绑定其它 claim B
        store = rstore.get_store()
        # 目标 claim A：E1 重复绑定幂等排除 → 只新增 E2、E3（M:N 允许）
        newly_a, errs_a = f9t.bind_new_evidence(
            run_id, claim_a, [e1, e2, e3], store=store
        )
        assert errs_a == []
        assert newly_a == [e2, e3]  # 不是"全部 new Evidence 即候选"（E1 排除）
        # 再次调用同输入 → 幂等：无新增（不产生重复候选）
        again_a, _ = f9t.bind_new_evidence(run_id, claim_a, [e1, e2, e3], store=store)
        assert again_a == []
        rows_a = int(
            store.execute(
                "SELECT count(*) AS n FROM claim_evidences WHERE claim_id=%s",
                (claim_a,),
            )[0]["n"]
        )
        assert rows_a == 3  # E1(预绑)+E2+E3；无重复
        # 其它 claim B：E3 已绑 B → 不重复候选
        newly_b, _ = f9t.bind_new_evidence(run_id, claim_b, [e1, e2, e3], store=store)
        assert newly_b == [e1, e2]

    def test_cap_8_candidates(self, research_sqlite):
        run_id, sqid, claim_ids, _ = _setup_claims()
        tool, _ = _fake_search_tool(ev_per_call=10)
        plan = _plan(run_id, sqid, claim_ids)
        res = _run(f9t.execute_targeted_plan(plan, run_id=run_id, search_tool=tool))
        entry = res["verification"][0]
        assert entry["candidates"] == 8
        assert entry["new_bindings"] == 8
        store = rstore.get_store()
        n_rows = store.execute(
            "SELECT count(*) AS n FROM verifications WHERE claim_id=%s",
            (claim_ids[0],),
        )
        assert int(n_rows[0]["n"]) == 8  # F3 幂等：恰 8 行

    def test_all_verdict_types_passthrough(self, research_sqlite):
        for verdict in (
            "SUPPORTS",
            "INSUFFICIENT",
            "CONTRADICTS",
            "UNVERIFIABLE",
            "ABSTAIN",
        ):
            run_id, sqid, claim_ids, _ = _setup_claims()
            tool, _ = _fake_search_tool(ev_per_call=1)
            plan = _plan(run_id, sqid, claim_ids)
            verifier = FakeVerifier(default_verdict=verdict, default_confidence=0.9)
            res = _run(
                f9t.execute_targeted_plan(
                    plan, run_id=run_id, search_tool=tool, verifier=verifier
                )
            )
            row = res["verification"][0]["verdict_rows"][0]
            assert row["verdict"] == verdict
            assert row["status"] == "succeeded"

    def test_verifier_ordinary_failure_preserved(self, research_sqlite):
        """F3 对 provider 失败执行 deterministic retry×1 后落 failed 行并返回（不向外抛）。"""
        run_id, sqid, claim_ids, _ = _setup_claims()
        tool, _ = _fake_search_tool(ev_per_call=1)
        plan = _plan(run_id, sqid, claim_ids)
        boom = FakeVerifier()
        boom.queue = [
            VerifierRuntimeError("provider", "boom"),
            VerifierRuntimeError("provider", "boom"),
        ]  # 两次尝试都失败 → failed 行
        res = _run(
            f9t.execute_targeted_plan(
                plan, run_id=run_id, search_tool=tool, verifier=boom
            )
        )
        entry = res["verification"][0]
        assert entry["status"] == "verified"  # F3 正常返回（行级失败）
        row = entry["verdict_rows"][0]
        assert row["status"] == "failed"
        assert row["verdict"] is None
        assert "boom" in (row.get("error") or "")

    def test_idempotent_no_duplicate_verification(self, research_sqlite):
        """第二轮无新 evidence → 无新增 binding → no_op，verification 行不重复。"""
        run_id, sqid, claim_ids, _ = _setup_claims()
        store = rstore.get_store()
        tool, _ = _fake_search_tool(ev_per_call=1)
        plan = _plan(run_id, sqid, claim_ids)
        _run(f9t.execute_targeted_plan(plan, run_id=run_id, search_tool=tool))
        n1 = int(
            store.execute(
                "SELECT count(*) AS n FROM verifications WHERE claim_id=%s",
                (claim_ids[0],),
            )[0]["n"]
        )
        # 第二遍：无新 evidence（tool 0 evidence）→ 无新 binding → no_op
        empty_tool, _ = _fake_search_tool(ev_per_call=0)
        res2 = _run(
            f9t.execute_targeted_plan(plan, run_id=run_id, search_tool=empty_tool)
        )
        n2 = int(
            store.execute(
                "SELECT count(*) AS n FROM verifications WHERE claim_id=%s",
                (claim_ids[0],),
            )[0]["n"]
        )
        assert n1 == 1 and n2 == 1
        assert res2["verification"][0]["status"] == "no_op"


class TestGovernedRealVerifierDPrime:
    def _ctx(self):
        from app.runtime.governance.context import (
            GovernanceExecution,
            enter_governance_execution,
        )
        from app.runtime.governance.counters import BudgetCounter

        ex = GovernanceExecution(task_id="t-dc", counter=BudgetCounter(), run_id="r-dc")
        return ex, enter_governance_execution

    def test_real_adapter_counts_llm_not_agent_step(self, research_sqlite, monkeypatch):
        monkeypatch.setenv("VERIFY_REAL_LLM", "1")
        run_id, sqid, claim_ids, _ = _setup_claims()
        ex, enter = self._ctx()
        model = _FakeChatModel()
        result_holder = {}
        with enter(ex):
            handler = ex.make_handler()
            verifier = f9t.GovernedRealVerifier(handler, model=model)
            tool, _ = _fake_search_tool(ev_per_call=1)
            plan = _plan(run_id, sqid, claim_ids)
            res = asyncio.run(
                f9t.execute_targeted_plan(
                    plan, run_id=run_id, search_tool=tool, verifier=verifier
                )
            )
            result_holder["res"] = res
            result_holder["llm"] = ex.counter.count("llm_calls")
            result_holder["steps"] = ex.counter.count("agent_steps")
            result_holder["model_calls"] = model.calls
        assert (
            result_holder["res"]["verification"][0]["verdict_rows"][0]["verdict"]
            == "SUPPORTS"
        )
        assert result_holder["llm"] == 1  # +1 llm
        assert result_holder["steps"] == 0  # +0 agent_step
        assert result_holder["model_calls"] == 1  # 无裸调（恰一次、经 handler）

    def test_real_adapter_not_constructed_without_flag(self, research_sqlite):
        ex, enter = self._ctx()
        with enter(ex):
            handler = ex.make_handler()
            with pytest.raises(RuntimeError):
                f9t.GovernedRealVerifier(handler)  # VERIFY_REAL_LLM != 1 且未注入 model


class TestGovernanceSeam:
    @pytest.fixture
    def gov_tmp(self):
        d = _TEST_TMP / f"targeted-{uuid.uuid4().hex}"
        d.mkdir(parents=True, exist_ok=True)
        yield d
        try:
            shutil.rmtree(d)
        except OSError:
            pass

    def _controller(self, gov_tmp):
        from app.runtime.governance import migrations as gov_migrations
        from app.runtime.governance import store as gov_store
        from app.runtime.governance.controller import GovernanceController

        db = str(gov_tmp / f"gov-{uuid.uuid4().hex}.sqlite")
        store = gov_store._GovernanceSqliteStore(db)
        gov_migrations.ensure_schema(store)
        ctl = GovernanceController(
            store, owner_instance="inst-b5", retry_delays=(0.01, 0.02)
        )
        ctl.watchdog_tick = 0.02
        return store, ctl

    def test_normal_completed_one_execution(self, research_sqlite, gov_tmp):
        from app.runtime.governance import store as gov_store
        from app.runtime.governance.models import TaskStatus

        async def scenario():
            run_id, sqid, claim_ids, _ = _setup_claims()
            store, ctl = self._controller(gov_tmp)
            rec = ctl.create_task("th-b5-1", run_id=run_id)
            tool, state = _fake_search_tool(ev_per_call=1)
            plan = _plan(run_id, sqid, claim_ids)

            async def shim():
                return await f9t.execute_targeted_plan(
                    plan, run_id=run_id, search_tool=tool
                )

            res = await ctl.execute(rec.task_id, shim(), policy={})
            row = gov_store.get_task(store, rec.task_id)
            assert row.status == TaskStatus.COMPLETED.value
            assert row.version == 1
            assert state["calls"] == 1
            assert res["verification"][0]["candidates"] == 1
            # search ⊆ tool：internet_search 名 → search+tool 双槽
            assert row.counters_snapshot["search_calls"] == 1
            assert row.counters_snapshot["tool_calls"] == 1
            store.close()

        asyncio.run(scenario())

    def test_search_budget_not_swallowed(self, research_sqlite, gov_tmp):
        from app.runtime.governance import store as gov_store
        from app.runtime.governance.counters import GovernanceLimitExceeded
        from app.runtime.governance.models import TaskStatus

        async def scenario():
            run_id, sqid, claim_ids, _ = _setup_claims()
            store, ctl = self._controller(gov_tmp)
            rec = ctl.create_task("th-b5-2", run_id=run_id)
            tool, state = _fake_search_tool(ev_per_call=1)
            plan = _plan(run_id, sqid, claim_ids)

            async def shim():
                return await f9t.execute_targeted_plan(
                    plan, run_id=run_id, search_tool=tool
                )

            with pytest.raises(GovernanceLimitExceeded):
                await ctl.execute(rec.task_id, shim(), policy={"max_search_calls": 0})
            row = gov_store.get_task(store, rec.task_id)
            assert row.status == TaskStatus.BUDGET_EXCEEDED.value
            assert state["calls"] == 0  # tool body 未执行
            store.close()

        asyncio.run(scenario())
