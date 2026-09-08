"""Checkpoint replay 验证的共享 harness（非测试收集模块）。

供 test_checkpoint_async_sqlite.py / test_checkpoint_replay_verify.py /
test_checkpoint_postgres.py 复用：构造 interrupt 图并返回结构化观测，
供各后端断言。只依赖 langgraph + 传入的 saver（双后端通用）。
"""

from __future__ import annotations

import uuid
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


class ReplayState(TypedDict, total=False):
    log: list
    counter: int
    marker_a: str
    marker_b: str


def build_replay_graph():
    """step1 → step2(interrupt) → step3；返回未 compile 的 StateGraph。"""

    def step1(state: ReplayState):
        return {
            "log": state.get("log", []) + ["step1 ran"],
            "counter": state.get("counter", 0) + 1,
            "marker_a": uuid.uuid4().hex,
        }

    def step2(state: ReplayState):
        resumed = interrupt("resume-please")
        return {
            "log": state["log"] + [f"step2 resumed={resumed}"],
            "marker_b": str(resumed),
        }

    def step3(state: ReplayState):
        return {"log": state["log"] + ["step3 ran"]}

    g = StateGraph(ReplayState)
    g.add_node("step1", step1)
    g.add_node("step2", step2)
    g.add_node("step3", step3)
    g.add_edge(START, "step1")
    g.add_edge("step1", "step2")
    g.add_edge("step2", "step3")
    g.add_edge("step3", END)
    return g


async def run_replay_scenario(saver: Any, thread_id: str = "replay-thread") -> dict:
    """执行一次完整 replay 数据面场景，返回结构化观测。"""
    graph = build_replay_graph().compile(checkpointer=saver)
    cfg = {"configurable": {"thread_id": thread_id}}

    # 1) run 到 interrupt（step2 前落 checkpoint）
    await graph.ainvoke({"log": [], "counter": 0}, cfg)
    snap = await graph.aget_state(cfg)
    snap_next = list(snap.next)
    snap_counter = snap.values.get("counter")

    # 2) 历史：条数 / newest-first 顺序 / parent 链闭合 / metadata.step
    hist = [h async for h in graph.aget_state_history(cfg)]
    ids = [h.config["configurable"]["checkpoint_id"] for h in hist]
    order_ok = ids == sorted(ids, reverse=True)
    chain_ok = True
    for i, h in enumerate(hist[:-1]):
        pid = (
            h.parent_config["configurable"]["checkpoint_id"]
            if h.parent_config
            else None
        )
        if pid != ids[i + 1]:
            chain_ok = False
    steps = [h.metadata.get("step") for h in hist]

    # 3) 从最新 checkpoint resume
    final = await graph.ainvoke(Command(resume="resumed-by-latest"), cfg)
    resume_counter = final["counter"]
    resume_marker_b = final["marker_b"]
    resume_log = list(final["log"])

    # 4) 按历史 checkpoint_id 直取（数据面支持任意时间点定位）
    target = next(h for h in hist if "step2" in h.next)
    target_id = target.config["configurable"]["checkpoint_id"]
    got = await saver.aget_tuple(
        {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": "",
                "checkpoint_id": target_id,
            }
        }
    )
    hist_fetch_ok = got is not None

    return {
        "snap_next": snap_next,
        "snap_counter": snap_counter,
        "history_count": len(hist),
        "order_ok": order_ok,
        "chain_ok": chain_ok,
        "steps": steps,
        "resume_counter": resume_counter,
        "resume_marker_b": resume_marker_b,
        "resume_log": resume_log,
        "hist_fetch_ok": hist_fetch_ok,
    }
