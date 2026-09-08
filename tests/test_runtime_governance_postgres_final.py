"""F8 Step 3 — Phase F：PostgreSQL Final Gate（真实 PG16）。

Layer 3 证据：真实 PG 上的 terminal CAS/race（补 completed×budget、completed×timeout；
completed×cancel / budget×timeout / supersede×cancel 由 Step 2 PG gate 已证）、durable write
failure→pending→flush 恢复、control-path failure(c2)、startup 遗留 running→aborted 收敛、
isolation/unique/terminal 不被 start 覆盖、migration 幂等 + 表族隔离、
controller-governed run_deep_agent 全链（F4 场景集）+ F7 boundary 矩阵。

生产代码零改动；sweeper/startup 产品模块属后续 Step —— 本文件以冻结 funnel 组合提供
"startup reconciliation 语义" 的 PG 证据（见 Limitation）。
"""

import asyncio
import os
import shutil
import threading
import uuid
from pathlib import Path

import pytest

from app.runtime.governance import migrations as gov_migrations
from app.runtime.governance import store as gov_store
from app.runtime.governance.controller import GovernanceController
from app.runtime.governance.models import (
    TaskRecord,
    TaskStatus,
    TerminalReason,
)

PG_DSN_ENV = "AGENT_CHECKPOINT_DSN_TEST"
PG_DSN = os.getenv(PG_DSN_ENV)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST_TMP = _REPO_ROOT / "_testtmp"


@pytest.fixture
def gov_tmp():
    d = _TEST_TMP / f"pgf-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    try:
        shutil.rmtree(d)
    except OSError:
        pass


try:
    import psycopg  # noqa: F401

    HAS_PSYCOPG = True
except Exception:  # pragma: no cover
    HAS_PSYCOPG = False

needs_pg = pytest.mark.skipif(
    not (HAS_PSYCOPG and PG_DSN),
    reason=f"需要 psycopg 与 {PG_DSN_ENV}（真实 PG Final Gate）",
)

from tests._step3_repo_harness import (  # noqa: E402
    SUBAGENT_SPEC,
    _make_extra_tools,
    run_governed_scenario,
    tool_call_msg,
)
from langchain_core.messages import AIMessage  # noqa: E402


def _store():
    store = gov_store._GovernancePostgresStore(PG_DSN)
    gov_migrations.ensure_schema(store)
    return store


def _running(store, thread="th-pf", run=None, owner="inst-old", task_id=None):
    rec = TaskRecord(
        task_id=task_id or uuid.uuid4().hex,
        thread_id=thread,
        run_id=run,
        owner_instance=owner,
        status=TaskStatus.RUNNING.value,
        version=0,
        created_at="2026-09-16T00:00:00+00:00",
    )
    gov_store.insert_task(store, rec)
    return rec.task_id


# ---------------------------------------------------------------------------
# F1：真实 PG 并发 race（两独立 controller + 两波读对齐闸门 → 恰一 rowcount==1/0）
# ---------------------------------------------------------------------------
class _PairWaveGate:
    def __init__(self, waves=2, timeout=20.0):
        self._barriers = [threading.Barrier(2) for _ in range(waves)]
        self._count = 0
        self._lock = threading.Lock()
        self._timeout = timeout

    def arrive(self):
        with self._lock:
            if self._count >= 2 * len(self._barriers):
                return
            self._count += 1
            idx = (self._count - 1) // 2
            b = self._barriers[idx]
        b.wait(timeout=self._timeout)


def _race_pg(monkeypatch, task_id, reason_a, kw_a, reason_b, kw_b):
    records: list = []
    orig_get = gov_store.get_task
    orig_upd = gov_store.terminal_update
    gate = _PairWaveGate()
    _tl = threading.local()

    def get_wrapper(store, tid):
        row = orig_get(store, tid)
        if tid == task_id:
            gate.arrive()
        return row

    def upd_wrapper(store, tid, **kw):
        ok = orig_upd(store, tid, **kw)
        records.append((getattr(_tl, "label", None), bool(ok), tid))
        return ok

    monkeypatch.setattr(gov_store, "get_task", get_wrapper)
    monkeypatch.setattr(gov_store, "terminal_update", upd_wrapper)

    out = {}

    def worker(label, reason, kwargs):
        _tl.label = label
        store = _store()
        ctl = GovernanceController(
            store, owner_instance=f"inst-{label}", retry_delays=(0.01, 0.02)
        )

        async def _run():
            return await ctl.terminalize(task_id, reason, **kwargs)

        loop = asyncio.new_event_loop()
        try:
            out[label] = loop.run_until_complete(_run())
        finally:
            loop.close()
            store.close()

    ta = threading.Thread(target=worker, args=("A", reason_a, kw_a))
    tb = threading.Thread(target=worker, args=("B", reason_b, kw_b))
    ta.start()
    tb.start()
    ta.join(timeout=60)
    tb.join(timeout=60)
    return records, out


