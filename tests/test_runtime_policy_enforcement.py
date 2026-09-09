"""P2-1 — Runtime Policy Enforcement（决策 D-Phase2-P2-1-001）。

覆盖：
1. normalize_policy 纯函数：None/{} → 默认表副本；显式键与默认合并；max_* / wall_clock
   类型与边界校验（fail-closed）；未知键透传；默认表与 controller/counters 冻结常量同源。
2. service.submit_task 生产路径必 governed：policy=None ⇒ policy_snapshot/effective_limits
   = 默认表；正常 → completed + durable task_completed；异常 → failed(agent_failure)
   （不再被吞成 completed）；watchdog 经 submit 生效（缩时 → timed_out）；预算 → 
   budget_exceeded + counters_snapshot；非法 policy → PolicyValidationError（任务不创建）。
3. bare 保留回归：controller.execute(policy=None)（dev/测试路径）仍 Step2 语义
   （正常完成、无 durable event）。

纪律：不新增 Agent 能力；不改 controller/agent/research；仅验证 runtime 提交/治理接线。
sqlite 隔离 store（无外部服务依赖）。
"""

import asyncio
import shutil
import uuid
from pathlib import Path

import pytest

from app.runtime.governance import migrations as gov_migrations
from app.runtime.governance import policy as gov_policy
from app.runtime.governance import service as gov_service
from app.runtime.governance import store as gov_store
from app.runtime.governance.controller import (
    DEFAULT_WALL_CLOCK_SECONDS,
    GovernanceController,
)
from app.runtime.governance.counters import DEFAULT_LIMITS, GovernanceLimitExceeded

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST_TMP = _REPO_ROOT / "_testtmp"

_EXPECTED_DEFAULTS = {
    "wall_clock_timeout": 600,
    "max_agent_steps": 200,
    "max_llm_calls": 120,
    "max_tool_calls": 300,
    "max_search_calls": 40,
}


@pytest.fixture
def gov_tmp():
    d = _TEST_TMP / f"p21-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    try:
        shutil.rmtree(d)
    except OSError:
        pass


def _controller(gov_tmp):
    """sqlite 隔离 governance store + 加速 watchdog/重试的 controller。"""
    db = str(gov_tmp / f"gov-{uuid.uuid4().hex}.sqlite")
    store = gov_store._GovernanceSqliteStore(db)  # noqa: SLF001
    gov_migrations.ensure_schema(store)
    ctl = GovernanceController(
        store, owner_instance="inst-p21", retry_delays=(0.01, 0.02)
    )
    ctl.watchdog_tick = 0.02
    return store, ctl


def _event_types(store, task_id):
    rows = store.execute(
        "SELECT event_type FROM governance_events WHERE task_id=%s ORDER BY seq",
        (task_id,),
    )
    return [r["event_type"] for r in rows]


# ---------------------------------------------------------------------------
# runner 替身（run_deep_agent 模块属性；不触碰真实 agent）
# ---------------------------------------------------------------------------
async def _ok_runner(query, session_id):
    return "ok"


async def _fail_runner(query, session_id):
    raise RuntimeError("agent boom")


async def _hang_runner(query, session_id):
    await asyncio.Event().wait()


async def _budget_runner(query, session_id):
    raise GovernanceLimitExceeded("llm_calls", 0, 0)


