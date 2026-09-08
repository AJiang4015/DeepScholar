"""F8 Step 3 — Phase A：BudgetCounter（Spec §5/§6，SQLite 层纯逻辑快速测试）。

覆盖：默认 limits（M-Spec §7.4）；先比较后放行（120 不发生 121）；limit=0 全拒；
search ⊆ tool（both-or-nothing，禁止直接 acquire search）；agent_steps ≠ llm_calls；
并发无 overshoot；快照；GovernanceLimitExceeded 是 GraphBubbleUp 子类（Probe B 语义）。

SQLite 层仅做逻辑测试；Phase F 的 PostgreSQL Gate 覆盖 lifecycle/race/persistence。
"""

import threading

import pytest

from langgraph.errors import GraphBubbleUp

from app.runtime.governance.counters import (
    DEFAULT_LIMITS,
    BudgetCounter,
    BudgetCounterError,
    GovernanceLimitExceeded,
)

#: 期望与 M-Spec §7.4 一致（Spec §2 引用，禁止悄悄改）
EXPECTED_DEFAULTS = {
    "llm_calls": 120,
    "tool_calls": 300,
    "search_calls": 40,
    "agent_steps": 200,
}


class TestDefaultsAndConfig:
    def test_default_limits_match_mspec_74(self):
        assert BudgetCounter().limits == EXPECTED_DEFAULTS
        assert DEFAULT_LIMITS == EXPECTED_DEFAULTS

    def test_custom_limits_and_unknown_kind_rejected(self):
        c = BudgetCounter(
            limits={
                "llm_calls": 5,
                "tool_calls": 1,
                "search_calls": 1,
                "agent_steps": 1,
            }
        )
        assert c.limit("llm_calls") == 5
        with pytest.raises(BudgetCounterError):
            BudgetCounter(limits={"llm_calls": 1, "nonsense": 1})
        with pytest.raises(BudgetCounterError):
            BudgetCounter(limits={"llm_calls": -1})

    def test_governance_limit_exceeded_extends_graph_bubble_up(self):
        # Probe B 锁定的穿透语义：必须能穿过 ToolNode 的 except GraphBubbleUp
        assert issubclass(GovernanceLimitExceeded, GraphBubbleUp)
        exc = GovernanceLimitExceeded("llm_calls", 120, 120)
        assert exc.kind == "llm_calls"
        assert exc.limit == 120 and exc.current == 120
        assert "llm_calls 120 >= 120" in str(exc)


class TestCompareBeforeExecute:
    def test_llm_120_no_121(self):
        """max_llm_calls=120：恰 120 次放行，第 121 次不得发生。"""
        c = BudgetCounter(
            limits={
                "llm_calls": 120,
                "tool_calls": 300,
                "search_calls": 40,
                "agent_steps": 200,
            }
        )
        accepted = sum(1 for _ in range(200) if c.acquire("llm_calls"))
        assert accepted == 120
        assert c.count("llm_calls") == 120
        assert c.acquire("llm_calls") is False
        assert c.count("llm_calls") == 120  # 无 overshoot

    def test_zero_limit_rejects_first_call(self):
        c = BudgetCounter(
            limits={
                "llm_calls": 0,
                "tool_calls": 0,
                "search_calls": 0,
                "agent_steps": 0,
            }
        )
        assert c.acquire("llm_calls") is False
        assert c.acquire("agent_steps") is False
        assert c.acquire_search_call() is False
        assert c.snapshot() == {
            "llm_calls": 0,
            "tool_calls": 0,
            "search_calls": 0,
            "agent_steps": 0,
        }

    def test_tool_calls_respect_limit(self):
        c = BudgetCounter(
            limits={
                "llm_calls": 1,
                "tool_calls": 3,
                "search_calls": 1,
                "agent_steps": 1,
            }
        )
        accepted = sum(1 for _ in range(10) if c.acquire("tool_calls"))
        assert accepted == 3
        assert c.acquire("tool_calls") is False

    def test_agent_steps_independent_of_llm_calls(self):
        """agent_steps ≠ llm_calls：不同 kind 不同计数与限额（Spec §2/§3）。"""
        c = BudgetCounter(
            limits={
                "llm_calls": 2,
                "tool_calls": 2,
                "search_calls": 1,
                "agent_steps": 1,
            }
        )
        assert c.acquire("llm_calls") is True
        assert c.acquire("llm_calls") is True
        assert c.acquire("llm_calls") is False  # llm 到 2
        assert c.acquire("agent_steps") is True  # step 仍可单独放行 1
        assert c.acquire("agent_steps") is False
        assert c.count("llm_calls") == 2
        assert c.count("agent_steps") == 1
        assert c.count("llm_calls") != c.count("agent_steps")