@needs_pg
class TestF1CasRacesPG:
    @pytest.mark.parametrize(
        "reason_a,kw_a,reason_b,kw_b",
        [
            (TerminalReason.COMPLETED, {}, TerminalReason.BUDGET_EXCEEDED, {}),
            (TerminalReason.COMPLETED, {}, TerminalReason.TIMED_OUT, {}),
        ],
        ids=["completed_x_budget", "completed_x_timeout"],
    )
    def test_race_exactly_one_durable_winner(
        self, monkeypatch, reason_a, kw_a, reason_b, kw_b
    ):
        store = _store()
        task_id = _running(store, "th-pf-race")
        store.close()
        records, out = _race_pg(monkeypatch, task_id, reason_a, kw_a, reason_b, kw_b)
        ok_true = [r for r in records if r[1] is True]
        ok_false = [r for r in records if r[1] is False]
        assert len(ok_true) == 1, f"恰一个 rowcount==1：{records}"
        assert len(ok_false) >= 1, f"loser rowcount==0：{records}"
        winner_label = ok_true[0][0]
        winner_reason = reason_a if winner_label == "A" else reason_b
        loser_label = "B" if winner_label == "A" else "A"
        check = _store()
        try:
            row = gov_store.get_task(check, task_id)
            assert row.version == 1  # 0→1 恰一次
            assert row.status == winner_reason.value  # DB 终态 = durable winner
            # loser 采纳 DB 事实（状态收敛）；不产生第二次 durable transition
            assert (
                out[loser_label]["already_terminal"] is True
                or out[loser_label]["durable"] is True
            )
            assert out[winner_label]["winner"] is True
        finally:
            check.close()


# ---------------------------------------------------------------------------
# F2：真实 PG durable write failure → pending → flush 恢复；c2 control failure
# ---------------------------------------------------------------------------
@needs_pg
class TestF2PersistenceFailurePG:
    def test_write_failure_pending_authority_then_flush(self, monkeypatch):
        async def scenario():
            store = _store()
            task_id = _running(store, "th-pf-f2")
            ctl = GovernanceController(
                store, owner_instance="inst-f2", retry_delays=(0.01, 0.02)
            )
            attempts = {"n": 0}

            def boom(*a, **k):
                attempts["n"] += 1
                raise RuntimeError("PG durable write failed")

            with monkeypatch.context() as m:
                m.setattr(gov_store, "terminal_update", boom)
                r = await ctl.terminalize(
                    task_id, TerminalReason.CANCELLED, counters={"s": 1}
                )
                assert r["winner"] is True
                assert r["durable"] is False and r["pending"] is True
                assert r["degraded_durability"] is True
                assert attempts["n"] == 3  # 立即 + 0.5s + 2s（缩时）
                # 内存裁决权威；真实 PG 行仍 running/v0
                row = gov_store.get_task(store, task_id)
                assert row.status == TaskStatus.RUNNING.value and row.version == 0
                # 不伪造 completed / 第二收敛 no-op
                r2 = await ctl.cancel(task_id)
                assert r2["winner"] is False and r2["already_terminal"] is True
            # 恢复 → flush：恰一次 transition、重复 flush 幂等
            f1 = await ctl.flush_pending()
            assert f1["flushed"] == 1 and f1["remaining"] == 0
            row2 = gov_store.get_task(store, task_id)
            assert row2.status == TaskStatus.CANCELLED.value
            assert row2.version == 1
            assert row2.counters_snapshot == {"s": 1}
            f2 = await ctl.flush_pending()
            assert f2["flushed"] == 0
            assert gov_store.get_task(store, task_id).version == 1
            store.close()

        asyncio.run(scenario())

    def test_c2_control_failure_converges(self):
        async def scenario():
            store = _store()
            task_id = _running(store, "th-pf-c2")
            ctl = GovernanceController(
                store, owner_instance="inst-c2", retry_delays=(0.01, 0.02)
            )
            await ctl._converge_control_failure(  # noqa: SLF001 — control-path failure 模拟
                task_id, None, RuntimeError("ctl internal")
            )
            row = gov_store.get_task(store, task_id)
            assert row.status == TaskStatus.FAILED.value
            assert row.error_kind == "governance_control_failure"
            assert row.version == 1
            store.close()

        asyncio.run(scenario())


