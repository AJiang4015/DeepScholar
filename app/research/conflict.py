"""F4 Conflict Detection（docs/spec/2026-09-10-conflict-detection.md rev2）。

职责（不含 Resolution / Source Reliability / Winner）：
- 确定性候选生成：同一 claim 内 SUPPORTS × CONTRADICTS（F3 verification verdict 为 signal，
  本模块 SHALL NOT re-evaluate Claim↔Evidence support）；
- explicit_pairs 仅绕过 verdict-based 候选生成，不绕过任何 structural/integrity 约束；
- 受控 semantic detector（默认 FakeDetector；real-LLM 可选且须显式启用，不进自动化 Gate）
  只做 pairwise relationship 分类；
- genuine/conflict_type 四状态 invariant 与 F4 artifact integrity invariants 由唯一写路径强制；
- verification_a/b FK 采用 ON DELETE SET NULL；写入时把 F3 signal 快照存入 metadata 防 provenance 丢失。

不引入 confidence；不引入 task_call_id；identity =
(run_id, claim_id, evidence_a_id, evidence_b_id) + detector_fingerprint（a<b 规范化）。
"""

from __future__ import annotations

import datetime
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from app.research import ids, store as _store
from app.research.schemas import ConflictStatus, ConflictType
from app.research.store import json_param
from app.research.validate import validate_run
from app.research.verify import VerifierSpec

logger = logging.getLogger("deepsearch.research.conflict")

GENUINE_TYPES = {ConflictType.CONTRADICTION.value, ConflictType.INCONSISTENCY.value}
DEFAULT_DETECTOR_SPEC = VerifierSpec(
    provider="fake", model="fake-detector", prompt_version="2026-09-10.conflict.v1"
)
DETECTOR_RATIONALE_MAX = 2000

_DETECTOR_INSTRUCTION = (
    "判断 Evidence A 与 Evidence B 之间对给定 claim 的 pairwise relationship。只输出 JSON："
    '{"genuine": true|false, "conflict_type": "CONTRADICTION|INCONSISTENCY|'
    'CONTEXTUAL_DIFFERENCE|TEMPORAL_DIFFERENCE|SCOPE_DIFFERENCE|NO_CONFLICT", '
    '"rationale": "<string>"}。'
    "时间/范围/条件差异不得判为 contradiction。禁止裁决谁更可信。"
    "禁止重新评价 Evidence 对 Claim 的支持状态（那是 F3 的职责）。"
)


class ConflictGateError(Exception):
    """run 存在 F2/F3 deterministic error → F4 拒绝。"""


class ConflictIntegrityError(ValueError):
    """F4 artifact integrity invariant 违反（写前拒绝）。"""


class DetectorRuntimeError(Exception):
    def __init__(self, kind: str, message: str = "") -> None:
        super().__init__(message or kind)
        self.kind = kind


class StoreUnavailableError(RuntimeError):
    pass


@dataclass
class ConflictBudget:
    """预算与候选策略（可配置，非架构约束）。"""

    max_pairs_per_claim: int = 12
    max_semantic_calls: Optional[int] = None
    include_opinion: bool = False


@dataclass
class CallCounter:
    calls: int = 0


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


# ---------------------------------------------------------------------------
# integrity：跨表一致性写前校验（§10b）
# ---------------------------------------------------------------------------
def _normalize_pair(a: str, b: str) -> tuple[str, str]:
    if not a or not b:
        raise ConflictIntegrityError("evidence id 不能为空")
    if a == b:
        raise ConflictIntegrityError(f"evidence_a != evidence_b 违反：{a}")
    return (a, b) if a < b else (b, a)


def _guard_pair(
    store, run_id: str, claim_id: str, evidence_a: str, evidence_b: str
) -> None:
    ev_a, ev_b = _normalize_pair(evidence_a, evidence_b)

    def _require_owned(table: str, key_col: str, key: str, label: str) -> dict:
        row = _fetch_one(store, f"SELECT * FROM {table} WHERE {key_col} = %s", (key,))
        if row is None:
            raise ConflictIntegrityError(f"{label} 不存在: {key}")
        if row["run_id"] != run_id:
            raise ConflictIntegrityError(f"{label} 不属于该 run（integrity）")
        return row

    claim = _require_owned("claims", "claim_id", claim_id, "claim")
    if claim["run_id"] != run_id:
        raise ConflictIntegrityError("claim.run_id != run_id（integrity）")
    _require_owned("evidences", "evidence_id", ev_a, "evidence_a")
    _require_owned("evidences", "evidence_id", ev_b, "evidence_b")

    for eid in (ev_a, ev_b):
        rows = store.execute(
            "SELECT binding_id FROM claim_evidences "
            "WHERE run_id=%s AND claim_id=%s AND evidence_id=%s",
            (run_id, claim_id, eid),
        )
        if not rows:
            raise ConflictIntegrityError(
                f"evidence {eid} 未 binding 到 claim（integrity）"
            )
    return None


