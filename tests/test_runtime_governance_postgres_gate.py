"""F8 Step 2 — PostgreSQL Gate Evidence Completion（P1–P5）。

只补 **真实 PostgreSQL** 上的 Gate 证据，不修改生产逻辑、不进入 Step 3。

背景：从 F8 起 PostgreSQL 是唯一生产基线与最终 Gate Backend；SQLite 仅快速单测。
本文件全部测试运行在真实 PG16（AGENT_CHECKPOINT_DSN_TEST，governance 独立表族）。

- P1 真并发 CAS 仲裁：两个**独立** GovernanceController（各自 asyncio.Lock / _decisions /
  各自 PG store 实例）对**同一 running task** 同时 terminalize。为避免"读-改-写"窗口被
  事件循环隐式串行化（单线程内 store 调用原子，后到者会在决策读时看到已 terminal 而走
  adoption、根本不发第二条 UPDATE），测试在 **store 边界**安装只读对齐闸门
  （_PairWaveGate，纯测试编排，不触碰生产同步模型）：让双方的"决策读"与"CAS 前重读"
  各自成对完成后才放行 → 两个真实 UPDATE 语句必然都发出、由 PG 行锁仲裁 →
  恰一个 rowcount=1、另一个 rowcount=0，并经 recorder 直接记录。
- P2 CAS loser/adoption 与 rowcount 语义：直接对真实 PG 行做 CAS（错 version → rc0，
  对 version → rc1）；独立第二 Controller 对已 terminal 任务收敛 → 采纳 DB 事实、
  零二次 UPDATE、version 不再 +1。
- P3 persistence failure → pending → flush：store 为**真实 PG**，仅在 terminal durable 写
  边界注入故障（3 次 retry 全失败 → pending/degraded，不谎报、不撤销内存裁决），恢复后
  flush_pending() 经真实 PG UPDATE 恰好一次收敛，重复 flush 幂等。
- P4 race matrix：completed×cancel / budget×timed_out / supersede×cancel 三组即 P1 的
  三个参数化用例（两个独立 Controller + 同一 PG task）。
- P5 Thread ≠ Task ≠ Run + duplicate task_id 唯一约束（真实 UniqueViolation）。
"""

import asyncio
import os
import threading
import uuid

import pytest

PG_TEST_DSN_ENV = "AGENT_CHECKPOINT_DSN_TEST"
PG_DSN = os.getenv(PG_TEST_DSN_ENV)

try:
    import psycopg  # noqa: F401

    HAS_PSYCOPG = True
except Exception:  # pragma: no cover
    HAS_PSYCOPG = False

needs_pg = pytest.mark.skipif(
    not (HAS_PSYCOPG and PG_DSN),
    reason=f"需要 psycopg 与 {PG_TEST_DSN_ENV}（真实 PG Gate 证据）",
)

from app.runtime.governance import migrations as gov_migrations  # noqa: E402
from app.runtime.governance import store as gov_store  # noqa: E402
from app.runtime.governance.controller import GovernanceController  # noqa: E402
from app.runtime.governance.models import (  # noqa: E402
    TaskRecord,
    TaskStatus,
    TerminalReason,
)


# ---------------------------------------------------------------------------
# 测试编排：store 边界读点对齐闸门 + rowcount recorder（线程安全；纯测试代码）
# ---------------------------------------------------------------------------
_tlocal = threading.local()


class _PairWaveGate:
    """让两个独立 writer 的"前 N 对读"成对对齐（waves 波次），之后放行。

    用于 P1：每个 Controller 的 terminalize 在发出 UPDATE 前恰有两次 get_task
    （决策读 + CAS 前重读）。把两次读分别做成两波对齐 → 双方都读到 running/v0 后
    才可能发 UPDATE → 两个真实 UPDATE 必然并发发出、由 PG 行锁仲裁出 rc1/rc0。
    """

    def __init__(self, waves: int = 2, timeout: float = 20.0) -> None:
        self._barriers = [threading.Barrier(2) for _ in range(waves)]
        self._max = waves * 2
        self._count = 0
        self._lock = threading.Lock()
        self._timeout = timeout

    def arrive(self) -> None:
        with self._lock:
            if self._count >= self._max:
                return  # 波次之后（retry/flush 读）直接放行
            self._count += 1
            idx = (self._count - 1) // 2
            barrier = self._barriers[idx] if idx < len(self._barriers) else None
        if barrier is not None:
            barrier.wait(timeout=self._timeout)