# ---------------------------------------------------------------------------
# F3：startup 遗留 running → aborted 收敛（冻结 funnel 组合；单实例、owner 仅 audit）
# ---------------------------------------------------------------------------
@needs_pg
class TestF3StartupReconciliationPG:
    def test_running_aborted_and_terminal_untouched(self):
        async def scenario():
            store = _store()
            run_a = _running(store, "th-pf-s1", run="r1", owner="inst-crashed")
            run_b = _running(store, "th-pf-s2", run="r2", owner="inst-crashed")
            # 已 terminal 任务（旧实例已完成）——不得被 reconciliation 改动
            done = _running(store, "th-pf-s3", run="r3", owner="inst-crashed")
            with store.transaction() as tx:
                tx.execute_rc(
                    "UPDATE governance_tasks SET status=%s, version=version+1 "
                    "WHERE task_id=%s",
                    ("completed", done),
                )
            # 新 runtime instance 启动 → 全量遗留 running → aborted（经冻结 funnel）
            ctl = GovernanceController(
                store, owner_instance="inst-new", retry_delays=(0.01, 0.02)
            )
            for tid in (run_a, run_b):
                r = await ctl.terminalize(tid, TerminalReason.ABORTED)
                assert r["winner"] is True and r["durable"] is True
                row = gov_store.get_task(store, tid)
                assert row.status == TaskStatus.ABORTED.value
                assert row.terminal_reason == "aborted"
                assert row.version == 1
            # 已 terminal 不被重复修改；不产生 fake research（无 finalize 路径）
            r_done = await ctl.terminalize(done, TerminalReason.ABORTED)
            assert r_done["winner"] is False and r_done["already_terminal"] is True
            assert gov_store.get_task(store, done).status == TaskStatus.COMPLETED.value
            assert gov_store.get_task(store, done).version == 1
            store.close()

        asyncio.run(scenario())


# ---------------------------------------------------------------------------
# F6/F7：isolation / unique / start 覆盖 / migration 幂等 / 表族隔离
# ---------------------------------------------------------------------------
@needs_pg
class TestF6F7IsolationPG:
    def test_two_tasks_isolated_and_start_does_not_overwrite_terminal(self):
        async def scenario():
            store = _store()
            ctl = GovernanceController(
                store, owner_instance="inst-f6", retry_delays=(0.01, 0.02)
            )
            t1 = ctl.create_task("th-f6a", run_id="ra")
            t2 = ctl.create_task("th-f6b", run_id="rb")
            await ctl.terminalize(t1.task_id, TerminalReason.COMPLETED)
            r2 = await ctl.terminalize(t2.task_id, TerminalReason.BUDGET_EXCEEDED)
            assert r2["durable"] is True
            a = gov_store.get_task(store, t1.task_id)
            b = gov_store.get_task(store, t2.task_id)
            assert a.status == "completed" and b.status == "budget_exceeded"  # 互不影响
            # terminal 任务不能被 start 覆盖
            assert ctl.start_task(t1.task_id, "2026-09-16T00:00:00+00:00") is False
            assert gov_store.get_task(store, t1.task_id).started_at is None
            store.close()

        asyncio.run(scenario())

    def test_duplicate_task_id_unique_and_migration_idempotent_and_table_isolation(
        self,
    ):
        store = _store()
        try:
            task_id = uuid.uuid4().hex
            _running(store, task_id=task_id)
            with pytest.raises(Exception) as excinfo:
                _running(store, task_id=task_id)
            assert getattr(excinfo.value, "sqlstate", None) == "23505"
            # migration 幂等（数据不破坏）
            gov_migrations.ensure_schema(store)
            gov_migrations.ensure_schema(store)
            assert gov_store.get_task(store, task_id) is not None
            # governance 表族独立（不混入 checkpoint/research 官方表）
            rows = store.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public'"
            )
            gov_like = {
                r["tablename"] for r in rows if r["tablename"].startswith("governance_")
            }
            # governance ensure_schema 不应新建 checkpoint_*/research_*（其它测试可能建表，故只断言 governance_ 前缀集合合法）
            assert gov_like <= {
                "governance_tasks",
                "governance_events",
                "governance_schema_migrations",
            }
        finally:
            store.close()