def _guard_verification(
    store, run_id: str, claim_id: str, evidence_id: str, verification_id: Optional[str]
):
    if verification_id is None:
        return None
    row = _fetch_one(
        store,
        "SELECT * FROM verifications WHERE verification_id = %s",
        (verification_id,),
    )
    if row is None:
        raise ConflictIntegrityError(f"verification 不存在: {verification_id}")
    if (
        row["run_id"] != run_id
        or row["claim_id"] != claim_id
        or row["evidence_id"] != evidence_id
    ):
        raise ConflictIntegrityError(
            "verification 与 run/claim/evidence 不对齐（integrity）"
        )
    return row


def _signal_snapshot(row: Optional[dict]) -> Optional[dict]:
    """F3 signal 快照（verification 删除后仍保留，防 provenance 丢失）。"""
    if row is None:
        return None
    return {
        "verification_id": row["verification_id"],
        "verdict": row.get("verdict"),
        "status": row.get("status"),
    }


# ---------------------------------------------------------------------------
# Context Envelope（最小包；F3 verdict 仅作为 signal，不改判）
# ---------------------------------------------------------------------------
def build_conflict_context(
    claim_row: dict,
    evidence_a_row: dict,
    evidence_b_row: dict,
    signal_a: Optional[dict],
    signal_b: Optional[dict],
) -> dict[str, Any]:
    def _ev_part(row: dict, signal: Optional[dict]) -> dict[str, Any]:
        part: dict[str, Any] = {
            "evidence_id": row["evidence_id"],
            "content": row["content"],
        }
        src = _fetch_one(
            _get_store(),
            "SELECT * FROM sources WHERE source_id = %s",
            (row.get("source_id"),),
        )
        if src is not None:
            part["source_meta"] = {
                k: src.get(k)
                for k in ("title", "canonical_url", "locator", "published_at")
                if src.get(k) is not None
            }
        if signal is not None:
            part["signal"] = {
                "verdict": signal.get("verdict"),
                "status": signal.get("status"),
            }
        return part

    return {
        "claim": {
            "claim_id": claim_row["claim_id"],
            "statement": claim_row["statement"],
            "claim_type": claim_row["claim_type"],
        },
        "evidence_a": _ev_part(evidence_a_row, signal_a),
        "evidence_b": _ev_part(evidence_b_row, signal_b),
    }


# ---------------------------------------------------------------------------
# detector：Fake 默认；RealLLM controlled optional（SHALL NOT re-evaluate support）
# ---------------------------------------------------------------------------
class BaseDetector:
    def respond(self, payload: dict[str, Any], hint: str = "") -> str:
        raise NotImplementedError


class FakeDetector(BaseDetector):
    def __init__(
        self,
        conflict_type: str = ConflictType.CONTRADICTION.value,
        resolver: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None,
        queue: Optional[list[Any]] = None,
    ) -> None:
        self.conflict_type = conflict_type
        self.resolver = resolver
        self.queue: list[Any] = list(queue or [])
        self.calls: list[dict[str, Any]] = []

    def respond(self, payload: dict[str, Any], hint: str = "") -> str:
        self.calls.append(
            {
                "claim_id": payload.get("claim", {}).get("claim_id"),
                "a": payload.get("evidence_a", {}).get("evidence_id"),
                "b": payload.get("evidence_b", {}).get("evidence_id"),
            }
        )
        if self.queue:
            item = self.queue.pop(0)
            if isinstance(item, Exception):
                if isinstance(item, DetectorRuntimeError):
                    raise item
                raise DetectorRuntimeError("provider", str(item))
            return item
        outcome = (
            self.resolver(payload)
            if self.resolver is not None
            else {
                "genuine": self.conflict_type in GENUINE_TYPES,
                "conflict_type": self.conflict_type,
            }
        )
        return json.dumps(
            {
                "genuine": bool(outcome.get("genuine", False)),
                "conflict_type": outcome.get("conflict_type", self.conflict_type),
                "rationale": outcome.get("rationale", "fake rationale"),
            },
            ensure_ascii=False,
        )


