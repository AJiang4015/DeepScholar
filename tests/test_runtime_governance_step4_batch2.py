"""F8 Step 4 Batch 2 — Replay API / cursor (task_id, governance_seq)（SQLite 快速 + PG Gate）。

覆盖：单 task replay；since_seq 边界；limit；next_seq；同 thread 双 task 交错 → 各自 cursor
无重复/无遗漏；task_id 缺省→active 解析；task_id 不属于 thread；task 不存在；event_id 幂等
（重发不重复）；同 task gap 检测；多 task 不得把别的 task 的 seq 当 cursor。

写路径（events 单写者）/schema 均只读冻结——本文件不改写，只测 replay 读取。
"""

import os
import shutil
import uuid
from pathlib import Path

import pytest

from app.runtime.governance import events as gov_events
from app.runtime.governance import migrations as gov_migrations
from app.runtime.governance import store as gov_store
from app.runtime.governance.controller import GovernanceController

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST_TMP = _REPO_ROOT / "_testtmp"


@pytest.fixture
def gov_tmp():
    d = _TEST_TMP / f"g4b-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    try:
        shutil.rmtree(d)
    except OSError:
        pass


def _store(gov_tmp, backend="sqlite"):
    if backend == "sqlite":
        db = str(gov_tmp / f"gov-{uuid.uuid4().hex}.sqlite")
        store = gov_store._GovernanceSqliteStore(db)
    else:
        store = gov_store._GovernancePostgresStore(
            os.environ["AGENT_CHECKPOINT_DSN_TEST"]
        )
    gov_migrations.ensure_schema(store)
    return store


def _task(store, thread, run=None):
    rec = __import__(
        "app.runtime.governance.models", fromlist=["TaskRecord"]
    ).TaskRecord(
        task_id=uuid.uuid4().hex,
        thread_id=thread,
        run_id=run or uuid.uuid4().hex,
        owner_instance="inst-b2",
        status="running",
        version=0,
        created_at="2026-09-17T00:00:00+00:00",
    )
    gov_store.insert_task(store, rec)
    return rec.task_id


def _emit(store, task_id, thread, run, status, eid=None):
    return gov_events.lifecycle_event(
        store,
        task_id=task_id,
        thread_id=thread,
        run_id=run,
        status=status,
        event_id=eid,
    )


