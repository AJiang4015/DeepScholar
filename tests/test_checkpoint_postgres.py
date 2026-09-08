"""PostgreSQL checkpointer 集成测试（独立测试库，skipif 门控）。

覆盖 docs/spec/2026-09-03-postgres-checkpoint-migration.md §7.5：
- managed_postgres_checkpointer 上下文（自建 pool）下的 replay 数据面；
- 并发多 run（同 pool）互不干扰；
- 失败 DSN → fail-fast RuntimeError；
- 工厂单例（bind 注入路径）在无 FastAPI 时不走（由 managed 上下文覆盖）。

环境要求（否则整类 skip）：
- psycopg / psycopg-pool / langgraph-checkpoint-postgres 可导入；
- AGENT_CHECKPOINT_DSN_TEST 指向**独立测试库**（禁止开发/生产库）。
"""

import asyncio
import os
import uuid

import pytest

from tests._checkpoint_replay_harness import run_replay_scenario

PG_TEST_DSN_ENV = "AGENT_CHECKPOINT_DSN_TEST"

try:
    import psycopg  # noqa: F401
    import psycopg_pool  # noqa: F401
    import langgraph.checkpoint.postgres  # noqa: F401

    HAS_PG_DEPS = True
except Exception:  # pragma: no cover
    HAS_PG_DEPS = False

needs_pg = pytest.mark.skipif(
    not (HAS_PG_DEPS and os.getenv(PG_TEST_DSN_ENV)),
    reason=f"需要 postgres 依赖与 {PG_TEST_DSN_ENV}（独立测试库）",
)

from app.runtime import checkpoint as cp  # noqa: E402


@needs_pg
class TestPostgresIntegration:
    @pytest.fixture(autouse=True)
    def _reset_injected_pool(self):
        """每用例后清理模块级注入状态，避免跨测试污染。"""
        yield

        class _FakePool:
            async def close(self):
                pass

        # 以 shutdown 语义清理（注入池为 None/假对象均可幂等关闭）
        async def _cleanup():
            await cp.shutdown_checkpoint_lifespan()
            if cp._injected_pool is not None:  # noqa: SLF001 - 测试兜底清理
                pool = cp._injected_pool
                cp._injected_pool = None
                await pool.close()

        asyncio.run(_cleanup())

    def test_managed_context_replay_scenario(self):
        dsn = os.getenv(PG_TEST_DSN_ENV)
        thread_id = f"pg-a-{uuid.uuid4().hex}"

        async def scenario():
            async with cp.managed_postgres_checkpointer(dsn) as saver:
                try:
                    return await run_replay_scenario(saver, thread_id=thread_id)
                finally:
                    await saver.adelete_thread(thread_id)  # 持久库线程清理

        obs = asyncio.run(scenario())
        assert obs["history_count"] == 3
        assert obs["chain_ok"] is True
        assert obs["order_ok"] is True
        assert obs["resume_counter"] == 1
        assert obs["hist_fetch_ok"] is True

    def test_concurrent_runs_isolated(self):
        dsn = os.getenv(PG_TEST_DSN_ENV)
        stamp = uuid.uuid4().hex[:8]

        async def one_run(i: int):
            thread_id = f"pg-conc-{stamp}-{i}"
            async with cp.managed_postgres_checkpointer(dsn) as saver:
                try:
                    obs = await run_replay_scenario(saver, thread_id=thread_id)
                    return i, obs
                finally:
                    await saver.adelete_thread(thread_id)  # 持久库线程清理

        async def scenario():
            results = await asyncio.gather(*(one_run(i) for i in range(5)))
            return results

        results = asyncio.run(scenario())
        assert len(results) == 5
        for i, obs in results:
            assert obs["resume_counter"] == 1
            assert obs["history_count"] == 3
            assert obs["chain_ok"] is True

    def test_fail_fast_on_unreachable_dsn(self):
        # 指向必然不可达的端口：pool.open/setup 失败 → fail-fast RuntimeError
        bad_dsn = "postgresql://u:p@127.0.0.1:1/nonexistent?connect_timeout=2"

        async def scenario():
            with pytest.raises(
                RuntimeError, match="无法初始化 PostgreSQL checkpointer"
            ):
                async with cp.managed_postgres_checkpointer(bad_dsn) as _saver:
                    pass  # 上下文进入即应抛出

        asyncio.run(scenario())

    def test_bind_injected_pool_double_bind_rejected(self):
        class _FakePool:
            def __init__(self, name):
                self.name = name

            async def close(self):
                pass

        async def scenario():
            await cp.bind_postgres_pool(_FakePool("pool-1"))
            with pytest.raises(RuntimeError, match="禁止重复 bind"):
                await cp.bind_postgres_pool(_FakePool("pool-2"))

        asyncio.run(scenario())
