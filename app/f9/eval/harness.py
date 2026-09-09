"""F9-P0 Batch 7 — eval harness：baseline / adaptive 各自独立 Controller.execute。

每次 run：
- Baseline：governance store 新 task；Controller.execute 内跑 run_deep_agent
  （进程内替换 main_agent.get_main_agent → ScriptedBaselineAgent，monkeypatch 由
  调用方负责，harness 只提供 run 函数；mock 以上下文方式传入）。
- Adaptive：governance store 新 task；Controller.execute 内跑 f9_orchestrator（注入
  scripted judge/plan/search/verifier + graph runner + finalize/status sink）。

指标收集（零 schema）：
- orchestrator summary（rounds/changed_claims/finalize_calls…）；
- research metrics：对 run_id 只读查询 registry/projection；
- cost：governance get_task → counters_snapshot + effective_limits；
- final answer：baseline agent.final / adaptive finalize_sink 捕获。

Frozen boundary：本模块不改任何冻结实现；monkeypatch 仅测试期注入。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

_u = lambda: uuid.uuid4().hex  # noqa: E731


@dataclass
class EvalRun:
    """单侧（baseline 或 adaptive）一次治理 execute 的结果容器。"""

    side: str  # "baseline" | "adaptive"
    task_id: str
    run_id: Optional[str] = None
    final: Optional[str] = None
    summary: dict[str, Any] = field(default_factory=dict)
    task_row: Optional[Any] = None  # gov TaskRecord
    research_state: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Governance 工具
# ---------------------------------------------------------------------------


def make_governance(db_dir, owner: str = "f9-b7-eval"):
    from app.runtime.governance import migrations as gm
    from app.runtime.governance import store as gs
    from app.runtime.governance.controller import GovernanceController

    path = db_dir / f"gov-{_u()}.sqlite"
    store = gs._GovernanceSqliteStore(str(path))
    gm.ensure_schema(store)
    ctl = GovernanceController(store, owner_instance=owner, retry_delays=(0.01, 0.02))
    ctl.watchdog_tick = 0.02
    return store, ctl


def run_baseline(
    gov_store,
    ctl,
    *,
    task_query: str,
    agent_factory,
    policy: Optional[dict[str, Any]] = None,
) -> EvalRun:
    """治理 execute 内跑真实 run_deep_agent（baseline）。

    agent_factory: () -> scripted agent（.astream(input, config) 契约）。本函数在
    execute 前进程内替换 main_agent.get_main_agent 为该 factory，执行后恢复；
    捕获被创建的 agent 状态（.final / .searches / .evidence_ids）供指标。
    """
    import asyncio

    from app.agent import main_agent as _ma
    from app.runtime.governance import store as gs
    from app.runtime.governance.models import TaskStatus

    thread_id = f"b7-baseline-{_u()}"
    rec = ctl.create_task(thread_id)
    holder: dict[str, Any] = {}

    real_get = _ma.get_main_agent

    async def _fake_get_main_agent():
        agent = agent_factory()
        holder["agent"] = agent
        return agent

    async def _run():
        from app.agent.main_agent import run_deep_agent

        _ma.get_main_agent = _fake_get_main_agent  # type: ignore[attr-defined]
        try:
            return await ctl.execute(
                rec.task_id, run_deep_agent(task_query, thread_id), policy=policy or {}
            )
        finally:
            _ma.get_main_agent = real_get  # type: ignore[attr-defined]

    result = asyncio.run(_run())  # noqa: F841 — 结果经 task_row 收敛校验
    row = gs.get_task(gov_store, rec.task_id)
    agent = holder.get("agent")

    out = EvalRun(
        side="baseline",
        task_id=rec.task_id,
        final=getattr(agent, "final", None) if agent else None,
        task_row=row,
        error=("" if row.status == TaskStatus.COMPLETED.value else row.status),
    )
    # research run_id 读取（唯一 run）
    research_store = _get_research_store()
    rows = research_store.execute("SELECT run_id FROM research_runs", ())
    out.run_id = rows[0]["run_id"] if rows else None
    return out


def run_adaptive(
    gov_store,
    ctl,
    *,
    task_query: str,
    session_id: Optional[str] = None,
    graph_runner=None,
    judge_model=None,
    plan_model=None,
    search_tool=None,
    verifier=None,
    store=None,
    policy: Optional[dict[str, Any]] = None,
    adaptive_enabled: bool = True,
    preseed_run_id: Optional[str] = None,
) -> EvalRun:
    """治理 execute 内跑 f9_orchestrator（注入 scripted seam）。

    preseed_run_id：若提供，ctl.create_task(run_id=preseed_run_id) 使 TaskRecord.run_id
    = 既有 run；orchestrator create_run_and_root(run_id=…) 因 run 已存在返回 None →
    走 provenance 复用分支（Batch6 controller seam 已验证）。场景需先自行建 run/root。
    """
    import asyncio

    from app.f9.orchestrator import f9_orchestrator
    from app.runtime.governance import store as gs
    from app.runtime.governance.models import TaskStatus

    sid = session_id or f"b7-adaptive-{_u()}"
    rec = (
        ctl.create_task(sid, run_id=preseed_run_id)
        if preseed_run_id
        else ctl.create_task(sid)
    )
    sink = {"finalize": 0, "status": [], "content": None}

    def finalize_sink(run_id: str, content: str):
        sink["finalize"] += 1
        sink["content"] = content

    def status_sink(run_id: str, status: str):
        sink["status"].append(status)

    async def _run():
        async def _coro():
            return await f9_orchestrator(
                task_query,
                sid,
                graph_runner=graph_runner,
                judge_model=judge_model,
                plan_model=plan_model,
                search_tool=search_tool,
                verifier=verifier,
                store=store,
                create_run=True,
                finalize_sink=finalize_sink,
                status_sink=status_sink,
                adaptive_enabled=adaptive_enabled,
            )

        return await ctl.execute(rec.task_id, _coro(), policy=policy or {})

    summary = asyncio.run(_run())
    row = gs.get_task(gov_store, rec.task_id)

    out = EvalRun(
        side="adaptive",
        task_id=rec.task_id,
        run_id=summary.get("run_id"),
        final=sink["content"],
        summary=summary,
        task_row=row,
        error=("" if row.status == TaskStatus.COMPLETED.value else row.status),
    )
    return out


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def _get_research_store():
    from app.research import store as rstore

    s = rstore.get_store()
    if s is None:
        raise RuntimeError("research store 不可用")
    return s


def collect_research_metrics(run_id: Optional[str]) -> dict[str, Any]:
    """对 run_id 只读汇总 research metrics（registry 直查，确定性键排序）。"""
    if not run_id:
        return {"run_id": None}
    store = _get_research_store()

    def one(sql: str, params=()):
        rows = store.execute(sql, params)
        return rows

    metrics: dict[str, Any] = {"run_id": run_id}
    metrics["evidence_count"] = len(
        one("SELECT evidence_id FROM evidences WHERE run_id=%s", (run_id,))
    )
    metrics["source_count"] = len(
        one("SELECT DISTINCT source_id FROM sources WHERE run_id=%s", (run_id,))
    )
    metrics["claim_count"] = len(
        one("SELECT claim_id FROM claims WHERE run_id=%s", (run_id,))
    )
    metrics["verification_succeeded"] = len(
        one(
            "SELECT verification_id FROM verifications WHERE run_id=%s "
            "AND status='succeeded'",
            (run_id,),
        )
    )
    metrics["conflicts_confirmed"] = len(
        one(
            "SELECT conflict_id FROM conflicts WHERE run_id=%s AND status='confirmed'",
            (run_id,),
        )
    )
    metrics["conflicts_unresolved"] = len(
        one(
            "SELECT conflict_id FROM conflicts WHERE run_id=%s AND status='confirmed' "
            "AND conflict_id NOT IN "
            "(SELECT conflict_id FROM reconciliations WHERE run_id=%s AND status='resolved')",
            (run_id, run_id),
        )
    )
    metrics["bindings"] = len(
        one(
            "SELECT binding_id FROM claim_evidences ce JOIN claims c "
            "ON ce.claim_id=c.claim_id WHERE c.run_id=%s",
            (run_id,),
        )
    )
    return metrics


def collect_cost(task_row) -> dict[str, Any]:
    """cost：从 governance TaskRecord 读 counters_snapshot + effective_limits。"""
    snap = getattr(task_row, "counters_snapshot", None) or {}
    limits = getattr(task_row, "effective_limits", None) or {}
    return {"counters": dict(snap), "limits": dict(limits)}


def required_coverage(run_id: Optional[str], required_sq_evidence: int = 1) -> dict:
    """required coverage：required sub-question 是否有 ≥N evidence（只读）。"""
    if not run_id:
        return {"required_sq": [], "covered": []}
    store = _get_research_store()
    sqs = store.execute(
        "SELECT sub_question_id, position FROM sub_questions WHERE run_id=%s "
        "AND parent_id IS NULL ORDER BY position",
        (run_id,),
    )
    counts = store.execute(
        "SELECT sub_question_id, COUNT(*) AS n FROM evidences "
        "WHERE run_id=%s GROUP BY sub_question_id",
        (run_id,),
    )
    by_sq = {r["sub_question_id"]: int(r["n"]) for r in counts}
    covered = [
        s["sub_question_id"]
        for s in sqs
        if by_sq.get(s["sub_question_id"], 0) >= required_sq_evidence
    ]
    return {
        "required_sq": [s["sub_question_id"] for s in sqs],
        "covered": covered,
        "all_covered": bool(sqs) and len(covered) == len(sqs),
    }
