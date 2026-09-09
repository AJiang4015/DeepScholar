"""F9-P0 Batch 1 — 测试共享 seeding helpers（非收集模块；sqlite 与 postgres 共用）。

全部直接 INSERT（显式 id/时间戳）以便跨后端构造**逻辑+物理完全相同**的 state，
用于 cross-DB 顺序/语义等价断言；绕过 registry 仅限测试数据准备，不测 registry。
"""

from __future__ import annotations

import datetime
import hashlib
from typing import Any, Optional

from app.research.store import json_param


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def iso(dt: str) -> str:
    """接受 'YYYY-MM-DDTHH:MM:SS+00:00' 原样返回（也可直接传 datetime）。"""
    if isinstance(dt, datetime.datetime):
        return dt.isoformat()
    return dt


def insert_run(
    store: Any, run_id: str, *, thread_id: str = "t", question: str = "q"
) -> None:
    with store.transaction() as tx:
        tx.execute(
            "INSERT INTO research_runs (run_id, thread_id, question, status, started_at, "
            "finished_at, plan, budget, metadata) "
            "VALUES (%s,%s,%s,%s,%s,NULL,NULL,NULL,%s)",
            (
                run_id,
                thread_id,
                question,
                "running",
                "2026-09-01T08:00:00+00:00",
                json_param({}, store.dialect),
            ),
        )


def insert_subq(
    store: Any,
    run_id: str,
    sq_id: str,
    *,
    position: int,
    question: str,
    status: str = "planned",
    parent_id: Optional[str] = None,
) -> None:
    with store.transaction() as tx:
        tx.execute(
            "INSERT INTO sub_questions (sub_question_id, run_id, parent_id, position, "
            "question, rationale, status, assigned_agent) "
            "VALUES (%s,%s,%s,%s,%s,NULL,%s,NULL)",
            (sq_id, run_id, parent_id, position, question, status),
        )


def insert_query(
    store: Any,
    run_id: str,
    query_id: str,
    sq_id: str,
    *,
    query: str = "query",
    fetched_at: str = "2026-09-01T09:00:00+00:00",
) -> None:
    with store.transaction() as tx:
        tx.execute(
            "INSERT INTO search_queries (query_id, run_id, sub_question_id, query, agent, "
            "tool, topic, seq, fetched_at, metadata) "
            "VALUES (%s,%s,%s,%s,%s,%s,NULL,NULL,%s,%s)",
            (
                query_id,
                run_id,
                sq_id,
                query,
                "network_search",
                "internet_search",
                fetched_at,
                json_param({}, store.dialect),
            ),
        )


def insert_source(
    store: Any,
    run_id: str,
    source_id: str,
    query_id: str,
    *,
    url: Optional[str] = None,
    source_type: str = "web",
    title: str = "T",
    locator: Optional[str] = None,
    canonical_key: Optional[str] = None,
    fetched_at: str = "2026-09-01T09:05:00+00:00",
) -> None:
    with store.transaction() as tx:
        tx.execute(
            "INSERT INTO sources (source_id, run_id, query_id, source_type, title, "
            "canonical_url, locator, canonical_key, fetched_at, agent, publisher, "
            "published_at, metadata) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,NULL,%s)",
            (
                source_id,
                run_id,
                query_id,
                source_type,
                title,
                url,
                locator or (url or source_id),
                canonical_key or (url or f"{source_type}:{source_id}"),
                fetched_at,
                "network_search",
                json_param({}, store.dialect),
            ),
        )


def insert_evidence(
    store: Any,
    run_id: str,
    evidence_id: str,
    source_id: str,
    sq_id: str,
    *,
    content: str,
    created_at: Optional[str] = None,
    locator: Optional[str] = None,
) -> None:
    with store.transaction() as tx:
        tx.execute(
            "INSERT INTO evidences (evidence_id, run_id, source_id, sub_question_id, "
            "content, locator, extraction_method, metadata, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                evidence_id,
                run_id,
                source_id,
                sq_id,
                content,
                locator or (content[:50] or evidence_id),
                "web_result",
                json_param({}, store.dialect),
                iso(created_at) if created_at else None,
            ),
        )


def insert_claim(
    store: Any,
    run_id: str,
    claim_id: str,
    sq_id: str,
    *,
    statement: str,
    created_at: str,
    claim_type: str = "FACT",
    status: str = "drafted",
) -> None:
    with store.transaction() as tx:
        tx.execute(
            "INSERT INTO claims (claim_id, run_id, sub_question_id, statement, "
            "statement_sha, claim_type, status, created_at, metadata) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                claim_id,
                run_id,
                sq_id,
                statement,
                sha256_hex(statement),
                claim_type,
                status,
                iso(created_at),
                json_param({}, store.dialect),
            ),
        )


def insert_binding(
    store: Any,
    run_id: str,
    binding_id: str,
    claim_id: str,
    evidence_id: str,
    *,
    created_at: str = "2026-09-01T12:00:00+00:00",
) -> None:
    with store.transaction() as tx:
        tx.execute(
            "INSERT INTO claim_evidences (binding_id, run_id, claim_id, evidence_id, "
            "created_at, metadata) VALUES (%s,%s,%s,%s,%s,%s)",
            (
                binding_id,
                run_id,
                claim_id,
                evidence_id,
                iso(created_at),
                json_param({}, store.dialect),
            ),
        )


