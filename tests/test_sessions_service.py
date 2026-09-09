"""Multi-Session — session service 测试（CRUD / 状态机 / 聚合 / 归属校验）。

覆盖（Decision Closure #3/#4/#7/#8/#9）：
- create（缺省 uuid / 提供 thread_id 注册 / 重复注册拒绝 / 字符集校验 / 字段校验）；
- list / detail（archived 过滤、聚合字段、tasks、query enrich 键存在）；
- 状态机：ACTIVE ⇄ ARCHIVED、幂等、running-task 归档守卫、archived 禁建任务；
- 隔离前置：session not found / task not found / task 不属于 session → 对应域错误。
"""

import asyncio
import re
import shutil
import uuid
from pathlib import Path

import pytest

from app.research import store as rstore
from app.runtime.governance import store as gov_store
from app.runtime.governance.controller import GovernanceController
from app.runtime.governance.models import TaskRecord
from app.session import service as session_service
from app.session.models import SessionStatus

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST_TMP = _REPO_ROOT / "_testtmp"

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


@pytest.fixture
def gov_tmp():
    d = _TEST_TMP / f"ms-svc-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    try:
        shutil.rmtree(d)
    except OSError:
        pass


@pytest.fixture
def controller(gov_tmp, monkeypatch):
    """fresh governance sqlite store + controller（无污染进程级单例）。"""
    from app.runtime.governance import migrations as gov_migrations

    store = gov_store._GovernanceSqliteStore(str(gov_tmp / "gov.sqlite"))
    gov_migrations.ensure_schema(store)
    yield GovernanceController(store)
    store.close()


@pytest.fixture(autouse=True)
def research_off(monkeypatch):
    """enrich 测试隔离：RESEARCH_STORE=disabled → research get_store None（fail-open 路径）。"""
    monkeypatch.setenv("RESEARCH_STORE", "disabled")
    monkeypatch.delenv("RESEARCH_DB", raising=False)
    monkeypatch.delenv("RESEARCH_DSN", raising=False)
    rstore.reset_store()
    yield
    rstore.reset_store()


def _task(thread_id, *, status="running", created_at, run_id=None):
    return TaskRecord(
        task_id=uuid.uuid4().hex,
        thread_id=thread_id,
        owner_instance="test",
        status=status,
        run_id=run_id,
        created_at=created_at,
    )


class TestCreateSession:
    def test_create_default_generates_safe_id(self, controller):
        sess = session_service.create_session(controller, title="机器人调研")
        assert SAFE_ID_RE.match(sess.session_id)
        assert sess.status == SessionStatus.ACTIVE.value
        assert sess.title == "机器人调研"
        assert sess.description is None
        assert sess.created_at == sess.updated_at
        assert not sess.is_archived

    def test_title_default_and_trim(self, controller):
        sess = session_service.create_session(controller)
        assert sess.title == session_service.DEFAULT_SESSION_TITLE
        sess2 = session_service.create_session(controller, title="   ")
        assert sess2.title == session_service.DEFAULT_SESSION_TITLE

    def test_provided_thread_registers_legacy(self, controller):
        thread = "legacy-manual-123-uuid"
        sess = session_service.create_session(controller, thread_id=thread)
        assert sess.session_id == thread

    def test_duplicate_registration_rejected(self, controller):
        session_service.create_session(controller, thread_id="same-thread")
        with pytest.raises(session_service.SessionStateError):
            session_service.create_session(controller, thread_id="same-thread")

    def test_invalid_thread_charset_rejected(self, controller):
        for bad in ("../etc", "a b", "a/b", "a\\b"):
            with pytest.raises(session_service.SessionValidationError):
                session_service.create_session(controller, thread_id=bad)

    def test_title_too_long_rejected(self, controller):
        with pytest.raises(session_service.SessionValidationError):
            session_service.create_session(
                controller, title="x" * (session_service.MAX_TITLE_LENGTH + 1)
            )

    def test_description_too_long_rejected(self, controller):
        with pytest.raises(session_service.SessionValidationError):
            session_service.create_session(
                controller,
                description="x" * (session_service.MAX_DESCRIPTION_LENGTH + 1),
            )