# ---------------------------------------------------------------------------
# 1. normalize_policy 纯函数
# ---------------------------------------------------------------------------
class TestNormalizePolicy:
    def test_defaults_are_frozen_mspec(self):
        # M-Spec §7.4 冻结默认值（policy 键名 max_*，同源另测）
        assert gov_policy.DEFAULT_GOVERNED_POLICY == _EXPECTED_DEFAULTS

    def test_same_source_as_controller_and_counters(self):
        # 同源锁定：默认表引用 controller/counters 冻结常量，防双源漂移
        from app.runtime.governance.controller import MAX_LIMIT_TO_COUNTER

        expected = {"wall_clock_timeout": DEFAULT_WALL_CLOCK_SECONDS}
        for max_key, kind in MAX_LIMIT_TO_COUNTER.items():
            expected[max_key] = DEFAULT_LIMITS[kind]
        assert gov_policy.DEFAULT_GOVERNED_POLICY == expected

    def test_none_returns_defaults_copy(self):
        out = gov_policy.normalize_policy(None)
        assert out == gov_policy.DEFAULT_GOVERNED_POLICY
        assert out is not gov_policy.DEFAULT_GOVERNED_POLICY  # 副本
        out["wall_clock_timeout"] = 1
        assert gov_policy.DEFAULT_GOVERNED_POLICY["wall_clock_timeout"] == 600

    def test_empty_dict_returns_defaults(self):
        assert gov_policy.normalize_policy({}) == gov_policy.DEFAULT_GOVERNED_POLICY

    def test_explicit_key_overrides_defaults(self):
        out = gov_policy.normalize_policy({"max_llm_calls": 5})
        assert out["max_llm_calls"] == 5
        assert out["wall_clock_timeout"] == 600
        assert out["max_agent_steps"] == 200

    def test_explicit_wall_clock(self):
        out = gov_policy.normalize_policy({"wall_clock_timeout": 120})
        assert out["wall_clock_timeout"] == 120

    @pytest.mark.parametrize(
        "bad",
        [
            {"max_llm_calls": -1},
            {"max_agent_steps": -5},
            {"max_tool_calls": "3"},
            {"max_search_calls": True},
        ],
    )
    def test_negative_or_wrong_type_max_rejected(self, bad):
        with pytest.raises(gov_policy.PolicyValidationError):
            gov_policy.normalize_policy(bad)

    @pytest.mark.parametrize(
        "bad",
        [
            {"wall_clock_timeout": 0},
            {"wall_clock_timeout": -5},
            {"wall_clock_timeout": None},
            {"wall_clock_timeout": True},
        ],
    )
    def test_wall_clock_zero_or_invalid_rejected(self, bad):
        with pytest.raises(gov_policy.PolicyValidationError):
            gov_policy.normalize_policy(bad)

    def test_non_dict_policy_rejected(self):
        with pytest.raises(gov_policy.PolicyValidationError):
            gov_policy.normalize_policy(123)  # type: ignore[arg-type]

    def test_unknown_key_passthrough(self):
        out = gov_policy.normalize_policy({"custom": 1, "max_llm_calls": 5})
        assert out["custom"] == 1  # 透传不报错
        assert out["max_llm_calls"] == 5


