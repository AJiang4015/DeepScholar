"""Multi-Session — sessions store / migration 测试（SQLite；function-level repository）。

覆盖（Decision Closure #2/#4/#9）：
- governance 迁移族 0002_sessions：fresh apply、upgrade 0001→0002、幂等 ensure、版本表；
- sessions CRUD（create/get/list/set_status）与双后端字段归一（from_row 含 PG datetime 路径）；
- 派生关联聚合 task_summary（task_count / running_tasks / latest_task，thread 作用域隔离）。
"""

import datetime as _dt
import shutil
import uuid
from pathlib import Path

import pytest

from app.runtime.governance import migrations as gov_migrations
from app.runtime.governance import store as gov_store
from app.runtime.governance.models import TaskRecord
from app.session import store as sess_store
from app.session.models import Session, SessionStatus

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST_TMP = _REPO_ROOT / "_testtmp"
_MIGRATIONS_DIR = _REPO_ROOT / "db" / "governance_migrations"


@pytest.fixture
def gov_tmp():
    d = _TEST_TMP / f"ms-gov-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    try:
        shutil.rmtree(d)
    except OSError:
        pass


@pytest.fixture
def gov_sqlite(gov_tmp):
    store = gov_store._GovernanceSqliteStore(str(gov_tmp / "gov.sqlite"))
    gov_migrations.ensure_schema(store)
    yield store
    store.close()


def _sess(
    session_id: str,
    *,
    title: str = "t",
    status: str = SessionStatus.ACTIVE.value,
    updated_at: str = "2026-10-03T00:00:00+00:00",
    description: str | None = None,
) -> Session:
    return Session(
        session_id=session_id,
        title=title,
        description=description,
        status=status,
        created_at="2026-10-03T00:00:00+00:00",
        updated_at=updated_at,
    )


def _task(
    thread_id: str,
    *,
    status: str,
    created_at: str,
    task_id: str | None = None,
    run_id: str | None = None,
) -> TaskRecord:
    return TaskRecord(
        task_id=task_id or uuid.uuid4().hex,
        thread_id=thread_id,
        owner_instance="test",
        status=status,
        run_id=run_id,
        created_at=created_at,
    )


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------
class TestSessionMigration:
    def test_fresh_applies_0001_and_0002(self, gov_sqlite):
        assert sorted(gov_migrations.applied_migration_versions(gov_sqlite)) == [
            "0001",
            "0002",
        ]
        rows = gov_sqlite.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'"
        )
        assert len(rows) == 1

    def test_ensure_schema_idempotent(self, gov_sqlite):
        gov_migrations.ensure_schema(gov_sqlite)
        assert sorted(gov_migrations.applied_migration_versions(gov_sqlite)) == [
            "0001",
            "0002",
        ]

    def test_upgrade_0001_to_0002(self, gov_tmp):
        """已有 0001 的库（旧 governance DB）→ ensure_schema 只补 0002，不动 0001。"""
        store = gov_store._GovernanceSqliteStore(str(gov_tmp / "old.sqlite"))
        # 模拟旧库：0001 DDL + 版本表（runner 自建）已应用、版本记录 0001
        sql = (_MIGRATIONS_DIR / "0001_governance.sqlite.sql").read_text(
            encoding="utf-8"
        )
        for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
            store.execute(stmt)
        with store.transaction() as tx:
            tx.execute(
                "CREATE TABLE IF NOT EXISTS governance_schema_migrations ("
                "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            tx.execute(
                "INSERT INTO governance_schema_migrations (version, applied_at) "
                "VALUES (%s, %s)",
                ("0001", "2026-10-03T00:00:00+00:00"),
            )
        gov_migrations.ensure_schema(store)  # 自动补 0002
        assert sorted(gov_migrations.applied_migration_versions(store)) == [
            "0001",
            "0002",
        ]
        store.close()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