def _install_store_boundary(monkeypatch, task_id, records):
    """包一层真实 store 调用：只对 race task 对齐读点；记录每次 terminal_update 的 rowcount。

    返回的 wrapper 不改任何 SQL/语义：读仍走真实 PG SELECT，写仍走真实 PG UPDATE
    （psycopg cursor.rowcount 由 terminal_update 返回 bool rowcount==1）。
    """
    orig_get = gov_store.get_task
    orig_update = gov_store.terminal_update
    gate = _PairWaveGate(waves=2)

    def get_wrapper(store, tid):
        row = orig_get(store, tid)  # 真实 PG 读先完成，再对齐
        if tid == task_id:
            gate.arrive()
        return row

    def update_wrapper(store, tid, **kwargs):
        ok = orig_update(store, tid, **kwargs)  # 真实 PG CAS UPDATE
        records.append((getattr(_tlocal, "label", None), bool(ok), tid))
        return ok

    monkeypatch.setattr(gov_store, "get_task", get_wrapper)
    monkeypatch.setattr(gov_store, "terminal_update", update_wrapper)
    return gate


def _race_worker(dsn, task_id, reason, kwargs, label, out):
    """独立 Controller 线程：独立内存(_lock/_decisions) + 独立 PG store 实例。"""
    _tlocal.label = label
    store = gov_store._GovernancePostgresStore(dsn)
    gov_migrations.ensure_schema(store)
    ctl = GovernanceController(
        store, owner_instance=f"inst-{label}", retry_delays=(0.01, 0.02)
    )

    async def run():
        return await ctl.terminalize(task_id, reason, **kwargs)

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        out[label] = loop.run_until_complete(run())
    except Exception as exc:  # noqa: BLE001 — 线程内异常上抛到主线程断言
        out["_error_" + label] = exc
    finally:
        loop.close()
        store.close()


def _pg_store(dsn=None):
    store = gov_store._GovernancePostgresStore(dsn or PG_DSN)
    gov_migrations.ensure_schema(store)
    return store


def _create_running_task(store, thread_id, run_id=None, task_id=None):
    rec = TaskRecord(
        task_id=task_id or uuid.uuid4().hex,
        thread_id=thread_id,
        owner_instance="inst-staging",
        status=TaskStatus.RUNNING.value,
        run_id=run_id,
        version=0,
        created_at="2026-09-14T00:00:00+00:00",
    )
    gov_store.insert_task(store, rec)
    return rec.task_id


#: P1 / P4 的三组 terminal contention class（每组两个独立 Controller）
P1_PAIRS = [
    (
        "completed_x_cancel",
        TerminalReason.COMPLETED,
        {"counters": {"side": "A"}},
        TerminalReason.CANCELLED,
        {"counters": {"side": "B"}},
    ),
    (
        "budget_x_timeout",
        TerminalReason.BUDGET_EXCEEDED,
        {"counters": {"side": "A"}},
        TerminalReason.TIMED_OUT,
        {"counters": {"side": "B"}},
    ),
    (
        "supersede_x_cancel",
        TerminalReason.SUPERSEDED,
        {"counters": {"side": "A"}, "superseded_by": "task-new"},
        TerminalReason.CANCELLED,
        {"counters": {"side": "B"}},
    ),
]