# ---------------------------------------------------------------------------
# 2. service.submit_task 生产路径必 governed
# ---------------------------------------------------------------------------
class TestSubmitTaskGovernedByDefault:
    def test_policy_none_injects_defaults_and_completes(self, gov_tmp, monkeypatch):
        async def scenario():
            store, ctl = _controller(gov_tmp)
            monkeypatch.setattr(gov_service, "run_deep_agent", _ok_runner)
            rec, task = gov_service.submit_task(
                ctl, thread_id="th-ok", query="q", policy=None
            )
            # 默认表注入（P2-1 核心断言）
            assert rec.policy_snapshot == _EXPECTED_DEFAULTS
            assert rec.effective_limits == _EXPECTED_DEFAULTS
            result = await task
            assert result == "ok"
            row = ctl.get_task(rec.task_id)
            assert row.status == "completed"
            # durable terminal event 存在（bare 时代无）→ 恒 governed 证据
            assert _event_types(store, rec.task_id) == [
                "task_started",
                "task_completed",
            ]
            store.close()

        asyncio.run(scenario())

    def test_exception_maps_to_failed_not_completed(self, gov_tmp, monkeypatch):
        async def scenario():
            store, ctl = _controller(gov_tmp)
            monkeypatch.setattr(gov_service, "run_deep_agent", _fail_runner)
            rec, task = gov_service.submit_task(
                ctl, thread_id="th-fail", query="q", policy=None
            )
            with pytest.raises(RuntimeError, match="agent boom"):
                await task
            row = ctl.get_task(rec.task_id)
            assert row.status == "failed"
            assert row.error_kind == "agent_failure"
            assert _event_types(store, rec.task_id) == [
                "task_started",
                "task_failed",
            ]
            store.close()

        asyncio.run(scenario())

    def test_watchdog_active_via_submit_times_out(self, gov_tmp, monkeypatch):
        async def scenario():
            store, ctl = _controller(gov_tmp)
            monkeypatch.setattr(gov_service, "run_deep_agent", _hang_runner)
            rec, task = gov_service.submit_task(
                ctl,
                thread_id="th-hang",
                query="q",
                policy={"wall_clock_timeout": 0.05},  # 缩时验证 watchdog 生效
            )
            with pytest.raises(asyncio.CancelledError):
                # wait_for 兜底：watchdog 异常未触发时 10s 超时显式失败而非悬挂
                await asyncio.wait_for(task, timeout=10)
            row = ctl.get_task(rec.task_id)
            assert row.status == "timed_out"
            assert row.terminal_reason == "timed_out"
            assert row.counters_snapshot is not None
            assert _event_types(store, rec.task_id) == [
                "task_started",
                "task_timed_out",
            ]
            store.close()

        asyncio.run(scenario())

    def test_budget_limit_funnels_budget_exceeded(self, gov_tmp, monkeypatch):
        async def scenario():
            store, ctl = _controller(gov_tmp)
            monkeypatch.setattr(gov_service, "run_deep_agent", _budget_runner)
            rec, task = gov_service.submit_task(
                ctl, thread_id="th-budget", query="q", policy=None
            )
            with pytest.raises(GovernanceLimitExceeded):
                await task
            row = ctl.get_task(rec.task_id)
            assert row.status == "budget_exceeded"
            assert row.counters_snapshot is not None
            assert _event_types(store, rec.task_id) == [
                "task_started",
                "task_budget_exceeded",
            ]
            store.close()

        asyncio.run(scenario())

    def test_explicit_policy_merged_with_defaults(self, gov_tmp, monkeypatch):
        async def scenario():
            store, ctl = _controller(gov_tmp)
            monkeypatch.setattr(gov_service, "run_deep_agent", _ok_runner)
            rec, task = gov_service.submit_task(
                ctl, thread_id="th-merge", query="q", policy={"max_llm_calls": 5}
            )
            expected = dict(_EXPECTED_DEFAULTS)
            expected["max_llm_calls"] = 5
            assert rec.policy_snapshot == expected
            await task
            store.close()

        asyncio.run(scenario())

    def test_invalid_policy_rejected_before_task_creation(self, gov_tmp, monkeypatch):
        async def scenario():
            store, ctl = _controller(gov_tmp)
            monkeypatch.setattr(gov_service, "run_deep_agent", _ok_runner)
            with pytest.raises(gov_policy.PolicyValidationError):
                gov_service.submit_task(
                    ctl,
                    thread_id="th-bad",
                    query="q",
                    policy={"wall_clock_timeout": 0},
                )
            # fail-closed：任务未创建（无 governance_tasks 行 / 无事件）
            rows = store.execute(
                "SELECT COUNT(*) AS n FROM governance_tasks WHERE thread_id=%s",
                ("th-bad",),
            )
            assert rows[0]["n"] == 0
            store.close()

        asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 3. bare 保留回归（controller.execute(policy=None)，dev/测试路径）
# ---------------------------------------------------------------------------
class TestBareSemanticsPreserved:
    def test_bare_execute_still_step2_semantics(self, gov_tmp):
        async def scenario():
            store, ctl = _controller(gov_tmp)
            rec = ctl.create_task("th-bare")
            assert (
                await ctl.execute(rec.task_id, _ok_runner("q", "th-bare"))
                == "ok"
            )
            row = ctl.get_task(rec.task_id)
            assert row.status == "completed"
            # bare 直接 terminalize：无 durable lifecycle event（Step2 语义不变）
            assert _event_types(store, rec.task_id) == []
            store.close()

        asyncio.run(scenario())