class TestSessionStoreCrud:
    def test_create_and_get_roundtrip(self, gov_sqlite):
        sess = _sess("abc123", title="深度研搜 · 机器人", description="desc")
        sess_store.create_session(gov_sqlite, sess)
        got = sess_store.get_session(gov_sqlite, "abc123")
        assert got is not None
        assert got.session_id == "abc123"
        assert got.title == "深度研搜 · 机器人"
        assert got.description == "desc"
        assert got.status == SessionStatus.ACTIVE.value
        assert got.created_at == got.updated_at
        assert not got.is_archived

    def test_get_unknown_returns_none(self, gov_sqlite):
        assert sess_store.get_session(gov_sqlite, "nope") is None

    def test_duplicate_create_raises(self, gov_sqlite):
        sess_store.create_session(gov_sqlite, _sess("dup1"))
        with pytest.raises(Exception):  # sqlite3.IntegrityError（PK 冲突）
            sess_store.create_session(gov_sqlite, _sess("dup1"))

    def test_set_status_and_missing(self, gov_sqlite):
        sid = "s1"
        sess_store.create_session(gov_sqlite, _sess(sid))
        rc = sess_store.set_status(
            gov_sqlite, sid, SessionStatus.ARCHIVED.value, "2026-10-03T00:00:01+00:00"
        )
        assert rc == 1
        got = sess_store.get_session(gov_sqlite, sid)
        assert got is not None and got.is_archived
        assert got.updated_at == "2026-10-03T00:00:01+00:00"
        assert (
            sess_store.set_status(
                gov_sqlite, "missing", SessionStatus.ARCHIVED.value, "x"
            )
            == 0
        )

    def test_from_row_normalizes_datetime(self):
        """PG timestamptz 读取（datetime）→ ISO str 归一（models._as_str）。"""
        row = {
            "session_id": "pg1",
            "title": "t",
            "description": None,
            "status": SessionStatus.ACTIVE.value,
            "created_at": _dt.datetime(2026, 10, 3, 0, 0, 0, tzinfo=_dt.timezone.utc),
            "updated_at": _dt.datetime(2026, 10, 3, 0, 0, 1, tzinfo=_dt.timezone.utc),
        }
        sess = Session.from_row(row)
        assert sess.created_at == "2026-10-03T00:00:00+00:00"
        assert sess.updated_at == "2026-10-03T00:00:01+00:00"
        d = sess.to_dict()
        assert d["session_id"] == "pg1" and d["status"] == "active"


class TestSessionStoreList:
    def _seed(self, gov_sqlite):
        active_a = _sess(
            "active-a",
            updated_at="2026-10-03T00:00:02+00:00",
            title="A-最近",
        )
        active_b = _sess("active-b", updated_at="2026-10-03T00:00:01+00:00")
        archived_c = _sess(
            "archived-c",
            status=SessionStatus.ARCHIVED.value,
            updated_at="2026-10-03T00:00:03+00:00",
        )
        for s in (active_a, active_b, archived_c):
            sess_store.create_session(gov_sqlite, s)

    def test_default_excludes_archived_and_totals(self, gov_sqlite):
        self._seed(gov_sqlite)
        sessions, total = sess_store.list_sessions(gov_sqlite)
        assert total == 2
        assert [s.session_id for s in sessions] == [
            "active-a",
            "active-b",
        ]  # updated desc

    def test_include_archived_returns_all(self, gov_sqlite):
        self._seed(gov_sqlite)
        sessions, total = sess_store.list_sessions(gov_sqlite, include_archived=True)
        assert total == 3
        assert sessions[0].session_id == "archived-c"  # updated desc 全局

    def test_limit_offset(self, gov_sqlite):
        self._seed(gov_sqlite)
        sessions, total = sess_store.list_sessions(
            gov_sqlite, include_archived=True, limit=1, offset=1
        )
        assert total == 3
        assert [s.session_id for s in sessions] == ["active-a"]


