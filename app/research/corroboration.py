"""F5 Independent Evidence Corroboration（docs/spec/2026-09-11-f5-audit-and-independent-corroboration.md rev2）。

核心原则：F5 measures independence; it does not score trust.
- Claim 内 global source clustering（统一 universe，禁止分别聚类 support/contradict）；
- deterministic union-find v1（R-URL/R-DOMAIN/R-TITLE/R-SHINGLE，OR 归并，chaining 锁死）；
- source_count = distinct source_id；independent_count = distinct global cluster id；
- cross-side independence = support/contradict cluster id 交集为空（仅计量，非 winner/可信）；
- source_profile 仅 descriptive；optional LLM review 默认 OFF（Fake reviewer 测试）；
- review 失败 → complete + metadata.review_failed=true；deterministic 失败 → failed。

禁止：ranking/weighting/winner/resolution/reliability score/authority/confidence/truth probability；
不引入 task_call_id；不改 F1–F4。
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from app.research import ids, store as _store
from app.research.schemas import CorroborationStatus
from app.research.store import json_param
from app.research.validate import validate_run

logger = logging.getLogger("deepsearch.research.corroboration")

METHOD_NAME_V1 = "corroboration.v1"


class CorroborationGateError(Exception):
    """run 存在 F2 deterministic error → F5 拒绝。"""


class ReviewerRuntimeError(Exception):
    def __init__(self, kind: str, message: str = "") -> None:
        super().__init__(message or kind)
        self.kind = kind


class StoreUnavailableError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Method spec（v1 契约默认值；fingerprint = sha256(canonical method_spec)）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CorroborationMethod:
    name: str = METHOD_NAME_V1
    version: str = "1"
    title_jaccard: float = 0.85
    shingle_jaccard: float = 0.60
    shingle_n: int = 5
    shingle_max_chars: int = 4000
    max_sources_per_claim: int = 50
    review_enabled: bool = False
    max_review_pairs: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "title_jaccard": self.title_jaccard,
            "shingle_jaccard": self.shingle_jaccard,
            "shingle_n": self.shingle_n,
            "shingle_max_chars": self.shingle_max_chars,
            "max_sources_per_claim": self.max_sources_per_claim,
            "review_enabled": self.review_enabled,
            "max_review_pairs": self.max_review_pairs,
        }

    def fingerprint(self) -> str:
        canonical = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


DEFAULT_METHOD = CorroborationMethod()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _utcnow_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _maybe_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return value


def _iso(value: Any) -> Any:
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    return value


def _get_store():
    store = _store.get_store()
    if store is None:
        raise StoreUnavailableError("research store 不可用（disabled/初始化失败）")
    return store


def _fetch_one(store, sql: str, params: tuple) -> Optional[dict]:
    rows = store.execute(sql, params)
    return rows[0] if rows else None


def _gate_check(run_id: str) -> None:
    report = validate_run(run_id)
    if report.errors:
        rules = sorted({e["rule"] for e in report.errors})
        raise CorroborationGateError(
            f"run {run_id} 存在 F2 deterministic error {rules}，拒绝 corroboration 计算"
        )


# ---------------------------------------------------------------------------
# text similarity helpers（deterministic）
# ---------------------------------------------------------------------------
def normalize_title(title: Optional[str]) -> str:
    """Title 归一化：NFKC、小写、去日期后缀/括号内容/分隔符，保留字母与数字（含 CJK）。

    输出无空白的字母数字串；等值比较与 R-TITLE 字符集 Jaccard 均基于此。
    日期后缀（2026-09-11 / 2026/9/11 / 2026年9月11日 等）先于分隔符归一去剥离。
    """
    if not title:
        return ""
    t = unicodedata.normalize("NFKC", str(title)).lower()
    t = re.sub(r"[()（）\[\]【】]", " ", t)  # 括号内容弱化（日期/副题常见）
    t = re.sub(
        r"(^|\s)\d{4}\s*[-年/.]\s*\d{1,2}(?:\s*[-月/.]\s*\d{1,2})?(?:日)?",
        " ",
        t,
    )
    t = re.sub(r"[-–—:：|｜/\\.,，。;；]+", " ", t)  # 分隔符归一
    out: list[str] = []
    for ch in t:
        if ch.isspace():
            continue
        if unicodedata.category(ch)[0] in ("L", "N"):
            out.append(ch)
    return "".join(out)


def _shingles(text: str, n: int, max_chars: int) -> set[str]:
    norm = re.sub(r"\s+", " ", (text or "")[:max_chars]).strip().lower()
    if len(norm) < n:
        return {norm} if norm else set()
    return {norm[i : i + n] for i in range(len(norm) - n + 1)}


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _domain_of(url: str) -> Optional[str]:
    if not url:
        return None
    if "://" not in url:
        url = "https://" + url
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    return host.lower() if host else None


def _path_prefix(url: str, segments: int = 3) -> tuple[Optional[str], str]:
    """返回 (domain, 去尾/后 path 前 segments 归一串)（无 path 视为空）。"""
    if not url:
        return None, ""
    if "://" not in url:
        url = "https://" + url
    try:
        parts = urlsplit(url)
    except ValueError:
        return None, ""
    host = parts.hostname.lower() if parts.hostname else None
    path = parts.path.rstrip("/") if parts.path else ""
    if not path or path == "/":
        return host, ""
    segs = [s for s in path.split("/") if s][:segments]
    return host, "/".join(segs)


# ---------------------------------------------------------------------------
# reviewer：Fake 默认；real LLM controlled optional（默认 OFF）
# ---------------------------------------------------------------------------
class BaseReviewer:
    def respond(self, payload: dict[str, Any], hint: str = "") -> str:
        raise NotImplementedError


class FakeReviewer(BaseReviewer):
    def __init__(
        self,
        same: bool = False,
        resolver: Optional[Callable[[dict[str, Any]], bool]] = None,
        queue: Optional[list[Any]] = None,
    ) -> None:
        self.same = same
        self.resolver = resolver
        self.queue: list[Any] = list(queue or [])
        self.calls: list[dict[str, Any]] = []

    def respond(self, payload: dict[str, Any], hint: str = "") -> str:
        self.calls.append(payload)
        if self.queue:
            item = self.queue.pop(0)
            if isinstance(item, Exception):
                if isinstance(item, ReviewerRuntimeError):
                    raise item
                raise ReviewerRuntimeError("provider", str(item))
            return item
        same = self.resolver(payload) if self.resolver is not None else self.same
        return json.dumps(
            {"same": bool(same), "rationale": "fake review"}, ensure_ascii=False
        )


class RealLLMReviewer(BaseReviewer):
    """controlled optional（VERIFY_REAL_LLM=1；不进自动化 Gate）。"""

    def __init__(self, method: CorroborationMethod) -> None:
        import os

        if os.getenv("VERIFY_REAL_LLM", "0") != "1":
            raise RuntimeError("real-LLM reviewer 需显式设置 VERIFY_REAL_LLM=1")
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("real-LLM reviewer 需要 openai SDK") from exc
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("real-LLM reviewer 需要 OPENAI_API_KEY")
        kwargs: dict[str, Any] = {"api_key": api_key}
        base_url = os.getenv("OPENAI_BASE_URL")
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)
        self.method = method

    def respond(self, payload: dict[str, Any], hint: str = "") -> str:
        system = (
            "判断两个来源是否来自同一原始内容（转载/镜像/同机构重发）。只输出 JSON "
            '{"same": true|false, "rationale": "<string>"}。不要输出 trust/ranking/权威。'
            + (f"\n{hint}" if hint else "")
        )
        resp = self._client.chat.completions.create(
            model="qwen-max",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            timeout_ms=30000,
        )
        content = (resp.choices[0].message.content or "").strip()
        if not content:
            raise ReviewerRuntimeError("provider", "empty response")
        return content


def _parse_review(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("same"), bool):
        raise ValueError("reviewer 输出非法")
    return payload


# ---------------------------------------------------------------------------
# Universe 采集（claim 内 global；verdict ∈ {SUPPORTS, CONTRADICTS}）
# ---------------------------------------------------------------------------
def _collect_universe(store, run_id: str, claim_id: str, method: CorroborationMethod):
    """收集 claim 的 SUPPORTS/CONTRADICTS 证据 → 引用 sources 全集 S（global universe）。

    每个 evidence 只取最新 succeeded 且 verdict∈{SUPPORTS,CONTRADICTS} 的 verification
    （同 evidence 多指纹验证时 deterministic 收敛）。返回 (source_list, by_id, ev_to_source)：
    - source_list：参与聚类的 source 条目（≤ max_sources_per_claim，按 source_id 字典序截断）；
    - by_id：source_id → source 条目；
    - ev_to_source：evidence_id → source_id（聚类成员证据与 F4 冲突两侧解析用）。
    """
    rows = store.execute(
        "SELECT v.evidence_id, v.verdict FROM verifications v "
        "WHERE v.run_id=%s AND v.claim_id=%s AND v.status='succeeded' "
        "AND v.verdict IN ('SUPPORTS','CONTRADICTS') "
        "AND v.verification_id = ("
        "SELECT v2.verification_id FROM verifications v2 "
        "WHERE v2.run_id=v.run_id AND v2.claim_id=v.claim_id "
        "AND v2.evidence_id=v.evidence_id AND v2.status='succeeded' "
        "AND v2.verdict IN ('SUPPORTS','CONTRADICTS') "
        "ORDER BY v2.completed_at DESC, v2.verification_id DESC LIMIT 1)"
        " ORDER BY v.evidence_id",
        (run_id, claim_id),
    )
    verdict_by_ev: dict[str, str] = {}
    for r in rows:
        verdict_by_ev[r["evidence_id"]] = r["verdict"]
    if not verdict_by_ev:
        return [], {}, {}

    placeholders = ",".join(["%s"] * len(verdict_by_ev))
    ev_full = store.execute(
        f"SELECT evidence_id, source_id, content FROM evidences "
        f"WHERE evidence_id IN ({placeholders})",
        tuple(sorted(verdict_by_ev)),
    )
    if not ev_full:
        return [], {}, {}
    source_ids = sorted({e["source_id"] for e in ev_full})
    sources = store.execute(
        f"SELECT * FROM sources WHERE run_id=%s AND source_id IN ({placeholders})",
        (run_id, *source_ids),
    )
    by_id_all = {s["source_id"]: s for s in sources}

    ev_to_source: dict[str, str] = {}
    content_by_source: dict[str, list[str]] = {}
    side_by_source: dict[str, set[str]] = {}
    for e in ev_full:
        sid = e["source_id"]
        ev_to_source[e["evidence_id"]] = sid
        content_by_source.setdefault(sid, []).append(e.get("content") or "")
        side_by_source.setdefault(sid, set()).add(verdict_by_ev[e["evidence_id"]])

    source_list = []
    for sid in source_ids:
        s = by_id_all.get(sid)
        if s is None:
            continue
        source_list.append(
            {
                "source_id": sid,
                "canonical_url": s.get("canonical_url") or s.get("locator") or "",
                "canonical_key": s.get("canonical_key") or "",
                "title": s.get("title") or "",
                "publisher": s.get("publisher"),
                "source_type": s.get("source_type") or "unknown",
                "agent": s.get("agent") or "unknown",
                "published_at": s.get("published_at"),
                "fetched_at": s.get("fetched_at"),
                "content": ("\n".join(content_by_source.get(sid, [])))[
                    : method.shingle_max_chars * 2
                ],
                "sides": side_by_source.get(sid, set()),
            }
        )
    capped = source_list[: method.max_sources_per_claim]
    return capped, {s["source_id"]: s for s in capped}, ev_to_source


# ---------------------------------------------------------------------------
# deterministic clustering v1（union-find）
# ---------------------------------------------------------------------------
class _UnionFind:
    def __init__(self, items: list[str]) -> None:
        self.parent = {i: i for i in items}

    def find(self, x: str) -> str:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def _pair_hits_rules(a: dict, b: dict, method: CorroborationMethod) -> bool:
    # R-URL
    if a["canonical_key"] and a["canonical_key"] == b["canonical_key"]:
        return True
    # R-DOMAIN（path 前 3 段或 path 为空）
    da, pa = _path_prefix(a["canonical_url"])
    db, pb = _path_prefix(b["canonical_url"])
    if da and da == db and (pa == pb or pa == "" or pb == ""):
        return True
    # R-TITLE
    ta, tb = normalize_title(a["title"]), normalize_title(b["title"])
    if ta and tb:
        if ta == tb:
            return True
        ta_set, tb_set = set(ta), set(tb)
        if jaccard(ta_set, tb_set) >= method.title_jaccard:
            return True
    # R-SHINGLE（5-gram over evidence content）
    sa, sb = (
        _shingles(a["content"], method.shingle_n, method.shingle_max_chars),
        _shingles(b["content"], method.shingle_n, method.shingle_max_chars),
    )
    if sa and sb and jaccard(sa, sb) >= method.shingle_jaccard:
        return True
    return False


def _side_view(cluster_rows, side: str) -> dict[str, Any]:
    """global cluster 按侧投影（AC2：support/contradict 只是统一聚类的视图）。

    source_count = 该侧 distinct source_id；independent_count = 该侧 distinct cluster id。
    """
    cluster_ids = [
        c["cluster_id"] for c in cluster_rows if c["member_side"] in (side, "both")
    ]
    member_source_ids = set()
    for c in cluster_rows:
        if c["member_side"] in (side, "both"):
            member_source_ids |= set(c["member_source_ids"])
    return {
        "source_count": len(member_source_ids),
        "independent_count": len(cluster_ids),
        "cluster_ids": cluster_ids,
    }


# ---------------------------------------------------------------------------
# compute & persist
# ---------------------------------------------------------------------------
def _persist(
    store, run_id, claim_id, method, *, status, payload, existing_id=None, error=None
):
    """写入 corroboration artifact。

    同 (run_id, claim_id, method_fingerprint) 已存在（如上次 deterministic failed 后重算）
    → UPDATE 同行（idempotency，identity 不变）；否则 INSERT 新行。
    """
    now = _utcnow_iso()
    with store.transaction() as tx:
        if existing_id is not None:
            tx.execute(
                "UPDATE corroborations SET status=%s, error=%s, computed_at=%s, "
                "metadata=%s, global_clusters=%s, support=%s, contradict=%s, "
                "conflicts_independence=%s, source_profile=%s WHERE corroboration_id=%s",
                (
                    status,
                    error,
                    now,
                    json_param(payload.get("metadata", {}), store.dialect),
                    json_param(payload.get("global_clusters", []), store.dialect),
                    json_param(payload.get("support", {}), store.dialect),
                    json_param(payload.get("contradict", {}), store.dialect),
                    json_param(
                        payload.get("conflicts_independence", []), store.dialect
                    ),
                    json_param(payload.get("source_profile", {}), store.dialect),
                    existing_id,
                ),
            )
            return existing_id
        cid = ids.new_corroboration_id()
        tx.execute(
            "INSERT INTO corroborations (corroboration_id, run_id, claim_id, method_spec, "
            "method_fingerprint, status, error, computed_at, metadata, global_clusters, "
            "support, contradict, conflicts_independence, source_profile) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                cid,
                run_id,
                claim_id,
                json_param(method.to_dict(), store.dialect),
                method.fingerprint(),
                status,
                error,
                now,
                json_param(payload.get("metadata", {}), store.dialect),
                json_param(payload.get("global_clusters", []), store.dialect),
                json_param(payload.get("support", {}), store.dialect),
                json_param(payload.get("contradict", {}), store.dialect),
                json_param(payload.get("conflicts_independence", []), store.dialect),
                json_param(payload.get("source_profile", {}), store.dialect),
            ),
        )
    return cid


def _read_row(store, run_id, claim_id, fp) -> Optional[dict]:
    return _fetch_one(
        store,
        "SELECT * FROM corroborations WHERE run_id=%s AND claim_id=%s AND method_fingerprint=%s",
        (run_id, claim_id, fp),
    )


def _row_to_result(row: dict) -> dict[str, Any]:
    return {
        "corroboration_id": row["corroboration_id"],
        "run_id": row["run_id"],
        "claim_id": row["claim_id"],
        "method_spec": _maybe_json(row.get("method_spec")) or {},
        "method_fingerprint": row["method_fingerprint"],
        "status": row["status"],
        "error": row.get("error"),
        "computed_at": _iso(row["computed_at"]),
        "metadata": _maybe_json(row.get("metadata")) or {},
        "global_clusters": _maybe_json(row.get("global_clusters")) or [],
        "support": _maybe_json(row.get("support")) or {},
        "contradict": _maybe_json(row.get("contradict")) or {},
        "conflicts_independence": _maybe_json(row.get("conflicts_independence")) or [],
        "source_profile": _maybe_json(row.get("source_profile")) or {},
    }


def _conflict_independence(store, run_id, claim_id, clusters_by_evidence):
    """F4 confirmed 冲突两侧的 cluster 独立性（AC5；仅计量，非 winner/可信信号）。

    每对冲突把两侧 evidence 解析到所属 global cluster（经 evidence→source→cluster）。
    independent_between_sides = 两侧 cluster id 交集为空。
    任一侧无法解析（不在 universe/cap 截断）→ 该冲突不可计量，跳过（deterministic）。
    """
    rows = store.execute(
        "SELECT conflict_id, evidence_a_id, evidence_b_id FROM conflicts "
        "WHERE run_id=%s AND claim_id=%s AND status='confirmed' ORDER BY conflict_id",
        (run_id, claim_id),
    )
    out = []
    for c in rows:
        ca = clusters_by_evidence.get(c["evidence_a_id"])
        cb = clusters_by_evidence.get(c["evidence_b_id"])
        if ca is None or cb is None:
            continue
        overlap = {ca["cluster_id"]} & {cb["cluster_id"]}
        out.append(
            {
                "conflict_id": c["conflict_id"],
                "side_a": {"cluster_ids": [ca["cluster_id"]], "independent_count": 1},
                "side_b": {"cluster_ids": [cb["cluster_id"]], "independent_count": 1},
                "independent_between_sides": bool(not overlap),
            }
        )
    return out


def _optional_review(store, method, reviewer, by_id, ids_sorted, uf) -> dict[str, Any]:
    """可选 cross-domain cluster review（默认 OFF，Fake reviewer 供测试）。

    候选：确定性未归并且不同 domain 的 pair（≤ max_review_pairs）；reviewer 判 same → union
    （只复核簇划分，不产生评分）。review 失败（两次尝试仍失败）→ metadata.review_failed=true
    携带 reason；不影响 deterministic 主结果（artifact 仍 complete，§11b）。
    """
    meta: dict[str, Any] = {}
    if not method.review_enabled or reviewer is None or method.max_review_pairs <= 0:
        return meta
    candidates = []
    for i in range(len(ids_sorted)):
        for j in range(i + 1, len(ids_sorted)):
            a, b = by_id[ids_sorted[i]], by_id[ids_sorted[j]]
            da, _p = _path_prefix(a["canonical_url"])
            db, _q = _path_prefix(b["canonical_url"])
            if (
                da
                and db
                and da != db
                and uf.find(a["source_id"]) != uf.find(b["source_id"])
            ):
                candidates.append((a, b))
    candidates = candidates[: method.max_review_pairs]
    meta["review_enabled"] = True
    meta["review_pairs"] = len(candidates)
    merged = 0
    failed_reason: Optional[str] = None
    for a, b in candidates:
        payload = _review_payload(a, b)
        outcome: Optional[dict] = None
        reason = ""
        for attempt in range(1, 3):
            try:
                outcome = _parse_review(
                    reviewer.respond(
                        payload, hint=("" if attempt == 1 else "只输出合法 JSON。")
                    )
                )
                break
            except Exception as exc:  # noqa: BLE001 - review 通道失败 → review_failed，不影响 artifact
                reason = f"{exc}"
        if outcome is not None and outcome.get("same"):
            uf.union(a["source_id"], b["source_id"])
            merged += 1
        elif failed_reason is None and reason:
            failed_reason = reason
    meta["review_merged"] = merged
    if failed_reason is not None:
        meta["review_failed"] = True
        meta["review_failed_reason"] = failed_reason[:500]
    return meta


def _review_payload(a: dict, b: dict) -> dict[str, Any]:
    def meta(s: dict) -> dict[str, Any]:
        return {
            "title": s["title"],
            "url": s["canonical_url"],
            "domain": _domain_of(s["canonical_url"]),
            "publisher": s.get("publisher"),
            "published_at": s.get("published_at"),
            "evidence_content": s["content"][:8000],
        }

    return {"source_a": meta(a), "source_b": meta(b)}


def _failed_payload() -> dict[str, Any]:
    return {
        "metadata": {},
        "global_clusters": [],
        "support": {},
        "contradict": {},
        "conflicts_independence": [],
        "source_profile": {},
    }


def _persist_failed(
    store,
    run_id: str,
    claim_id: str,
    method: CorroborationMethod,
    fp: str,
    existing_id: Optional[str],
    message: str,
) -> dict[str, Any]:
    cid = _persist(
        store,
        run_id,
        claim_id,
        method,
        status=CorroborationStatus.FAILED.value,
        payload=_failed_payload(),
        existing_id=existing_id,
        error=message,
    )
    return {
        "corroboration_id": cid,
        "run_id": run_id,
        "claim_id": claim_id,
        "method_spec": method.to_dict(),
        "method_fingerprint": fp,
        "status": CorroborationStatus.FAILED.value,
        "error": message,
        "computed_at": _utcnow_iso(),
        "metadata": {},
        "global_clusters": [],
        "support": {},
        "contradict": {},
        "conflicts_independence": [],
        "source_profile": {},
    }


def compute_claim_corroboration(
    claim_id: str,
    *,
    method: Optional[CorroborationMethod] = None,
    reviewer: Optional[BaseReviewer] = None,
) -> dict[str, Any]:
    """单 claim corroboration（§11 workflow）。

    幂等：同 (run_id, claim_id, method_fingerprint) 已有 complete artifact → 复用；
    failed artifact → 重算并 UPDATE 同行（状态收敛，同指纹始终单行）。
    §11b：gate/读取/聚类 deterministic 失败 → failed artifact + error；
    optional review 失败不影响 complete（metadata.review_failed）。
    """
    store = _get_store()
    claim_row = _fetch_one(store, "SELECT * FROM claims WHERE claim_id=%s", (claim_id,))
    if claim_row is None:
        raise ValueError(f"claim 不存在: {claim_id}")
    run_id = claim_row["run_id"]
    method = method or DEFAULT_METHOD
    fp = method.fingerprint()
    existing = _read_row(store, run_id, claim_id, fp)
    if (
        existing is not None
        and existing["status"] == CorroborationStatus.COMPLETE.value
    ):
        return _row_to_result(existing)
    existing_id = existing["corroboration_id"] if existing is not None else None

    # gate（deterministic 失败 → failed artifact；§11b）
    try:
        _gate_check(run_id)
    except CorroborationGateError as exc:
        return _persist_failed(
            store, run_id, claim_id, method, fp, existing_id, f"gate: {exc}"
        )

    try:
        source_list, by_id, ev_to_source = _collect_universe(
            store, run_id, claim_id, method
        )
        ids_sorted = sorted(by_id)
        uf = _UnionFind(ids_sorted)
        for i in range(len(ids_sorted)):
            for j in range(i + 1, len(ids_sorted)):
                a, b = by_id[ids_sorted[i]], by_id[ids_sorted[j]]
                if _pair_hits_rules(a, b, method):
                    uf.union(a["source_id"], b["source_id"])

        review_meta = _optional_review(store, method, reviewer, by_id, ids_sorted, uf)

        components: dict[str, list[str]] = {}
        for sid in ids_sorted:
            components.setdefault(uf.find(sid), []).append(sid)
        keyed: list[dict[str, Any]] = []
        for members in components.values():
            members_sorted = sorted(members)
            base = min(
                (by_id[m]["canonical_key"] or by_id[m]["canonical_url"] or m)
                for m in members_sorted
            )
            keyed.append({"members": members_sorted, "base": base})
        keyed.sort(key=lambda c: c["base"])

        clusters_by_source: dict[str, dict[str, Any]] = {}
        cluster_rows: list[dict[str, Any]] = []
        for idx, c in enumerate(keyed, start=1):
            cid_key = f"{c['base']}#{idx}"
            sides: set[str] = set()
            member_evidence_ids: set[str] = set()
            for m in c["members"]:
                sides |= by_id[m]["sides"]
                member_evidence_ids.update(
                    eid for eid, sid in ev_to_source.items() if sid == m
                )
            member_side = (
                "both"
                if sides == {"SUPPORTS", "CONTRADICTS"}
                else (
                    "support"
                    if "SUPPORTS" in sides
                    else ("contradict" if "CONTRADICTS" in sides else "none")
                )
            )
            cluster_rows.append(
                {
                    "cluster_id": cid_key,
                    "cluster_key": cid_key,
                    "member_source_ids": c["members"],
                    "member_evidence_ids": sorted(member_evidence_ids),
                    "member_side": member_side,
                }
            )
            for m in c["members"]:
                clusters_by_source[m] = {"cluster_id": cid_key}

        clusters_by_evidence: dict[str, dict[str, Any]] = {}
        for eid, sid in ev_to_source.items():
            cluster = clusters_by_source.get(sid)
            if cluster is not None:
                clusters_by_evidence[eid] = {"cluster_id": cluster["cluster_id"]}

        support = _side_view(cluster_rows, "support")
        contradict = _side_view(cluster_rows, "contradict")
        conflicts = _conflict_independence(
            store, run_id, claim_id, clusters_by_evidence
        )
        profile = _source_profile(source_list)
        payload = {
            "metadata": review_meta,
            "global_clusters": cluster_rows,
            "support": support,
            "contradict": contradict,
            "conflicts_independence": conflicts,
            "source_profile": profile,
        }
        cid_db = _persist(
            store,
            run_id,
            claim_id,
            method,
            status=CorroborationStatus.COMPLETE.value,
            payload=payload,
            existing_id=existing_id,
        )
        result = dict(_row_to_result(_read_row(store, run_id, claim_id, fp)))
        result["corroboration_id"] = cid_db
        return result
    except Exception as exc:  # noqa: BLE001 - deterministic 失败 → failed artifact
        return _persist_failed(
            store, run_id, claim_id, method, fp, existing_id, str(exc)
        )


def _source_profile(source_list: list[dict]) -> dict[str, Any]:
    def _count(key):
        out: dict[str, int] = {}
        for s in source_list:
            v = s.get(key)
            if v:
                out[str(v)] = out.get(str(v), 0) + 1
        return out

    def _freshness():
        out = {"<1y": 0, "1-3y": 0, ">3y": 0, "unknown": 0}
        now = datetime.datetime.now(datetime.timezone.utc)
        for s in source_list:
            ts = s.get("published_at") or s.get("fetched_at")
            try:
                dt = datetime.datetime.fromisoformat(str(ts))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                age_days = (now - dt).days
                if age_days < 365:
                    out["<1y"] += 1
                elif age_days < 365 * 3:
                    out["1-3y"] += 1
                else:
                    out[">3y"] += 1
            except (TypeError, ValueError):
                out["unknown"] += 1
        return out

    domains = {}
    for s in source_list:
        d = _domain_of(s.get("canonical_url"))
        if d:
            domains[d] = domains.get(d, 0) + 1

    return {
        "source_type_distribution": _count("source_type"),
        "domain_distribution": domains,
        "publisher_distribution": _count("publisher"),
        "freshness_distribution": _freshness(),
        "agent_distribution": _count("agent"),
    }


def compute_run_corroborations(
    run_id: str,
    *,
    method: Optional[CorroborationMethod] = None,
    reviewer: Optional[BaseReviewer] = None,
) -> dict[str, Any]:
    store = _get_store()
    claims = store.execute(
        "SELECT claim_id FROM claims WHERE run_id=%s ORDER BY created_at, claim_id",
        (run_id,),
    )
    summary = {"run_id": run_id, "claims": [], "complete": 0, "failed": 0}
    for row in claims:
        result = compute_claim_corroboration(
            row["claim_id"], method=method, reviewer=reviewer
        )
        summary["claims"].append(
            {"claim_id": row["claim_id"], "status": result.get("status")}
        )
        summary["complete"] += 1 if result.get("status") == "complete" else 0
        summary["failed"] += 1 if result.get("status") == "failed" else 0
    return summary


def get_corroboration(
    claim_id: str, *, method: Optional[CorroborationMethod] = None
) -> Optional[dict]:
    store = _get_store()
    claim_row = _fetch_one(store, "SELECT * FROM claims WHERE claim_id=%s", (claim_id,))
    if claim_row is None:
        return None
    method = method or DEFAULT_METHOD
    row = _read_row(store, claim_row["run_id"], claim_id, method.fingerprint())
    return _row_to_result(row) if row else None


def list_corroborations(
    run_id: str, *, claim_id: Optional[str] = None
) -> list[dict[str, Any]]:
    store = _get_store()
    sql = "SELECT * FROM corroborations WHERE run_id=%s"
    params: list[Any] = [run_id]
    if claim_id:
        sql += " AND claim_id=%s"
        params.append(claim_id)
    sql += " ORDER BY computed_at, corroboration_id"
    return [_row_to_result(r) for r in store.execute(sql, tuple(params))]
