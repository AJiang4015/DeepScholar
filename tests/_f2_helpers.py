"""F2 测试共享 helpers（非收集模块）。"""

from __future__ import annotations

from app.research import provenance, registry
from app.research.normalize import canonical_key_for


def make_run(prefix: str = "f2"):
    """创建 research run + root sub_question，返回 (run_id, sub_question_id)。"""
    run_id, sqid = registry.create_run_and_root(f"{prefix}-thread", f"{prefix} 问题?")
    assert run_id and sqid
    return run_id, sqid


def add_web_evidence(
    run_id: str, sqid: str, content: str, url: str = "https://x.example/1"
):
    """F1 通路：query + web source + evidence；返回 (query_id, source_id, evidence_id)。"""
    qid = registry.record_search_query(
        run_id, sqid, agent="network_search", tool="internet_search", query="query"
    )
    sid = registry.upsert_source(
        run_id,
        qid,
        source_type="web",
        agent="network_search",
        title="X",
        locator=url,
        canonical_key=canonical_key_for("web", canonical_url=url),
        canonical_url=url,
    )
    eid = registry.append_evidence(
        run_id, sid, sqid, content=content, locator=url, extraction_method="web_result"
    )
    return qid, sid, eid


def subq_of(run_id: str) -> str:
    subs = provenance.list_sub_questions(run_id)
    assert subs, "需要 root sub_question"
    return subs[0].sub_question_id