class TestReplaySqlite:
    def test_single_task_replay_and_since_and_limit(self, gov_tmp):
        store = _store(gov_tmp)
        tid = _task(store, "th-1")
        run = gov_store.get_task(store, tid).run_id
        for st in ("running", "completed"):
            _emit(store, tid, "th-1", run, st)
        # 全量
        r0 = gov_events.replay_events(store, thread_id="th-1", task_id=tid, since_seq=0)
        assert [e["governance_seq"] for e in r0["events"]] == [1, 2]
        assert r0["next_seq"] == 2 and r0["gap"] is False
        # since_seq 边界：>1 → 只回 2；>2 → 空
        r1 = gov_events.replay_events(store, thread_id="th-1", task_id=tid, since_seq=1)
        assert [e["governance_seq"] for e in r1["events"]] == [2]
        r2 = gov_events.replay_events(store, thread_id="th-1", task_id=tid, since_seq=2)
        assert r2["events"] == [] and r2["next_seq"] is None
        # limit
        for st3 in ("cancelled",):
            _emit(store, tid, "th-1", run, st3)
        rl = gov_events.replay_events(
            store, thread_id="th-1", task_id=tid, since_seq=0, limit=2
        )
        assert len(rl["events"]) == 2 and rl["next_seq"] == 2
        # limit clamp 1000
        assert gov_events.MAX_REPLAY_LIMIT == 1000
        store.close()

    def test_double_task_cursors_no_dup_no_gap(self, gov_tmp):
        store = _store(gov_tmp)
        th = "th-d"
        ta = _task(store, th)
        tb = _task(store, th)
        ra = gov_store.get_task(store, ta).run_id
        rb = gov_store.get_task(store, tb).run_id
        # 交错：A1,A2,B1,A3,B2（A 3 条、B 2 条）
        _emit(store, ta, th, ra, "running")
        _emit(store, ta, th, ra, "completed")
        _emit(store, tb, th, rb, "running")
        _emit(store, ta, th, ra, "budget_exceeded")
        _emit(store, tb, th, rb, "completed")
        a = gov_events.replay_events(store, thread_id=th, task_id=ta)
        b = gov_events.replay_events(store, thread_id=th, task_id=tb)
        assert [e["governance_seq"] for e in a["events"]] == [1, 2, 3]
        assert [e["governance_seq"] for e in b["events"]] == [1, 2]
        assert a["gap"] is False and b["gap"] is False
        # 各自 cursor 前进：a from since=2 → 只剩 3；不得混入 b 的任何 seq
        a2 = gov_events.replay_events(store, thread_id=th, task_id=ta, since_seq=2)
        assert [e["task_id"] for e in a2["events"]] == [ta]
        assert [e["governance_seq"] for e in a2["events"]] == [3]
        store.close()

    def test_gap_detection_same_task(self, gov_tmp):
        store = _store(gov_tmp)
        tid = _task(store, "th-g")
        run = gov_store.get_task(store, tid).run_id
        _emit(store, tid, "th-g", run, "running")
        _emit(store, tid, "th-g", run, "completed")
        _emit(store, tid, "th-g", run, "cancelled")
        # 人为删除 seq=2 → 回放 gap
        with store.transaction() as tx:
            tx.execute(
                "DELETE FROM governance_events WHERE task_id=%s AND seq=%s", (tid, 2)
            )
        r = gov_events.replay_events(store, thread_id="th-g", task_id=tid, since_seq=0)
        assert r["gap"] is True
        assert [e["governance_seq"] for e in r["events"]] == [1, 3]
        store.close()

    def test_event_id_idempotency_and_not_in_thread(self, gov_tmp):
        store = _store(gov_tmp)
        tid = _task(store, "th-x")
        run = gov_store.get_task(store, tid).run_id
        eid = uuid.uuid4().hex
        _emit(store, tid, "th-x", run, "completed", eid=eid)
        _emit(store, tid, "th-x", run, "completed", eid=eid)  # 重复 → 幂等
        rows = store.execute(
            "SELECT COUNT(*) AS n FROM governance_events WHERE event_id=%s", (eid,)
        )
        assert rows[0]["n"] == 1
        # 跨 thread 过滤：错误 thread → 空（replay 限定 thread+task）
        r = gov_events.replay_events(store, thread_id="other-th", task_id=tid)
        assert r["events"] == []
        store.close()

    def test_catchup_equivalence_and_no_dup(self, gov_tmp):
        """WS catch-up 等价：replay(since=N) == replay(0) 的尾部（无重复、无遗漏）。"""
        store = _store(gov_tmp)
        tid = _task(store, "th-cu")
        run = gov_store.get_task(store, tid).run_id
        for st in ("running", "completed", "budget_exceeded"):
            _emit(store, tid, "th-cu", run, st)
        r0 = gov_events.replay_events(
            store, thread_id="th-cu", task_id=tid, since_seq=0
        )
        mid = r0["next_seq"] - 1  # 假想上次握手已见 seq<=mid
        head = gov_events.replay_events(
            store, thread_id="th-cu", task_id=tid, since_seq=0, limit=mid
        )
        tail = gov_events.replay_events(
            store, thread_id="th-cu", task_id=tid, since_seq=mid
        )
        head_ids = [e["event_id"] for e in head["events"]]
        tail_ids = [e["event_id"] for e in tail["events"]]
        assert len(head_ids) == len(set(head_ids))
        assert len(head_ids) + len(tail_ids) == len(r0["events"])  # 无遗漏
        assert not (set(head_ids) & set(tail_ids))  # 无重复
        # WS governance_replay 帧字段 = replay 事件视图（client 以 event_id 去重）
        sample = tail["events"][0]
        for key in (
            "event_id",
            "task_id",
            "thread_id",
            "governance_seq",
            "event_type",
            "payload",
            "durable",
        ):
            assert key in sample
        store.close()

    def test_resolution_helper_semantics(self, gov_tmp, monkeypatch):
        """task_id 缺省 → active 解析 / 不属于 thread / 不存在（服务层 helper 语义）。"""
        from app.api import server as srv

        store = _store(gov_tmp)
        ctl = GovernanceController(
            store, owner_instance="inst", retry_delays=(0.01, 0.02)
        )
        th = "th-r"
        ta = ctl.create_task(th, run_id="r1")
        tb = ctl.create_task("other-th", run_id="r2")
        monkeypatch.setattr(srv, "_task_registry", {th: ta.task_id})
        ctl2_ref = {"ctl": ctl}
        monkeypatch.setattr(srv, "_require_governance", lambda: ctl2_ref["ctl"])
        # 缺省 → active task
        got, err = srv._resolve_replay_task(th, None)
        assert got == ta.task_id and err is None
        # 明确 task、属于该 thread → ok；不属于 → err
        got2, err2 = srv._resolve_replay_task(th, ta.task_id)
        assert got2 == ta.task_id and err2 is None
        got3, err3 = srv._resolve_replay_task(th, tb.task_id)
        assert got3 is None and err3 and err3[0] == "not_in_thread"
        got4, err4 = srv._resolve_replay_task(th, "missing")
        assert got4 is None and err4[0] == "not_found"
        # 无活跃且缺省 → no_active
        got5, err5 = srv._resolve_replay_task("empty-th", None)
        assert got5 is None and err5[0] == "no_active"
        store.close()


