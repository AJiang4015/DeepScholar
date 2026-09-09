"""F9-P0 Batch 7 — 8 benchmark scenarios（positive + negative/control）。

每个 scenario = {id, label, kind, task, world 装配, baseline 行为, adaptive 注入,
期望断言标签}。baseline 与 adaptive 使用同一 world（同 task / 同确定性搜索结果 /
同 F8 policy）。

注意：adaptive 行为由冻结 orchestrator + scripted judge/plan/search/verify 驱动；
本文件只装配 deterministic 输入，不改变 orchestrator 语义。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.f9.eval.agents import ScriptedBaselineAgent
from app.f9.eval.world import Ev, World

SCENARIO_IDS = (
    "s1_sufficient_no_followup",
    "s2_missing_required_targeted",
    "s3_unverified_claim_reverify",
    "s4_conflict_reconcile",
    "s5_insufficient_corroboration",
    "s6_diminishing_stop",
    "s7_near_budget",
    "s8_fallback_judge_failure",
)


@dataclass
class Scenario:
    id: str
    label: str
    kind: str  # positive | control | negative
    task: str
    world: World
    baseline_queries: list[str]
    adaptive_policy: dict[str, Any]
    expected: dict[str, Any]
    judge_failure: bool = False


def _build_s1() -> Scenario:
    """already-sufficient → adaptive 不应多余 follow-up（positive-control）。

    world 对 round0/baseline 初始 query 提供 2 条 evidence（root sq 覆盖）→
    adaptive round0 后 projection 无 gap → 不调 judge/plan/targeted。
    """
    world = World(
        task="S1: 单一必答问题，检索已充分。",
        seed="s1",
        query_to_evidence={
            "s1-q0": [
                Ev("s1-q0", "src-a", "fact A about answer"),
                Ev("s1-q0", "src-b", "fact B about answer"),
            ]
        },
    )
    return Scenario(
        id="s1_sufficient_no_followup",
        label="already-sufficient → no unnecessary follow-up",
        kind="control",
        task=world.task,
        world=world,
        baseline_queries=["s1-q0"],
        adaptive_policy={},
        expected={
            "adaptive_no_extra_search": True,  # 禁止为指标加搜
            "adaptive_rounds_le": 2,  # round0 + 至多 1 adaptive 轮（no-gap 即停）
            "gate": "control",
        },
    )


def _build_s2() -> Scenario:
    """missing required coverage → adaptive targeted follow-up 补足。"""
    world = World(
        task="S2: 必答问题（初始无任何证据）。",
        seed="s2",
        extra_evidence=[
            Ev("q-target", "src-c1", "fact C1 (targeted)"),
            Ev("q-target", "src-c2", "fact C2 (targeted)"),
        ],
    )
    return Scenario(
        id="s2_missing_required_targeted",
        label="missing required → targeted follow-up",
        kind="positive",
        task=world.task,
        world=world,
        baseline_queries=["s2-q0"],  # world 无此 route → baseline/round0 均无证据
        adaptive_policy={},
        expected={"adaptive_required_covered": True, "gate": "improve"},
    )


def _build_s3() -> Scenario:
    world = World(
        task="S3: 已有 claim 但未验证。",
        seed="s3",
        extra_evidence=[Ev("q-verify", "src-d", "fact D")],
    )
    return Scenario(
        id="s3_unverified_claim_reverify",
        label="unverified claim → re-verification（preseed claim run）",
        kind="positive",
        task=world.task,
        world=world,
        baseline_queries=["baseline-q-0"],
        adaptive_policy={},
        expected={"changed_claim_verified": True, "gate": "seam"},
    )


def _build_s4() -> Scenario:
    world = World(
        task="S4: 冲突证据。",
        seed="s4",
        extra_evidence=[Ev("q-conf", "src-e", "fact E (conflict side)")],
    )
    return Scenario(
        id="s4_conflict_reconcile",
        label="conflict → F4 seam 兼容（preseed claim run）",
        kind="positive",
        task=world.task,
        world=world,
        baseline_queries=["baseline-q-0"],
        adaptive_policy={},
        expected={"f4_compatible": True, "gate": "seam"},
    )


def _build_s5() -> Scenario:
    world = World(
        task="S5: 独立佐证不足。",
        seed="s5",
        extra_evidence=[Ev("q-corro", "src-f", "fact F (second source)")],
    )
    return Scenario(
        id="s5_insufficient_corroboration",
        label="insufficient corroboration → F5 seam 兼容（preseed claim run）",
        kind="positive",
        task=world.task,
        world=world,
        baseline_queries=["baseline-q-0"],
        adaptive_policy={},
        expected={"f5_compatible": True, "gate": "seam"},
    )


def _build_s6() -> Scenario:
    world = World(
        task="S6: 一轮补足后无进展 → 正确停止。",
        seed="s6",
        extra_evidence=[Ev("q-dim", "src-g", "fact G")],
    )
    return Scenario(
        id="s6_diminishing_stop",
        label="diminishing return / correct stopping",
        kind="control",
        task=world.task,
        world=world,
        baseline_queries=["baseline-q-0"],
        adaptive_policy={"max_research_rounds": 3},
        expected={"adaptive_rounds_le": 2, "stopped_before_max": True, "gate": "control"},
    )


def _build_s7() -> Scenario:
    world = World(
        task="S7: 近预算停止（不得超支）。",
        seed="s7",
        extra_evidence=[Ev("q-budget", "src-h", "fact H")],
    )
    return Scenario(
        id="s7_near_budget",
        label="near-budget stopping",
        kind="control",
        task=world.task,
        world=world,
        baseline_queries=["baseline-q-0"],
        adaptive_policy={"max_llm_calls": 6},
        expected={"adaptive_cost_compliant": True, "gate": "control"},
    )


def _build_s8() -> Scenario:
    world = World(
        task="S8: judge 失败 → 同 execute fallback。",
        seed="s8",
        extra_evidence=[Ev("q-fallback", "src-i", "fact I")],
    )
    return Scenario(
        id="s8_fallback_judge_failure",
        label="Judge failure → same-execution fallback",
        kind="negative",
        task=world.task,
        world=world,
        baseline_queries=["baseline-q-0"],
        adaptive_policy={},
        expected={"adaptive_fallback_completed": True, "gate": "control"},
        judge_failure=True,
    )


_BUILDERS = {
    "s1_sufficient_no_followup": _build_s1,
    "s2_missing_required_targeted": _build_s2,
    "s3_unverified_claim_reverify": _build_s3,
    "s4_conflict_reconcile": _build_s4,
    "s5_insufficient_corroboration": _build_s5,
    "s6_diminishing_stop": _build_s6,
    "s7_near_budget": _build_s7,
    "s8_fallback_judge_failure": _build_s8,
}


def get_scenario(sid: str) -> Scenario:
    if sid not in _BUILDERS:
        raise KeyError(f"unknown scenario: {sid}")
    return _BUILDERS[sid]()


def all_scenarios() -> list[Scenario]:
    return [get_scenario(sid) for sid in SCENARIO_IDS]


def make_baseline_agent(sc: Scenario):
    """baseline scripted agent：先做 baseline_queries 的搜索（world 有则命中），
    最终文本引用该 run 的 source locators（供 citation 判定）。"""
    world = sc.world

    class _Agent(ScriptedBaselineAgent):
        async def astream(self, inputs, config=None):
            from app.research import context as rctx

            ctx = rctx.get_research_context()
            run_id, sqid = (ctx if ctx else (None, None))
            for i, q in enumerate(self.queries):
                evs = world.results_for(q)
                if not evs:
                    evs = [Ev(query=q, title=f"base-{i}", content=f"base-fact-{i}")]
                if run_id and sqid:
                    from app.f9.eval.world import ingest_search_results

                    added = ingest_search_results(run_id, sqid, q, evs)
                    self.evidence_ids.extend(added)
                self.searches.append(q)
                if i == 0:
                    from langchain_core.messages import AIMessage

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
                                            "id": "t-b",
                                            "type": "tool_call",
                                        }
                                    ],
                                )
                            ]
                        }
                    }
            # final：引用本 run locators（deterministic 前缀）
            locs = _run_locators(run_id)
            text = (
                "根据检索证据综合回答（baseline）。\n证据引用：" + ",".join(locs)
                if locs
                else "根据已有知识回答。"
            )
            self.final = text
            yield {
                "model": {
                    "messages": [
                        __import__("langchain_core.messages", fromlist=["AIMessage"]).AIMessage(
                            content=text
                        )
                    ]
                }
            }

    return _Agent(world, list(sc.baseline_queries))


def make_adaptive_graph_runner_for(sc: Scenario, round0_query: Optional[str] = None):
    """adaptive graph runner：round0 执行 world 初始搜索（与 baseline 同 query，公平
    起点），final synthesis 引用当前 run locators。与 baseline 同 world 路由。

    注：round0 也走 world 确定性结果；若 world 对该 query 无结果 → 不落 evidence
    （由 orchestrator 后续 gap→targeted 补足）。
    """
    import asyncio

    state = {"stages": []}
    q0 = round0_query or (sc.baseline_queries[0] if sc.baseline_queries else None)

    async def runner(prompt: str, config: dict):
        await asyncio.sleep(0)
        thread = (config.get("configurable") or {}).get("thread_id", "")
        stage = thread.split("::")[1] if "::" in thread else "unknown"
        state["stages"].append(stage)
        if stage == "round0" and q0:
            from app.f9.eval.world import ingest_search_results

            from app.research import context as rctx

            ctx = rctx.get_research_context()
            if ctx:
                run_id, sqid = ctx
                evs = sc.world.results_for(q0)
                if evs:
                    ingest_search_results(run_id, sqid, q0, evs)
            return "round0 content"
        if stage == "final_synthesis":
            return (
                "final synthesis (adaptive) 证据引用:"
                + ",".join(_run_locators_of(config))
            )
        return f"content-{stage}"

    return runner, state


def _run_locators(run_id) -> list[str]:
    if not run_id:
        return []
    from app.f9.eval.harness import _get_research_store

    store = _get_research_store()
    rows = store.execute(
        "SELECT locator FROM sources WHERE run_id=%s ORDER BY source_id", (run_id,)
    )
    return [str(r["locator"]) for r in rows]


def _run_locators_of(config) -> list[str]:
    from app.research import context as rctx

    ctx = rctx.get_research_context()
    return _run_locators(ctx[0] if ctx else None)