class TestListAndDetail:
    def _two_sessions(self, controller, archive_b: bool = False):
        a = session_service.create_session(controller, title="A", thread_id="thread-a")
        b = session_service.create_session(controller, title="B", thread_id="thread-b")
        if archive_b:
            session_service.archive_session(controller, b.session_id)
        return a, b

    def test_list_default_excludes_archived(self, controller):
        a, b = self._two_sessions(controller, archive_b=True)
        res = session_service.list_sessions(controller)
        assert res["total"] == 1
        assert [s["session_id"] for s in res["sessions"]] == [a.session_id]

    def test_list_include_archived(self, controller):
        a, b = self._two_sessions(controller, archive_b=True)
        res = session_service.list_sessions(controller, include_archived=True)
        assert res["total"] == 2
        assert {s["session_id"] for s in res["sessions"]} == {
            a.session_id,
            b.session_id,
        }

    def test_list_item_carries_aggregate_keys(self, controller):
        a, _b = self._two_sessions(controller)
        item = session_service.list_sessions(controller)["sessions"][0]
        for key in (
            "session_id",
            "title",
            "status",
            "created_at",
            "updated_at",
            "task_count",
            "running_tasks",
            "latest_task",
        ):
            assert key in item
        assert item["task_count"] == 0 and item["running_tasks"] == 0

    def test_list_limit_validation(self, controller):
        with pytest.raises(session_service.SessionValidationError):
            session_service.list_sessions(
                controller, limit=session_service.MAX_LIST_LIMIT + 1
            )
        with pytest.raises(session_service.SessionValidationError):
            session_service.list_sessions(controller, offset=-1)

    def test_detail_unknown_session(self, controller):
        with pytest.raises(session_service.SessionNotFoundError):
            session_service.get_session_detail(controller, "missing")

    def test_detail_metadata_tasks_and_latest(self, controller):
        sess = session_service.create_session(
            controller, title="detail", thread_id="thread-det"
        )
        gov_store.insert_task(
            controller._store,  # noqa: SLF001
            _task(
                "thread-det",
                status="running",
                created_at="2026-10-03T00:00:02+00:00",
                run_id="run-2",
            ),
        )
        gov_store.insert_task(
            controller._store,  # noqa: SLF001
            _task(
                "thread-det",
                status="completed",
                created_at="2026-10-03T00:00:01+00:00",
                run_id="run-1",
            ),
        )
        detail = session_service.get_session_detail(controller, sess.session_id)
        assert detail["session"]["session_id"] == sess.session_id
        assert detail["task_count"] == 2
        assert detail["running_tasks"] == 1
        assert detail["latest_task"]["run_id"] == "run-2"
        assert [t["run_id"] for t in detail["tasks"]] == ["run-2", "run-1"]
        for t in detail[
            "tasks"
        ]:  # query enrich 键存在（research disabled → None，fail-open）
            assert "query" in t
        assert detail["tasks"][0]["query"] is None

    def test_detail_readable_when_archived(self, controller):
        sess = session_service.create_session(
            controller, title="hist", thread_id="thread-hist"
        )
        gov_store.insert_task(
            controller._store,  # noqa: SLF001
            _task(
                "thread-hist",
                status="completed",
                created_at="2026-10-03T00:00:01+00:00",
                run_id="run-hist",
            ),
        )
        session_service.archive_session(controller, sess.session_id)
        detail = session_service.get_session_detail(controller, sess.session_id)
        assert detail["session"]["status"] == SessionStatus.ARCHIVED.value
        assert detail["task_count"] == 1  # 历史仍可追溯