class RealLLMDetector(BaseDetector):
    """可选 controlled real-LLM adapter（显式 VERIFY_REAL_LLM=1；不进自动化 Gate）。"""

    def __init__(self, spec: VerifierSpec) -> None:
        import os

        if os.getenv("VERIFY_REAL_LLM", "0") != "1":
            raise RuntimeError("real-LLM detector 需显式设置 VERIFY_REAL_LLM=1")
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("real-LLM detector 需要 openai SDK") from exc
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("real-LLM detector 需要 OPENAI_API_KEY")
        kwargs: dict[str, Any] = {"api_key": api_key}
        base_url = os.getenv("OPENAI_BASE_URL")
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)
        self.spec = spec

    def respond(self, payload: dict[str, Any], hint: str = "") -> str:
        system = _DETECTOR_INSTRUCTION + (f"\n{hint}" if hint else "")
        resp = self._client.chat.completions.create(
            model=self.spec.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=self.spec.temperature,
            max_tokens=self.spec.max_tokens,
            timeout_ms=self.spec.timeout_ms,
        )
        content = (resp.choices[0].message.content or "").strip()
        if not content:
            raise DetectorRuntimeError("provider", "empty response")
        return content


# ---------------------------------------------------------------------------
# parse + invariant（§10c）
# ---------------------------------------------------------------------------
def _parse_detector_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("detector 输出不是 JSON object")
    return payload


def _derive_outcome(payload: dict[str, Any]) -> dict[str, Any]:
    """按 §10c invariant 从输出推导状态；genuine 与 type 矛盾 → 视为 malformed。"""
    ctype = (payload.get("conflict_type") or "").strip().upper()
    if ctype not in {t.value for t in ConflictType}:
        raise ValueError(f"非法 conflict_type: {ctype!r}")
    genuine = payload.get("genuine")
    if not isinstance(genuine, bool):
        raise ValueError("genuine 缺失/非 boolean")
    derived = ctype in GENUINE_TYPES
    if genuine != derived:
        raise ValueError("genuine 与 conflict_type 矛盾（invariant）")
    rationale = str(payload.get("rationale") or "")[:DETECTOR_RATIONALE_MAX] or None
    status = (
        ConflictStatus.CONFIRMED.value if derived else ConflictStatus.REJECTED.value
    )
    return {
        "status": status,
        "conflict_type": ctype,
        "genuine": derived,
        "rationale": rationale,
    }


# ---------------------------------------------------------------------------
# persistence（唯一写路径）
# ---------------------------------------------------------------------------
def _existing(store, run_id, claim_id, ev_a, ev_b, fp) -> Optional[dict]:
    return _fetch_one(
        store,
        "SELECT * FROM conflicts WHERE run_id=%s AND claim_id=%s AND evidence_a_id=%s "
        "AND evidence_b_id=%s AND detector_fingerprint=%s",
        (run_id, claim_id, ev_a, ev_b, fp),
    )


def _insert_candidate(
    store,
    run_id,
    claim_id,
    ev_a,
    ev_b,
    spec,
    fp,
    source,
    ver_a,
    ver_b,
    metadata_extra=None,
) -> str:
    cid = ids.new_conflict_id()
    now = _utcnow_iso()
    meta = dict(metadata_extra or {})
    with store.transaction() as tx:
        tx.execute(
            "INSERT INTO conflicts (conflict_id, run_id, claim_id, evidence_a_id, "
            "evidence_b_id, verification_a_id, verification_b_id, detector_spec, "
            "detector_fingerprint, candidate_source, status, conflict_type, genuine, "
            "rationale, error, created_at, completed_at, metadata) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,NULL,NULL,NULL,%s,NULL,%s)",
            (
                cid,
                run_id,
                claim_id,
                ev_a,
                ev_b,
                ver_a,
                ver_b,
                json_param(spec.to_dict(), store.dialect),
                fp,
                source,
                ConflictStatus.CANDIDATE.value,
                now,
                json_param(meta, store.dialect),
            ),
        )
    return cid


