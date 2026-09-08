"""Replay 验证协议测试（双后端参数化）。

覆盖 docs/spec/2026-09-03-postgres-checkpoint-migration.md §7.4 的自动测试部分：
Run → 多 checkpoint → get_state_history → 指定 checkpoint_id → resume，
断言 history/parent chain 数据面；PG 变体仅在 AGENT_CHECKPOINT_DSN_TEST 存在时执行。

能力分层（防误判口径，勿写成"PG 获得 replay 产品能力"）：
- checkpoint persistence/history / parent chain / 任意 checkpoint_id 可取 = saver 数据面；
- resume（Command(resume=...) 从最新中断点续跑）= LangGraph graph runtime 能力；
- 本文件验证两者在数据面上成立；产品级 resume 入口（API/UI）属后续阶段。
"""

import asyncio
import os
import shutil
import uuid
from pathlib import Path

import pytest

pytest.importorskip("langgraph.checkpoint.sqlite", reason="需要 langgraph-checkpoint")
pytest.importorskip("aiosqlite", reason="需要 aiosqlite")

from app.runtime import checkpoint as cp  # noqa: E402
from tests._checkpoint_replay_harness import run_replay_scenario  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST_TMP = _REPO_ROOT / "_testtmp"
PG_TEST_DSN_ENV = "AGENT_CHECKPOINT_DSN_TEST"

try:  # PG 可选驱动
    import psycopg  # noqa: F401

    HAS_PSYCOPG = True
except Exception:  # pragma: no cover
    HAS_PSYCOPG = False

HAS_PG_TEST_DSN = bool(os.getenv(PG_TEST_DSN_ENV))
needs_pg = pytest.mark.skipif(
    not (HAS_PSYCOPG and HAS_PG_TEST_DSN),
    reason=f"需要 psycopg 与 {PG_TEST_DSN_ENV}（独立测试库）",
)


def _assert_replay_observations(obs: dict) -> None:
    """双后端共享断言（数据面 + latest resume）。"""
    assert obs["snap_next"] == ["step2"]
    assert obs["snap_counter"] == 1
    assert obs["history_count"] == 3, "interrupt 场景应产生 3 层历史"
    assert obs["order_ok"] is True, "get_state_history 应为 newest-first"
    assert obs["chain_ok"] is True, "parent_checkpoint_id 链必须闭合"
    assert obs["steps"] == [1, 0, -1]
    assert obs["resume_counter"] == 1, "latest resume 不应重跑 step1"
    assert obs["resume_marker_b"] == "resumed-by-latest"
    assert obs["hist_fetch_ok"] is True, "按历史 checkpoint_id 应能直取"


class TestReplaySqlite:
    def test_replay_verify_sqlite(self, monkeypatch):
        d = _TEST_TMP / f"rv-sql-{uuid.uuid4().hex}"
        d.mkdir(parents=True, exist_ok=True)
        db = d / "cp.sqlite"
        monkeypatch.delenv(cp.CHECKPOINT_BACKEND_ENV, raising=False)
        monkeypatch.setenv(cp.CHECKPOINT_DB_ENV, str(db))
        monkeypatch.delenv(cp.CHECKPOINT_DSN_ENV, raising=False)

        async def scenario():
            saver = await cp.get_checkpointer()
            try:
                return await run_replay_scenario(saver, thread_id="rv-sqlite")
            finally:
                await cp.close_checkpointer()

        obs = asyncio.run(scenario())
        _assert_replay_observations(obs)
        assert db.exists()
        try:
            shutil.rmtree(d)
        except OSError:
            pass


class TestReplayPostgres:
    @needs_pg
    def test_replay_verify_postgres(self):
        dsn = os.getenv(PG_TEST_DSN_ENV)
        suffix = uuid.uuid4().hex
        thread_id = f"rv-pg-{suffix}"

        async def scenario():
            # 每测试用独立 thread + managed 上下文（独立 pool，精确生命周期）
            async with cp.managed_postgres_checkpointer(dsn) as saver:
                try:
                    return await run_replay_scenario(saver, thread_id=thread_id)
                finally:
                    await saver.adelete_thread(thread_id)  # 持久库线程清理

        obs = asyncio.run(scenario())
        _assert_replay_observations(obs)
