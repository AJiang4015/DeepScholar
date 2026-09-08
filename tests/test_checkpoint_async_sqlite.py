"""官方 AsyncSqliteSaver（sqlite 后端）行为测试。

覆盖：astream 兼容 / interrupt + resume / history + parent chain /
按 checkpoint_id 取历史 / 同 loop sync 调用陷阱 / 跨 loop 重建 / 多路径隔离。
全部在 _testtmp 下用临时 DB，不依赖外部服务。
"""

import asyncio
import shutil
import uuid
from pathlib import Path

import pytest

pytest.importorskip(
    "langgraph.checkpoint.sqlite", reason="需要 langgraph-checkpoint-sqlite"
)
pytest.importorskip("aiosqlite", reason="需要 aiosqlite")

from app.runtime import checkpoint as cp  # noqa: E402
from tests._checkpoint_replay_harness import run_replay_scenario  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST_TMP = _REPO_ROOT / "_testtmp"


def _point_env(monkeypatch, db_path: Path) -> None:
    monkeypatch.delenv(cp.CHECKPOINT_BACKEND_ENV, raising=False)
    monkeypatch.setenv(cp.CHECKPOINT_DB_ENV, str(db_path))
    monkeypatch.delenv(cp.CHECKPOINT_DSN_ENV, raising=False)


@pytest.fixture
def sqlite_tmp():
    d = _TEST_TMP / f"asql-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    try:
        shutil.rmtree(d)
    except OSError:
        pass


@pytest.fixture(autouse=True)
def _close_holder_after_each_test():
    """每个用例后关闭模块级 holder（aiosqlite 工作线程随连接关闭，避免进程退出阻塞）。"""
    yield
    asyncio.run(cp.close_checkpointer())


class TestAsyncSqliteReplayDataPlane:
    def test_full_replay_scenario(self, monkeypatch, sqlite_tmp):
        db = sqlite_tmp / "cp.sqlite"
        _point_env(monkeypatch, db)

        async def scenario():
            saver = await cp.get_checkpointer()
            return await run_replay_scenario(saver, thread_id="t1")

        obs = asyncio.run(scenario())
        assert obs["snap_next"] == ["step2"]
        assert obs["snap_counter"] == 1
        assert obs["history_count"] == 3, "interrupt 场景应产生 3 层历史"
        assert obs["order_ok"] is True
        assert obs["chain_ok"] is True
        assert obs["steps"] == [1, 0, -1]
        assert obs["resume_counter"] == 1, "resume 不应重跑 step1"
        assert obs["resume_marker_b"] == "resumed-by-latest"
        assert obs["hist_fetch_ok"] is True

    def test_sync_call_inside_loop_raises_invalid_state(self, monkeypatch, sqlite_tmp):
        db = sqlite_tmp / "cp.sqlite"
        _point_env(monkeypatch, db)

        async def scenario():
            saver = await cp.get_checkpointer()
            # 官方 AsyncSaver 的 sync 方法同 loop 调用 → InvalidStateError
            with pytest.raises(asyncio.InvalidStateError):
                saver.get_tuple({"configurable": {"thread_id": "x"}})

        asyncio.run(scenario())

    def test_cross_loop_rebuilds_saver(self, monkeypatch, sqlite_tmp):
        """跨 loop（多次 asyncio.run）访问同一路径 → 自动重建而非复用旧 loop 实例。"""
        db = sqlite_tmp / "cp.sqlite"
        _point_env(monkeypatch, db)

        async def get():
            return await cp.get_checkpointer()

        first = asyncio.run(get())
        second = asyncio.run(get())
        assert first is not second, "跨 loop 必须重建（实例绑定旧 loop 不可复用）"

    def test_path_switch_closes_old_holder(self, monkeypatch, sqlite_tmp):
        db1 = sqlite_tmp / "a.sqlite"
        db2 = sqlite_tmp / "b.sqlite"

        async def scenario():
            _point_env(monkeypatch, db1)
            s1 = await cp.get_checkpointer()
            _point_env(monkeypatch, db2)
            s2 = await cp.get_checkpointer()
            return s1, s2

        s1, s2 = asyncio.run(scenario())
        assert s1 is not s2
        assert db1.exists() and db2.exists()