def _complete(
    store,
    conflict_id: str,
    *,
    status: str,
    conflict_type,
    genuine,
    rationale=None,
    error=None,
) -> None:
    now = _utcnow_iso()
    with store.transaction() as tx:
        tx.execute(
            "UPDATE conflicts SET status=%s, conflict_type=%s, genuine=%s, rationale=%s, "
            "error=%s, completed_at=%s WHERE conflict_id=%s",
            (status, conflict_type, genuine, rationale, error, now, conflict_id),
        )


# ---------------------------------------------------------------------------
# run/claim 级入口
# ---------------------------------------------------------------------------
def _gate_check(run_id: str) -> None:
    report = validate_run(run_id)
    if report.errors:
        rules = sorted({e["rule"] for e in report.errors})
        raise ConflictGateError(
            f"run {run_id} 存在 F2/F3 deterministic error {rules}，拒绝 conflict detection"
        )


def _candidate_pairs(
    store, claim_row: dict, spec, detector, counter, budget
) -> list[dict]:
    """确定性候选：SUPPORTS × CONTRADICTS（同一 claim 的 F3 成功 verdict）。"""
    rows = store.execute(
        "SELECT evidence_id, verdict, verification_id FROM verifications "
        "WHERE run_id=%s AND claim_id=%s AND status='succeeded' AND verdict IS NOT NULL",
        (claim_row["run_id"], claim_row["claim_id"]),
    )
    supports: dict[str, str] = {}
    contradicts: dict[str, str] = {}
    for r in rows:
        if r["verdict"] == "SUPPORTS":
            supports[r["evidence_id"]] = r["verification_id"]
        elif r["verdict"] == "CONTRADICTS":
            contradicts[r["evidence_id"]] = r["verification_id"]
    pairs = []
    for s_ev in supports:
        for c_ev in contradicts:
            a, b = _normalize_pair(s_ev, c_ev)
            pairs.append(
                {
                    "ev_a": a,
                    "ev_b": b,
                    "ver_a": supports.get(a) or contradicts.get(a),
                    "ver_b": supports.get(b) or contradicts.get(b),
                }
            )
    # cited 优先 + 上限
    cited = [
        r["evidence_id"]
        for r in store.execute(
            "SELECT evidence_id FROM citations WHERE run_id=%s AND claim_id=%s "
            "ORDER BY created_at, citation_id",
            (claim_row["run_id"], claim_row["claim_id"]),
        )
    ]
    pairs.sort(
        key=lambda p: (
            p["ev_a"] not in cited,
            p["ev_b"] not in cited,
            p["ev_a"],
            p["ev_b"],
        )
    )
    return pairs[: budget.max_pairs_per_claim]


def _prepare_candidate(
    store, claim_row: dict, pair: dict, spec: VerifierSpec, fp: str
) -> dict:
    """预插入 candidate（幂等）；返回 prepared（cid + 对齐的 verification rows）。"""
    run_id = claim_row["run_id"]
    _guard_pair(store, run_id, claim_row["claim_id"], pair["ev_a"], pair["ev_b"])
    ver_a = _guard_verification(
        store, run_id, claim_row["claim_id"], pair["ev_a"], pair["ver_a"]
    )
    ver_b = _guard_verification(
        store, run_id, claim_row["claim_id"], pair["ev_b"], pair["ver_b"]
    )
    existing = _existing(
        store, run_id, claim_row["claim_id"], pair["ev_a"], pair["ev_b"], fp
    )
    if existing is None:
        cid = _insert_candidate(
            store,
            run_id,
            claim_row["claim_id"],
            pair["ev_a"],
            pair["ev_b"],
            spec,
            fp,
            "verdict_opposed",
            ver_a["verification_id"] if ver_a else None,
            ver_b["verification_id"] if ver_b else None,
            metadata_extra={
                "signal_snapshot_a": _signal_snapshot(ver_a),
                "signal_snapshot_b": _signal_snapshot(ver_b),
            },
        )
    else:
        cid = existing["conflict_id"]
    return {"pair": pair, "conflict_id": cid}


