"""F9-P0 Batch 5 — Targeted Research + Incremental F3 Verification（单 F8 execute 内；不执行 Batch6）。

裁决记录（用户 2026-09-28）：D-A 直接调用既有 `internet_search` 工具（不建第二 Agent/Graph/
ResearchRun；不触发 F7）；D-B 每个 target claim 至多 8 个本轮新增 binding 作 verification
candidate；D-C′ 经 F3 public `semantic_verify_claim(verifier=...)` 注入 seam 提供
`GovernedRealVerifier` adapter（真实 LLM 挂 F8 GovernanceHandler，禁止改 F3/F8）；D-D 本批只做
F3 增量，F4–F6 编排归 Batch6。

不变式：
- 单 run_id / 单 ResearchRun / 单 GovernanceExecution / BudgetCounter / deadline / cancel；
- 不 create_run_and_root / 不 set_run_status / 不调 F7；
- 只调用 F1–F6 public API + 既有 internet_search 的自身 ingest（不重实现 SearchQuery/Source/
  Evidence ingestion）；DB 读取仅用于 snapshot diff（只读 SELECT）；
- snapshot 语义：new Evidence ≠ new Claim–Evidence Binding；incremental candidate = **本轮实际
  新增 binding**（对 target claim），重复 binding 幂等不重复计候选；
- F8 control（GovernanceLimitExceeded / CancelledError / timeout）不捕获、向 Controller 传播；
- ordinary（tool/verifier/malformed/no candidate）→ 记入 outcome，不中断后续合法工作；
- 输出 bounded/deterministic（无随机/无 wall-clock），不产下一轮 Plan、不实现 Orchestrator。
"""

from __future__ import annotations

import asyncio
import datetime
from typing import Any, Optional

from app.research import registry, store as rstore
from app.research import context as research_ctx
from app.research.verify import (
    DEFAULT_SPEC,
    VerifyBudget,
    semantic_verify_claim,
)

from app.runtime.governance.callbacks import GovernanceCallbackHandler
from app.runtime.governance.context import GovernanceExecution, get_governance_execution
from app.runtime.governance.counters import GovernanceLimitExceeded

#: D-B：每个 target claim 本轮新增 binding（= verification candidate）上限（Batch8 校准前固定）
MAX_VERIFY_CANDIDATES_PER_CLAIM: int = 8

#: 允许执行的 query 状态（Batch4 已剔除 duplicate；只跑 accepted/retry）
_EXECUTABLE_STATUSES = frozenset({"accepted", "retry"})

#: 结果上限常量（bounded；非冻结协议值）
MAX_CLAIMS_RESULT: int = 32
MAX_QUERY_RESULT: int = 32


class TargetedError(Exception):
    """Batch5 普通错误/输入契约错误（run/plan 缺键、store 不可用等）。

    F8 control 信号不包装为本异常（必须原样传播）。
    """