class TestTaskSummary:
    def _seed_threads(self, gov_sqlite):
        # Session A 两个 task（一个 running、一个 completed）；Session B 一个 completed；C 无 task
        gov_store.insert_task(
            gov_sqlite,
            _task(
                "A",
                status="running",
                created_at="2026-10-03T00:00:02+00:00",
                run_id="run-a2",
            ),
        )
        gov_store.insert_task(
            gov_sqlite,
            _task(
                "A",
                status="completed",
                created_at="2026-10-03T00:00:01+00:00",
                run_id="run-a1",
            ),
        )
        gov_store.insert_task(
            gov_sqlite,
            _task(
                "B",
                status="completed",
                created_at="2026-10-03T00:00:03+00:00",
                run_id="run-b1",
            ),
        )
        return ["A", "B", "C"]

    def test_aggregation_and_latest(self, gov_sqlite):
        threads = self._seed_threads(gov_sqlite)
        summary = sess_store.task_summary(gov_sqlite, threads)
        a = summary["A"]
        assert a["task_count"] == 2
        assert a["running_tasks"] == 1
        assert a["latest_task"]["task_id"] is not None
        assert a["latest_task"]["run_id"] == "run-a2"  # created_at 最新
        assert a["latest_task"]["status"] == "running"
        b = summary["B"]
        assert b["task_count"] == 1 and b["running_tasks"] == 0
        assert b["latest_task"]["run_id"] == "run-b1"
        c = summary["C"]
        assert c["task_count"] == 0 and c["running_tasks"] == 0
        assert c["latest_task"] is None

    def test_thread_scope_isolation(self, gov_sqlite):
        self._seed_threads(gov_sqlite)
        summary = sess_store.task_summary(gov_sqlite, ["A"])
        assert set(summary) == {"A"}
        assert summary["A"]["task_count"] == 2  # B 的 task 不计入 A

    def test_empty_input(self, gov_sqlite):
        assert sess_store.task_summary(gov_sqlite, []) == {}


class TestArchiveGuard:
    def test_guarded_archive_when_no_running(self, gov_sqlite):
        sid = "guard-ok"
        sess_store.create_session(gov_sqlite, _sess(sid))
        rc = sess_store.archive_if_no_running(
            gov_sqlite, sid, "2026-10-03T00:00:01+00:00"
        )
        assert rc == 1
        assert sess_store.get_session(gov_sqlite, sid).is_archived

    def test_guarded_archive_blocked_by_running_task(self, gov_sqlite):
        sid = "guard-run"
        sess_store.create_session(gov_sqlite, _sess(sid))
        gov_store.insert_task(
            gov_sqlite,
            _task(sid, status="running", created_at="2026-10-03T00:00:01+00:00"),
        )
        rc = sess_store.archive_if_no_running(
            gov_sqlite, sid, "2026-10-03T00:00:02+00:00"
        )
        assert rc == 0
        got = sess_store.get_session(gov_sqlite, sid)
        assert got is not None and not got.is_archived  # 状态未被篡改

    def test_guarded_archive_allowed_when_terminal_tasks_only(self, gov_sqlite):
        """terminal Task 不阻塞归档（允许 ARCHIVED + 历史 task 并存）。"""
        sid = "guard-done"
        sess_store.create_session(gov_sqlite, _sess(sid))
        gov_store.insert_task(
            gov_sqlite,
            _task(sid, status="completed", created_at="2026-10-03T00:00:01+00:00"),
        )
        rc = sess_store.archive_if_no_running(
            gov_sqlite, sid, "2026-10-03T00:00:02+00:00"
        )
        assert rc == 1
        assert sess_store.get_session(gov_sqlite, sid).is_archived

    def test_guarded_archive_blocked_when_already_archived(self, gov_sqlite):
        sid = "guard-arch"
        sess_store.create_session(
            gov_sqlite, _sess(sid, status=SessionStatus.ARCHIVED.value)
        )
        rc = sess_store.archive_if_no_running(
            gov_sqlite, sid, "2026-10-03T00:00:01+00:00"
        )
        assert rc == 0
