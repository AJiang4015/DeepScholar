"""Research registry（F1 + F2）：artifact 写入入口（deterministic，fail-open）。

F1：ResearchRun/SubQuestion/SearchQuery/Source/Evidence（append / Source 幂等）。
F2：Claim / ClaimEvidence / Citation（docs/spec/2026-09-08-claim-citation-binding.md rev2）——
    claims 内容级幂等 (run_id, statement_sha)；binding (claim_id, evidence_id) 幂等；
    citation (run_id, claim_id, evidence_id) 幂等且要求先 binding（R2）；citation_id 为稳定
    artifact identity（无呈现编号列）；apply_validation_outcome 是 claim.status 唯一写入口。

所有写入显式携带 run_id。store 不可用（disabled/初始化失败）时公共函数 no-op 返回 None——
不改变 Agent 执行语义；完整性守卫错误（run/子问题/claim/evidence/binding 一致性、R2/R7）
显式上抛，调用方（finalizer/工具 guard）决定处理。

常量：Evidence content 上限 8000；metadata JSON 上限 2000；Claim statement 2000；
Citation quote 500 / locator 200。
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import re
from contextlib import contextmanager
from typing import Any, Optional

from app.research import ids, store as _store
from app.research.schemas import ClaimStatus, ClaimType
from app.research.store import json_param

logger = logging.getLogger("deepsearch.research.registry")

EV_CONTENT_MAX = 8000
METADATA_JSON_MAX = 2000
QUESTION_MAX = 2000
CLAIM_STATEMENT_MAX = 2000
QUOTE_MAX = 500
LOCATOR_MAX = 200
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class ResearchRunNotFoundError(Exception):
    """目标 run 不存在（写入被拒绝）。"""


def _utcnow_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _meta(value: dict[str, Any] | None) -> dict[str, Any]:
    return dict(value or {})


def _shrink_meta(value: dict[str, Any] | None) -> dict[str, Any]:
    """超限 metadata 降级为摘要（防 artifact 库膨胀）。"""
    payload = _meta(value)
    encoded = json.dumps(payload, ensure_ascii=False, default=str)
    if len(encoded) <= METADATA_JSON_MAX:
        return payload
    return {"_dropped": True, "_summary": encoded[:400]}


@contextmanager
def artifacts_guard(label: str):
    """F1 fail-open：研究面任何异常只记日志，绝不影响 Agent 主链路/工具返回值。"""
    try:
        yield
    except Exception as exc:  # noqa: BLE001
        logger.warning("[research] %s failed (fail-open): %s", label, exc)


def _require_run(store, run_id: str) -> None:
    rows = store.execute(
        "SELECT run_id FROM research_runs WHERE run_id = %s", (run_id,)
    )
    if not rows:
        raise ResearchRunNotFoundError(f"research run 不存在: {run_id}")


def create_run_and_root(
    thread_id: str,
    question: str,
    run_id: Optional[str] = None,
) -> Optional[tuple[str, str]]:
    """创建 ResearchRun + root SubQuestion（同事务）；返回 (run_id, sub_question_id)。"""
    store = _store.get_store()
    if store is None:
        return None
    rid = run_id or ids.new_run_id()
    sqid = ids.new_sub_question_id()
    now = _utcnow_iso()
    q = question[:QUESTION_MAX]
    try:
        with store.transaction() as tx:
            tx.execute(
                "INSERT INTO research_runs (run_id, thread_id, question, status, "
                "started_at, finished_at, plan, budget, metadata) "
                "VALUES (%s, %s, %s, %s, %s, NULL, NULL, NULL, %s)",
                (rid, thread_id, q, "running", now, json_param({}, store.dialect)),
            )
            tx.execute(
                "INSERT INTO sub_questions (sub_question_id, run_id, parent_id, "
                "position, question, rationale, status, assigned_agent) "
                "VALUES (%s, %s, NULL, 0, %s, NULL, 'planned', NULL)",
                (sqid, rid, q),
            )
    except Exception as exc:  # noqa: BLE001 - fail-open
        logger.warning("[research] create_run_and_root failed (fail-open): %s", exc)
        return None
    return rid, sqid


def set_run_status(run_id: str, status: str, finished_at: Optional[str] = None) -> None:
    store = _store.get_store()
    if store is None:
        return
    try:
        _require_run(store, run_id)
        ts = finished_at or _utcnow_iso()
        with store.transaction() as tx:
            tx.execute(
                "UPDATE research_runs SET status = %s, finished_at = %s "
                "WHERE run_id = %s",
                (
                    status,
                    ts if status in ("finished", "failed", "cancelled") else None,
                    run_id,
                ),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[research] set_run_status failed (fail-open): %s", exc)


def record_search_query(
    run_id: str,
    sub_question_id: str,
    *,
    agent: str,
    tool: str,
    query: str,
    topic: Optional[str] = None,
    seq: Optional[int] = None,
    metadata: Optional[dict[str, Any]] = None,
    fetched_at: Optional[str] = None,
) -> Optional[str]:
    """记录一次检索执行（append 语义），返回 query_id 或 None。"""
    store = _store.get_store()
    if store is None:
        return None
    try:
        _require_run(store, run_id)
        qid = ids.new_query_id()
        now = fetched_at or _utcnow_iso()
        with store.transaction() as tx:
            tx.execute(
                "INSERT INTO search_queries (query_id, run_id, sub_question_id, query, "
                "agent, tool, topic, seq, fetched_at, metadata) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    qid,
                    run_id,
                    sub_question_id,
                    query[:8000],
                    agent,
                    tool,
                    topic,
                    seq,
                    now,
                    json_param(_shrink_meta(metadata), store.dialect),
                ),
            )
        return qid
    except ResearchRunNotFoundError:
        raise  # 显式 API 错误；工具侧由 artifacts_guard 转 fail-open
    except Exception as exc:  # noqa: BLE001
        logger.warning("[research] record_search_query failed (fail-open): %s", exc)
        return None


def upsert_source(
    run_id: str,
    query_id: str,
    *,
    source_type: str,
    agent: str,
    title: str,
    locator: str,
    canonical_key: str,
    canonical_url: Optional[str] = None,
    fetched_at: Optional[str] = None,
    publisher: Optional[str] = None,
    published_at: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """幂等注册 Source：同 (run_id, source_type, canonical_key) 收敛到同一 source_id。"""
    store = _store.get_store()
    if store is None:
        return None
    try:
        _require_run(store, run_id)
        existing = store.execute(
            "SELECT source_id FROM sources "
            "WHERE run_id = %s AND source_type = %s AND canonical_key = %s",
            (run_id, source_type, canonical_key),
        )
        if existing:
            return existing[0]["source_id"]
        sid = ids.new_source_id()
        now = fetched_at or _utcnow_iso()
        try:
            with store.transaction() as tx:
                tx.execute(
                    "INSERT INTO sources (source_id, run_id, query_id, source_type, title, "
                    "canonical_url, locator, canonical_key, fetched_at, agent, publisher, "
                    "published_at, metadata) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        sid,
                        run_id,
                        query_id,
                        source_type,
                        title[:500],
                        canonical_url[:2000] if canonical_url else None,
                        locator[:2000],
                        canonical_key[:2000],
                        now,
                        agent,
                        publisher[:300] if publisher else None,
                        published_at,
                        json_param(_shrink_meta(metadata), store.dialect),
                    ),
                )
        except Exception:  # noqa: BLE001 - 并发/重复键竞争 → 回查既有
            existing = store.execute(
                "SELECT source_id FROM sources "
                "WHERE run_id = %s AND source_type = %s AND canonical_key = %s",
                (run_id, source_type, canonical_key),
            )
            if existing:
                return existing[0]["source_id"]
            raise
        return sid
    except ResearchRunNotFoundError:
        raise  # 显式 API 错误；工具侧由 artifacts_guard 转 fail-open
    except Exception as exc:  # noqa: BLE001
        logger.warning("[research] upsert_source failed (fail-open): %s", exc)
        return None


def append_evidence(
    run_id: str,
    source_id: str,
    sub_question_id: str,
    *,
    content: str,
    locator: str,
    extraction_method: str,
    metadata: Optional[dict[str, Any]] = None,
    created_at: Optional[str] = None,
) -> Optional[str]:
    """追加一条 Evidence（append 语义），返回 evidence_id 或 None。"""
    store = _store.get_store()
    if store is None:
        return None
    try:
        _require_run(store, run_id)
        eid = ids.new_evidence_id()
        now = created_at or _utcnow_iso()
        trimmed, truncated = _truncate(content or "", EV_CONTENT_MAX)
        meta = _meta(metadata)
        if truncated:
            meta = dict(meta)
            meta["truncated"] = True
        with store.transaction() as tx:
            tx.execute(
                "INSERT INTO evidences (evidence_id, run_id, source_id, sub_question_id, "
                "content, locator, extraction_method, metadata, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    eid,
                    run_id,
                    source_id,
                    sub_question_id,
                    trimmed,
                    locator[:2000],
                    extraction_method,
                    json_param(_shrink_meta(meta), store.dialect),
                    now,
                ),
            )
        return eid
    except ResearchRunNotFoundError:
        raise  # 显式 API 错误；工具侧由 artifacts_guard 转 fail-open
    except Exception as exc:  # noqa: BLE001
        logger.warning("[research] append_evidence failed (fail-open): %s", exc)
        return None


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# F2：Claim / ClaimEvidence / Citation（docs/spec/2026-09-08-claim-citation-binding.md rev2）
# 纪律：确定性写入 + 写前守卫；fail-open 仅在 store 不可用时（None）；
# 完整性守卫（run/子问题/claim/evidence/binding 一致性、R2/R7）抛显式错误，绝不静默绕过。
# ---------------------------------------------------------------------------
def _row(store, table: str, key_col: str, value: str):
    rows = store.execute(f"SELECT * FROM {table} WHERE {key_col} = %s", (value,))
    return rows[0] if rows else None


def _ensure_claim_type(claim_type) -> str:
    raw = claim_type.value if isinstance(claim_type, ClaimType) else claim_type
    if raw not in {t.value for t in ClaimType}:
        raise ValueError(
            f"非法 claim_type: {raw!r}；允许值：{sorted(t.value for t in ClaimType)}"
        )
    return str(raw)


def _require_owned(
    store, run_id: str, table: str, key_col: str, key: str, label: str
) -> dict:
    row = _row(store, table, key_col, key)
    if row is None:
        raise ValueError(f"{label} 不存在: {key}")
    if row["run_id"] != run_id:
        raise ValueError(
            f"{label} 不属于该 run（cross-run 拒绝）: {key} run={row['run_id']}"
        )
    return row


def create_claim(
    run_id: str,
    sub_question_id: str,
    statement: str,
    claim_type,
    metadata: Optional[dict[str, Any]] = None,
    created_at: Optional[str] = None,
) -> Optional[str]:
    """创建 Claim。同 run 同 statement_sha 内容级幂等 → 返回既有 claim_id。

    守卫：run 存在；sub_question 存在且同 run；claim_type 受控枚举。
    """
    store = _store.get_store()
    if store is None:
        return None
    try:
        _require_run(store, run_id)
        typ = _ensure_claim_type(claim_type)
        _require_owned(
            store,
            run_id,
            "sub_questions",
            "sub_question_id",
            sub_question_id,
            "sub_question",
        )
        text = (statement or "").strip()
        if not text:
            raise ValueError("claim statement 不能为空")
        text = text[:CLAIM_STATEMENT_MAX]
        sha = sha256_hex(text)
        existing = store.execute(
            "SELECT claim_id FROM claims WHERE run_id = %s AND statement_sha = %s",
            (run_id, sha),
        )
        if existing:
            return existing[0]["claim_id"]
        cid = ids.new_claim_id()
        now = created_at or _utcnow_iso()
        with store.transaction() as tx:
            tx.execute(
                "INSERT INTO claims (claim_id, run_id, sub_question_id, statement, "
                "statement_sha, claim_type, status, created_at, metadata) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    cid,
                    run_id,
                    sub_question_id,
                    text,
                    sha,
                    typ,
                    ClaimStatus.DRAFTED.value,
                    now,
                    json_param(_shrink_meta(metadata), store.dialect),
                ),
            )
        return cid
    except (ResearchRunNotFoundError, ValueError):
        raise  # 守卫错误显式上抛（调用方决定 fail-open 或终止）
    except Exception as exc:  # noqa: BLE001
        logger.warning("[research] create_claim failed (fail-open): %s", exc)
        return None


def bind_claim_evidence(
    run_id: str,
    claim_id: str,
    evidence_id: str,
    metadata: Optional[dict[str, Any]] = None,
    created_at: Optional[str] = None,
) -> Optional[str]:
    """绑定 Claim ↔ Evidence（M:N）。(claim_id, evidence_id) 幂等 → 返回既有 binding_id。

    守卫：claim/evidence 存在且均同 run（R6）。
    """
    store = _store.get_store()
    if store is None:
        return None
    try:
        _require_run(store, run_id)
        _require_owned(store, run_id, "claims", "claim_id", claim_id, "claim")
        _require_owned(
            store, run_id, "evidences", "evidence_id", evidence_id, "evidence"
        )
        existing = store.execute(
            "SELECT binding_id FROM claim_evidences "
            "WHERE claim_id = %s AND evidence_id = %s",
            (claim_id, evidence_id),
        )
        if existing:
            return existing[0]["binding_id"]
        bid = ids.new_binding_id()
        now = created_at or _utcnow_iso()
        with store.transaction() as tx:
            tx.execute(
                "INSERT INTO claim_evidences (binding_id, run_id, claim_id, evidence_id, "
                "created_at, metadata) VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    bid,
                    run_id,
                    claim_id,
                    evidence_id,
                    now,
                    json_param(_shrink_meta(metadata), store.dialect),
                ),
            )
        return bid
    except (ResearchRunNotFoundError, ValueError):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[research] bind_claim_evidence failed (fail-open): %s", exc)
        return None


def create_citation(
    run_id: str,
    claim_id: str,
    evidence_id: str,
    quote: Optional[str] = None,
    locator: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    created_at: Optional[str] = None,
) -> Optional[str]:
    """创建 Citation（claim+evidence 双绑定锚点）。

    守卫：claim/evidence 存在且同 run；必须先存在 claim_evidences binding（R2）；
    locator 长度 ≤200 且无控制字符（R7 写前）；quote 截断 ≤500（R8 子串由 validator 检）。
    幂等：(run_id, claim_id, evidence_id) → 返回既有 citation_id。
    说明：citation_id 为唯一稳定 artifact identity；呈现编号 [n] 不落库（render_citations 派生）。
    """
    store = _store.get_store()
    if store is None:
        return None
    try:
        _require_run(store, run_id)
        _require_owned(store, run_id, "claims", "claim_id", claim_id, "claim")
        _require_owned(
            store, run_id, "evidences", "evidence_id", evidence_id, "evidence"
        )
        binding = store.execute(
            "SELECT binding_id FROM claim_evidences "
            "WHERE claim_id = %s AND evidence_id = %s",
            (claim_id, evidence_id),
        )
        if not binding:
            raise ValueError(
                "citation 必须先存在 claim_evidences binding（R2）: "
                f"claim={claim_id} evidence={evidence_id}"
            )
        if locator is not None:
            if len(locator) > LOCATOR_MAX or _CONTROL_CHARS.search(locator):
                raise ValueError(
                    f"locator 不合法（R7）：长度≤{LOCATOR_MAX} 且不含控制字符"
                )
        quote_trim = quote[:QUOTE_MAX] if quote is not None else None
        existing = store.execute(
            "SELECT citation_id FROM citations "
            "WHERE run_id = %s AND claim_id = %s AND evidence_id = %s",
            (run_id, claim_id, evidence_id),
        )
        if existing:
            return existing[0]["citation_id"]
        cid = ids.new_citation_id()
        now = created_at or _utcnow_iso()
        with store.transaction() as tx:
            tx.execute(
                "INSERT INTO citations (citation_id, run_id, claim_id, evidence_id, "
                "quote, locator, metadata, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    cid,
                    run_id,
                    claim_id,
                    evidence_id,
                    quote_trim,
                    locator,
                    json_param(_shrink_meta(metadata), store.dialect),
                    now,
                ),
            )
        return cid
    except (ResearchRunNotFoundError, ValueError):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[research] create_citation failed (fail-open): %s", exc)
        return None


def apply_validation_outcome(run_id: str, report) -> int:
    """claim.status 的**唯一写入口**（finalizer workflow 专用，非 validator）。

    依据 ViolationReport.cited_claim_ids（有 ≥1 citation 覆盖）把 claim 从 drafted
    推进到 validated；unsupported / unreferenced 保持 drafted。返回**实际变更**条数
    （已是 validated 的 claim 不计）。
    """
    store = _store.get_store()
    if store is None:
        return 0
    cited_ids = list(getattr(report, "cited_claim_ids", []) or [])
    if getattr(report, "run_id", None) != run_id:
        raise ValueError("apply_validation_outcome 的 report 与 run_id 不一致")
    updated = 0
    try:
        with store.transaction() as tx:
            for cid in cited_ids:
                rows = tx.execute(
                    "SELECT status FROM claims WHERE run_id = %s AND claim_id = %s",
                    (run_id, cid),
                )
                if rows and rows[0].get("status") == ClaimStatus.DRAFTED.value:
                    tx.execute(
                        "UPDATE claims SET status = %s "
                        "WHERE run_id = %s AND claim_id = %s",
                        (ClaimStatus.VALIDATED.value, run_id, cid),
                    )
                    updated += 1
        return updated
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[research] apply_validation_outcome failed (fail-open): %s", exc
        )
        return 0
