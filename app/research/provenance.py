"""Research provenance 查询（F1，只读函数层；HTTP 端点在后续阶段评估）。

全部显式 run 作用域（run isolation 纪律）；返回 pydantic 模型（model_dump 可用）。
"""

from __future__ import annotations

import datetime
from typing import Any, Optional

from app.research import store as _store
from app.research.schemas import (
    Claim,
    Citation,
    Evidence,
    ResearchRun,
    Source,
    SubQuestion,
)


def _get_store():
    return _store.get_store()


def _iso(value: Any) -> Any:
    """时间戳列双后端归一化：PG timestamptz 返回 datetime，sqlite 存 TEXT。

    读取边界统一转 ISO 字符串（与 pydantic 模型 str 字段及 JSON 契约一致）。
    """
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    return value


def get_run(run_id: str) -> Optional[ResearchRun]:
    store = _get_store()
    if store is None:
        return None
    rows = store.execute("SELECT * FROM research_runs WHERE run_id = %s", (run_id,))
    if not rows:
        return None
    row = rows[0]
    return ResearchRun(
        run_id=row["run_id"],
        thread_id=row["thread_id"],
        question=row["question"],
        status=row["status"],
        started_at=_iso(row["started_at"]),
        finished_at=_iso(row.get("finished_at")),
        plan=_maybe_json(row.get("plan")),
        budget=_maybe_json(row.get("budget")),
        metadata=_maybe_json(row.get("metadata")) or {},
    )


def list_sub_questions(run_id: str) -> list[SubQuestion]:
    store = _get_store()
    if store is None:
        return []
    rows = store.execute(
        "SELECT * FROM sub_questions WHERE run_id = %s ORDER BY position",
        (run_id,),
    )
    return [
        SubQuestion(
            sub_question_id=r["sub_question_id"],
            run_id=r["run_id"],
            parent_id=r.get("parent_id"),
            position=r["position"],
            question=r["question"],
            rationale=r.get("rationale"),
            status=r["status"],
            assigned_agent=r.get("assigned_agent"),
        )
        for r in rows
    ]


def list_sources(run_id: str, *, source_type: Optional[str] = None) -> list[Source]:
    store = _get_store()
    if store is None:
        return []
    if source_type:
        rows = store.execute(
            "SELECT * FROM sources WHERE run_id = %s AND source_type = %s "
            "ORDER BY fetched_at",
            (run_id, source_type),
        )
    else:
        rows = store.execute(
            "SELECT * FROM sources WHERE run_id = %s ORDER BY fetched_at", (run_id,)
        )
    return [
        Source(
            source_id=r["source_id"],
            run_id=r["run_id"],
            query_id=r["query_id"],
            source_type=r["source_type"],
            title=r["title"],
            canonical_url=r.get("canonical_url"),
            locator=r["locator"],
            canonical_key=r["canonical_key"],
            fetched_at=_iso(r["fetched_at"]),
            agent=r["agent"],
            publisher=r.get("publisher"),
            published_at=_iso(r.get("published_at")),
            metadata=_maybe_json(r.get("metadata")) or {},
        )
        for r in rows
    ]


def list_evidences(run_id: str, *, source_id: Optional[str] = None) -> list[Evidence]:
    store = _get_store()
    if store is None:
        return []
    if source_id:
        rows = store.execute(
            "SELECT * FROM evidences WHERE run_id = %s AND source_id = %s "
            "ORDER BY created_at",
            (run_id, source_id),
        )
    else:
        rows = store.execute(
            "SELECT * FROM evidences WHERE run_id = %s ORDER BY created_at",
            (run_id,),
        )
    return [
        Evidence(
            evidence_id=r["evidence_id"],
            run_id=r["run_id"],
            source_id=r["source_id"],
            sub_question_id=r["sub_question_id"],
            content=r["content"],
            locator=r["locator"],
            extraction_method=r["extraction_method"],
            metadata=_maybe_json(r.get("metadata")) or {},
            created_at=_iso(r.get("created_at")),
        )
        for r in rows
    ]


def get_evidence_chain(evidence_id: str) -> Optional[dict[str, Any]]:
    """反向反查：evidence → source → search_query → sub_question → run（五跳）。"""
    store = _get_store()
    if store is None:
        return None
    rows = store.execute(
        "SELECT * FROM evidences WHERE evidence_id = %s", (evidence_id,)
    )
    if not rows:
        return None
    ev = rows[0]

    def first(table: str, col: str, value: str) -> Optional[dict]:
        out = store.execute(f"SELECT * FROM {table} WHERE {col} = %s", (value,))
        return out[0] if out else None

    source = first("sources", "source_id", ev["source_id"])
    query = first("search_queries", "query_id", source["query_id"]) if source else None
    subq = (
        first("sub_questions", "sub_question_id", ev["sub_question_id"])
        if ev.get("sub_question_id")
        else None
    )
    run = first("research_runs", "run_id", ev["run_id"]) if ev.get("run_id") else None

    def _clean(row: Optional[dict]) -> Optional[dict]:
        """行级清洗：datetime → ISO 字符串（PG timestamptz），保证 JSON 可序列化。"""
        if row is None:
            return None
        out = {}
        for k, v in dict(row).items():
            if isinstance(v, datetime.datetime):
                out[k] = v.isoformat()
            elif isinstance(v, (dict, list)):
                out[k] = _maybe_json(v)
            else:
                out[k] = v
        return out

    return {
        "evidence": _clean(ev),
        "source": _clean(source),
        "search_query": _clean(query),
        "sub_question": _clean(subq),
        "run": _clean(run),
    }


