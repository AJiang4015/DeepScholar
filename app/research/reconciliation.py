"""F6 Conflict Reconciliation（docs/spec/2026-09-12-f6-conflict-reconciliation.md rev2）。

定位（F6 explains/registers conflict state; it does not adjudicate truth）：
- 将 F4 conflict fact 与 F5 independence/cluster fact 组合成可审计的 claim-level
  conflict state / contested register；
- 每条 F4 confirmed 冲突产出 reconciliation outcome：
  - cluster intersection != empty → SAME_ORIGIN_CONTRADICTION（同源自我矛盾）；
  - independent + F4 INCONSISTENCY → DETAIL_INCONSISTENCY（仅登记 F4 类别）；
  - independent + F4 CONTRADICTION → GENUINE_CONTESTED（真争点，保留 contested）；
  - independence/cluster signal missing or malformed → status=failed（硬红线：Unknown ≠ Not Independent）。
- F6 **不重新聚类、不重新计算 independence**，只读消费 F5 corroboration artifact。
- 可选受控 review 默认 OFF（Fake reviewer 测试；real 须 VERIFY_REAL_LLM=1，不进自动化 Gate）；
  reviewer 最多产出 rationale/detail/metadata，**不得改变 outcome 语义族**；
  review 失败 → complete + metadata.review_failed=true。
- identity=(conflict_id, method_fingerprint)；complete 复用；failed 允许重算并 UPDATE 同行。

禁止：winner/truth/reliability/authority；修改 F3 verdict / F4 conflict / claim 内容；
不引入 task_call_id；不改 F1–F5。
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from app.research import ids, store as _store
from app.research.schemas import (
    ConflictType,
    ReconciliationOutcome,
    ReconciliationStatus,
    SemanticVerdict,
    VerifyStatus,
)
from app.research.store import json_param
from app.research.validate import validate_run

logger = logging.getLogger("deepsearch.research.reconciliation")

METHOD_NAME_V1 = "reconciliation.v1"

#: claim register 派生态（只读视图值，不落表）
REGISTER_UNVERIFIED = "unverified"
REGISTER_VERIFIED_CONSISTENT = "verified_consistent"
REGISTER_SAME_ORIGIN_ONLY = "same_origin_contradiction_only"
REGISTER_DETAIL = "detail_inconsistency"
REGISTER_GENUINE = "genuine_contested"
REGISTER_INCOMPLETE = "incomplete_input"

#: outcome → register 标签映射
_OUTCOME_TO_REGISTER = {
    ReconciliationOutcome.SAME_ORIGIN_CONTRADICTION.value: REGISTER_SAME_ORIGIN_ONLY,
    ReconciliationOutcome.DETAIL_INCONSISTENCY.value: REGISTER_DETAIL,
    ReconciliationOutcome.GENUINE_CONTESTED.value: REGISTER_GENUINE,
}

#: register precedence（outcome 值序：越高越优先；从高往低找第一个命中）
_REGISTER_PRECEDENCE = (
    ReconciliationOutcome.GENUINE_CONTESTED.value,
    ReconciliationOutcome.DETAIL_INCONSISTENCY.value,
    ReconciliationOutcome.SAME_ORIGIN_CONTRADICTION.value,
)

_RATIONALE_MAX = 500


class ReconciliationGateError(Exception):
    """run 存在 F2 deterministic error → F6 拒绝（作为 failed artifact 落库）。"""


class ReconciliationInputError(ValueError):
    """required F6 input 缺失/非法（corroboration / cluster signal / conflict 覆盖）。"""


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
class ReconciliationMethod:
    name: str = METHOD_NAME_V1
    version: str = "1"
    max_conflicts_per_claim: int = 50
    review_enabled: bool = False
    max_review_pairs: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "max_conflicts_per_claim": self.max_conflicts_per_claim,
            "review_enabled": self.review_enabled,
            "max_review_pairs": self.max_review_pairs,
        }

    def fingerprint(self) -> str:
        canonical = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


DEFAULT_METHOD = ReconciliationMethod()


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
        raise ReconciliationGateError(
            f"run {run_id} 存在 F2 deterministic error {rules}，拒绝 reconciliation 计算"
        )


# ---------------------------------------------------------------------------
# 只读消费 F5 corroboration（F6 不重聚类 / 不重算 independence）
# ---------------------------------------------------------------------------
def _claim_row(store, claim_id: str) -> dict:
    row = _fetch_one(store, "SELECT * FROM claims WHERE claim_id=%s", (claim_id,))
    if row is None:
        raise ValueError(f"claim 不存在: {claim_id}")
    return row


def _confirmed_conflicts(store, run_id: str, claim_id: str) -> list[dict]:
    return store.execute(
        "SELECT conflict_id, evidence_a_id, evidence_b_id, conflict_type, "
        "candidate_source, rationale, metadata FROM conflicts "
        "WHERE run_id=%s AND claim_id=%s AND status='confirmed' "
        "ORDER BY conflict_id",
        (run_id, claim_id),
    )


def _read_corroborations(store, run_id: str, claim_id: str) -> list[dict]:
    """读取该 claim 的全部 corroboration artifact（按最新优先），供 conflict 对齐。"""
    return store.execute(
        "SELECT corroboration_id, method_spec, method_fingerprint, status, "
        "computed_at, metadata, conflicts_independence, support, contradict "
        "FROM corroborations WHERE run_id=%s AND claim_id=%s AND status='complete' "
        "ORDER BY computed_at DESC, corroboration_id DESC",
        (run_id, claim_id),
    )


def _find_f5_entry(corroborations: list[dict], conflict_id: str) -> Optional[dict]:
    """在（任意 fingerprint 的）corroboration artifact 中定位该 conflict 的 F5 信号。

    返回该条 conflicts_independence entry（side_a/side_b.cluster_ids、
    independent_between_sides）；找不到 → None（conflict 未被 F5 覆盖）。
    """
    for row in corroborations:
        entries = _maybe_json(row.get("conflicts_independence")) or []
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("conflict_id") == conflict_id:
                return entry
    return None


def _extract_sides(entry: dict) -> tuple[list[str], list[str], bool]:
    """校验并抽取 side cluster_ids 与 independent_between_sides。

    任何缺失/类型非法 → ReconciliationInputError（硬红线：missing/malformed 必须 failed，
    绝不当作 same-origin 或独立）。
    """
    for side in ("side_a", "side_b"):
        part = entry.get(side)
        if not isinstance(part, dict):
            raise ReconciliationInputError(f"{side} 缺失/非对象: {entry!r}")
        cluster_ids = part.get("cluster_ids")
        if (
            not isinstance(cluster_ids, list)
            or not cluster_ids
            or not all(isinstance(x, str) and x for x in cluster_ids)
        ):
            raise ReconciliationInputError(
                f"{side}.cluster_ids 缺失/非法: {part!r}（Unknown ≠ Not Independent）"
            )
    a = [str(x) for x in entry["side_a"]["cluster_ids"]]
    b = [str(x) for x in entry["side_b"]["cluster_ids"]]
    independent = entry.get("independent_between_sides")
    if not isinstance(independent, bool):
        raise ReconciliationInputError(
            f"independent_between_sides 缺失/非 boolean: {entry!r}"
        )
    return a, b, independent


def _decide_outcome(
    conflict_row: dict, entry: dict
) -> tuple[ReconciliationOutcome, str]:
    """deterministic outcome 判定（Spec §8a/§9 锁死）。

    - cluster intersection != empty → SAME_ORIGIN_CONTRADICTION；
    - 交集为空且 independent_between_sides == true：
        F4 INCONSISTENCY → DETAIL_INCONSISTENCY；
        F4 CONTRADICTION → GENUINE_CONTESTED；
    - F5 派生布尔与交集不一致 / 其它 conflict_type → malformed（failed）。
    """
    a, b, independent = _extract_sides(entry)
    inter = set(a) & set(b)
    if inter:
        if independent is True:  # F5 派生信号与交集矛盾 → malformed
            raise ReconciliationInputError(
                "F5 信号不一致：cluster 交集非空但 independent_between_sides=true"
            )
        return (
            ReconciliationOutcome.SAME_ORIGIN_CONTRADICTION,
            f"cluster intersection non-empty: {sorted(inter)}",
        )
    if independent is False:  # 交集为空但 F5 说 not independent → malformed
        raise ReconciliationInputError(
            "F5 信号不一致：cluster 交集为空但 independent_between_sides=false"
        )
    ctype = (conflict_row.get("conflict_type") or "").strip().upper()
    if ctype == ConflictType.INCONSISTENCY.value:
        return (
            ReconciliationOutcome.DETAIL_INCONSISTENCY,
            "independent + F4 INCONSISTENCY（仅登记 F4 已识别类别，不裁决影响）",
        )
    if ctype == ConflictType.CONTRADICTION.value:
        return (
            ReconciliationOutcome.GENUINE_CONTESTED,
            "independent + F4 CONTRADICTION（保留 contested；不代表任何来源正确）",
        )
    raise ReconciliationInputError(
        f"unexpected F4 conflict_type for confirmed conflict: {ctype!r}"
    )


# ---------------------------------------------------------------------------
# reviewer：Fake 默认；real LLM controlled optional（默认 OFF）
# ---------------------------------------------------------------------------
class BaseReviewer:
    def respond(self, payload: dict[str, Any], hint: str = "") -> str:
        raise NotImplementedError


class FakeReviewer(BaseReviewer):
    """确定性 reviewer：输出 detail/rationale（不输出 outcome；仅用于测试通道）。"""

    def __init__(
        self,
        detail: Optional[str] = "fake-detail",
        rationale: Optional[str] = "fake rationale",
        resolver: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None,
        queue: Optional[list[Any]] = None,
    ) -> None:
        self.detail = detail
        self.rationale = rationale
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
        out = (
            self.resolver(payload)
            if self.resolver is not None
            else {"detail": self.detail, "rationale": self.rationale}
        )
        if not isinstance(out, dict):
            raise ReviewerRuntimeError("provider", "reviewer 返回非 dict")
        return json.dumps(out, ensure_ascii=False)


class RealLLMReviewer(BaseReviewer):
    """controlled optional（VERIFY_REAL_LLM=1；不进自动化 Gate）。"""

    def __init__(self) -> None:
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

    def respond(self, payload: dict[str, Any], hint: str = "") -> str:
        system = (
            "你只解释一个已被判定为 GENUINE_CONTESTED 的 evidence 冲突的可解释性。"
            "禁止改变 outcome、禁止裁决哪方正确、禁止评估来源可信度。"
            '只输出 JSON：{"rationale": "<string>", "detail": "<string> 可选"}。'
            "若语义上无法进一步解释，detail 使用 semantic_unresolvable。"
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
    """reviewer 输出解析：仅接受 rationale（str）+ 可选 detail（str）。"""
    raw = (text or "").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("reviewer 输出不是 JSON object")
    rationale = payload.get("rationale")
    detail = payload.get("detail")
    if not isinstance(rationale, str) or not rationale.strip():
        raise ValueError("reviewer 输出缺少 rationale")
    if detail is not None and not isinstance(detail, str):
        raise ValueError("reviewer detail 必须为 string 或 null")
    return {
        "rationale": rationale.strip()[:_RATIONALE_MAX],
        "detail": (detail.strip()[:_RATIONALE_MAX] if detail else None),
    }


# ---------------------------------------------------------------------------
# persistence（唯一写路径）
# ---------------------------------------------------------------------------
def _existing(store, conflict_id: str, fp: str) -> Optional[dict]:
    return _fetch_one(
        store,
        "SELECT * FROM reconciliations WHERE conflict_id=%s AND method_fingerprint=%s",
        (conflict_id, fp),
    )


def _persist(
    store,
    run_id,
    claim_id,
    conflict_id,
    method,
    *,
    status,
    outcome=None,
    detail=None,
    metadata=None,
    error=None,
    existing_id=None,
) -> str:
    now = _utcnow_iso()
    meta = dict(metadata or {})
    with store.transaction() as tx:
        if existing_id is not None:
            tx.execute(
                "UPDATE reconciliations SET status=%s, outcome=%s, detail=%s, "
                "error=%s, computed_at=%s, metadata=%s WHERE reconciliation_id=%s",
                (
                    status,
                    outcome,
                    detail,
                    error,
                    now,
                    json_param(meta, store.dialect),
                    existing_id,
                ),
            )
            return existing_id
        rid = ids.new_reconciliation_id()
        tx.execute(
            "INSERT INTO reconciliations (reconciliation_id, run_id, claim_id, "
            "conflict_id, method_spec, method_fingerprint, status, outcome, detail, "
            "error, computed_at, metadata) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                rid,
                run_id,
                claim_id,
                conflict_id,
                json_param(method.to_dict(), store.dialect),
                method.fingerprint(),
                status,
                outcome,
                detail,
                error,
                now,
                json_param(meta, store.dialect),
            ),
        )
    return rid


def _row_to_result(row: dict) -> dict[str, Any]:
    return {
        "reconciliation_id": row["reconciliation_id"],
        "run_id": row["run_id"],
        "claim_id": row["claim_id"],
        "conflict_id": row["conflict_id"],
        "method_spec": _maybe_json(row.get("method_spec")) or {},
        "method_fingerprint": row["method_fingerprint"],
        "status": row["status"],
        "outcome": row.get("outcome"),
        "detail": row.get("detail"),
        "error": row.get("error"),
        "computed_at": _iso(row["computed_at"]),
        "metadata": _maybe_json(row.get("metadata")) or {},
    }


def _persist_failed(
    store,
    run_id: str,
    claim_id: str,
    conflict_id: str,
    method: ReconciliationMethod,
    fp: str,
    existing_id: Optional[str],
    message: str,
) -> dict[str, Any]:
    rid = _persist(
        store,
        run_id,
        claim_id,
        conflict_id,
        method,
        status=ReconciliationStatus.FAILED.value,
        metadata={},
        error=message,
        existing_id=existing_id,
    )
    return {
        "reconciliation_id": rid,
        "run_id": run_id,
        "claim_id": claim_id,
        "conflict_id": conflict_id,
        "method_spec": method.to_dict(),
        "method_fingerprint": fp,
        "status": ReconciliationStatus.FAILED.value,
        "outcome": None,
        "detail": None,
        "error": message,
        "computed_at": _utcnow_iso(),
        "metadata": {},
    }


# ---------------------------------------------------------------------------
# per-conflict reconcile
# ---------------------------------------------------------------------------
def _reconcile_one(
    store,
    run_id: str,
    claim_row: dict,
    conflict_row: dict,
    method: ReconciliationMethod,
    reviewer: Optional[BaseReviewer],
) -> dict[str, Any]:
    conflict_id = conflict_row["conflict_id"]
    fp = method.fingerprint()
    existing = _existing(store, conflict_id, fp)
    if (
        existing is not None
        and existing["status"] == ReconciliationStatus.COMPLETE.value
    ):
        return _row_to_result(existing)
    existing_id = existing["reconciliation_id"] if existing is not None else None

    claim_id = claim_row["claim_id"]
    try:
        corroborations = _read_corroborations(store, run_id, claim_id)
        entry = _find_f5_entry(corroborations, conflict_id)
        if entry is None:
            raise ReconciliationInputError(
                "corroboration 缺失或该 conflict 不在 corroboration.conflicts_independence "
                "中（先跑 compute_claim_corroboration）"
            )
        outcome, detail = _decide_outcome(conflict_row, entry)
    except ReconciliationInputError as exc:
        return _persist_failed(
            store, run_id, claim_id, conflict_id, method, fp, existing_id, str(exc)
        )

    metadata: dict[str, Any] = {
        # F4/F5 signal 快照（provenance：不丢来源）
        "f4_signal": {
            "conflict_type": conflict_row.get("conflict_type"),
            "candidate_source": conflict_row.get("candidate_source"),
        },
        "f5_signal": {
            "side_a_cluster_ids": entry["side_a"]["cluster_ids"],
            "side_b_cluster_ids": entry["side_b"]["cluster_ids"],
            "independent_between_sides": entry.get("independent_between_sides"),
        },
        "decision": {"rule": "deterministic", "note": detail},
    }

    # optional review：仅对 GENUINE_CONTESTED 生效；不得改变 outcome（§10 rev2）
    if (
        method.review_enabled
        and reviewer is not None
        and method.max_review_pairs > 0
        and outcome == ReconciliationOutcome.GENUINE_CONTESTED
    ):
        payload = _review_payload(claim_row, conflict_row, entry)
        parsed: Optional[dict[str, Any]] = None
        reason = ""
        for attempt in range(1, 3):
            try:
                parsed = _parse_review(
                    reviewer.respond(
                        payload,
                        hint=(
                            "" if attempt == 1 else "上次输出非法，只输出合法 JSON。"
                        ),
                    )
                )
                break
            except Exception as exc:  # noqa: BLE001 - review 通道失败 → review_failed，不影响 artifact
                reason = f"{exc}"
        metadata["review_enabled"] = True
        metadata["review_pairs"] = 1
        if parsed is not None:
            metadata["rationale"] = parsed["rationale"]
            if parsed["detail"] is not None:
                metadata["review_detail"] = parsed["detail"]
        else:
            metadata["review_failed"] = True
            metadata["review_failed_reason"] = reason[:_RATIONALE_MAX]

    rid = _persist(
        store,
        run_id,
        claim_id,
        conflict_id,
        method,
        status=ReconciliationStatus.COMPLETE.value,
        outcome=outcome.value,
        detail=(metadata.get("review_detail") or None),
        metadata=metadata,
        existing_id=existing_id,
    )
    return {
        "reconciliation_id": rid,
        "run_id": run_id,
        "claim_id": claim_id,
        "conflict_id": conflict_id,
        "method_spec": method.to_dict(),
        "method_fingerprint": fp,
        "status": ReconciliationStatus.COMPLETE.value,
        "outcome": outcome.value,
        "detail": (metadata.get("review_detail") or None),
        "error": None,
        "computed_at": _utcnow_iso(),
        "metadata": metadata,
    }


def _review_payload(claim_row: dict, conflict_row: dict, entry: dict) -> dict[str, Any]:
    store = _get_store()
    claim_id = claim_row["claim_id"]

    def _evidence(evidence_id: str) -> dict[str, Any]:
        ev = (
            _fetch_one(
                store, "SELECT * FROM evidences WHERE evidence_id=%s", (evidence_id,)
            )
            or {}
        )
        src = None
        if ev.get("source_id"):
            src = _fetch_one(
                store, "SELECT * FROM sources WHERE source_id=%s", (ev["source_id"],)
            )
        part: dict[str, Any] = {
            "evidence_id": evidence_id,
            "content": (ev.get("content") or "")[:8000],
        }
        if src is not None:
            part["source_meta"] = {
                k: src.get(k)
                for k in ("title", "canonical_url", "published_at")
                if src.get(k) is not None
            }
        return part

    return {
        "claim": {
            "claim_id": claim_id,
            "statement": claim_row.get("statement"),
            "claim_type": claim_row.get("claim_type"),
        },
        "conflict_id": conflict_row["conflict_id"],
        "evidence_a": _evidence(conflict_row["evidence_a_id"]),
        "evidence_b": _evidence(conflict_row["evidence_b_id"]),
        "f5_signal": {
            "side_a_cluster_ids": entry["side_a"]["cluster_ids"],
            "side_b_cluster_ids": entry["side_b"]["cluster_ids"],
            "independent_between_sides": entry.get("independent_between_sides"),
        },
        "deterministic_outcome": ReconciliationOutcome.GENUINE_CONTESTED.value,
    }


# ---------------------------------------------------------------------------
# claim / run 级入口
# ---------------------------------------------------------------------------
def _has_supports(store, run_id: str, claim_id: str) -> bool:
    row = _fetch_one(
        store,
        "SELECT verification_id FROM verifications WHERE run_id=%s AND claim_id=%s "
        "AND status=%s AND verdict=%s LIMIT 1",
        (
            run_id,
            claim_id,
            VerifyStatus.SUCCEEDED.value,
            SemanticVerdict.SUPPORTS.value,
        ),
    )
    return row is not None


def conflict_register(
    claim_id: str, *, method: Optional[ReconciliationMethod] = None
) -> dict[str, Any]:
    """claim 级派生 register（只读，不落表；Spec §8b）。

    1) 任一 confirmed conflict 缺 complete reconciliation → incomplete_input（绝不静默降级）；
    2) 无 succeeded SUPPORTS verification → unverified（覆盖冲突驱动状态）；
    3) 否则 precedence：genuine_contested > detail_inconsistency >
       same_origin_contradiction_only；无冲突状态才可 verified_consistent
       （verified_consistent 绝不因"无 contradiction"单独成立，见 §8b 必要条件）。
    """
    store = _get_store()
    claim_row = _claim_row(store, claim_id)
    run_id = claim_row["run_id"]
    method = method or DEFAULT_METHOD
    fp = method.fingerprint()
    conflicts = _confirmed_conflicts(store, run_id, claim_id)

    missing = []
    for conflict in conflicts:
        row = _existing(store, conflict["conflict_id"], fp)
        if row is None:
            missing.append(
                {"conflict_id": conflict["conflict_id"], "reason": "no_reconciliation"}
            )
        elif row["status"] != ReconciliationStatus.COMPLETE.value:
            missing.append(
                {
                    "conflict_id": conflict["conflict_id"],
                    "reason": f"reconciliation_status={row['status']}",
                }
            )
    if missing:
        return {
            "claim_id": claim_id,
            "run_id": run_id,
            "register": REGISTER_INCOMPLETE,
            "missing": missing,
            "counts": {"confirmed_conflicts": len(conflicts)},
        }

    # verified evidence 条件：至少一个 succeeded SUPPORTS verification（§8b）
    if not _has_supports(store, run_id, claim_id):
        return {
            "claim_id": claim_id,
            "run_id": run_id,
            "register": REGISTER_UNVERIFIED,
            "missing": [],
            "counts": {"confirmed_conflicts": len(conflicts)},
        }

    outcomes = []
    for conflict in conflicts:
        row = _existing(store, conflict["conflict_id"], fp)
        if row is not None and row.get("outcome"):
            outcomes.append(row["outcome"])
    state = REGISTER_VERIFIED_CONSISTENT
    for candidate in _REGISTER_PRECEDENCE:
        if candidate in outcomes:
            state = _OUTCOME_TO_REGISTER[candidate]
            break
    return {
        "claim_id": claim_id,
        "run_id": run_id,
        "register": state,
        "missing": [],
        "counts": {
            "confirmed_conflicts": len(conflicts),
            "outcomes": {o: outcomes.count(o) for o in sorted(set(outcomes))},
        },
    }


def reconcile_claim_conflicts(
    claim_id: str,
    *,
    method: Optional[ReconciliationMethod] = None,
    reviewer: Optional[BaseReviewer] = None,
) -> dict[str, Any]:
    """单 claim reconciliation（§9 workflow）。identity=(conflict_id, method_fingerprint)。

    gate/读取/信号缺失等 deterministic 失败 → 该 conflict 落 failed artifact + error（§14）；
    complete artifact 幂等复用；failed 允许重算并 UPDATE 同行。
    optional review 失败不影响 complete（metadata.review_failed）。
    """
    store = _get_store()
    claim_row = _claim_row(store, claim_id)
    run_id = claim_row["run_id"]
    method = method or DEFAULT_METHOD
    fp = method.fingerprint()
    try:
        _gate_check(run_id)
        gate_error = None
    except ReconciliationGateError as exc:
        gate_error = f"gate: {exc}"
    conflicts = _confirmed_conflicts(store, run_id, claim_id)[
        : method.max_conflicts_per_claim
    ]
    items = []
    for conflict_row in conflicts:
        conflict_id = conflict_row["conflict_id"]
        existing = _existing(store, conflict_id, fp)
        if (
            existing is not None
            and existing["status"] == ReconciliationStatus.COMPLETE.value
        ):
            items.append(_row_to_result(existing))
            continue
        existing_id = existing["reconciliation_id"] if existing is not None else None
        if gate_error is not None:
            items.append(
                _persist_failed(
                    store,
                    run_id,
                    claim_id,
                    conflict_id,
                    method,
                    fp,
                    existing_id,
                    gate_error,
                )
            )
            continue
        items.append(
            _reconcile_one(store, run_id, claim_row, conflict_row, method, reviewer)
        )
    register = conflict_register(claim_id, method=method)
    return {
        "claim_id": claim_id,
        "run_id": run_id,
        "items": items,
        "register": register,
        "llm_calls": sum(1 for i in items if i.get("metadata", {}).get("review_pairs")),
    }


def reconcile_run_conflicts(
    run_id: str,
    *,
    method: Optional[ReconciliationMethod] = None,
    reviewer: Optional[BaseReviewer] = None,
) -> dict[str, Any]:
    """run 级批量 reconciliation（只处理有 confirmed conflicts 的 claims）。"""
    store = _get_store()
    method = method or DEFAULT_METHOD
    rows = store.execute(
        "SELECT DISTINCT c.* FROM claims c "
        "JOIN conflicts f ON f.run_id = c.run_id AND f.claim_id = c.claim_id "
        "AND f.status='confirmed' WHERE c.run_id=%s ORDER BY c.created_at, c.claim_id",
        (run_id,),
    )
    summary = {
        "run_id": run_id,
        "claims": [],
        "conflicts": 0,
        "complete": 0,
        "failed": 0,
        "genuine_contested": 0,
        "detail_inconsistency": 0,
        "same_origin": 0,
        "llm_calls": 0,
    }
    for claim_row in rows:
        result = reconcile_claim_conflicts(
            claim_row["claim_id"], method=method, reviewer=reviewer
        )
        summary["conflicts"] += len(result["items"])
        summary["complete"] += sum(
            1
            for i in result["items"]
            if i["status"] == ReconciliationStatus.COMPLETE.value
        )
        summary["failed"] += sum(
            1
            for i in result["items"]
            if i["status"] == ReconciliationStatus.FAILED.value
        )
        for item in result["items"]:
            outcome = item.get("outcome")
            if outcome == ReconciliationOutcome.GENUINE_CONTESTED.value:
                summary["genuine_contested"] += 1
            elif outcome == ReconciliationOutcome.DETAIL_INCONSISTENCY.value:
                summary["detail_inconsistency"] += 1
            elif outcome == ReconciliationOutcome.SAME_ORIGIN_CONTRADICTION.value:
                summary["same_origin"] += 1
        summary["llm_calls"] += result.get("llm_calls", 0)
        summary["claims"].append(
            {
                "claim_id": claim_row["claim_id"],
                "register": result["register"].get("register"),
                "items": result["items"],
            }
        )
    return summary


def run_unresolved(run_id: str) -> dict[str, Any]:
    """run 级 unresolved 摘要：register == genuine_contested 的 claims + confirmed conflicts
    及其 GENUINE_CONTESTED reconciliation（只读派生；不含 detail/same-origin）。"""
    store = _get_store()
    rows = store.execute(
        "SELECT DISTINCT c.* FROM claims c "
        "JOIN conflicts f ON f.run_id = c.run_id AND f.claim_id = c.claim_id "
        "AND f.status='confirmed' WHERE c.run_id=%s ORDER BY c.created_at, c.claim_id",
        (run_id,),
    )
    out = {"run_id": run_id, "unresolved_claims": []}
    for claim_row in rows:
        register = conflict_register(claim_row["claim_id"])
        if register["register"] != REGISTER_GENUINE:
            continue
        conflicts = []
        for conflict in _confirmed_conflicts(store, run_id, claim_row["claim_id"]):
            rec = _existing(
                store, conflict["conflict_id"], DEFAULT_METHOD.fingerprint()
            )
            conflicts.append(
                {
                    "conflict_id": conflict["conflict_id"],
                    "conflict_type": conflict.get("conflict_type"),
                    "reconciliation": (
                        {
                            "status": rec["status"],
                            "outcome": rec.get("outcome"),
                            "detail": rec.get("detail"),
                        }
                        if rec is not None
                        else None
                    ),
                }
            )
        out["unresolved_claims"].append(
            {
                "claim_id": claim_row["claim_id"],
                "statement": claim_row.get("statement"),
                "conflicts": conflicts,
            }
        )
    return out


# ---------------------------------------------------------------------------
# 只读查询 / provenance
# ---------------------------------------------------------------------------
def get_reconciliation(
    claim_id: str,
    *,
    conflict_id: Optional[str] = None,
    method: Optional[ReconciliationMethod] = None,
) -> Optional[dict[str, Any]]:
    store = _get_store()
    claim_row = _fetch_one(store, "SELECT * FROM claims WHERE claim_id=%s", (claim_id,))
    if claim_row is None:
        return None
    method = method or DEFAULT_METHOD
    sql = "SELECT * FROM reconciliations WHERE run_id=%s AND claim_id=%s AND method_fingerprint=%s"
    params: list[Any] = [claim_row["run_id"], claim_id, method.fingerprint()]
    if conflict_id:
        sql += " AND conflict_id=%s"
        params.append(conflict_id)
    sql += " ORDER BY computed_at DESC, reconciliation_id DESC LIMIT 1"
    row = _fetch_one(store, sql, tuple(params))
    return _row_to_result(row) if row else None


def list_reconciliations(
    run_id: str, *, claim_id: Optional[str] = None
) -> list[dict[str, Any]]:
    store = _get_store()
    sql = "SELECT * FROM reconciliations WHERE run_id=%s"
    params: list[Any] = [run_id]
    if claim_id:
        sql += " AND claim_id=%s"
        params.append(claim_id)
    sql += " ORDER BY computed_at, reconciliation_id"
    return [_row_to_result(r) for r in store.execute(sql, tuple(params))]