def _confirm_pair(
    store, claim_row, prepared, spec, fp, detector, counter
) -> dict[str, Any]:
    """对已落库 candidate 执行 semantic confirm；confirmed/rejected 复用；candidate/failed 更新同行。"""
    run_id = claim_row["run_id"]
    pair = prepared["pair"]
    cid = prepared["conflict_id"]
    existing = _existing(
        store, run_id, claim_row["claim_id"], pair["ev_a"], pair["ev_b"], fp
    )
    if existing is not None and existing["status"] in {
        ConflictStatus.CONFIRMED.value,
        ConflictStatus.REJECTED.value,
    }:
        return {
            "conflict_id": existing["conflict_id"],
            "reused": True,
            "status": existing["status"],
            "conflict_type": existing.get("conflict_type"),
            "evidence_a_id": pair["ev_a"],
            "evidence_b_id": pair["ev_b"],
        }

    ver_a = _guard_verification(
        store, run_id, claim_row["claim_id"], pair["ev_a"], pair["ver_a"]
    )
    ver_b = _guard_verification(
        store, run_id, claim_row["claim_id"], pair["ev_b"], pair["ver_b"]
    )
    ev_a_row = _fetch_one(
        store, "SELECT * FROM evidences WHERE evidence_id=%s", (pair["ev_a"],)
    )
    ev_b_row = _fetch_one(
        store, "SELECT * FROM evidences WHERE evidence_id=%s", (pair["ev_b"],)
    )
    if ev_a_row is None or ev_b_row is None:
        raise ConflictIntegrityError("candidate evidence 不存在")

    payload = build_conflict_context(
        claim_row,
        ev_a_row,
        ev_b_row,
        _signal_snapshot(ver_a),
        _signal_snapshot(ver_b),
    )
    outcome: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    for attempt in range(1, 3):
        counter.calls += 1
        try:
            text = detector.respond(
                payload,
                hint=("" if attempt == 1 else "上次输出非法，只输出合法 JSON。"),
            )
        except DetectorRuntimeError as exc:
            if attempt == 2:
                error = f"{exc.kind}: {exc}"
            continue
        except Exception as exc:  # noqa: BLE001
            if attempt == 2:
                error = f"provider: {exc}"
            continue
        try:
            parsed = _parse_detector_json(text)
            outcome = _derive_outcome(parsed)
            break
        except (ValueError, json.JSONDecodeError):
            if attempt == 2:
                error = "malformed_output: 两次输出均无法解析为合法 conflict JSON"

    if outcome is not None:
        _complete(
            store,
            cid,
            status=outcome["status"],
            conflict_type=outcome["conflict_type"],
            genuine=outcome["genuine"],
            rationale=outcome["rationale"],
        )
        return {
            "conflict_id": cid,
            "reused": False,
            "status": outcome["status"],
            "conflict_type": outcome["conflict_type"],
            "evidence_a_id": pair["ev_a"],
            "evidence_b_id": pair["ev_b"],
        }
    _complete(
        store,
        cid,
        status=ConflictStatus.FAILED.value,
        conflict_type=None,
        genuine=None,
        error=error,
    )
    return {
        "conflict_id": cid,
        "reused": False,
        "status": "failed",
        "conflict_type": None,
        "evidence_a_id": pair["ev_a"],
        "evidence_b_id": pair["ev_b"],
        "error": error,
    }