# ---------------------------------------------------------------------------
# F4/F5：controller-governed run_deep_agent 全链 on PG + F7 boundary 矩阵
# ---------------------------------------------------------------------------
@needs_pg
class TestF4F5RunDeepAgentPG:
    @pytest.mark.parametrize(
        "scenario_fn",
        [
            "e1_normal",
            "e2_llm_budget",
            "e3_tool_budget",
            "e4_search_budget",
            "e5_subagent_budget",
            "e6_timeout",
            "e7_cancel",
            "e8_recursion",
            "e9_ordinary_failure",
        ],
    )
    def test_matrix(self, gov_tmp, scenario_fn):
        def run(**kw):
            return run_governed_scenario(
                scenario_fn, gov_tmp, store_kind="postgres", **kw
            )

        if scenario_fn == "e1_normal":
            out = run(responses=[AIMessage(content="FINAL OK")], policy={})
            assert out["db"]["status"] == "completed" and out["db"]["version"] == 1
            assert len(out["finalize_calls"]) == 1
            assert sum(1 for e in out["events"] if e == "task_result") == 1
            return
        if scenario_fn == "e2_llm_budget":
            out = run(
                responses=[tool_call_msg("probe_tool", {"q": "q"}, "pf1")],
                policy={"max_llm_calls": 1},
            )
        elif scenario_fn == "e3_tool_budget":
            out = run(
                responses=[tool_call_msg("probe_tool", {"q": "q"}, "pf2")],
                policy={"max_tool_calls": 0},
            )
        elif scenario_fn == "e4_search_budget":
            out = run(
                responses=[tool_call_msg("internet_search", {"query": "q"}, "pf3")],
                policy={"max_search_calls": 0},
                tools=_make_extra_tools("search"),
            )
        elif scenario_fn == "e5_subagent_budget":
            out = run(
                responses=[
                    tool_call_msg(
                        "task",
                        {"description": "d", "subagent_type": "probe-sub"},
                        "pf4",
                    )
                ],
                policy={"max_llm_calls": 1},
                subagents=SUBAGENT_SPEC,
            )
        elif scenario_fn == "e6_timeout":
            out = run(
                responses=[tool_call_msg("slow_tool", {"q": "q"}, "pf5")],
                policy={"wall_clock_timeout": 0.2},
                tools=_make_extra_tools("slow"),
            )
            assert out["db"]["linger"] is True
        elif scenario_fn == "e7_cancel":
            out = run(
                responses=[tool_call_msg("probe_tool", {"q": "q"}, "pf6")],
                policy={"wall_clock_timeout": 600},
                block_after_first=True,
                governed_cancel_after=0.1,
            )
        elif scenario_fn == "e8_recursion":
            out = run(
                responses=[
                    tool_call_msg("probe_tool", {"q": "q"}, f"pf7{i}") for i in range(6)
                ]
                + [AIMessage(content="FINAL")],
                policy={"framework_recursion_limit": 3},
            )
        else:  # e9
            out = run(raise_error=RuntimeError("boom"), policy={})

        # 治理终止矩阵：F7=0、无 task_result、version=1、非 completed
        assert out["finalize_calls"] == []
        assert "task_result" not in out["events"]
        assert out["db"]["version"] == 1
        assert out["db"]["status"] != "completed"
        if scenario_fn in (
            "e2_llm_budget",
            "e3_tool_budget",
            "e4_search_budget",
            "e5_subagent_budget",
        ):
            assert out["db"]["status"] == "budget_exceeded"
            if scenario_fn == "e2_llm_budget":
                assert out["provider_calls"] == 1
            if scenario_fn == "e3_tool_budget":
                assert out["tool_ran"] == 0
            if scenario_fn == "e4_search_budget":
                assert out["tool_by"].get("internet_search", 0) == 0
        elif scenario_fn == "e6_timeout":
            assert out["db"]["status"] == "timed_out"
        elif scenario_fn == "e7_cancel":
            assert out["db"]["status"] == "cancelled"
        elif scenario_fn == "e8_recursion":
            assert out["db"]["status"] == "failed"
            assert out["db"]["error_kind"] == "framework_recursion_safety"
        else:
            assert out["db"]["status"] == "failed"
            assert out["db"]["error_kind"] == "agent_failure"