def insert_verification(
    store: Any,
    run_id: str,
    verification_id: str,
    claim_id: str,
    evidence_id: str,
    *,
    verdict: Optional[str],
    status: str,
    created_at: str,
    fingerprint: Optional[str] = None,
) -> None:
    fp = fingerprint or f"fp-{verification_id}"
    with store.transaction() as tx:
        tx.execute(
            "INSERT INTO verifications (verification_id, run_id, claim_id, evidence_id, "
            "verifier_spec, verifier_fingerprint, verdict, rationale, confidence, status, "
            "error, created_at, completed_at, metadata) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,NULL,NULL,%s,NULL,%s,%s,%s)",
            (
                verification_id,
                run_id,
                claim_id,
                evidence_id,
                json_param({}, store.dialect),
                fp,
                verdict,
                status,
                iso(created_at),
                iso(created_at) if status == "succeeded" else None,
                json_param({}, store.dialect),
            ),
        )


def insert_conflict(
    store: Any,
    run_id: str,
    conflict_id: str,
    claim_id: str,
    evidence_a_id: str,
    evidence_b_id: str,
    *,
    conflict_type: Optional[str],
    genuine: bool,
    status: str = "confirmed",
    created_at: str = "2026-09-01T15:00:00+00:00",
    fingerprint: Optional[str] = None,
) -> None:
    fp = fingerprint or f"fp-{conflict_id}"
    with store.transaction() as tx:
        tx.execute(
            "INSERT INTO conflicts (conflict_id, run_id, claim_id, evidence_a_id, "
            "evidence_b_id, detector_spec, detector_fingerprint, candidate_source, status, "
            "conflict_type, genuine, rationale, error, created_at, completed_at, metadata) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,NULL,%s,%s,%s)",
            (
                conflict_id,
                run_id,
                claim_id,
                evidence_a_id,
                evidence_b_id,
                json_param({}, store.dialect),
                fp,
                "detector",
                status,
                conflict_type,
                bool(genuine),
                iso(created_at),
                iso(created_at) if status == "confirmed" else None,
                json_param({}, store.dialect),
            ),
        )


def insert_reconciliation(
    store: Any,
    run_id: str,
    reconciliation_id: str,
    claim_id: str,
    conflict_id: str,
    *,
    status: str,
    outcome: Optional[str],
    computed_at: str = "2026-09-01T16:00:00+00:00",
    fingerprint: str = "fp-rec",
) -> None:
    with store.transaction() as tx:
        tx.execute(
            "INSERT INTO reconciliations (reconciliation_id, run_id, claim_id, conflict_id, "
            "method_spec, method_fingerprint, status, outcome, detail, error, computed_at, "
            "metadata) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NULL,NULL,%s,%s)",
            (
                reconciliation_id,
                run_id,
                claim_id,
                conflict_id,
                json_param({}, store.dialect),
                fingerprint,
                status,
                outcome,
                iso(computed_at),
                json_param({}, store.dialect),
            ),
        )


def insert_corroboration(
    store: Any,
    run_id: str,
    corroboration_id: str,
    claim_id: str,
    *,
    status: str,
    support: Optional[dict[str, Any]],
    computed_at: str = "2026-09-01T17:00:00+00:00",
    fingerprint: str = "fp-corr",
) -> None:
    with store.transaction() as tx:
        tx.execute(
            "INSERT INTO corroborations (corroboration_id, run_id, claim_id, method_spec, "
            "method_fingerprint, status, error, computed_at, metadata, global_clusters, "
            "support, contradict, conflicts_independence, source_profile) "
            "VALUES (%s,%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s,%s,%s,%s)",
            (
                corroboration_id,
                run_id,
                claim_id,
                json_param({}, store.dialect),
                fingerprint,
                status,
                iso(computed_at),
                json_param({}, store.dialect),
                json_param([], store.dialect),
                json_param(support or {}, store.dialect),
                json_param({}, store.dialect),
                json_param([], store.dialect),
                json_param({}, store.dialect),
            ),
        )


def purge_run(store: Any, run_id: str) -> None:
    """级联清理单个 run（FK ON DELETE CASCADE）。"""
    with store.transaction() as tx:
        tx.execute("DELETE FROM research_runs WHERE run_id = %s", (run_id,))