def detect_claim_conflicts(
    claim_id: str,
    *,
    spec: Optional[VerifierSpec] = None,
    budget: Optional[ConflictBudget] = None,
    detector: Optional[BaseDetector] = None,
    explicit_pairs: Optional[list[tuple[str, str]]] = None,
    counter: Optional[CallCounter] = None,
) -> dict[str, Any]:
    """单 claim 冲突检测。explicit_pairs 仅绕过 verdict 候选，不绕过任何 integrity。"""
    store = _get_store()
    claim_row = _fetch_one(store, "SELECT * FROM claims WHERE claim_id=%s", (claim_id,))
    if claim_row is None:
        raise ValueError(f"claim 不存在: {claim_id}")
    run_id = claim_row["run_id"]
    _gate_check(run_id)
    budget = budget or ConflictBudget()
    if not budget.include_opinion and claim_row.get("claim_type") == "OPINION":
        return {
            "claim_id": claim_id,
            "run_id": run_id,
            "skipped": "opinion",
            "items": [],
        }
    spec = spec or DEFAULT_DETECTOR_SPEC
    detector = detector or FakeDetector()
    counter = counter or CallCounter()

    items = []
    fp = spec.fingerprint()
    if explicit_pairs is not None:
        for x, y in explicit_pairs:
            a, b = _normalize_pair(x, y)
            _guard_pair(store, run_id, claim_id, a, b)
            ver_a = _fetch_one(
                store,
                "SELECT verification_id FROM verifications WHERE run_id=%s AND claim_id=%s "
                "AND evidence_id=%s AND status='succeeded' ORDER BY created_at LIMIT 1",
                (run_id, claim_id, a),
            )
            ver_b = _fetch_one(
                store,
                "SELECT verification_id FROM verifications WHERE run_id=%s AND claim_id=%s "
                "AND evidence_id=%s AND status='succeeded' ORDER BY created_at LIMIT 1",
                (run_id, claim_id, b),
            )
            prepared = _prepare_candidate(
                store,
                claim_row,
                {
                    "ev_a": a,
                    "ev_b": b,
                    "ver_a": ver_a["verification_id"] if ver_a else None,
                    "ver_b": ver_b["verification_id"] if ver_b else None,
                },
                spec,
                fp,
            )
            items.append(
                _confirm_pair(store, claim_row, prepared, spec, fp, detector, counter)
            )
    else:
        pairs = _candidate_pairs(store, claim_row, spec, detector, counter, budget)
        # 先全量落 candidate（预算截断时保留 candidate 残留）
        prepared_list = [
            _prepare_candidate(store, claim_row, pair, spec, fp) for pair in pairs
        ]
        for prepared in prepared_list:
            if (
                budget.max_semantic_calls is not None
                and counter.calls >= budget.max_semantic_calls
            ):
                break
            items.append(
                _confirm_pair(store, claim_row, prepared, spec, fp, detector, counter)
            )
    return {
        "claim_id": claim_id,
        "run_id": run_id,
        "items": items,
        "llm_calls": counter.calls,
        "summary": claim_conflict_summary(claim_id),
    }


def detect_run_conflicts(
    run_id: str,
    *,
    spec: Optional[VerifierSpec] = None,
    budget: Optional[ConflictBudget] = None,
    detector: Optional[BaseDetector] = None,
) -> dict[str, Any]:
    store = _get_store()
    _gate_check(run_id)
    budget = budget or ConflictBudget()
    spec = spec or DEFAULT_DETECTOR_SPEC
    detector = detector or FakeDetector()
    counter = CallCounter()

    rows = store.execute(
        "SELECT DISTINCT c.* FROM claims c "
        "JOIN claim_evidences ce ON ce.claim_id = c.claim_id "
        "WHERE c.run_id = %s ORDER BY c.created_at, c.claim_id",
        (run_id,),
    )
    summary = {
        "run_id": run_id,
        "claims": [],
        "budget_exhausted": False,
        "candidates": 0,
        "confirmed": 0,
        "rejected": 0,
        "failed": 0,
        "llm_calls": 0,
        "partial": False,
    }
    for claim_row in rows:
        if not budget.include_opinion and claim_row.get("claim_type") == "OPINION":
            continue
        if (
            budget.max_semantic_calls is not None
            and counter.calls >= budget.max_semantic_calls
        ):
            summary["budget_exhausted"] = True
            break
        try:
            result = detect_claim_conflicts(
                claim_row["claim_id"],
                spec=spec,
                budget=budget,
                detector=detector,
                counter=counter,
            )
        except (ConflictGateError, ConflictIntegrityError):
            continue
        items = result.get("items", [])
        summary["claims"].append(
            {
                "claim_id": claim_row["claim_id"],
                "items": items,
                "summary": result.get("summary"),
            }
        )
        summary["candidates"] += len(items)
        summary["confirmed"] += sum(1 for i in items if i.get("status") == "confirmed")
        summary["rejected"] += sum(1 for i in items if i.get("status") == "rejected")
        summary["failed"] += sum(1 for i in items if i.get("status") == "failed")
        if (
            budget.max_semantic_calls is not None
            and counter.calls >= budget.max_semantic_calls
        ):
            summary["budget_exhausted"] = True
            break
    summary["llm_calls"] = counter.calls
    summary["partial"] = summary["failed"] > 0 or summary["budget_exhausted"]
    return summary


