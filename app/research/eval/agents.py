"""F9-P0 Batch 7 — scripted agents：baseline 替身与 adaptive 注入 helpers。

- ScriptedBaselineAgent：实现 run_deep_agent 消费的 `.astream(input, config)` 契约；
  内部按 world 确定性执行搜索（写 research evidence）并产出最终 content。
- adaptive 侧复用 f9_orchestrator 既有注入 seam（graph_runner / judge_model /
  plan_model / search_tool / verifier…），本模块提供可复用的 scripted 构造，
  不改 orchestrator/main_agent。

Frozen boundary：不改 app/agent/main_agent.py；本模块仅被 harness 以进程内
monkeypatch 方式接回 run_deep_agent 的 get_main_agent。
"""
from __future__ import annotations

import uuid
from typing import Optional

from langchain_core.messages import AIMessage

from app.research.eval.world import Ev, World, ingest_search_results


def _u() -> str:
    return uuid.uuid4().hex


class ScriptedBaselineAgent:
    """deterministic baseline graph 替身（供 run_deep_agent 的 get_main_agent 返回）。

    astream 行为：
    1. 先执行 world 路由的确定性搜索（queries 由构造参数给出；经当前 research ctx
       写入 evidences/sources）；
    2. 产出一次"子智能体调用"chunk（模拟 network_search 上报）；
    3. 产出最终 content（引用本轮检索到 evidence 的确定性文本）。
    记录 state：searches / final / evidence_ids，供 metrics/断言。
    """

    def __init__(
        self,
        world: World,
        queries: list[str],
        *,
        final_text: Optional[str] = None,
    ) -> None:
        self.world = world
        self.queries = list(queries)
        self.final_text = final_text
        self.searches: list[str] = []
        self.final: Optional[str] = None
        self.evidence_ids: list[str] = []

    async def astream(self, inputs: dict, config: Optional[dict] = None):
        from app.research import context as rctx

        ctx = rctx.get_research_context()
        run_id, sqid = (ctx if ctx else (None, None))
        for i, q in enumerate(self.queries):
            evs = self.world.results_for(q)
            if not evs and i == 0 and self.world._extra:
                evs = list(self.world._extra)
            if not evs:
                evs = [Ev(query=q, title=f"baseline-{i}", content=f"fact-{i}-{_u()}")]
            if run_id and sqid:
                added = ingest_search_results(run_id, sqid, q, evs)
                self.evidence_ids.extend(added)
            self.searches.append(q)
            if i == 0:
                yield {
                    "model": {
                        "messages": [
                            AIMessage(
                                content="",
                                tool_calls=[
                                    {
                                        "name": "task",
                                        "args": {
                                            "subagent_type": "network_search",
                                            "description": "search",
                                        },
                                        "id": f"t-{_u()}",
                                        "type": "tool_call",
                                    }
                                ],
                            )
                        ]
                    }
                }
        text = self.final_text or (
            "根据检索到的证据综合回答（baseline deterministic）。\n"
            "结论：baseline answer seed=" + str(self.world._seed)
        )
        self.final = text
        yield {"model": {"messages": [AIMessage(content=text)]}}


# ---------------------------------------------------------------------------
# Adaptive 侧注入构造（复用 Batch6 orchestrator 测试同款 fake 模式）
# ---------------------------------------------------------------------------


def make_adaptive_graph_runner(stage_text: Optional[dict[str, str]] = None):
    """graph runner：round0/final_synthesis 每段返回确定性文本（记录 stages）。"""
    state = {"stages": []}
    default_text = {
        "round0": "round0 baseline content",
        "final_synthesis": "final synthesis content (adaptive)",
    }
    merged = dict(default_text)
    if stage_text:
        merged.update(stage_text)

    async def runner(prompt: str, config: dict):
        await asyncio_sleep_zero()
        thread = (config.get("configurable") or {}).get("thread_id", "")
        stage = thread.split("::")[1] if "::" in thread else "unknown"
        state["stages"].append(stage)
        return merged.get(stage, f"content-{stage}")

    return runner, state


def asyncio_sleep_zero():
    import asyncio

    return asyncio.sleep(0)


def make_scripted_judge(important_map: Optional[dict[str, bool]] = None, raise_error=None):
    """judge fake：对全部 gap 返回 judgment（important 可 per-gap 覆盖，默认 True）。

    raise_error：若非 None，模型调用时抛该异常（供 S8 Judge-failure fallback 场景）。
    """
    import json
    import re

    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.outputs import ChatGeneration, ChatResult

    def resolver(messages):
        if raise_error is not None:
            raise raise_error
        text = ""
        for m in messages:
            text += " " + str(getattr(m, "content", "") or "")
        gap_ids = list(dict.fromkeys(re.findall(r"gap_id=([^\s]+)", text)))
        important = important_map or {}
        return json.dumps(
            {
                "judgments": [
                    {
                        "gap_id": gid,
                        "important": bool(important.get(gid, True)),
                        "reason": "r",
                        "priority": "medium",
                        "semantic_need": "n",
                    }
                    for gid in gap_ids
                ]
            }
        )

    class _JudgeModel(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "batch7-scripted-judge"

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(content=resolver(list(messages)))
                    )
                ]
            )

        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(content=resolver(list(messages)))
                    )
                ]
            )

    return _JudgeModel()


def make_scripted_plan():
    """plan fake：对全部重要 gap 各生成一个确定性 plan（一条 research query）。"""
    import json
    import re

    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.outputs import ChatGeneration, ChatResult

    def resolver(messages):
        text = ""
        for m in messages:
            text += " " + str(getattr(m, "content", "") or "")
        gap_ids = list(dict.fromkeys(re.findall(r"gap_id=([^\s]+)", text)))
        return json.dumps(
            {
                "plans": [
                    {
                        "gap_id": gid,
                        "objective": f"follow-up for {gid}",
                        "queries": [f"followup-query {gid}"],
                    }
                    for gid in gap_ids
                ]
            }
        )

    class _PlanModel(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "batch7-scripted-plan"

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(content=resolver(list(messages)))
                    )
                ]
            )

        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(content=resolver(list(messages)))
                    )
                ]
            )

    return _PlanModel()


def make_scripted_search_tool(world: World):
    """search tool：LangChain @tool 包装 world.results_for → registry ingest。

    research ctx 由调用方（f9_orchestrator targeted 的 _run_query）已设置，
    与 Batch5/6 fake ingest tool 同构；确定性（同 query 同结果）。
    """
    from langchain_core.tools import tool as lc_tool

    from app.research.eval.world import ingest_search_results

    @lc_tool(description="deterministic scripted internet_search")
    def internet_search(query: str) -> str:
        from app.research import context as rctx

        ctx = rctx.get_research_context()
        if not ctx:
            return "ERR:no-research-ctx"
        run_id, sqid = ctx
        evs = world.results_any(query)
        added = ingest_search_results(run_id, sqid, query, evs) if evs else []
        return f"OK:{query}:{len(added)}"

    return internet_search
