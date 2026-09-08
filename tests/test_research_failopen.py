"""F1 fail-open 测试：research 面任何失败都不改变 Agent 执行语义/工具返回值。"""

import logging

from app.research import provenance, registry, store as rstore
from app.research.config import RESEARCH_STORE_ENV


class TestDisabledStore:
    def test_disabled_all_apis_noop(self, research_tmp, monkeypatch):
        monkeypatch.setenv(RESEARCH_STORE_ENV, "disabled")
        rstore.reset_store()
        assert registry.create_run_and_root("t", "q") is None
        assert (
            registry.record_search_query("r", "s", agent="a", tool="t", query="q")
            is None
        )
        assert (
            registry.upsert_source(
                "r",
                "q",
                source_type="web",
                agent="a",
                title="t",
                locator="l",
                canonical_key="k",
            )
            is None
        )
        assert (
            registry.append_evidence(
                "r", "s", "s", content="c", locator="l", extraction_method="m"
            )
            is None
        )
        registry.set_run_status("r", "finished")  # 不抛
        assert provenance.get_run("r") is None
        assert provenance.list_sources("r") == []
        assert provenance.get_evidence_chain("x") is None

    def test_store_outage_noop(self, research_sqlite, monkeypatch, caplog):
        """运行期 store 故障（磁盘/PG down）→ registry 返回 None、仅 warning，不扩散。"""

        class _BrokenStore:
            dialect = "sqlite"

            def transaction(self):
                raise RuntimeError("simulated store outage")

            def execute(self, sql, params=()):
                raise RuntimeError("simulated store outage")

        monkeypatch.setattr(rstore, "_instance", _BrokenStore())
        with caplog.at_level(logging.WARNING, logger="deepsearch.research.registry"):
            assert registry.create_run_and_root("t", "q") is None
            assert (
                registry.record_search_query("r", "s", agent="a", tool="t", query="q")
                is None
            )
        assert any("fail-open" in r.message for r in caplog.records)


class TestArtifactsGuard:
    def test_guard_swallows_and_continues(self):
        """guard 块内异常被吞；guard 后代码继续执行（等价于工具返回值不变）。"""

        def fake_tool():
            sentinel = {"answer": "原始工具结果"}
            with registry.artifacts_guard("fake register"):
                raise ValueError("registry boom")
            return sentinel

        assert fake_tool() == {"answer": "原始工具结果"}

    def test_missing_run_guard_in_tool_like_flow(self, research_sqlite):
        """run 不存在时工具侧经 guard 不炸、返回原值（fail-open）。"""

        def fake_tool(run_id, sqid):
            with registry.artifacts_guard("tool register"):
                registry.record_search_query(
                    run_id, sqid, agent="a", tool="t", query="q"
                )
            return "tool-answer"

        assert fake_tool("missing-run", "missing-sq") == "tool-answer"

    def test_guard_partial_success_then_independent_call(self, research_sqlite):
        """注册块内中途失败只影响该块；块外后续注册不受影响。"""
        run_id, sqid = registry.create_run_and_root("t", "q")

        def block_then_continue():
            with registry.artifacts_guard("multi"):
                first = registry.record_search_query(
                    run_id, sqid, agent="a", tool="t", query="q1"
                )
                raise RuntimeError("mid-block failure")
            # guard 外继续执行
            second = registry.record_search_query(
                run_id, sqid, agent="a", tool="t", query="q2"
            )
            return first, second

        first, second = block_then_continue()
        assert first is not None  # 第一个注册成功（块内异常发生在它之后）
        assert second is not None  # guard 外独立调用不受影响
        rows = rstore.get_store().execute(
            "SELECT query_id FROM search_queries WHERE run_id = %s ORDER BY fetched_at",
            (run_id,),
        )
        assert len(rows) == 2
