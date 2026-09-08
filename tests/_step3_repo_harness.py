"""F8 Step 3 Phase C — repo-integrated run_deep_agent harness（非 pytest 收集模块）。

在**真实 run_deep_agent**（main_agent.py，含 F7 finalize/monitor/CancelledError 分支）上
驱动 S1 治理接线 probe/测试；LLM 用 ScriptedChatModel（无网络），research/checkpoint/monitor
以 recorder 替身隔离 —— 这些属于 repo-integrated-but-determined 路径；
真实 provider E2E 仍受凭据限制（见报告 Known Limitations）。

不修改任何生产代码；本模块仅测试/取证用。
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import time
import uuid
from pathlib import Path

_HAS_DEAPAGENTS = importlib.util.find_spec("deepagents") is not None
_HAS_RESEARCH_BRIDGE = importlib.util.find_spec("app.research.bridge") is not None

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST_TMP = _REPO_ROOT / "_testtmp"

from langchain_core.language_models.chat_models import BaseChatModel  # noqa: E402

PROVIDER_CALLS = {"n": 0}
TOOL_RAN = {"n": 0}
#: named tools side-effect（slow/search 等额外探测工具）
TOOL_RAN_BY: dict[str, int] = {}


def _make_extra_tools(kind: str):
    """生成额外探测工具：kind='slow' → slow_tool(阻塞)；kind='search' → internet_search。"""
    from langchain_core.tools import tool  # noqa: PLC0415

    if kind == "slow":

        @tool(description="deterministic blocking tool")
        def slow_tool(q: str) -> str:
            TOOL_RAN_BY["slow_tool"] = TOOL_RAN_BY.get("slow_tool", 0) + 1
            import time  # noqa: PLC0415

            time.sleep(0.5)
            return f"SLOW:{q}"

        return [slow_tool]

    if kind == "search":

        @tool(description="deterministic search tool")
        def internet_search(query: str) -> str:
            TOOL_RAN_BY["internet_search"] = TOOL_RAN_BY.get("internet_search", 0) + 1
            return f"SEARCH:{query}"

        return [internet_search]

    raise ValueError(kind)


#: 保证 import 期无网络构造（tools/subagents 在模块 import 时可能读这些 env 键）时不会崩
_DUMMY_ENV = {
    "OPENAI_API_KEY": "sk-dummy-f8-not-used",
    "BAILIAN_API_KEY": "dummy-bailian",
    "TAVILY_API_KEY": "dummy-tavily",
    "RAGFLOW_API_KEY": "dummy-ragflow",
    "RAGFLOW_BASE_URL": "http://127.0.0.1:9380",
    "MYSQL_HOST": "127.0.0.1",
}


class ScriptedChatModel(BaseChatModel):
    """Deterministic fake chat model（继承 BaseChatModel → 真实 callback/metadata 链路）。"""

    def __init__(self, responses=None, raise_error=None, **kwargs):
        super().__init__(**kwargs)
        self._responses = list(responses or [])
        self._raise_error = raise_error

    @property
    def _llm_type(self) -> str:
        return "scripted-phaseC"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        PROVIDER_CALLS["n"] += 1
        if self._raise_error is not None:
            raise self._raise_error  # 异常实例/类直接 raise
        if not self._responses:
            raise RuntimeError("scripted model exhausted")
        from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: PLC0415

        return ChatResult(generations=[ChatGeneration(message=self._responses.pop(0))])


class MonitorRecorder:
    """R3 语义 recorder：run 级终态 per-key 至多一次（may_emit 语义替身）。"""

    def __init__(self):
        self.events: list[str] = []
        self._emitted: set[str] = set()

    def _rec(self, name, *a, **k):
        self.events.append(name)

    def report_task_started(self, *a, **k):
        self._rec("task_started", *a, **k)

    def report_session_dir(self, *a, **k):
        self._rec("session_dir", *a, **k)

    def report_assistant(self, *a, **k):
        self._rec("assistant", *a, **k)

    def report_task_result(self, *a, **k):
        self._rec("task_result", *a, **k)

    def report_task_cancelled(self, *a, **k):
        self._rec("task_cancelled", *a, **k)

    def report_error(self, *a, **k):
        self._rec("error", *a, **k)


class _OnceGuard:
    def __init__(self):
        self._used: set[str] = set()

    def may_emit(self, key: str) -> bool:
        if key in self._used:
            return False
        self._used.add(key)
        return True


def _make_probe_tools():
    from langchain_core.tools import tool  # noqa: PLC0415

    @tool
    def probe_tool(q: str) -> str:
        """deterministic probe tool."""
        TOOL_RAN["n"] += 1
        return f"TOOL_OUT:{q}"

    return [probe_tool]


def _build_fake_get_main_agent(
    responses,
    raise_error=None,
    subagents=None,
    captures=None,
    block_after_first: bool = False,
    tools=None,
):
    """返回 async get_main_agent 替身：真实 create_deep_agent 编译 + astream spy（记录 config）。"""
    if not _HAS_DEAPAGENTS:
        raise RuntimeError("deepagents 不可用（该 harness 需在项目 .venv 环境运行）")

    async def _get_main_agent():
        from deepagents import create_deep_agent  # noqa: PLC0415

        tl = _make_probe_tools() if tools is None else tools
        model = ScriptedChatModel(responses=responses or [], raise_error=raise_error)
        agent = create_deep_agent(
            model=model,
            system_prompt="probe agent",
            tools=tl,
            subagents=subagents or [],
        )
        orig = agent.astream

        async def _spy_astream(inputs, config=None, **kw):
            if captures is not None:
                captures.append(
                    {
                        "recursion_limit": config.get("recursion_limit"),
                        "callbacks_types": [
                            type(c).__name__ for c in (config.get("callbacks") or [])
                        ],
                        "thread_id": (
                            (config.get("configurable") or {}).get("thread_id")
                        ),
                    }
                )
            if block_after_first:
                yield {"model": {"messages": []}}
                await asyncio.Event().wait()
                return
            async for chunk in orig(inputs, config=config, **kw):
                yield chunk

        agent.astream = _spy_astream
        return agent

    return _get_main_agent


def _ensure_main_agent_module():
    """保证 app.agent.main_agent 可导入（缺凭据/外部服务时用 dummy env 防止 import 期崩溃）。"""
    for _k, _v in _DUMMY_ENV.items():
        os.environ.setdefault(_k, _v)
    import app.agent.main_agent as ma  # noqa: PLC0415

    return ma


async def _run(ma, session_id, ctx=None, cancel_after=None):
    """在 ctx（governance-active）或 None（非治理）下运行真实 run_deep_agent。"""
    from app.runtime.governance.context import enter_governance_execution  # noqa: PLC0415

    outcome = {"raised": None, "raised_type": None, "returned_normal": False}
    try:
        if ctx is not None:
            with enter_governance_execution(ctx):
                task = asyncio.ensure_future(ma.run_deep_agent("run probe", session_id))
                if cancel_after is not None:
                    await asyncio.sleep(cancel_after)
                    task.cancel()
                try:
                    await task
                    outcome["returned_normal"] = True
                except BaseException as exc:  # noqa: BLE001
                    outcome["raised_type"] = type(exc).__name__
                    outcome["raised"] = str(exc)[:200]
                    if os.environ.get("F8_PHASE_C_DEBUG_TB"):
                        import traceback  # noqa: PLC0415

                        traceback.print_exc(limit=16)
        else:
            task = asyncio.ensure_future(ma.run_deep_agent("run probe", session_id))
            if cancel_after is not None:
                await asyncio.sleep(cancel_after)
                task.cancel()
            try:
                await task
                outcome["returned_normal"] = True
            except BaseException as exc:  # noqa: BLE001
                outcome["raised_type"] = type(exc).__name__
                outcome["raised"] = str(exc)[:200]
    except BaseException as exc:  # noqa: BLE001 — context 段外的异常
        outcome["raised_type"] = type(exc).__name__
        outcome["raised"] = str(exc)[:200]
    return outcome


def run_scenario(
    name: str,
    *,
    responses=None,
    raise_error=None,
    limits=None,
    subagents=None,
    governance: bool = True,
    cancel_after=None,
    block_after_first: bool = False,
    research_fake: bool = True,
):
    """同步包装：真实 run_deep_agent + S1 注入 spy 捕获 + recorder；返回 outcome dict。"""
    if not _HAS_DEAPAGENTS:
        return {"skipped": "deepagents unavailable"}

    from app.runtime.governance.context import GovernanceExecution  # noqa: PLC0415
    from app.runtime.governance.counters import BudgetCounter  # noqa: PLC0415

    base_limits = {
        "llm_calls": 120,
        "tool_calls": 300,
        "search_calls": 40,
        "agent_steps": 200,
    }
    if limits:
        base_limits.update(limits)

    async def _scenario() -> dict:
        ma = _ensure_main_agent_module()
        # ---- patch 隔离 ----
        orig = {
            "project_root_path": ma.project_root_path,
            "monitor": ma.monitor,
            "RunTerminalGuard": ma.RunTerminalGuard,
            "get_main_agent": ma.get_main_agent,
            "create_run_and_root": ma.research_reg.create_run_and_root,
            "set_run_status": ma.research_reg.set_run_status,
        }
        orig_bridge_finalize = None
        bridge_patched = False
        if _HAS_RESEARCH_BRIDGE:
            import app.research.bridge as bridge  # noqa: PLC0415

            if hasattr(bridge, "finalize_run"):
                orig_bridge_finalize = bridge.finalize_run
        tmp_root = _TEST_TMP / f"phc-{uuid.uuid4().hex[:8]}"
        tmp_root.mkdir(parents=True, exist_ok=True)
        captures: list = []
        fake_get = _build_fake_get_main_agent(
            responses=responses,
            raise_error=raise_error,
            subagents=subagents,
            captures=captures,
            block_after_first=block_after_first,
        )
        monitor_rec = MonitorRecorder()
        status_calls: list[str] = []
        finalize_calls: list[str] = []
        bridge_patched = False

        def _stub_finalize(run_id, content):
            finalize_calls.append(content)

        try:
            ma.project_root_path = tmp_root
            ma.monitor = monitor_rec
            ma.RunTerminalGuard = _OnceGuard
            ma.get_main_agent = fake_get
            ma.research_reg.create_run_and_root = (
                (
                    lambda session_id, task_query, run_id=None: (
                        f"fake-run-{name}",
                        "fake-sub",
                    )
                )
                if research_fake
                else (lambda session_id, task_query, run_id=None: None)
            )
            ma.research_reg.set_run_status = lambda run_id, status: status_calls.append(
                status
            )
            if _HAS_RESEARCH_BRIDGE and orig_bridge_finalize is not None:
                import app.research.bridge as bridge  # noqa: PLC0415

                bridge.finalize_run = _stub_finalize
                bridge_patched = True

            session_id = f"phc-session-{uuid.uuid4().hex[:8]}"
            ctx = (
                GovernanceExecution(
                    task_id=f"task-{name}",
                    counter=BudgetCounter(limits=base_limits),
                    run_id=f"run-{name}",
                )
                if governance
                else None
            )
            outcome = await _run(ma, session_id, ctx=ctx, cancel_after=cancel_after)
            if ctx is not None:
                outcome["counts"] = ctx.counter.snapshot()
                outcome["diagnostics"] = list(ctx.diagnostics)
                outcome["handler_class_counts"] = ctx.make_handler().class_counts
            outcome["events"] = list(monitor_rec.events)
            outcome["status_calls"] = list(status_calls)
            outcome["finalize_calls"] = list(finalize_calls)
            outcome["captures"] = list(captures)
            outcome["provider_calls"] = PROVIDER_CALLS["n"]
            outcome["tool_ran"] = TOOL_RAN["n"]
        finally:
            ma.project_root_path = orig["project_root_path"]
            ma.monitor = orig["monitor"]
            ma.RunTerminalGuard = orig["RunTerminalGuard"]
            ma.get_main_agent = orig["get_main_agent"]
            ma.research_reg.create_run_and_root = orig["create_run_and_root"]
            ma.research_reg.set_run_status = orig["set_run_status"]
            if bridge_patched and orig_bridge_finalize is not None:
                import app.research.bridge as bridge  # noqa: PLC0415

                bridge.finalize_run = orig_bridge_finalize
            PROVIDER_CALLS["n"] = 0
            TOOL_RAN["n"] = 0
        return outcome

    return asyncio.run(_scenario())


def tool_call_msg(name: str, args: dict, cid: str):
    from langchain_core.messages import AIMessage  # noqa: PLC0415

    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": cid, "type": "tool_call"}],
    )


SUBAGENT_SPEC = [
    {
        "name": "probe-sub",
        "description": "probe subagent",
        "system_prompt": "you are sub",
        "tools": [],  # 由 deepagents 继承主 tools 策略依赖；此处显式空工具，避免多余调用
    }
]


def run_governed_scenario(
    name: str,
    gov_tmp: Path,
    *,
    responses=None,
    raise_error=None,
    policy=None,
    tools=None,
    subagents=None,
    block_after_first: bool = False,
    governed_cancel_after=None,
    research_fake: bool = True,
    store_kind: str = "sqlite",
):
    """Phase E：真实 run_deep_agent 在 GovernanceController.execute(policy) 治理下执行。

    使用真实 main_agent.run_deep_agent 全链（F7/monitor/CancelledError 分支原样）+ 真实
    governance sqlite store + 冻结 controller（deterministic repo-integrated 路径）。
    返回 outcome（含 TaskRecord 行、事件、config capture、provider/tool 计数）。
    """
    if not _HAS_DEAPAGENTS:
        return {"skipped": "deepagents unavailable"}

    from app.runtime.governance import migrations as gov_migrations  # noqa: PLC0415
    from app.runtime.governance import store as gov_store  # noqa: PLC0415
    from app.runtime.governance.controller import GovernanceController  # noqa: PLC0415

    async def _scn() -> dict:
        ma = _ensure_main_agent_module()
        orig = {
            "project_root_path": ma.project_root_path,
            "monitor": ma.monitor,
            "RunTerminalGuard": ma.RunTerminalGuard,
            "get_main_agent": ma.get_main_agent,
            "create_run_and_root": ma.research_reg.create_run_and_root,
            "set_run_status": ma.research_reg.set_run_status,
        }
        orig_bridge = None
        bridge_patched = False
        if _HAS_RESEARCH_BRIDGE:
            import app.research.bridge as bridge  # noqa: PLC0415

            if hasattr(bridge, "finalize_run"):
                orig_bridge = bridge.finalize_run

        tmp_root = _TEST_TMP / f"phe-{uuid.uuid4().hex[:8]}"
        tmp_root.mkdir(parents=True, exist_ok=True)
        captures: list = []
        fake_get = _build_fake_get_main_agent(
            responses=responses,
            raise_error=raise_error,
            subagents=subagents,
            captures=captures,
            block_after_first=block_after_first,
            tools=tools,
        )
        monitor_rec = MonitorRecorder()
        status_calls: list[str] = []
        finalize_calls: list[str] = []
        session_id = f"phe-session-{uuid.uuid4().hex[:8]}"

        db = str(gov_tmp / f"gov-{uuid.uuid4().hex}.sqlite")
        store = None
        if store_kind == "sqlite":
            store = gov_store._GovernanceSqliteStore(db)
        else:
            dsn = os.environ.get("AGENT_CHECKPOINT_DSN_TEST") or os.environ.get(
                "AGENT_CHECKPOINT_DSN"
            )
            if not dsn:
                return {
                    "skipped": "AGENT_CHECKPOINT_DSN(_TEST) 未设置（PG store 模式）"
                }
            store = gov_store._GovernancePostgresStore(dsn)
        gov_migrations.ensure_schema(store)
        ctl = GovernanceController(
            store, owner_instance="inst-phe", retry_delays=(0.01, 0.02)
        )
        ctl.watchdog_tick = 0.02

        outcome: dict = {}
        try:
            ma.project_root_path = tmp_root
            ma.monitor = monitor_rec
            ma.RunTerminalGuard = _OnceGuard
            ma.get_main_agent = fake_get
            ma.research_reg.create_run_and_root = (
                (lambda sid, tq, run_id=None: (f"fake-run-{name}", "fake-sub"))
                if research_fake
                else (lambda sid, tq, run_id=None: None)
            )
            ma.research_reg.set_run_status = lambda run_id, status: status_calls.append(
                status
            )
            if _HAS_RESEARCH_BRIDGE and orig_bridge is not None:
                import app.research.bridge as bridge  # noqa: PLC0415

                bridge.finalize_run = lambda run_id, content: finalize_calls.append(
                    content
                )
                bridge_patched = True

            PROVIDER_CALLS["n"] = 0
            TOOL_RAN["n"] = 0
            TOOL_RAN_BY.clear()

            rec = ctl.create_task(f"th-{name}")
            pol = dict(policy or {})
            exec_task = asyncio.create_task(
                ctl.execute(
                    rec.task_id,
                    ma.run_deep_agent("run probe", session_id),
                    policy=pol,
                )
            )
            t0 = time.monotonic()
            if governed_cancel_after is not None:
                await asyncio.sleep(governed_cancel_after)
                await ctl.cancel(rec.task_id)
            try:
                await exec_task
                outcome["execute_raised"] = None
            except BaseException as exc:  # noqa: BLE001
                outcome["execute_raised"] = type(exc).__name__
            outcome["elapsed_total"] = round(time.monotonic() - t0, 3)
            row = ctl.get_task(rec.task_id)
            outcome["db"] = {
                "status": row.status,
                "terminal_reason": row.terminal_reason,
                "error_kind": row.error_kind,
                "error": (row.error or "")[:80],
                "version": row.version,
                "linger": row.underlying_linger_observed,
                "counters_snapshot": row.counters_snapshot,
            }
            outcome["events"] = list(monitor_rec.events)
            outcome["status_calls"] = list(status_calls)
            outcome["finalize_calls"] = list(finalize_calls)
            outcome["captures"] = list(captures)
            outcome["provider_calls"] = PROVIDER_CALLS["n"]
            outcome["tool_ran"] = TOOL_RAN["n"]
            outcome["tool_by"] = dict(TOOL_RAN_BY)
        finally:
            ma.project_root_path = orig["project_root_path"]
            ma.monitor = orig["monitor"]
            ma.RunTerminalGuard = orig["RunTerminalGuard"]
            ma.get_main_agent = orig["get_main_agent"]
            ma.research_reg.create_run_and_root = orig["create_run_and_root"]
            ma.research_reg.set_run_status = orig["set_run_status"]
            if bridge_patched and orig_bridge is not None:
                import app.research.bridge as bridge  # noqa: PLC0415

                bridge.finalize_run = orig_bridge
            if store is not None:
                store.close()
            PROVIDER_CALLS["n"] = 0
            TOOL_RAN["n"] = 0
        return outcome

    return asyncio.run(_scn())