# ---------------------------------------------------------------------------
# 只读查询 / provenance / 汇总
# ---------------------------------------------------------------------------
def list_conflicts(
    run_id: str,
    *,
    claim_id: Optional[str] = None,
    status: Optional[str] = None,
    conflict_type: Optional[str] = None,
) -> list[dict[str, Any]]:
    store = _get_store()
    sql = "SELECT * FROM conflicts WHERE run_id = %s"
    params: list[Any] = [run_id]
    if claim_id:
        sql += " AND claim_id = %s"
        params.append(claim_id)
    if status:
        sql += " AND status = %s"
        params.append(status)
    if conflict_type:
        sql += " AND conflict_type = %s"
        params.append(conflict_type)
    sql += " ORDER BY created_at, conflict_id"
    return [_conflict_dict(r) for r in store.execute(sql, tuple(params))]


def conflict_chain(conflict_id: str) -> Optional[dict[str, Any]]:
    store = _get_store()
    row = _fetch_one(
        store, "SELECT * FROM conflicts WHERE conflict_id=%s", (conflict_id,)
    )
    if row is None:
        return None

    def _one(table: str, key_col: str, value: str):
        r = _fetch_one(store, f"SELECT * FROM {table} WHERE {key_col}=%s", (value,))
        return _clean(r)

    def _clean(r):
        if r is None:
            return None
        return {
            k: (
                v.isoformat()
                if isinstance(v, datetime.datetime)
                else _maybe_json(v)
                if isinstance(v, (dict, list))
                else v
            )
            for k, v in dict(r).items()
        }

    return {
        "conflict": _clean(row),
        "claim": _one("claims", "claim_id", row["claim_id"]),
        "evidence_a": _one("evidences", "evidence_id", row["evidence_a_id"]),
        "evidence_b": _one("evidences", "evidence_id", row["evidence_b_id"]),
        "verification_a": (
            _one("verifications", "verification_id", row["verification_a_id"])
            if row.get("verification_a_id")
            else None
        ),
        "verification_b": (
            _one("verifications", "verification_id", row["verification_b_id"])
            if row.get("verification_b_id")
            else None
        ),
        "detector_spec": _maybe_json(row.get("detector_spec")) or {},
    }


def claim_conflict_summary(claim_id: str) -> dict[str, Any]:
    store = _get_store()
    claim_row = _fetch_one(store, "SELECT * FROM claims WHERE claim_id=%s", (claim_id,))
    if claim_row is None:
        raise ValueError(f"claim 不存在: {claim_id}")
    rows = store.execute(
        "SELECT status, conflict_type FROM conflicts WHERE run_id=%s AND claim_id=%s",
        (claim_row["run_id"], claim_id),
    )
    counts = {t.value: 0 for t in ConflictType}
    statuses = {s.value: 0 for s in ConflictStatus}
    confirmed = 0
    for r in rows:
        statuses[r["status"]] = statuses.get(r["status"], 0) + 1
        if r.get("conflict_type") and r["status"] == ConflictStatus.CONFIRMED.value:
            counts[r["conflict_type"]] = counts.get(r["conflict_type"], 0) + 1
            confirmed += 1
    return {
        "claim_id": claim_id,
        "has_confirmed_conflict": confirmed > 0,
        "counts": counts,
        "statuses": statuses,
    }


def _conflict_dict(row: dict) -> dict[str, Any]:
    return {
        "conflict_id": row["conflict_id"],
        "run_id": row["run_id"],
        "claim_id": row["claim_id"],
        "evidence_a_id": row["evidence_a_id"],
        "evidence_b_id": row["evidence_b_id"],
        "verification_a_id": row.get("verification_a_id"),
        "verification_b_id": row.get("verification_b_id"),
        "detector_spec": _maybe_json(row.get("detector_spec")) or {},
        "detector_fingerprint": row["detector_fingerprint"],
        "candidate_source": row["candidate_source"],
        "status": row["status"],
        "conflict_type": row.get("conflict_type"),
        "genuine": row.get("genuine"),
        "rationale": row.get("rationale"),
        "error": row.get("error"),
        "created_at": _iso(row["created_at"]),
        "completed_at": _iso(row.get("completed_at")),
        "metadata": _maybe_json(row.get("metadata")) or {},
    }
