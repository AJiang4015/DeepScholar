"""F9-P0 Batch 6 — Round Orchestrator tests（sqlite + F8 seam）。

覆盖（D1–D6/C1–C3）：单 execute invariant、round0 exactly-once、no-gap/all-unimportant/
judge-fallback/plan-fallback stopping、finalize exactly once（normal=1，governance terminal=0）、
D4 changed-only F4/F5/F6 接线（spy）、D5 政策注入、F8 seam（normal/budget/cancel/timeout）。
真实图执行（deepagents/main agent）E2E 受凭据/环境限制（报告 Limitations）；此处用 fake
graph runner + fake judge/plan models + fake ingest tool 驱动编排层与 seam。
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import uuid
from pathlib import Path

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool as lc_tool

from app.f9 import orchestrator as f9o
from app.research import context as research_ctx
from app.research import registry
from app.research import store as rstore
from tests._f2_helpers import add_web_evidence, make_run

_TEST_TMP = Path(__file__).resolve().parent.parent / "_testtmp"


def _u() -> str:
    return uuid.uuid4().hex


def _gap_ids_from(messages):
    ids = []
    for m in messages:
        text = getattr(m, "content", "") or ""
        ids += re.findall(r"gap_id=([^\s]+)", text)
    return list(dict.fromkeys(ids))


class _JSONModel(BaseChatModel):
    def __init__(self, resolver=None, raise_error=None):
        super().__init__()
        object.__setattr__(self, "_state", {"calls": 0})
        object.__setattr__(self, "_resolver", resolver)
        object.__setattr__(self, "_raise_error", raise_error)

    @property
    def _llm_type(self) -> str:
        return "fake-orchestrator-llm"

    @property
    def calls(self) -> int:
        return self._state["calls"]

    def _text(self, messages):
        self._state["calls"] += 1
        if self._raise_error is not None:
            raise self._raise_error
        if self._resolver is None:
            raise RuntimeError("no resolver")
        return self._resolver(messages)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(
            generations=[
                ChatGeneration(message=AIMessage(content=self._text(messages)))
            ]
        )

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(
            generations=[
                ChatGeneration(message=AIMessage(content=self._text(messages)))
            ]
        )


def _judge_model(important=True, priority="medium", raise_error=None):
    def resolver(messages):
        ids = _gap_ids_from(messages)
        return json.dumps(
            {
                "judgments": [
                    {
                        "gap_id": i,
                        "important": important,
                        "reason": "r",
                        "priority": priority,
                        "semantic_need": "n",
                    }
                    for i in ids
                ]
            }
        )

    return _JSONModel(resolver=resolver, raise_error=raise_error)


def _plan_model(raise_error=None):
    def resolver(messages):
        ids = _gap_ids_from(messages)
        return json.dumps(
            {
                "plans": [
                    {"gap_id": i, "objective": f"obj-{i}", "queries": [f"q-{i}-1"]}
                    for i in ids
                ]
            }
        )

    return _JSONModel(resolver=resolver, raise_error=raise_error)


def _make_graph_runner(hang_stage=None, raise_stage=None, llm_stage=None, model=None):
    """fake graph runner：每段真实让步（sleep(0)）使 governance cancel/timeout 可投递；
    hang_stage/raise_stage 控制特定段挂起/抛错；llm_stage+model 使该段经 handler 计费一次。"""
    state = {"stages": [], "calls": 0}

    async def runner(prompt: str, config: dict):
        await asyncio.sleep(0)  # 真实让步：graph 每次调用是可取消点
        thread = (config.get("configurable") or {}).get("thread_id", "")
        stage = thread.split("::")[1] if "::" in thread else "unknown"
        state["calls"] += 1
        state["stages"].append(stage)
        if hang_stage == stage:
            await asyncio.Event().wait()
        if raise_stage == stage:
            raise RuntimeError(f"graph boom in {stage}")
        if llm_stage == stage and model is not None:
            # 模拟该段 graph 内的一次 LLM 调用（经 handler callbacks 计费）
            from langchain_core.messages import HumanMessage

            cbs = (config or {}).get("callbacks") or []
            await model.ainvoke(
                [HumanMessage(content=prompt)], config={"callbacks": cbs}
            )
        return f"content-from-{stage}"

    return runner, state


def _make_ingest_tool(ev_per_call=1):
    state = {"calls": 0}

    @lc_tool(description="fake ingest tool (research ctx mirror)")
    def internet_search(query: str) -> str:
        state["calls"] += 1
        run_id, sq = research_ctx.get_research_context()
        if not run_id or not sq:
            raise RuntimeError("no research ctx")
        for _ in range(ev_per_call):
            eid = _u()
            url = f"https://orch.example/{eid}"
            qid = registry.record_search_query(
                run_id, sq, agent="network_search", tool="internet_search", query=query
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
                content=f"orch-ev-{eid}",
                locator=url,
                extraction_method="web_result",
            )
        return "OK"

    return internet_search, state


def _seed_covered_run():
    run_id, sqid = make_run()
    for _ in range(2):
        add_web_evidence(run_id, sqid, f"cover-{_u()}")
    return run_id, sqid


def _seed_claim_run(claim_count=1):
    """run + root sq + N claims（无 evidence → CE1 claim_no_evidence gap）。"""
    run_id, sqid = make_run()
    cids = []
    for _ in range(claim_count):
        cid = registry.create_claim(run_id, sqid, f"orch-claim-{_u()}", "FACT")
        assert cid
        cids.append(cid)
    return run_id, sqid, cids


class _HangSearchTool:
    """targeted 段挂起工具：ainvoke 置 started 后永久等待（governance cancel 投递点）。"""

    def __init__(self):
        self.started = asyncio.Event()

    async def ainvoke(self, payload=None, config=None):
        self.started.set()
        await asyncio.Event().wait()
        return "hang-unreachable"


def _seed_insufficient_run():
    run_id, sqid = make_run()
    add_web_evidence(run_id, sqid, f"one-{_u()}")
    return run_id, sqid


def _seed_empty_run():
    return make_run()


def _run(coro):
    return asyncio.run(coro)


def _finalize_sinks():
    state = {"finalize": 0, "status": []}

    def finalize_sink(run_id: str, content: str):
        state["finalize"] += 1

    def status_sink(run_id: str, status: str):
        state["status"].append(status)

    return finalize_sink, status_sink, state


def _run_governed(seed_run_id, coro_fn):
    """unit 级：governance ctx 包装（run_id=seed；不建 run，复用 seed 既有 run）。"""
    from app.runtime.governance.context import (
        GovernanceExecution,
        enter_governance_execution,
    )
    from app.runtime.governance.counters import BudgetCounter

    ex = GovernanceExecution(
        task_id=f"t-{_u()}", counter=BudgetCounter(), run_id=seed_run_id
    )
    with enter_governance_execution(ex):
        return _run(coro_fn(ex))


class TestOrchestratorFlow:
    def _run_flow(self, run_id, **kw):
        finalize_sink, status_sink, fs = _finalize_sinks()
        base = {
            "judge_model": _judge_model(),
            "plan_model": _plan_model(),
            "graph_runner": _make_graph_runner()[0],
            "search_tool": _make_ingest_tool()[0],
        }
        allowed = {
            "judge_model",
            "plan_model",
            "graph_runner",
            "research_policy",
            "adaptive_enabled",
            "conflict_detector",
            "corroboration_reviewer",
            "reconciliation_reviewer",
        }
        base.update({k: v for k, v in kw.items() if k in allowed})

        async def go(ex):
            return await f9o.f9_orchestrator(
                "q?",
                "s-f",
                create_run=False,
                finalize_sink=finalize_sink,
                status_sink=status_sink,
                **base,
            )

        return _run_governed(run_id, go), fs

    def test_no_gap_stop_finalize_once(self, research_sqlite):
        run_id, _ = _seed_covered_run()
        graph, gstate = _make_graph_runner()
        res, fs = self._run_flow(run_id, graph_runner=graph)
        assert res["baseline_executed"] is True
        assert res["stopped_reason"] == "no_gap"
        assert gstate["stages"] == ["round0", "final_synthesis"]
        assert fs["finalize"] == 1
        assert fs["status"] == ["finished"]
        runs = rstore.get_store().execute(
            "SELECT count(*) AS n FROM research_runs WHERE run_id=%s", (run_id,)
        )
        assert int(runs[0]["n"]) == 1

    def test_judge_failure_fallback_finalize_once_no_round0_replay(
        self, research_sqlite
    ):
        run_id, _ = _seed_empty_run()
        graph, gstate = _make_graph_runner()
        judge_m = _judge_model(raise_error=RuntimeError("judge down"))
        res, fs = self._run_flow(run_id, graph_runner=graph, judge_model=judge_m)
        assert res["stopped_reason"] == "adaptive_fallback"
        assert "judge_failure_fallback" in res["errors"]
        assert fs["finalize"] == 1
        assert gstate["stages"].count("round0") == 1
        assert gstate["stages"][-1] == "final_synthesis"

    def test_plan_failure_fallback(self, research_sqlite):
        run_id, _ = _seed_empty_run()
        plan_m = _plan_model(raise_error=RuntimeError("plan down"))
        res, fs = self._run_flow(run_id, plan_model=plan_m)
        assert res["stopped_reason"] == "adaptive_fallback"
        assert "plan_failure_fallback" in res["errors"]
        assert fs["finalize"] == 1

    def test_all_gaps_unimportant(self, research_sqlite):
        run_id, _ = _seed_empty_run()
        judge_m = _judge_model(important=False)
        res, fs = self._run_flow(run_id, judge_model=judge_m)
        assert res["stopped_reason"] == "all_gaps_unimportant"
        assert fs["finalize"] == 1

    def test_adaptive_adds_evidence_then_stops(self, research_sqlite):
        run_id, _ = _seed_insufficient_run()
        graph, _ = _make_graph_runner()
        tool, _ = _make_ingest_tool(ev_per_call=2)
        # 注入 search_tool 需要走 kw 透传之外：单独执行
        finalize_sink, status_sink, fs = _finalize_sinks()

        async def go(ex):
            return await f9o.f9_orchestrator(
                "q?",
                "s-ad",
                judge_model=_judge_model(),
                plan_model=_plan_model(),
                graph_runner=graph,
                search_tool=tool,
                create_run=False,
                finalize_sink=finalize_sink,
                status_sink=status_sink,
            )

        res = _run_governed(run_id, go)
        assert res["executed_plans"] >= 1
        assert fs["finalize"] == 1
        assert res["stopped_reason"] in ("no_gap", "max_rounds")

    def test_requires_governance_context(self, research_sqlite):
        with pytest.raises(f9o.OrchestratorError):
            _run(f9o.f9_orchestrator("q", "s-x"))


class TestD4ChangedOnlyF456:
    def test_f456_only_changed_claims(self, research_sqlite, monkeypatch):
        run_id, sqid = make_run()
        cids = []
        for i in range(2):
            eid = add_web_evidence(run_id, sqid, f"c{i}-{_u()}")[2]
            cid = registry.create_claim(run_id, sqid, f"claim-{i}-{_u()}", "FACT")
            registry.bind_claim_evidence(run_id, cid, eid)
            cids.append(cid)
        from app.research import corroboration as f5mod
        from app.research import conflict as f4mod
        from app.research import reconciliation as f6mod

        calls = {"f4": [], "f5": [], "f6": []}

        def fake_f4(claim_id, **kw):
            calls["f4"].append(claim_id)
            return {"items": []}

        def fake_f5(claim_id, **kw):
            calls["f5"].append(claim_id)
            return {"status": "complete"}

        def fake_f6(claim_id, **kw):
            calls["f6"].append(claim_id)
            return {"register": {"register": "x"}, "items": []}

        monkeypatch.setattr(f4mod, "detect_claim_conflicts", fake_f4)
        monkeypatch.setattr(f5mod, "compute_claim_corroboration", fake_f5)
        monkeypatch.setattr(f6mod, "reconcile_claim_conflicts", fake_f6)
        out = _run(
            f9o._run_f456_incremental(run_id, [cids[0]], store=rstore.get_store())
        )
        assert calls["f4"] == [cids[0]]
        assert calls["f5"] == [cids[0]]
        assert calls["f6"] == []  # 无 confirmed conflicts → F6 不触发
        assert out[0]["f4"]["status"] == "executed"
        _run(f9o._run_f456_incremental(run_id, [], store=rstore.get_store()))
        assert calls["f4"] == [cids[0]]  # 空 changed → 零调用


class TestGovernanceSeam:
    @pytest.fixture
    def gov_tmp(self):
        d = _TEST_TMP / f"orch-{uuid.uuid4().hex}"
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
            store, owner_instance="inst-orch", retry_delays=(0.01, 0.02)
        )
        ctl.watchdog_tick = 0.02
        return store, ctl

    def test_normal_completed_one_execution(self, research_sqlite, gov_tmp):
        from app.runtime.governance import store as gov_store
        from app.runtime.governance.models import TaskStatus

        run_id, _ = _seed_covered_run()
        store, ctl = self._controller(gov_tmp)
        rec = ctl.create_task("th-o1", run_id=run_id)
        graph, _ = _make_graph_runner()
        tool, _ = _make_ingest_tool()
        finalize_sink, status_sink, fs = _finalize_sinks()

        async def shim():
            return await f9o.f9_orchestrator(
                "q",
                "s-o1",
                judge_model=_judge_model(),
                plan_model=_plan_model(),
                graph_runner=graph,
                search_tool=tool,
                finalize_sink=finalize_sink,
                status_sink=status_sink,
            )

        async def scenario():
            res = await ctl.execute(rec.task_id, shim(), policy={})
            row = gov_store.get_task(store, rec.task_id)
            return res, row

        res, row = _run(scenario())
        assert row.status == TaskStatus.COMPLETED.value
        assert row.version == 1
        assert res["finalize_calls"] == 1
        assert fs["status"] == ["finished"]
        store.close()

    def test_budget_during_judge_no_finalize(self, research_sqlite, gov_tmp):
        from app.runtime.governance import store as gov_store
        from app.runtime.governance.counters import GovernanceLimitExceeded
        from app.runtime.governance.models import TaskStatus

        run_id, _ = _seed_empty_run()  # gap → judge 触发（llm 预算）
        store, ctl = self._controller(gov_tmp)
        rec = ctl.create_task("th-o2", run_id=run_id)
        graph, _ = _make_graph_runner()
        tool, _ = _make_ingest_tool()
        finalize_sink, _, fs = _finalize_sinks()

        async def shim():
            return await f9o.f9_orchestrator(
                "q",
                "s-o2",
                judge_model=_judge_model(),
                plan_model=_plan_model(),
                graph_runner=graph,
                search_tool=tool,
                finalize_sink=finalize_sink,
                status_sink=None,
            )

        async def scenario():
            with pytest.raises(GovernanceLimitExceeded):
                await ctl.execute(rec.task_id, shim(), policy={"max_llm_calls": 0})
            row = gov_store.get_task(store, rec.task_id)
            return row

        row = _run(scenario())
        assert row.status == TaskStatus.BUDGET_EXCEEDED.value
        assert fs["finalize"] == 0  # F7 exactly 0（governance terminal）
        store.close()

    def test_cancel_during_final_synthesis_no_finalize(self, research_sqlite, gov_tmp):
        from app.runtime.governance import store as gov_store
        from app.runtime.governance.models import TaskStatus

        run_id, _ = _seed_covered_run()
        store, ctl = self._controller(gov_tmp)
        rec = ctl.create_task("th-o3", run_id=run_id)
        graph, _ = _make_graph_runner(hang_stage="final_synthesis")
        tool, _ = _make_ingest_tool()
        finalize_sink, _, fs = _finalize_sinks()

        async def shim():
            return await f9o.f9_orchestrator(
                "q",
                "s-o3",
                judge_model=_judge_model(),
                plan_model=_plan_model(),
                graph_runner=graph,
                search_tool=tool,
                finalize_sink=finalize_sink,
                status_sink=None,
            )

        async def scenario():
            task = asyncio.create_task(ctl.execute(rec.task_id, shim(), policy={}))
            for _ in range(200):
                if ctl.has_active_handle(rec.task_id):
                    break
                await asyncio.sleep(0.001)
            assert ctl.has_active_handle(rec.task_id)
            res = await ctl.cancel(rec.task_id)
            assert res["winner"] is True
            with pytest.raises(asyncio.CancelledError):
                await task
            row = gov_store.get_task(store, rec.task_id)
            return row

        row = _run(scenario())
        assert row.status == TaskStatus.CANCELLED.value
        assert fs["finalize"] == 0
        store.close()

    def test_timeout_during_final_synthesis(self, research_sqlite, gov_tmp):
        from app.runtime.governance import store as gov_store
        from app.runtime.governance.models import TaskStatus

        run_id, _ = _seed_covered_run()
        store, ctl = self._controller(gov_tmp)
        rec = ctl.create_task("th-o4", run_id=run_id)
        graph, _ = _make_graph_runner(hang_stage="final_synthesis")
        tool, _ = _make_ingest_tool()
        finalize_sink, _, fs = _finalize_sinks()

        async def shim():
            return await f9o.f9_orchestrator(
                "q",
                "s-o4",
                judge_model=_judge_model(),
                plan_model=_plan_model(),
                graph_runner=graph,
                search_tool=tool,
                finalize_sink=finalize_sink,
                status_sink=None,
            )

        async def scenario():
            task = asyncio.create_task(
                ctl.execute(rec.task_id, shim(), policy={"wall_clock_timeout": 0.25})
            )
            with pytest.raises(asyncio.CancelledError):
                await task
            row = gov_store.get_task(store, rec.task_id)
            return row

        row = _run(scenario())
        assert row.status == TaskStatus.TIMED_OUT.value
        assert fs["finalize"] == 0
        store.close()

    def test_cancel_during_round0_no_finalize(self, research_sqlite, gov_tmp):
        """矩阵 #1：round0 baseline graph 段内 cancel → CANCELLED & finalize==0。"""
        from app.runtime.governance import store as gov_store
        from app.runtime.governance.models import TaskStatus

        run_id, _ = _seed_covered_run()
        store, ctl = self._controller(gov_tmp)
        rec = ctl.create_task("th-o5", run_id=run_id)
        graph, _ = _make_graph_runner(hang_stage="round0")
        tool, _ = _make_ingest_tool()
        finalize_sink, _, fs = _finalize_sinks()

        async def shim():
            return await f9o.f9_orchestrator(
                "q",
                "s-o5",
                judge_model=_judge_model(),
                plan_model=_plan_model(),
                graph_runner=graph,
                search_tool=tool,
                finalize_sink=finalize_sink,
                status_sink=None,
            )

        async def scenario():
            task = asyncio.create_task(ctl.execute(rec.task_id, shim(), policy={}))
            for _ in range(300):
                if ctl.has_active_handle(rec.task_id):
                    break
                await asyncio.sleep(0.001)
            assert ctl.has_active_handle(rec.task_id)
            res = await ctl.cancel(rec.task_id)
            assert res["winner"] is True
            with pytest.raises(asyncio.CancelledError):
                await task
            row = gov_store.get_task(store, rec.task_id)
            return row

        row = _run(scenario())
        assert row.status == TaskStatus.CANCELLED.value
        assert fs["finalize"] == 0
        store.close()

    def test_cancel_during_targeted_no_finalize(self, research_sqlite, gov_tmp):
        """矩阵 #2：targeted research（search tool 挂起）段内 cancel → CANCELLED & finalize==0。"""
        from app.runtime.governance import store as gov_store
        from app.runtime.governance.models import TaskStatus

        run_id, _ = _seed_insufficient_run()  # gap → judge → plan → targeted
        store, ctl = self._controller(gov_tmp)
        rec = ctl.create_task("th-o6", run_id=run_id)
        graph, _ = _make_graph_runner()
        hang_tool = _HangSearchTool()
        finalize_sink, _, fs = _finalize_sinks()

        async def shim():
            return await f9o.f9_orchestrator(
                "q",
                "s-o6",
                judge_model=_judge_model(),
                plan_model=_plan_model(),
                graph_runner=graph,
                search_tool=hang_tool,
                finalize_sink=finalize_sink,
                status_sink=None,
            )

        async def scenario():
            task = asyncio.create_task(ctl.execute(rec.task_id, shim(), policy={}))
            for _ in range(1000):
                if hang_tool.started.is_set():
                    break
                await asyncio.sleep(0.001)
            assert hang_tool.started.is_set(), "未进入 targeted search tool 调用"
            res = await ctl.cancel(rec.task_id)
            assert res["winner"] is True
            with pytest.raises(asyncio.CancelledError):
                await task
            row = gov_store.get_task(store, rec.task_id)
            return row

        row = _run(scenario())
        assert row.status == TaskStatus.CANCELLED.value
        assert fs["finalize"] == 0
        store.close()

    def test_cancel_during_changed_claims_f456_no_finalize(
        self, research_sqlite, gov_tmp, monkeypatch
    ):
        """矩阵 #3：changed-claim 增量段（F4/F5/F6）内 cancel → CANCELLED & finalize==0。

        先真实跑一轮 adaptive（judge→plan→targeted 增加 binding→changed claims），以
        conflict.detect_claim_conflicts spy（进入 F4 时置 entered）判定 orchestrator 已进入
        F456 段；随后 orchestrator 在下一 changed claim 让步点投递取消 → 段内 CANCELLED。
        """
        from app.runtime.governance import store as gov_store
        from app.runtime.governance.models import TaskStatus
        from app.research import conflict as f4mod

        run_id, sqid, _ = _seed_claim_run(claim_count=2)
        store, ctl = self._controller(gov_tmp)
        rec = ctl.create_task("th-o7", run_id=run_id)
        graph, _ = _make_graph_runner()
        tool, _ = _make_ingest_tool(ev_per_call=2)
        finalize_sink, _, fs = _finalize_sinks()
        entered = asyncio.Event()
        calls = {"n": 0}

        real_detect = f4mod.detect_claim_conflicts

        def spy_detect(claim_id, **kwargs):
            calls["n"] += 1
            entered.set()  # F4 已处理首个 changed claim → 确定处于 F456 段
            return real_detect(claim_id, **kwargs)

        monkeypatch.setattr(f4mod, "detect_claim_conflicts", spy_detect)

        async def shim():
            return await f9o.f9_orchestrator(
                "q",
                "s-o7",
                judge_model=_judge_model(),
                plan_model=_plan_model(),
                graph_runner=graph,
                search_tool=tool,
                finalize_sink=finalize_sink,
                status_sink=None,
            )

        async def scenario():
            task = asyncio.create_task(ctl.execute(rec.task_id, shim(), policy={}))
            # 阻塞等待 F4 已处理首个 changed claim（orchestrator 正处于 F456 段内）。
            # 不允许秒数轮询竞态：sleep(0) 计数会在 orchestrator 到达 F456 前耗尽。
            await asyncio.wait_for(entered.wait(), timeout=20)
            assert calls["n"] > 0, "未进入 changed-claim 增量段（F456）"
            await asyncio.sleep(0)  # 让 orchestrator 推进到下一 changed-claim 让步点
            res = await ctl.cancel(rec.task_id)
            assert res["winner"] is True
            with pytest.raises(asyncio.CancelledError):
                await task
            row = gov_store.get_task(store, rec.task_id)
            return row

        row = _run(scenario())
        assert row.status == TaskStatus.CANCELLED.value
        assert calls["n"] > 0
        assert fs["finalize"] == 0
        store.close()

    def test_budget_during_final_synthesis_no_finalize(self, research_sqlite, gov_tmp):
        """矩阵 #6：final synthesis graph 段耗尽 llm budget → BUDGET_EXCEEDED & finalize==0。"""
        from app.runtime.governance import store as gov_store
        from app.runtime.governance.counters import GovernanceLimitExceeded
        from app.runtime.governance.models import TaskStatus

        run_id, _ = _seed_covered_run()  # no-gap → 直接 final synthesis
        store, ctl = self._controller(gov_tmp)
        rec = ctl.create_task("th-o8", run_id=run_id)
        # runner 在 final_synthesis 段做一次带 handler 的 LLM 调用（模拟 synthesis 内模型消耗）
        graph, _ = _make_graph_runner(
            llm_stage="final_synthesis", model=_JSONModel(resolver=lambda msgs: "x")
        )
        tool, _ = _make_ingest_tool()
        finalize_sink, _, fs = _finalize_sinks()

        async def shim():
            return await f9o.f9_orchestrator(
                "q",
                "s-o8",
                judge_model=_judge_model(),
                plan_model=_plan_model(),
                graph_runner=graph,
                search_tool=tool,
                finalize_sink=finalize_sink,
                status_sink=None,
            )

        async def scenario():
            with pytest.raises(GovernanceLimitExceeded):
                await ctl.execute(rec.task_id, shim(), policy={"max_llm_calls": 0})
            row = gov_store.get_task(store, rec.task_id)
            return row

        row = _run(scenario())
        assert row.status == TaskStatus.BUDGET_EXCEEDED.value
        assert fs["finalize"] == 0
        store.close()

    def test_judge_failure_fallback_completed_finalize_once(self, research_sqlite, gov_tmp):
        """矩阵 #7：ordinary Judge failure → adaptive fallback → final synthesis → F7 once。"""
        from app.runtime.governance import store as gov_store
        from app.runtime.governance.models import TaskStatus

        run_id, _ = _seed_empty_run()  # required gap → judge 触发 → failure → fallback
        store, ctl = self._controller(gov_tmp)
        rec = ctl.create_task("th-j1", run_id=run_id)
        graph, _ = _make_graph_runner()
        tool, _ = _make_ingest_tool()
        finalize_sink, status_sink, fs = _finalize_sinks()
        judge_failing = _judge_model(raise_error=RuntimeError("judge down"))

        async def shim():
            return await f9o.f9_orchestrator(
                "q",
                "s-j1",
                judge_model=judge_failing,
                plan_model=_plan_model(),
                graph_runner=graph,
                search_tool=tool,
                finalize_sink=finalize_sink,
                status_sink=status_sink,
            )

        async def scenario():
            res = await ctl.execute(rec.task_id, shim(), policy={})
            row = gov_store.get_task(store, rec.task_id)
            return res, row

        res, row = _run(scenario())
        assert row.status == TaskStatus.COMPLETED.value
        assert res["stopped_reason"] == "adaptive_fallback"
        assert fs["finalize"] == 1
        assert fs["status"] == ["finished"]
        runs = rstore.get_store().execute(
            "SELECT count(*) AS n FROM research_runs WHERE run_id=%s", (run_id,)
        )
        assert int(runs[0]["n"]) == 1  # 无第二 ResearchRun
        store.close()

    def test_plan_failure_fallback_completed_finalize_once(self, research_sqlite, gov_tmp):
        """矩阵 #8：ordinary Plan failure → adaptive fallback → final synthesis → F7 once。"""
        from app.runtime.governance import store as gov_store
        from app.runtime.governance.models import TaskStatus

        run_id, _ = _seed_empty_run()  # gap → judge → plan failure → fallback
        store, ctl = self._controller(gov_tmp)
        rec = ctl.create_task("th-p1", run_id=run_id)
        graph, _ = _make_graph_runner()
        tool, _ = _make_ingest_tool()
        finalize_sink, status_sink, fs = _finalize_sinks()
        plan_failing = _plan_model(raise_error=RuntimeError("plan down"))

        async def shim():
            return await f9o.f9_orchestrator(
                "q",
                "s-p1",
                judge_model=_judge_model(),
                plan_model=plan_failing,
                graph_runner=graph,
                search_tool=tool,
                finalize_sink=finalize_sink,
                status_sink=status_sink,
            )

        async def scenario():
            res = await ctl.execute(rec.task_id, shim(), policy={})
            row = gov_store.get_task(store, rec.task_id)
            return res, row

        res, row = _run(scenario())
        assert row.status == TaskStatus.COMPLETED.value
        assert res["stopped_reason"] == "adaptive_fallback"
        assert fs["finalize"] == 1
        assert fs["status"] == ["finished"]
        runs = rstore.get_store().execute(
            "SELECT count(*) AS n FROM research_runs WHERE run_id=%s", (run_id,)
        )
        assert int(runs[0]["n"]) == 1  # 无第二 ResearchRun
        store.close()