# ---------------------------------------------------------------------------
# F2：Claim / Citation 读取与渲染（只读；呈现编号 [n] 不落库）
# ---------------------------------------------------------------------------
def list_claims(run_id: str) -> list[Claim]:
    store = _get_store()
    if store is None:
        return []
    rows = store.execute(
        "SELECT * FROM claims WHERE run_id = %s ORDER BY created_at, claim_id",
        (run_id,),
    )
    return [
        Claim(
            claim_id=r["claim_id"],
            run_id=r["run_id"],
            sub_question_id=r["sub_question_id"],
            statement=r["statement"],
            statement_sha=r["statement_sha"],
            claim_type=r["claim_type"],
            status=r["status"],
            created_at=_iso(r["created_at"]),
            metadata=_maybe_json(r.get("metadata")) or {},
        )
        for r in rows
    ]


def list_citations(run_id: str) -> list[Citation]:
    store = _get_store()
    if store is None:
        return []
    rows = store.execute(
        "SELECT * FROM citations WHERE run_id = %s ORDER BY created_at, citation_id",
        (run_id,),
    )
    return [
        Citation(
            citation_id=r["citation_id"],
            run_id=r["run_id"],
            claim_id=r["claim_id"],
            evidence_id=r["evidence_id"],
            quote=r.get("quote"),
            locator=r.get("locator"),
            metadata=_maybe_json(r.get("metadata")) or {},
            created_at=_iso(r["created_at"]),
        )
        for r in rows
    ]


def get_claim_chain(claim_id: str) -> Optional[dict[str, Any]]:
    """claim → bindings → evidences → sources；含该 claim 的 citations。"""
    store = _get_store()
    if store is None:
        return None
    rows = store.execute("SELECT * FROM claims WHERE claim_id = %s", (claim_id,))
    if not rows:
        return None
    claim_row = rows[0]

    def _clean(row: Optional[dict]) -> Optional[dict]:
        if row is None:
            return None
        out = {}
        for k, v in dict(row).items():
            if isinstance(v, datetime.datetime):
                out[k] = v.isoformat()
            elif isinstance(v, (dict, list)):
                out[k] = _maybe_json(v)
            else:
                out[k] = v
        return out

    bindings = store.execute(
        "SELECT * FROM claim_evidences WHERE claim_id = %s ORDER BY created_at, binding_id",
        (claim_id,),
    )
    evidence_support = []
    evidence_ids = [b["evidence_id"] for b in bindings]
    for eid in evidence_ids:
        ev_rows = store.execute(
            "SELECT * FROM evidences WHERE evidence_id = %s", (eid,)
        )
        if not ev_rows:
            continue
        ev = ev_rows[0]
        src_rows = store.execute(
            "SELECT * FROM sources WHERE source_id = %s", (ev["source_id"],)
        )
        evidence_support.append(
            {
                "evidence": _clean(ev),
                "source": _clean(src_rows[0] if src_rows else None),
            }
        )
    citations = store.execute(
        "SELECT * FROM citations WHERE claim_id = %s ORDER BY created_at, citation_id",
        (claim_id,),
    )
    return {
        "claim": _clean(claim_row),
        "evidences": evidence_support,
        "citations": [_clean(c) for c in citations],
    }


def render_citations(run_id: str) -> list[dict[str, Any]]:
    """确定性渲染编号（presentation identifier，不落库）。

    排序：created_at ASC, citation_id ASC；编号 = 排序后位置 1..N。
    新增 citation 不会改变既有 citation_id 的 artifact identity；历史快照各自冻结。
    """
    store = _get_store()
    if store is None:
        return []
    rows = store.execute(
        "SELECT citation_id, created_at FROM citations WHERE run_id = %s",
        (run_id,),
    )
    ordered = sorted(
        rows, key=lambda r: (_iso(r["created_at"]) or "", r["citation_id"])
    )
    return [
        {"citation_id": r["citation_id"], "number": i + 1}
        for i, r in enumerate(ordered)
    ]


def _maybe_json(value: Any) -> Any:
    """sqlite 存 JSON 文本，PG 返回 dict/jsonb；统一为可序列化对象。"""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        import json

        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return value
