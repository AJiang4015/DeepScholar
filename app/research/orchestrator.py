"""F9-P0 Batch 6 — Round Orchestrator（单次 F8 governed execution 内装配 Batch1–5）。

Decision/Plan：docs/plan/2026-09-29-f9-p0-batch6-implementation-plan.md（L3；Review Closures C1–C3）。
裁决：D1 Option A（F9-scoped graph invocation；见 ARCHITECTURE.md §3 例外）、D2（round0 =
baseline graph invocation 且全额计费、最多一次）、D3（round in-memory）、D4（每轮 changed
claims 增量 F4→F5→F6）、D5（Research Policy 临时默认）、D6（Final Synthesis = 最后 graph
阶段 + finalize_run 恰一次）。

不变量：
- 单 Controller.execute / GovernanceExecution / BudgetCounter / deadline·cancel / ResearchRun
  （不新建）；本模块内绝无 controller/submit/new task/new execute；
- round0 baseline 最多一次（显式 `baseline_executed`）；禁止 round0 重放；
- F7 只经 public `research_bridge.finalize_run` 在 normal completion 后恰一次；cancel/
  timeout/budget/异常路径不 finalize；
- F9 STOPPED/NO_PROGRESS 是 orchestrator 状态，不映射 F8 TaskStatus（F8 terminal 由 F8 收敛）；
- 每图调用独立 thread_id（避免 checkpoint 累积父历史 → bounded context）；graph invocation
  的 glue（handler/recursion/research ctx/session·thread ctx/monitor/cancel）由本模块维护；
- ordinary failure（judge/plan/projection/tool/verify/detector/reviewer）→ 记录/fail-open 或
  baseline fallback（不重跑 round0 → 直接 Final Synthesis）；GovernanceLimitExceeded /
  CancelledError → 不 catch（向 Controller 传播）；
- 不修改 F8 / F1–F7 / run_deep_agent / main_agent / Batch1–5（real agent 仅 lazy 引用）。

依赖注入 seam（测试用）：graph runner、judge_model、plan_model、search_tool、verifier、
F4 detector / F5・F6 reviewer、finalize/status hook、store。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Awaitable, Callable, Optional

from app.research import gaps as f9gaps
from app.research import judge as f9judge
from app.research import plan as f9plan
from app.research import projection as f9proj
from app.research import targeted as f9targeted
from app.runtime.governance.context import (
    GovernanceExecution,
    get_governance_execution,
)

#: D5 Research Policy 临时默认（Batch8 Eval 校准前非冻结产品参数）
RESEARCH_POLICY_DEFAULTS: dict[str, int] = {
    "max_research_rounds": 3,  # 硬上限（不代表必须执行 3 轮）
    "max_no_progress_rounds": 1,
    "repeated_gap_threshold": 2,
}

STOP_REASONS = (
    "no_gap",
    "all_gaps_unimportant",
    "sufficient_coverage",
    "no_progress",
    "repeated_gap",
    "max_rounds",
    "adaptive_fallback",
)

_ROUND_STATES = frozenset(
    {
        "ROUND_STARTED",
        "PROJECTION_READY",
        "GAPS_READY",
        "JUDGED",
        "PLAN_VALIDATED",
        "RESEARCH_EXECUTED",
        "EVIDENCE_UPDATED",
        "VERIFICATION_UPDATED",
        "ROUND_EVALUATED",
        "FOLLOW_UP",
        "STOPPED",
        "NO_PROGRESS",
    }
)


class OrchestratorError(Exception):
    """orchestrator 层普通错误（输入/状态非法）。F8 control 不包装。"""


def _policy(policy: Optional[dict[str, Any]]) -> dict[str, int]:
    merged = dict(RESEARCH_POLICY_DEFAULTS)
    merged.update({k: int(v) for k, v in (policy or {}).items()})
    return merged


async def _default_graph_runner(prompt: str, config: dict[str, Any]) -> str:
    """真实 graph runner（D1 Option A；glue 已由 orchestrator 注入 config）。

    仅 lazy import main_agent（避免模块导入期依赖凭据）；astream 汇总最后 model 文本。
    """
    from app.agent.main_agent import get_main_agent  # noqa: PLC0415

    agent = await get_main_agent()
    final_content: Optional[str] = None
    async for chunk in agent.astream(
        {"messages": [{"role": "user", "content": prompt}]}, config=config
    ):
        for node_name, state in chunk.items():
            if node_name != "model" or not state or "messages" not in state:
                continue
            messages = state["messages"]
            if not messages or not isinstance(messages, list):
                continue
            last = messages[-1]
            if not getattr(last, "tool_calls", None) and getattr(last, "content", None):
                final_content = last.content
    return final_content or ""


async def _run_graph(
    prompt: str,
    *,
    session_id: str,
    stage: str,
    handler: Any,
    recursion_limit: Optional[int],
    runner: Callable[[str, dict[str, Any]], Awaitable[str]],
    emit_monitor: bool = False,
) -> str:
    """单次 graph invocation（glue 注入 + 独立 thread_id；不触发 F7/不建 run）。"""
    thread_id = f"{session_id}::{stage}::{uuid.uuid4().hex}"
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    if handler is not None:
        config.setdefault("callbacks", [])
        config["callbacks"].append(handler)
        if recursion_limit is not None:
            config.setdefault("recursion_limit", recursion_limit)
    if emit_monitor:
        from app.api.monitor import monitor  # noqa: PLC0415

        monitor.report_assistant(stage, {"description": "f9 graph invocation"})
    return await runner(prompt, config)


def _claim_statement_context(projection: dict[str, Any]) -> dict[str, str]:
    return {
        c["claim_id"]: str(c.get("statement") or "")[:400]
        for c in (projection.get("claims") or [])
    }


def _round_change_summary(round_result: dict[str, Any]) -> dict[str, Any]:
    """从 Batch5 result 提取本轮 changed claims / 新增 evidence / 失败计数（确定性）。"""
    changed = sorted(
        v["claim_id"]
        for v in round_result.get("verification", [])
        if int(v.get("new_bindings") or 0) > 0
    )
    added = list(round_result.get("added_evidence_ids") or [])
    failures = list(round_result.get("failures") or [])
    return {"changed_claims": changed, "added_evidence": added, "failures": failures}


async def _run_f456_incremental(
    run_id: str,
    changed_claims: list[str],
    *,
    store: Any,
    detector: Any = None,
    corroboration_reviewer: Any = None,
    reconciliation_reviewer: Any = None,
    handler: Any = None,
) -> list[dict[str, Any]]:
    """D4：对 changed claims 增量 F4→F5→F6（claim-level；real 经 seam 注入，默认 Fake）。

    ordinary 失败 fail-open 记录；F8 control 异常不 catch（向上传播）。
    """
    from app.research import corroboration as f5  # noqa: PLC0415
    from app.research import conflict as f4  # noqa: PLC0415
    from app.research import reconciliation as f6  # noqa: PLC0415

    out: list[dict[str, Any]] = []
    for claim_id in changed_claims:
        # 每 claim 让步：F456 段可能含多个 claim 的 DB/LLM 同步工作，让步点保证
        # F8 cancel/timeout 可在该段内投递（CancelledError 原样传播，不 catch）。
        await asyncio.sleep(0)
        entry: dict[str, Any] = {"claim_id": claim_id}
        try:
            f4_result = f4.detect_claim_conflicts(claim_id, detector=detector)
            entry["f4"] = {
                "status": "executed",
                "items": len(f4_result.get("items", [])),
                "confirmed": sum(
                    1
                    for i in f4_result.get("items", [])
                    if i.get("status") == "confirmed"
                ),
            }
            confirmed_conflicts = [
                i for i in f4_result.get("items", []) if i.get("status") == "confirmed"
            ]
        except Exception as exc:  # noqa: BLE001 — ordinary fail-open
            out.append(
                {
                    "claim_id": claim_id,
                    "f4": {"status": "failed", "error": str(exc)[:300]},
                }
            )
            continue
        try:
            f5_result = f5.compute_claim_corroboration(
                claim_id, reviewer=corroboration_reviewer
            )
            entry["f5"] = {"status": f5_result.get("status")}
        except Exception as exc:  # noqa: BLE001 — ordinary fail-open
            entry["f5"] = {"status": "failed", "error": str(exc)[:300]}
        try:
            if confirmed_conflicts:
                f6_result = f6.reconcile_claim_conflicts(
                    claim_id, reviewer=reconciliation_reviewer
                )
                entry["f6"] = {
                    "status": f6_result.get("register", {}).get("register"),
                    "items": len(f6_result.get("items", [])),
                }
            else:
                entry["f6"] = {"status": "skipped_no_conflict"}
        except Exception as exc:  # noqa: BLE001 — ordinary fail-open
            entry["f6"] = {"status": "failed", "error": str(exc)[:300]}
        out.append(entry)
    return out


def _gap_signature(gaps: dict[str, Any]) -> frozenset[str]:
    from app.research.judge import gap_id_of  # noqa: PLC0415

    return frozenset(gap_id_of(s) for s in (gaps.get("signals") or []))


async def f9_orchestrator(
    task_query: str,
    session_id: str,
    *,
    research_policy: Optional[dict[str, Any]] = None,
    graph_runner: Optional[Callable[[str, dict[str, Any]], Awaitable[str]]] = None,
    judge_model: Any = None,
    plan_model: Any = None,
    search_tool: Any = None,
    verifier: Any = None,
    conflict_detector: Any = None,
    corroboration_reviewer: Any = None,
    reconciliation_reviewer: Any = None,
    store: Any = None,
    create_run: bool = True,
    emit_monitor: bool = False,
    finalize_sink: Optional[Callable[[str, str], Any]] = None,
    status_sink: Optional[Callable[[str, str], Any]] = None,
    adaptive_enabled: bool = True,
) -> dict[str, Any]:
    """F9 Round Orchestrator 主入口（在 governance-active 上下文内调用）。

    返回 summary：{run_id, rounds, stopped_reason, judged_rounds, executed_plans,
    changed_claims, finalize_calls, finalized}；异常语义见模块 docstring。
    """
    execution: Optional[GovernanceExecution] = get_governance_execution()
    if execution is None:
        raise OrchestratorError("f9_orchestrator 必须在 F8 governed execution 内调用")
    run_id = execution.run_id or uuid.uuid4().hex
    pol = _policy(research_policy)
    runner = graph_runner or _default_graph_runner
    handler = execution.make_handler()

    store_holder: Any = store
    from app.research import store as rstore  # noqa: PLC0415

    if store_holder is None:
        store_holder = rstore.get_store()

    from app.research import context as research_ctx  # noqa: PLC0415
    from app.research import registry as research_reg  # noqa: PLC0415

    created_run = False
    tokens = None
    baseline_executed = False
    stopped_reason: Optional[str] = None
    summary: dict[str, Any] = {
        "run_id": run_id,
        "rounds": 0,
        "judged_rounds": 0,
        "executed_plans": 0,
        "changed_claims": [],
        "stopped_reason": None,
        "finalize_calls": 0,
        "finalized": False,
        "errors": [],
    }
    try:
        if store_holder is not None:
            if create_run:
                run_ids = research_reg.create_run_and_root(
                    session_id, task_query[:2000], run_id=run_id
                )
                if run_ids:
                    created_run = True
                    tokens = research_ctx.set_research_context(*run_ids)
            if tokens is None:
                # 复用既有 run（不新建）：从 run 读取 root sub_question 并设置 research ctx
                from app.research import provenance  # noqa: PLC0415

                subs = provenance.list_sub_questions(run_id)
                root_sq = (
                    sorted(subs, key=lambda s: (int(s.position), s.sub_question_id))[0]
                    if subs
                    else None
                )
                if root_sq is not None:
                    tokens = research_ctx.set_research_context(
                        run_id, root_sq.sub_question_id
                    )
        if emit_monitor:
            from app.api.monitor import monitor  # noqa: PLC0415

            monitor.report_task_started(task_query[:500])

        # ---- round0 baseline（D2：最多一次；全额计费经 graph handler）----
        if adaptive_enabled:
            await _run_graph(
                task_query,
                session_id=session_id,
                stage="round0",
                handler=handler,
                recursion_limit=execution.recursion_limit,
                runner=runner,
                emit_monitor=emit_monitor,
            )
            baseline_executed = (
                True  # exactly-once（C2）：adaptive/fallback 永不重跑 round0
            )
            summary["rounds"] += 1

        # ---- adaptive rounds（D3）----
        no_progress_rounds = 0
        prev_gap_signature: Optional[frozenset[str]] = None
        gap_rounds_seen: dict[str, int] = {}
        r = 0
        while adaptive_enabled and r < int(pol["max_research_rounds"]):
            summary["rounds"] += 1
            r += 1
            projection = f9proj.project(
                run_id, round=r, budget=execution.counter.to_dict(), store=store_holder
            )
            gaps = f9gaps.detect_gaps(projection)
            if not gaps.get("signals"):
                stopped_reason = "no_gap"
                break
            sign = _gap_signature(gaps)
            for gid in sign:
                gap_rounds_seen[gid] = gap_rounds_seen.get(gid, 0) + 1
            if prev_gap_signature is not None and sign == prev_gap_signature:
                no_progress_rounds += 1
            else:
                no_progress_rounds = 0
            prev_gap_signature = sign
            if no_progress_rounds >= int(pol["max_no_progress_rounds"]) and r > 1:
                stopped_reason = "no_progress"
                break
            stale = [
                g
                for g, n in gap_rounds_seen.items()
                if n >= int(pol["repeated_gap_threshold"])
            ]
            if r > 1 and stale:
                stopped_reason = "repeated_gap"
                summary["errors"].append(f"repeated_gap:{','.join(sorted(stale)[:5])}")
                break

            # Judge（handler 计费；failure → fallback）
            try:
                judgment = await f9judge.judge_gaps(projection, gaps, judge_model)
            except f9judge.JudgeError:
                stopped_reason = "adaptive_fallback"
                summary["errors"].append("judge_failure_fallback")
                break
            summary["judged_rounds"] += 1
            if not any(
                j.get("important") is True for j in judgment.get("judgments", [])
            ):
                stopped_reason = "all_gaps_unimportant"
                break

            # Plan proposal（failure → fallback）
            try:
                plans_out = await f9plan.propose_followup_plans(
                    projection,
                    gaps,
                    judgment,
                    plan_model,
                    tool="internet_search",
                    agent="network_search",
                )
            except f9plan.PlanProposalFailure:
                stopped_reason = "adaptive_fallback"
                summary["errors"].append("plan_failure_fallback")
                break
            if not plans_out.get("plans"):
                stopped_reason = "no_valid_plan"
                break

            # Targeted research + 增量 F3（Batch5）
            changed: list[str] = []
            failures_round: list[dict[str, str]] = []
            round_added = 0
            for plan in plans_out["plans"]:
                try:
                    res = await f9targeted.execute_targeted_plan(
                        plan,
                        run_id=run_id,
                        search_tool=search_tool,
                        verifier=verifier,
                        store=store_holder,
                    )
                    summary["executed_plans"] += 1
                    info = _round_change_summary(res)
                    changed.extend(info["changed_claims"])
                    failures_round.extend(info["failures"])
                    round_added += len(info["added_evidence"])
                except Exception as exc:  # noqa: BLE001 — ordinary continue
                    failures_round.append(
                        {
                            "kind": "targeted_failed",
                            "detail": f"{type(exc).__name__}: {str(exc)[:200]}",
                        }
                    )
            changed = sorted(set(changed))
            summary["changed_claims"] = sorted(
                set(summary["changed_claims"]) | set(changed)
            )
            # diminishing-return 记录（每轮新增 evidence 计数；评估由下轮重投影/stopping 消费）
            summary.setdefault("evidence_added_per_round", []).append(round_added)

            # D4 增量 F4→F5→F6（changed claims；claim-level；adapter seam）
            if changed:
                f456 = await _run_f456_incremental(
                    run_id,
                    changed,
                    store=store_holder,
                    detector=conflict_detector,
                    corroboration_reviewer=corroboration_reviewer,
                    reconciliation_reviewer=reconciliation_reviewer,
                    handler=handler,
                )
                summary.setdefault("f456", []).extend(f456)

        if stopped_reason is None:
            stopped_reason = "max_rounds"

        # ---- Final Synthesis（D6：最后 graph 阶段；同 execute）----
        prompt = (
            "根据当前研究状态撰写最终综合答案。"
            f"\n研究 run: {run_id}\n上一轮状态: {stopped_reason or 'ok'}"
        )
        final_content = await _run_graph(
            prompt,
            session_id=session_id,
            stage="final_synthesis",
            handler=handler,
            recursion_limit=execution.recursion_limit,
            runner=runner,
            emit_monitor=emit_monitor,
        )
        summary.setdefault("synthesis_content_len", len(final_content))

        # ---- F7 exactly once（仅 normal；仅本 orchestrator 创建 run 或显式 sink）----
        if finalize_sink is not None:
            finalize_sink(run_id, final_content)
            summary["finalize_calls"] += 1
            summary["finalized"] = True
        elif created_run and store_holder is not None:
            from app.research import bridge as research_bridge  # noqa: PLC0415

            research_bridge.finalize_run(run_id, final_content or "")
            summary["finalize_calls"] += 1
            summary["finalized"] = True
        if status_sink is not None:
            status_sink(run_id, "finished")
        elif created_run and store_holder is not None:
            research_reg.set_run_status(run_id, "finished")
        if emit_monitor:
            from app.api.monitor import monitor  # noqa: PLC0415

            monitor.report_task_result(final_content or "")
        summary["stopped_reason"] = stopped_reason
        summary["baseline_executed"] = baseline_executed
        return summary
    except asyncio.CancelledError:
        if created_run and store_holder is not None and status_sink is None:
            research_reg.set_run_status(run_id, "cancelled")
        elif status_sink is not None:
            status_sink(run_id, "cancelled")
        raise
    except Exception:
        if created_run and store_holder is not None and status_sink is None:
            research_reg.set_run_status(run_id, "failed")
        elif status_sink is not None:
            status_sink(run_id, "failed")
        raise
    finally:
        if tokens is not None:
            research_ctx.reset_research_context(*tokens)