@needs_pg
class TestPostgresGateEvidence:
    """P1 + P4：两个独立 Controller 真并发 CAS 仲裁（三组 contention class）。"""

    @pytest.mark.parametrize(
        "name,reason_a,kwargs_a,reason_b,kwargs_b",
        P1_PAIRS,
        ids=[p[0] for p in P1_PAIRS],
    )
    def test_p1_true_concurrent_cas(
        self, monkeypatch, name, reason_a, kwargs_a, reason_b, kwargs_b
    ):
        records: list = []

        with monkeypatch.context() as m:
            staging = _pg_store()
            task_id = _create_running_task(
                staging, thread_id=f"th-{name}", run_id=f"run-{name}"
            )
            staging.close()

            _install_store_boundary(m, task_id, records)
            out: dict = {}
            t_a = threading.Thread(
                target=_race_worker,
                args=(PG_DSN, task_id, reason_a, kwargs_a, "A", out),
            )
            t_b = threading.Thread(
                target=_race_worker,
                args=(PG_DSN, task_id, reason_b, kwargs_b, "B", out),
            )
            t_a.start()
            t_b.start()
            t_a.join(timeout=60)
            t_b.join(timeout=60)
            assert not t_a.is_alive() and not t_b.is_alive(), (
                "race worker 线程未在超时内结束"
            )
            assert "_error_A" not in out and "_error_B" not in out, out

        # -- rowcount 直接证据：恰一次 ok=True（rowcount==1），至少一次 ok=False（rowcount==0）
        ok_true = [r for r in records if r[1] is True]
        ok_false = [r for r in records if r[1] is False]
        assert len(ok_true) == 1, (
            f"必须恰一个 durable winner（rowcount==1），实际：{records}"
        )
        assert len(ok_false) >= 1, f"必须观察到 loser rowcount==0，实际：{records}"
        winner_label = ok_true[0][0]
        loser_label = "B" if winner_label == "A" else "A"
        winner_reason = reason_a if winner_label == "A" else reason_b

        # -- 最终 DB 状态：单终态、version 0→1 恰一次、快照与 durable winner 一致
        check = _pg_store()
        try:
            row = gov_store.get_task(check, task_id)
            assert row is not None
            assert row.status == winner_reason.value, (
                f"DB 终态应为 durable winner({winner_reason.value})，实际 {row.status}"
            )
            assert row.version == 1, f"version 必须 0→1 恰一次，实际 {row.version}"
            assert row.counters_snapshot == {"side": winner_label}
            if winner_reason == TerminalReason.SUPERSEDED:
                assert row.superseded_by == "task-new"
            else:
                assert row.superseded_by is None
        finally:
            check.close()

        # -- 双方 funnel 结果语义
        wr = out[winner_label]
        lr = out[loser_label]
        assert wr["winner"] is True
        assert wr["durable"] is True
        assert wr["status"] == winner_reason.value
        # loser：自身内存裁决过（winner=True 是"本进程决策者"语义），但 durable 唯一性由
        # recorder 的恰一个 rowcount==1 证明；loser 最终采纳 DB 事实（状态/快照收敛到 winner）
        assert lr["durable"] is True  # 采纳后 durable（非第二次 CAS 写）
        assert lr["pending"] is False
        assert lr["status"] == winner_reason.value
        assert lr["counters_snapshot"] == {"side": winner_label}
        assert lr["already_terminal"] is False or lr["adopted_from_db"] is True

        # -- 收敛后追加 funnel 仍是 no-op，version 不再变
        store3 = _pg_store()
        try:
            ctl3 = GovernanceController(store3, owner_instance="inst-follow")
            r3 = ctl3.terminalize(task_id, TerminalReason.CANCELLED)
            r3 = asyncio.run(r3)
            assert r3["already_terminal"] is True and r3["winner"] is False
            assert gov_store.get_task(store3, task_id).version == 1
        finally:
            store3.close()

    # ------------------------------------------------------------------
    def test_p2_cas_rowcount_semantics_on_real_pg(self):
        """P2a：真实 PG store 层 CAS rowcount 直接证据（错 version → rc0；对 → rc1）。"""
        store = _pg_store()
        try:
            task_id = _create_running_task(store, "th-cas-rc")
            # 错误 version → rowcount==0
            assert (
                gov_store.terminal_update(
                    store,
                    task_id,
                    expected_version=5,
                    status="completed",
                    terminal_reason="completed",
                    error_kind=None,
                    error=None,
                    counters_snapshot=None,
                    superseded_by=None,
                    underlying_linger_observed=None,
                    finished_at="2026-09-14T00:00:00+00:00",
                )
                is False
            )
            assert gov_store.get_task(store, task_id).version == 0  # 未误写
            # 正确 version → rowcount==1，version 0→1
            assert (
                gov_store.terminal_update(
                    store,
                    task_id,
                    expected_version=0,
                    status="completed",
                    terminal_reason="completed",
                    error_kind=None,
                    error=None,
                    counters_snapshot={"s": 1},
                    superseded_by=None,
                    underlying_linger_observed=None,
                    finished_at="2026-09-14T00:00:00+00:00",
                )
                is True
            )
            row = gov_store.get_task(store, task_id)
            assert row.version == 1 and row.status == "completed"
            # 已 terminal 后任何 CAS（无论 version）→ rowcount==0
            assert (
                gov_store.terminal_update(
                    store,
                    task_id,
                    expected_version=1,
                    status="cancelled",
                    terminal_reason="cancelled",
                    error_kind=None,
                    error=None,
                    counters_snapshot=None,
                    superseded_by=None,
                    underlying_linger_observed=None,
                    finished_at="2026-09-14T00:00:00+00:00",
                )
                is False
            )
            assert (
                gov_store.get_task(store, task_id).version == 1
            )  # 无第二次 transition
        finally:
            store.close()

    def test_p2_cas_loser_adoption_no_second_writer(self, monkeypatch):
        """P2b：独立第二 Controller 对已 terminal 任务收敛 → 零 UPDATE、采纳 DB、version 不变。"""
        records: list = []

        with monkeypatch.context() as m:
            _install_store_boundary(m, "__p2_none__", records)  # 不 gate 本用例读
            store_a = _pg_store()
            task_id = _create_running_task(store_a, "th-p2-adopt")
            ctl_a = GovernanceController(store_a, owner_instance="inst-a")
            ra = asyncio.run(
                ctl_a.terminalize(task_id, TerminalReason.COMPLETED, counters={"c": 1})
            )
            assert ra["winner"] is True and ra["durable"] is True
            assert (
                len(records) == 1 and records[0][1] is True
            )  # A 是唯一 durable writer
            store_a.close()

            # 独立 Controller B（独立内存 + 独立 store），对已 terminal 任务收敛
            store_b = _pg_store()
            ctl_b = GovernanceController(store_b, owner_instance="inst-b")
            rb = asyncio.run(ctl_b.terminalize(task_id, TerminalReason.CANCELLED))
            assert rb["winner"] is False
            assert rb["already_terminal"] is True
            assert rb["durable"] is True
            assert rb["adopted_from_db"] is True
            assert rb["status"] == TaskStatus.COMPLETED.value
            # B 未发出任何第二次 UPDATE（records 仍只有 A 的一次）
            assert len(records) == 1, f"B 不得产生第二次 transition：{records}"
            row = gov_store.get_task(store_b, task_id)
            assert row.version == 1  # version 不从 1 再变 2
            assert row.status == TaskStatus.COMPLETED.value
            assert row.counters_snapshot == {"c": 1}
            store_b.close()

    # ------------------------------------------------------------------
    def test_p3_persistence_failure_pending_then_flush(self, monkeypatch):
        """P3：真实 PG store + 仅在 durable 写边界注入故障 → pending → 恢复 → flush 收敛。"""
        store = _pg_store()
        task_id = _create_running_task(store, "th-p3")
        ctl = GovernanceController(
            store, owner_instance="inst-p3", retry_delays=(0.01, 0.02)
        )
        counters = {"agent_steps": 3}
        attempts = {"n": 0}

        def failing_update(*a, **k):
            attempts["n"] += 1
            raise RuntimeError("PG durable write injected failure")

        async def scenario():
            with monkeypatch.context() as m:
                # 只注入 terminal durable 写路径；读仍走真实 PG（row 保持 running/v0）
                m.setattr(gov_store, "terminal_update", failing_update)
                r = await ctl.terminalize(
                    task_id, TerminalReason.CANCELLED, counters=counters
                )
                assert r["winner"] is True
                assert r["durable"] is False  # 绝不谎报
                assert r["pending"] is True
                assert r["degraded_durability"] is True
                assert r["attempts"] == 3  # 立即 + 0.5s + 2s（测试缩时）
                assert attempts["n"] == 3
                # 内存裁决不被撤销
                dec = ctl.terminal_decision(task_id)
                assert dec is not None and dec["status"] == TaskStatus.CANCELLED.value
                assert dec["durable"] is False and dec["pending"] is True
                # DB 真实状态仍 running/v0（读走真实 PG）
                row = gov_store.get_task(store, task_id)
                assert row.status == TaskStatus.RUNNING.value and row.version == 0
                # pending 期间重复收敛 → no-op（内存裁决权威）
                r2 = await ctl.cancel(task_id)
                assert r2["winner"] is False and r2["already_terminal"] is True
                assert r2["durable"] is False and r2["pending"] is True
            # 恢复 PG → flush 收敛（真实 PG CAS UPDATE）
            out1 = await ctl.flush_pending()
            assert out1["flushed"] == 1 and out1["remaining"] == 0
            row2 = gov_store.get_task(store, task_id)
            assert row2.status == TaskStatus.CANCELLED.value
            assert row2.version == 1  # 只发生一次 terminal transition
            assert row2.counters_snapshot == counters
            assert ctl.pending_snapshot() == []
            # 重复 flush 幂等：不产生第二次 transition
            out2 = await ctl.flush_pending()
            assert out2["flushed"] == 0 and out2["remaining"] == 0
            assert gov_store.get_task(store, task_id).version == 1
            view = ctl.lifecycle(task_id)
            assert (
                view["degraded_durability"] is False
                and view["pending_terminal"] is False
            )

        asyncio.run(scenario())
        store.close()

    # ------------------------------------------------------------------
    def test_p5_thread_task_run_independent_lifecycles(self):
        """P5：同 thread 多 task、run_id 独立关联、task_id 为生命周期主键（真实 PG）。"""
        store = _pg_store()
        try:
            ctl = GovernanceController(store, owner_instance="inst-p5")
            ta = ctl.create_task("th-T", run_id="run-RA")
            tb = ctl.create_task("th-T", run_id="run-RB")
            assert ta.task_id != tb.task_id
            assert ta.thread_id == tb.thread_id == "th-T"
            assert ta.run_id == "run-RA" and tb.run_id == "run-RB"
            # A terminal 不影响 B；B 独立收敛
            ra = asyncio.run(ctl.terminalize(ta.task_id, TerminalReason.COMPLETED))
            assert ra["durable"] is True
            rb = asyncio.run(ctl.terminalize(tb.task_id, TerminalReason.COMPLETED))
            assert rb["durable"] is True
            a = gov_store.get_task(store, ta.task_id)
            b = gov_store.get_task(store, tb.task_id)
            assert a.status == "completed" and a.version == 1
            assert b.status == "completed" and b.version == 1
            assert a.thread_id == b.thread_id == "th-T"
            assert a.task_id != b.task_id  # lifecycle 以 task_id 为主键
        finally:
            store.close()

    def test_p5_duplicate_task_id_unique_violation(self):
        """P5：duplicate task_id 由真实 PG 唯一约束拒绝（sqlstate 23505），非应用层检查。"""
        store = _pg_store()
        try:
            task_id = uuid.uuid4().hex
            _create_running_task(store, "th-dup", run_id="r1", task_id=task_id)
            with pytest.raises(Exception) as excinfo:
                _create_running_task(store, "th-dup2", run_id="r2", task_id=task_id)
            sqlstate = getattr(excinfo.value, "sqlstate", None)
            assert sqlstate == "23505", (
                f"期望唯一约束违规(23505)，实际 {sqlstate}: {excinfo.value}"
            )
            # 未被覆盖：原行仍在
            row = gov_store.get_task(store, task_id)
            assert row is not None and row.thread_id == "th-dup"
        finally:
            store.close()

    def test_p5_start_does_not_overwrite_terminal_row(self):
        """start_task 不覆盖 terminal 行（真实 PG UPDATE 语义：WHERE running）。"""
        store = _pg_store()
        try:
            ctl = GovernanceController(store, owner_instance="inst-p5s")
            rec = ctl.create_task("th-s")
            asyncio.run(ctl.terminalize(rec.task_id, TerminalReason.COMPLETED))
            assert ctl.start_task(rec.task_id, "2026-09-14T00:00:00+00:00") is False
            row = gov_store.get_task(store, rec.task_id)
            assert row.status == "completed" and row.started_at is None
            assert row.version == 1
        finally:
            store.close()