PG_TEST_DSN_ENV = "AGENT_CHECKPOINT_DSN_TEST"

try:
    import psycopg  # noqa: F401

    HAS_PSYCOPG = True
except Exception:  # pragma: no cover
    HAS_PSYCOPG = False

needs_pg = pytest.mark.skipif(
    not (HAS_PSYCOPG and os.getenv(PG_TEST_DSN_ENV)),
    reason=f"需要 psycopg 与 {PG_TEST_DSN_ENV}",
)


@needs_pg
class TestReplayPostgres:
    def test_replay_asc_since_limit_next(self):
        d = _TEST_TMP / f"pgb2-{uuid.uuid4().hex}"
        d.mkdir(parents=True, exist_ok=True)
        try:
            store = _store(d, backend="postgres")
            tid = _task(store, "th-pg")
            run = gov_store.get_task(store, tid).run_id
            for st in ("running", "completed", "budget_exceeded"):
                _emit(store, tid, "th-pg", run, st)
            r = gov_events.replay_events(
                store, thread_id="th-pg", task_id=tid, since_seq=0
            )
            assert [e["governance_seq"] for e in r["events"]] == [1, 2, 3]
            assert r["next_seq"] == 3 and r["gap"] is False
            r1 = gov_events.replay_events(
                store, thread_id="th-pg", task_id=tid, since_seq=1, limit=1
            )
            assert [e["governance_seq"] for e in r1["events"]] == [2]
            # 跨 task 独立 cursor
            tid2 = _task(store, "th-pg")
            run2 = gov_store.get_task(store, tid2).run_id
            _emit(store, tid2, "th-pg", run2, "running")
            a = gov_events.replay_events(store, thread_id="th-pg", task_id=tid)
            b = gov_events.replay_events(store, thread_id="th-pg", task_id=tid2)
            assert a["next_seq"] == 3 and b["next_seq"] == 1
            store.close()
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_gap_and_idempotent_event(self):
        d = _TEST_TMP / f"pgb2g-{uuid.uuid4().hex}"
        d.mkdir(parents=True, exist_ok=True)
        try:
            store = _store(d, backend="postgres")
            tid = _task(store, "th-pgg")
            run = gov_store.get_task(store, tid).run_id
            _emit(store, tid, "th-pgg", run, "running")
            eid = uuid.uuid4().hex
            _emit(store, tid, "th-pgg", run, "completed", eid=eid)
            _emit(store, tid, "th-pgg", run, "completed", eid=eid)  # 幂等
            _emit(store, tid, "th-pgg", run, "cancelled")  # seq=3
            _emit(store, tid, "th-pgg", run, "budget_exceeded")  # seq=4
            with store.transaction() as tx:
                tx.execute(
                    "DELETE FROM governance_events WHERE task_id=%s AND seq=%s",
                    (tid, 3),
                )
            r = gov_events.replay_events(
                store, thread_id="th-pgg", task_id=tid, since_seq=0
            )
            assert r["gap"] is True and [e["governance_seq"] for e in r["events"]] == [
                1,
                2,
                4,
            ]
            rows = store.execute(
                "SELECT COUNT(*) AS n FROM governance_events WHERE event_id=%s", (eid,)
            )
            assert rows[0]["n"] == 1
            store.close()
        finally:
            shutil.rmtree(d, ignore_errors=True)