class TestSearchSubsetOfTool:
    def test_search_acquire_consumes_both_slots(self):
        c = BudgetCounter(
            limits={
                "llm_calls": 10,
                "tool_calls": 2,
                "search_calls": 1,
                "agent_steps": 10,
            }
        )
        assert c.acquire_search_call() is True
        assert c.count("tool_calls") == 1
        assert c.count("search_calls") == 1
        assert (
            c.acquire_search_call() is False
        )  # search 达限（both-or-nothing：不部分递增）
        assert c.count("tool_calls") == 1 and c.count("search_calls") == 1
        # tool 槽位被 search 占用后，普通 tool 仍可用剩余槽位
        assert c.acquire("tool_calls") is True
        assert c.acquire("tool_calls") is False
        # 不变量：search <= tool
        assert c.count("search_calls") <= c.count("tool_calls")

    def test_search_limited_by_tool_budget_first(self):
        c = BudgetCounter(
            limits={
                "llm_calls": 10,
                "tool_calls": 0,
                "search_calls": 40,
                "agent_steps": 10,
            }
        )
        assert c.acquire_search_call() is False  # tool 达限先行拒绝
        assert c.count("tool_calls") == 0 and c.count("search_calls") == 0

    def test_direct_search_acquire_rejected(self):
        c = BudgetCounter()
        with pytest.raises(BudgetCounterError):
            c.acquire("search_calls")

    def test_invariant_search_never_exceeds_tool(self):
        c = BudgetCounter(
            limits={
                "llm_calls": 100,
                "tool_calls": 50,
                "search_calls": 20,
                "agent_steps": 100,
            }
        )
        for i in range(60):
            if i % 3 == 0:
                c.acquire_search_call()
            else:
                c.acquire("tool_calls")
        assert c.count("search_calls") <= c.count("tool_calls")


class TestConcurrencyNoOvershoot:
    def test_concurrent_llm_acquires_exactly_at_limit(self):
        """并发回调不得 overshoot：N 线程抢 limit → 恰 limit 次 True。"""
        limit = 120
        c = BudgetCounter(
            limits={
                "llm_calls": limit,
                "tool_calls": 1,
                "search_calls": 1,
                "agent_steps": 1,
            }
        )
        results = []
        threads = []
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            ok = 0
            for _ in range(60):
                if c.acquire("llm_calls"):
                    ok += 1
            results.append(ok)

        for _ in range(8):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        assert sum(results) == limit
        assert c.count("llm_calls") == limit
        assert c.acquire("llm_calls") is False

    def test_snapshot_stable_and_to_dict(self):
        c = BudgetCounter(
            limits={
                "llm_calls": 2,
                "tool_calls": 2,
                "search_calls": 1,
                "agent_steps": 2,
            }
        )
        c.acquire("llm_calls")
        c.acquire_search_call()
        snap = c.snapshot()
        assert snap == {
            "llm_calls": 1,
            "tool_calls": 1,
            "search_calls": 1,
            "agent_steps": 0,
        }
        d = c.to_dict()
        assert d["counts"] == snap
        assert d["limits"]["llm_calls"] == 2
        # snapshot 是拷贝，外部改不影响内部
        snap["llm_calls"] = 999
        assert c.count("llm_calls") == 1