class GovernedRealVerifier:
    """D-C′：F3 BaseVerifier protocol 的 governed real-LLM adapter（不改 F3 RealLLMVerifier）。

    respond() 以 LangChain ChatModel 同步 invoke 并挂当前 GovernanceHandler（on_llm_start →
    llm_calls +1 / agent_steps +0）。F3 的 parse/normalize/fingerprint/idempotency 语义不变
    （respond 只返回原始文本）。
    """

    def __init__(
        self,
        handler: GovernanceCallbackHandler,
        *,
        model: Any = None,
        spec: Any = None,
    ) -> None:
        import os  # noqa: PLC0415

        if os.getenv("VERIFY_REAL_LLM", "0") != "1" and model is None:
            raise RuntimeError(
                "GovernedRealVerifier 需显式设置 VERIFY_REAL_LLM=1（或测试注入 model）"
            )
        self._handler = handler
        self._spec = spec or DEFAULT_SPEC
        if model is not None:
            self._model = model
        else:
            from langchain_openai import ChatOpenAI  # noqa: PLC0415

            self._model = ChatOpenAI(
                model=self._spec.model,
                temperature=getattr(self._spec, "temperature", None),
                max_tokens=getattr(self._spec, "max_tokens", None),
            )

    def respond(self, payload: dict[str, Any], hint: str = "") -> str:
        """与 F3 RealLLMVerifier 同职责：返回原始文本；verdict 解析在 F3 内完成。"""
        system = _F3_VERIFY_INSTRUCTION
        if hint:
            system = system + f"\n{hint}"
        from langchain_core.messages import HumanMessage, SystemMessage  # noqa: PLC0415

        messages = [
            SystemMessage(content=system),
            HumanMessage(content=__import__("json").dumps(payload, ensure_ascii=False)),
        ]
        response = self._model.invoke(messages, config={"callbacks": [self._handler]})
        content = getattr(response, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("verifier empty response")
        return content.strip()


#: F3 real 路径的验证指令（只读引用 F3 内部常量，禁止复制语义/改动 F3；若 F3 移除则构造抛错）
try:
    from app.research.verify import _INSTRUCTION as _F3_VERIFY_INSTRUCTION  # noqa: PLC0415
except Exception:  # pragma: no cover - F3 内部变化时 fail-fast
    _F3_VERIFY_INSTRUCTION = (
        "Return a JSON object with keys verdict, rationale, confidence judging whether the "
        "evidence content supports the claim statement."
    )


def _ts(value: Any) -> Optional[datetime.datetime]:
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        dt = value
    else:
        try:
            dt = datetime.datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    else:
        dt = dt.astimezone(datetime.timezone.utc)
    return dt


def _iso(value: Any) -> str:
    dt = _ts(value)
    return dt.isoformat() if dt is not None else str(value)


def _ev_rows(store: Any, run_id: str) -> list[dict[str, Any]]:
    rows = store.execute(
        "SELECT evidence_id, created_at FROM evidences WHERE run_id = %s", (run_id,)
    )
    out = [dict(r) for r in rows]
    out.sort(key=lambda r: (_iso(r.get("created_at")), str(r["evidence_id"])))
    return out


def _src_rows(store: Any, run_id: str) -> list[dict[str, Any]]:
    rows = store.execute(
        "SELECT source_id, fetched_at FROM sources WHERE run_id = %s", (run_id,)
    )
    out = [dict(r) for r in rows]
    out.sort(key=lambda r: (_iso(r.get("fetched_at")), str(r["source_id"])))
    return out


def _bound_ids(store: Any, claim_id: str) -> set[str]:
    rows = store.execute(
        "SELECT evidence_id FROM claim_evidences WHERE claim_id = %s", (claim_id,)
    )
    return {str(r["evidence_id"]) for r in rows}


def evidence_snapshot(store: Any, run_id: str) -> dict[str, Any]:
    return {
        "evidences": [r["evidence_id"] for r in _ev_rows(store, run_id)],
        "sources": [r["source_id"] for r in _src_rows(store, run_id)],
    }


def _diff(prev: list[str], cur: list[str]) -> list[str]:
    return [eid for eid in cur if eid not in set(prev)]


def _sorted_new(new_ids: set[str], rows: list[dict[str, Any]]) -> list[str]:
    return [r["evidence_id"] for r in rows if r["evidence_id"] in new_ids]


async def _run_query(
    query: str,
    *,
    run_id: str,
    sub_question_id: str,
    tool: Any,
    handler: Optional[GovernanceCallbackHandler],
    store: Any,
) -> dict[str, Any]:
    """执行单条 targeted query（D-A：既有 internet_search seam；工具自带 ingest）。"""
    token_run, token_sq = research_ctx.set_research_context(run_id, sub_question_id)
    try:
        before = evidence_snapshot(store, run_id)
        if handler is not None:
            await tool.ainvoke({"query": query}, config={"callbacks": [handler]})
        else:
            await tool.ainvoke({"query": query})
    finally:
        research_ctx.reset_research_context(token_run, token_sq)
    after = evidence_snapshot(store, run_id)
    return {
        "evidence_added": _diff(before["evidences"], after["evidences"]),
        "sources_added": _diff(before["sources"], after["sources"]),
    }


def bind_new_evidence(
    run_id: str,
    claim_id: str,
    new_evidence_ids: list[str],
    *,
    store: Any,
    cap: int = MAX_VERIFY_CANDIDATES_PER_CLAIM,
) -> tuple[list[str], list[dict[str, str]]]:
    """本轮新增 binding（幂等）：跳过已绑定/本次已加；cap=D-B；返回 (newly, errors)。

    candidate 语义：只有本 executor **实际新增的 binding** 才成为 verification candidate
    （new Evidence ≠ new Binding；重复 binding 幂等不重复计候选）。
    """
    prior = _bound_ids(store, claim_id)
    newly: list[str] = []
    errors: list[dict[str, str]] = []
    for eid in new_evidence_ids:
        if eid in prior or eid in newly:
            continue  # 已绑定（幂等）或本轮已加 → 不重复候选
        try:
            registry.bind_claim_evidence(run_id, claim_id, eid)
        except Exception as exc:  # noqa: BLE001 — ordinary binding guard failure
            errors.append(
                {
                    "kind": "bind_failed",
                    "detail": f"claim={claim_id} evidence={eid}: {str(exc)[:200]}",
                }
            )
            continue
        newly.append(eid)
        if len(newly) >= cap:
            break
    return newly, errors


async def execute_targeted_plan(
    plan: dict[str, Any],
    *,
    run_id: str,
    search_tool: Any = None,
    verifier: Any = None,
    verify_spec: Any = None,
    store: Any = None,
) -> dict[str, Any]:
    """Batch5 主入口：执行 plan 的 accepted/retry queries → 本轮新增 binding → 增量 F3 verify。

    search_tool=None → 惰性导入既有 internet_search（D-A；避免模块导入期依赖 TAVILY_API_KEY）。
    须在 F8 governed execution 内调用（query 计费）；research ctx 由本函数按 query 设置/
    复位（同 run_id）。返回 bounded TargetedRoundResult（见模块 docstring / Batch5 report）。
    """
    if not isinstance(plan, dict):
        raise TargetedError("plan 必须为 dict（Batch4 ValidatedPlan）")
    if search_tool is None:
        # D-A：默认既有 internet_search；惰性导入（模块导入期不依赖 TAVILY_API_KEY env）。
        try:
            from app.tools.tavily_tool import internet_search as _default_tool  # noqa: PLC0415

            search_tool = _default_tool
        except Exception as exc:  # noqa: BLE001 — env/依赖缺失
            raise TargetedError(
                f"默认 internet_search 不可用（需 TAVILY_API_KEY / tavily 依赖）: {exc}"
            ) from exc
    for key in (
        "target_sub_question_id",
        "queries",
        "verification_target_claim_ids",
        "gap_refs",
        "objective",
        "priority",
        "plan_id",
    ):
        if key not in plan:
            raise TargetedError(f"plan 缺少键: {key}（Batch4 契约）")
    queries = plan["queries"]
    if not isinstance(queries, list) or not queries:
        raise TargetedError("plan.queries 必须为非空 list")
    target_sq = plan["target_sub_question_id"]
    claims = [
        c for c in plan["verification_target_claim_ids"] or [] if isinstance(c, str)
    ]
    claims = sorted(set(claims))
    if len(claims) > MAX_CLAIMS_RESULT:
        claims = claims[:MAX_CLAIMS_RESULT]

    effective_store = rstore.get_store() if store is None else store
    if effective_store is None:
        raise TargetedError("research store 不可用（Batch5 拒绝空状态伪造）")

    execution: Optional[GovernanceExecution] = get_governance_execution()
    handler: Optional[GovernanceCallbackHandler] = None
    if execution is not None:
        handler = execution.make_handler()

    executed_queries: list[dict[str, Any]] = []
    added_evidence: set[str] = set()
    failures: list[dict[str, str]] = []
    for q in queries[:MAX_QUERY_RESULT]:
        qid = q.get("dedup_identity", "")
        kind = q.get("kind", "research_query")
        query_text = q.get("query")
        if q.get("status") not in _EXECUTABLE_STATUSES:
            executed_queries.append(
                {
                    "query": query_text,
                    "dedup_identity": qid,
                    "kind": kind,
                    "status": "skipped_duplicate",
                    "sources_added": 0,
                    "evidence_added": 0,
                    "error": None,
                }
            )
            continue
        if kind != "research_query":
            # re_verify 属独立 namespace / 已有 evidence 复验 feature：Batch5 不执行，跳过记录
            executed_queries.append(
                {
                    "query": query_text,
                    "dedup_identity": qid,
                    "kind": kind,
                    "status": "skipped_duplicate",
                    "sources_added": 0,
                    "evidence_added": 0,
                    "error": "re_verify namespace not in Batch5 scope",
                }
            )
            continue
        if not isinstance(query_text, str) or not query_text.strip():
            failures.append({"kind": "invalid_query", "detail": str(query_text)[:200]})
            continue
        try:
            outcome = await _run_query(
                query_text,
                run_id=run_id,
                sub_question_id=target_sq,
                tool=search_tool,
                handler=handler,
                store=effective_store,
            )
        except GovernanceLimitExceeded:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — ordinary tool/provider failure
            executed_queries.append(
                {
                    "query": query_text,
                    "dedup_identity": qid,
                    "kind": kind,
                    "status": "failed",
                    "sources_added": 0,
                    "evidence_added": 0,
                    "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                }
            )
            continue
        ev_ids = outcome["evidence_added"]
        added_evidence.update(ev_ids)
        executed_queries.append(
            {
                "query": query_text,
                "dedup_identity": qid,
                "kind": kind,
                "status": "executed",
                "sources_added": len(outcome["sources_added"]),
                "evidence_added": len(ev_ids),
                "error": None,
            }
        )

    # ---- 本轮新增 binding（只对 target claims；幂等；cap=D-B 8）----
    ev_rows = _ev_rows(effective_store, run_id)
    sorted_new = _sorted_new(added_evidence, ev_rows)
    bound_by_claim: dict[str, list[str]] = {}
    for claim_id in claims:
        newly, bind_errors = bind_new_evidence(
            run_id, claim_id, sorted_new, store=effective_store
        )
        failures.extend(bind_errors)
        bound_by_claim[claim_id] = newly

    # ---- 增量 F3 verification（candidates = 本轮新增 binding；cap 8）----
    verification: list[dict[str, Any]] = []
    effective_verifier = verifier
    if effective_verifier is None and handler is not None:
        try:
            effective_verifier = GovernedRealVerifier(handler, spec=verify_spec)
        except RuntimeError:
            effective_verifier = None  # VERIFY_REAL_LLM 未启用 → F3 默认 Fake
    for claim_id in claims:
        newly = bound_by_claim[claim_id]
        if not newly:
            verification.append(
                {
                    "claim_id": claim_id,
                    "status": "no_op",
                    "new_bindings": 0,
                    "candidates": 0,
                    "truncated": False,
                    "items": 0,
                    "aggregate": None,
                    "verdict_rows": [],
                }
            )
            continue
        truncated = len(newly) > MAX_VERIFY_CANDIDATES_PER_CLAIM
        allowed = newly[:MAX_VERIFY_CANDIDATES_PER_CLAIM]
        try:
            result = semantic_verify_claim(
                claim_id,
                spec=verify_spec,
                allowed_evidence_ids=allowed,
                budget=VerifyBudget(
                    max_evidence_per_claim=MAX_VERIFY_CANDIDATES_PER_CLAIM,
                    include_opinion=False,
                ),
                verifier=effective_verifier,
            )
        except GovernanceLimitExceeded:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — ordinary verifier/gate failure
            verification.append(
                {
                    "claim_id": claim_id,
                    "status": "failed",
                    "new_bindings": len(newly),
                    "candidates": len(allowed),
                    "truncated": truncated,
                    "items": 0,
                    "aggregate": None,
                    "verdict_rows": [],
                    "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                }
            )
            continue
        rows = []
        for item in result.get("items", []):
            rows.append(
                {
                    "evidence_id": item.get("evidence_id"),
                    "status": item.get("status"),
                    "verdict": item.get("verdict"),
                    "error": item.get("error"),
                    "reused": bool(item.get("reused", False)),
                }
            )
        verification.append(
            {
                "claim_id": claim_id,
                "status": "verified",
                "new_bindings": len(newly),
                "candidates": len(allowed),
                "truncated": truncated,
                "items": len(result.get("items", [])),
                "aggregate": result.get("aggregate"),
                "verdict_rows": rows,
            }
        )

    added_ids_sorted = sorted_new
    return {
        "run_id": run_id,
        "plan_id": plan["plan_id"],
        "executed_queries": executed_queries,
        "verification": verification,
        "added_evidence_ids": added_ids_sorted,
        "ledger_updates": [
            {
                "dedup_identity": x.get("dedup_identity", ""),
                "kind": x.get("kind", "research_query"),
                "outcome": "succeeded" if x.get("status") == "executed" else "failed",
                "sources_produced": bool(x.get("sources_added")),
            }
            for x in executed_queries
            if x.get("status") in ("executed", "failed")
        ],
        "failures": failures,
        "size": {
            "max_candidates_per_claim": MAX_VERIFY_CANDIDATES_PER_CLAIM,
            "truncated": bool(
                len(queries) > MAX_QUERY_RESULT or len(claims) > MAX_CLAIMS_RESULT
            ),
        },
    }


__all__ = [
    "TargetedError",
    "GovernedRealVerifier",
    "MAX_VERIFY_CANDIDATES_PER_CLAIM",
    "evidence_snapshot",
    "bind_new_evidence",
    "execute_targeted_plan",
]