class TestStateMachine:
    def test_archive_and_idempotent(self, controller):
        sess = session_service.create_session(controller, thread_id="t-arc")
        res = session_service.archive_session(controller, sess.session_id)
        assert res["status"] == SessionStatus.ARCHIVED.value
        assert res["already_archived"] is False
        res2 = session_service.archive_session(controller, sess.session_id)
        assert res2["already_archived"] is True

    def test_unarchive_and_idempotent(self, controller):
        sess = session_service.create_session(controller, thread_id="t-res")
        session_service.archive_session(controller, sess.session_id)
        res = session_service.unarchive_session(controller, sess.session_id)
        assert res["status"] == SessionStatus.ACTIVE.value
        assert res["already_active"] is False
        res2 = session_service.unarchive_session(controller, sess.session_id)
        assert res2["already_active"] is True

    def test_archive_unknown_session(self, controller):
        with pytest.raises(session_service.SessionNotFoundError):
            session_service.archive_session(controller, "missing")

    def test_archive_with_running_task_rejected(self, controller):
        sess = session_service.create_session(controller, thread_id="t-run")
        gov_store.insert_task(
            controller._store,  # noqa: SLF001
            _task("t-run", status="running", created_at="2026-10-03T00:00:01+00:00"),
        )
        with pytest.raises(session_service.SessionStateError):
            session_service.archive_session(controller, sess.session_id)
        # 取消后（terminal）允许归档
        running = gov_store.list_tasks(
            controller._store,  # noqa: SLF001
            thread_id="t-run",
        )[0]

        async def _cancel():
            await controller.cancel_governed(running.task_id)

        asyncio.run(_cancel())
        res = session_service.archive_session(controller, sess.session_id)
        assert res["already_archived"] is False

    def test_assert_active_rejects_archived(self, controller):
        sess = session_service.create_session(controller, thread_id="t-arch")
        session_service.archive_session(controller, sess.session_id)
        with pytest.raises(session_service.SessionStateError):
            session_service.assert_session_active(controller, sess.session_id)
        session_service.unarchive_session(controller, sess.session_id)
        session_service.assert_session_active(controller, sess.session_id)  # 不抛


class TestTaskMembershipIsolation:
    def _seed(self, controller):
        a = session_service.create_session(controller, thread_id="iso-a")
        b = session_service.create_session(controller, thread_id="iso-b")
        gov_store.insert_task(
            controller._store,  # noqa: SLF001
            _task(
                "iso-a",
                status="running",
                created_at="2026-10-03T00:00:01+00:00",
                run_id="ra",
            ),
        )
        gov_store.insert_task(
            controller._store,  # noqa: SLF001
            _task(
                "iso-b",
                status="running",
                created_at="2026-10-03T00:00:01+00:00",
                run_id="rb",
            ),
        )
        ta = gov_store.list_tasks(controller._store, thread_id="iso-a")[0]  # noqa: SLF001
        tb = gov_store.list_tasks(controller._store, thread_id="iso-b")[0]  # noqa: SLF001
        return a, b, ta, tb

    def test_list_tasks_only_own_session(self, controller):
        a, b, ta, tb = self._seed(controller)
        a_tasks = session_service.list_session_tasks(controller, a.session_id)
        b_tasks = session_service.list_session_tasks(controller, b.session_id)
        assert {t["task_id"] for t in a_tasks} == {ta.task_id}
        assert {t["task_id"] for t in b_tasks} == {tb.task_id}
        assert all(t["thread_id"] == "iso-a" for t in a_tasks)

    def test_validate_ok_for_own_task(self, controller):
        a, _b, ta, _tb = self._seed(controller)
        rec = session_service.validate_task_in_session(
            controller, a.session_id, ta.task_id
        )
        assert rec.task_id == ta.task_id

    def test_validate_cross_session_rejected(self, controller):
        a, _b, _ta, tb = self._seed(controller)
        with pytest.raises(session_service.TaskNotInSessionError):
            session_service.validate_task_in_session(
                controller, a.session_id, tb.task_id
            )

    def test_validate_unknown_task(self, controller):
        a, _b, _ta, _tb = self._seed(controller)
        with pytest.raises(session_service.TaskNotFoundError):
            session_service.validate_task_in_session(
                controller, a.session_id, "no-such-task"
            )

    def test_validate_unknown_session(self, controller):
        _a, _b, ta, _tb = self._seed(controller)
        with pytest.raises(session_service.SessionNotFoundError):
            session_service.validate_task_in_session(controller, "missing", ta.task_id)