def seed_rich_scenario(store: Any, run_id: str, *, reverse: bool = False) -> None:
    """rich scenario（固定 id/时间戳；reverse=True 打乱插入顺序）。

    内容：root sq(covered) + child sq；3 evidence（2 domain）；2 claims（同一 created_at，
    靠 claim_id 打破平局）；verifications（succeeded+failed、同刻双 succeeded 按 id 取末）；
    3 conflicts（无 reconciliation / GENUINE_CONTESTED / DETAIL_INCONSISTENCY 缓解）；
    1 complete corroboration（support.independent_count=2）。
    """
    insert_run(store, run_id, question="rich scenario?")
    insert_subq(
        store, run_id, "sq-root", position=0, question="root q", status="planned"
    )
    insert_subq(
        store, run_id, "sq-child", position=1, question="child q", parent_id="sq-root"
    )

    def order(items):
        return reversed(items) if reverse else items

    for query_id, sq_id, s_id, url, t in order(
        [
            (
                "q-a",
                "sq-root",
                "s-a",
                "https://www.example.com/a",
                "2026-09-01T09:00:00+00:00",
            ),
            (
                "q-b",
                "sq-child",
                "s-b",
                "https://news.bbc.co.uk/x",
                "2026-09-01T09:30:00+00:00",
            ),
        ]
    ):
        insert_query(store, run_id, query_id, sq_id, fetched_at=t)
        insert_source(
            store, run_id, s_id, query_id, url=url, title=f"title-{s_id}", fetched_at=t
        )
    insert_evidence(
        store,
        run_id,
        "e-a1",
        "s-a",
        "sq-root",
        content="alpha one",
        created_at="2026-09-02T10:00:00+00:00",
    )
    insert_evidence(
        store,
        run_id,
        "e-a2",
        "s-a",
        "sq-root",
        content="alpha two",
        created_at="2026-09-02T10:05:00+00:00",
    )
    insert_evidence(
        store,
        run_id,
        "e-b1",
        "s-b",
        "sq-child",
        content="bravo",
        created_at="2026-09-02T11:00:00+00:00",
    )
    insert_evidence(
        store,
        run_id,
        "e-a3",
        "s-a",
        "sq-root",
        content="alpha three",
        created_at="2026-09-02T11:30:00+00:00",
    )
    # claims 同一 created_at → (created_at, claim_id) 排序平局由 id 打破
    for cid in order(["c-1", "c-2"]):
        insert_claim(
            store,
            run_id,
            cid,
            "sq-root" if cid == "c-1" else "sq-child",
            statement=f"statement {cid}",
            created_at="2026-09-02T12:00:00+00:00",
        )
    for bid, cid, eid in order(
        [
            ("b-1", "c-1", "e-a1"),
            ("b-2", "c-1", "e-a2"),
            ("b-3", "c-2", "e-b1"),
            ("b-4", "c-1", "e-a3"),
        ]
    ):
        insert_binding(store, run_id, bid, cid, eid)
    # c-1：succeeded SUPPORTS(13:00) + failed CONTRADICTS(14:00) → best_verdict=SUPPORTS
    insert_verification(
        store,
        run_id,
        "v-1",
        "c-1",
        "e-a1",
        verdict="SUPPORTS",
        status="succeeded",
        created_at="2026-09-02T13:00:00+00:00",
    )
    insert_verification(
        store,
        run_id,
        "v-2",
        "c-1",
        "e-a2",
        verdict="CONTRADICTS",
        status="failed",
        created_at="2026-09-02T14:00:00+00:00",
    )
    # c-2：同刻双 succeeded → (created_at, verification_id) 取 id 较大者 v-z
    insert_verification(
        store,
        run_id,
        "v-az",
        "c-2",
        "e-b1",
        verdict="SUPPORTS",
        status="succeeded",
        created_at="2026-09-02T13:30:00+00:00",
    )
    insert_verification(
        store,
        run_id,
        "v-zz",
        "c-2",
        "e-b1",
        verdict="INSUFFICIENT",
        status="succeeded",
        created_at="2026-09-02T13:30:00+00:00",
    )
    # conflicts：f-1 无 reconciliation（未缓解）；f-2 GENUINE_CONTESTED（未缓解）；
    # f-3 DETAIL_INCONSISTENCY（缓解，不进输出）
    for fid, ea, eb, ctype, genuine in order(
        [
            ("f-1", "e-a1", "e-a2", "CONTRADICTION", True),
            ("f-2", "e-a1", "e-a3", "CONTRADICTION", True),
            ("f-3", "e-a2", "e-a3", "INCONSISTENCY", True),
        ]
    ):
        insert_conflict(
            store,
            run_id,
            fid,
            "c-1",
            ea,
            eb,
            conflict_type=ctype,
            genuine=genuine,
            created_at="2026-09-02T15:00:00+00:00",
        )
    insert_reconciliation(
        store,
        run_id,
        "r-2",
        "c-1",
        "f-2",
        status="complete",
        outcome="GENUINE_CONTESTED",
        computed_at="2026-09-02T16:00:00+00:00",
    )
    insert_reconciliation(
        store,
        run_id,
        "r-3",
        "c-1",
        "f-3",
        status="complete",
        outcome="DETAIL_INCONSISTENCY",
        computed_at="2026-09-02T16:05:00+00:00",
    )
    insert_corroboration(
        store,
        run_id,
        "co-1",
        "c-1",
        status="complete",
        support={"source_count": 2, "independent_count": 2, "cluster_ids": ["a", "b"]},
        computed_at="2026-09-02T17:00:00+00:00",
    )
