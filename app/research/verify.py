"""F3 Semantic Verification（docs/spec/2026-09-09-semantic-verification.md rev2）。

分层：F2 deterministic validation（R1–R10）是结构 Gate；F3 只做"Evidence 内容是否足以支持
Claim"的语义判断。本模块属 research artifact 平面的后处理消费者：

- 不新增 Agent、不进 Agent 回路、不接 message history / Agent-visible context；
- 单条 (claim, evidence) verification 仅允许 VerifyStatus.pending/succeeded/failed，
  partial 只属 batch/run 层 ExecutionSummary；
- SemanticVerdict 仅 5 种（无 ERROR）；confidence 为辅助信号（NULL 合法，禁止伪造）；
- verifier 默认 FakeVerifier；真实 LLM 为可选的 controlled adapter（显式启用，不进自动化 Gate）；
- 幂等键 UNIQUE(run_id, claim_id, evidence_id, verifier_fingerprint)；不引入 task_call_id。

实现纪律：结构 Gate errors 非空 → 拒绝写入（VerificationGateError）；
R3/R10 warning 不阻止单条验证；citation coverage 只作默认候选优先级；allowed_evidence_ids 显式覆盖。
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from app.research import ids, store as _store
from app.research.schemas import SemanticVerdict, VerifyStatus
from app.research.store import json_param
from app.research.validate import validate_run

logger = logging.getLogger("deepsearch.research.verify")

#: 系统确定性 acceptance policy：confidence < 阈值 → ABSTAIN（非统计概率语义）
DEFAULT_CONFIDENCE_THRESHOLD = 0.6
DEFAULT_RATIONALE_MAX = 2000
DEFAULT_TIMEOUT_MS = 30000

_INSTRUCTION = (
    "判断给出的 evidence 内容（含 quote）是否足以支持 claim。只输出 JSON："
    '{"verdict": "SUPPORTS|INSUFFICIENT|CONTRADICTS|UNVERIFIABLE|ABSTAIN", '
    '"rationale": "<string>", "confidence": <number|null>}。'
    "INSUFFICIENT/UNVERIFIABLE/ABSTAIN 是合法输出，禁止编造支持。"
    "source 标题/URL/元数据只用于标识，不得作为证据内容。"
)


class VerificationGateError(Exception):
    """run 存在 F2 deterministic error → semantic verification 被拒。"""


class VerifierRuntimeError(Exception):
    """verifier 执行失败（timeout/provider/parse）；kind 用于 retry 策略。"""

    def __init__(self, kind: str, message: str = "") -> None:
        super().__init__(message or kind)
        self.kind = kind


class StoreUnavailableError(RuntimeError):
    """research store 不可用。"""


# ---------------------------------------------------------------------------
# VerifierSpec / VerifyBudget
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class VerifierSpec:
    """verifier 配置快照（fingerprint 唯一键的组成）。"""

    provider: str = "fake"
    model: str = "fake-model"
    prompt_version: str = "2026-09-09.v1"
    temperature: float = 0.0
    max_tokens: int = 256
    timeout_ms: int = DEFAULT_TIMEOUT_MS

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout_ms": self.timeout_ms,
        }

    def fingerprint(self) -> str:
        canonical = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class VerifyBudget:
    """预算与候选策略（deterministic，零 LLM 成本）。"""

    max_evidence_per_claim: int = 8
    max_claims_per_run: Optional[int] = None
    max_llm_calls: Optional[int] = None
    include_opinion: bool = False


@dataclass
class AttemptCounter:
    """单次执行内的 LLM 调用计数（budget 门）。"""

    calls: int = 0


# ---------------------------------------------------------------------------
# 基础 helpers
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


def _require_claim(store, claim_id: str) -> dict:
    row = _fetch_one(store, "SELECT * FROM claims WHERE claim_id = %s", (claim_id,))
    if row is None:
        raise ValueError(f"claim 不存在: {claim_id}")
    return row


def _bound_evidence_ids(store, claim_id: str) -> list[str]:
    rows = store.execute(
        "SELECT evidence_id, created_at FROM claim_evidences "
        "WHERE claim_id = %s ORDER BY created_at, binding_id",
        (claim_id,),
    )
    return [r["evidence_id"] for r in rows]


def _cited_evidence_ids(store, run_id: str, claim_id: str) -> list[str]:
    rows = store.execute(
        "SELECT evidence_id FROM citations "
        "WHERE run_id = %s AND claim_id = %s ORDER BY created_at, citation_id",
        (run_id, claim_id),
    )
    return [r["evidence_id"] for r in rows]


def _evidence_row(store, evidence_id: str) -> dict:
    row = _fetch_one(
        store, "SELECT * FROM evidences WHERE evidence_id = %s", (evidence_id,)
    )
    if row is None:
        raise ValueError(f"evidence 不存在: {evidence_id}")
    return row


def _source_for(store, source_id: str) -> Optional[dict]:
    return _fetch_one(store, "SELECT * FROM sources WHERE source_id = %s", (source_id,))


def _quote_for(store, claim_id: str, evidence_id: str) -> Optional[str]:
    rows = store.execute(
        "SELECT quote FROM citations WHERE claim_id = %s AND evidence_id = %s "
        "ORDER BY created_at LIMIT 1",
        (claim_id, evidence_id),
    )
    return rows[0]["quote"] if rows and rows[0].get("quote") else None


# ---------------------------------------------------------------------------
# Evidence Context builder（source metadata 仅标识，非证据内容）
# ---------------------------------------------------------------------------
def build_evidence_context(
    claim_row: dict,
    evidence_row: dict,
    source_row: Optional[dict],
    quote: Optional[str],
) -> dict[str, Any]:
    """构造 LLM 唯一输入包。不含 message history / 其他 claim / 会话上下文。"""
    ctx: dict[str, Any] = {
        "claim": {
            "claim_id": claim_row["claim_id"],
            "statement": claim_row["statement"],
            "claim_type": claim_row["claim_type"],
        },
        "evidence": {
            "evidence_id": evidence_row["evidence_id"],
            "content": evidence_row["content"],
        },
    }
    if quote:
        ctx["evidence"]["quote"] = quote
    if source_row is not None:
        # Source metadata 是 provenance context，不是 evidentiary content
        ctx["evidence"]["source_meta"] = {
            k: source_row.get(k)
            for k in ("title", "canonical_url", "locator", "published_at")
            if source_row.get(k) is not None
        }
    ctx["evidence"]["extraction_method"] = evidence_row.get("extraction_method")
    return ctx


# ---------------------------------------------------------------------------
# JSON 解析（deterministic）
# ---------------------------------------------------------------------------
def parse_verifier_json(text: str) -> dict[str, Any]:
    """解析 verifier JSON 输出（容忍代码块围栏）。"""
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
        raise ValueError("verifier 输出不是 JSON object")
    return payload


def _normalize_verdict(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("verdict 缺失/非法")
    v = value.strip().upper()
    if v not in {item.value for item in SemanticVerdict}:
        raise ValueError(f"非法 verdict: {value!r}")
    return v


# ---------------------------------------------------------------------------
# Verifier 抽象：Fake 默认；RealLLM 可选 controlled adapter
# ---------------------------------------------------------------------------
class BaseVerifier:
    """verifier 协议：respond(payload, hint) -> 原始文本（随后走 deterministic 解析）。"""

    def respond(self, payload: dict[str, Any], hint: str = "") -> str:
        raise NotImplementedError


class FakeVerifier(BaseVerifier):
    """默认测试通道：确定性输出；可选 queue 模拟 malformed/异常/按 payload 决策。"""

    def __init__(
        self,
        default_verdict: str = SemanticVerdict.SUPPORTS.value,
        default_confidence: Optional[float] = None,
        resolver: Optional[Callable[[dict[str, Any]], str]] = None,
        queue: Optional[list[Any]] = None,
    ) -> None:
        self.default_verdict = default_verdict
        self.default_confidence = default_confidence
        self.resolver = resolver
        self.queue: list[Any] = list(queue or [])
        self.calls: list[dict[str, Any]] = []

    def respond(self, payload: dict[str, Any], hint: str = "") -> str:
        self.calls.append(
            {
                "claim_id": payload.get("claim", {}).get("claim_id"),
                "evidence_id": payload.get("evidence", {}).get("evidence_id"),
                "content_len": len(payload.get("evidence", {}).get("content", "")),
            }
        )
        if self.queue:
            item = self.queue.pop(0)
            if isinstance(item, Exception):
                if isinstance(item, VerifierRuntimeError):
                    raise item
                raise VerifierRuntimeError("provider", str(item))
            return item  # 模拟 malformed 文本
        verdict = (
            self.resolver(payload)
            if self.resolver is not None
            else self.default_verdict
        )
        conf = self.default_confidence
        return json.dumps(
            {"verdict": verdict, "rationale": "fake rationale", "confidence": conf},
            ensure_ascii=False,
        )


class RealLLMVerifier(BaseVerifier):
    """可选的 controlled real-LLM adapter（显式启用，不进自动化 Gate）。

    复用现有 OpenAI-compatible 环境变量（OPENAI_BASE_URL / OPENAI_API_KEY）与 openai SDK；
    未启用/缺依赖时构造抛错，调用方不应在默认路径使用。
    """

    def __init__(self, spec: VerifierSpec) -> None:
        import os

        if os.getenv("VERIFY_REAL_LLM", "0") != "1":
            raise RuntimeError("real-LLM verifier 需显式设置 VERIFY_REAL_LLM=1")
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - 依赖缺失路径
            raise RuntimeError("real-LLM verifier 需要 openai SDK") from exc
        base_url = os.getenv("OPENAI_BASE_URL")
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("real-LLM verifier 需要 OPENAI_API_KEY")
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)
        self.spec = spec

    def respond(self, payload: dict[str, Any], hint: str = "") -> str:
        system = _INSTRUCTION + (f"\n{hint}" if hint else "")
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
            raise VerifierRuntimeError("provider", "empty response")
        return content


DEFAULT_SPEC = VerifierSpec()


# ---------------------------------------------------------------------------
# 持久化（唯一写路径）与幂等
# ---------------------------------------------------------------------------
def _existing(
    store, run_id: str, claim_id: str, evidence_id: str, fp: str
) -> Optional[dict]:
    return _fetch_one(
        store,
        "SELECT * FROM verifications WHERE run_id=%s AND claim_id=%s "
        "AND evidence_id=%s AND verifier_fingerprint=%s",
        (run_id, claim_id, evidence_id, fp),
    )


def _insert_pending(store, run_id, claim_id, evidence_id, spec, fp) -> str:
    vid = ids.new_verification_id()
    now = _utcnow_iso()
    with store.transaction() as tx:
        tx.execute(
            "INSERT INTO verifications (verification_id, run_id, claim_id, evidence_id, "
            "verifier_spec, verifier_fingerprint, verdict, rationale, confidence, status, "
            "error, created_at, completed_at, metadata) "
            "VALUES (%s,%s,%s,%s,%s,%s,NULL,NULL,NULL,%s,NULL,%s,NULL,%s)",
            (
                vid,
                run_id,
                claim_id,
                evidence_id,
                json_param(spec.to_dict(), store.dialect),
                fp,
                VerifyStatus.PENDING.value,
                now,
                json_param({}, store.dialect),
            ),
        )
    return vid


def _complete(
    store,
    verification_id: str,
    *,
    verdict: Optional[str],
    rationale: Optional[str],
    confidence: Optional[float],
    status: str,
    error: Optional[str] = None,
) -> None:
    now = _utcnow_iso()
    with store.transaction() as tx:
        tx.execute(
            "UPDATE verifications SET verdict=%s, rationale=%s, confidence=%s, "
            "status=%s, error=%s, completed_at=%s WHERE verification_id=%s",
            (
                verdict,
                rationale,
                confidence,
                status,
                error,
                now if status != VerifyStatus.PENDING.value else None,
                verification_id,
            ),
        )


# ---------------------------------------------------------------------------
# 单条 (claim, evidence) 验证 + retry/failure 语义
# ---------------------------------------------------------------------------
def _verify_pair(
    store,
    run_id: str,
    claim_row: dict,
    evidence_row: dict,
    spec: VerifierSpec,
    verifier: BaseVerifier,
    counter: AttemptCounter,
    threshold: Optional[float],
    timeout_ms: Optional[int],
) -> dict[str, Any]:
    fp = spec.fingerprint()
    existing = _existing(
        store, run_id, claim_row["claim_id"], evidence_row["evidence_id"], fp
    )
    if existing is not None and existing["status"] == VerifyStatus.SUCCEEDED.value:
        return {
            "verification_id": existing["verification_id"],
            "reused": True,
            "status": "succeeded",
            "verdict": existing["verdict"],
            "evidence_id": evidence_row["evidence_id"],
        }
    vid = (
        existing["verification_id"]
        if existing is not None
        else _insert_pending(
            store, run_id, claim_row["claim_id"], evidence_row["evidence_id"], spec, fp
        )
    )

    quote = _quote_for(store, claim_row["claim_id"], evidence_row["evidence_id"])
    payload = build_evidence_context(
        claim_row,
        evidence_row,
        _source_for(store, evidence_row.get("source_id")),
        quote,
    )
    outcome: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    for attempt in range(1, 3):  # deterministic retry ×1
        counter.calls += 1
        try:
            text = verifier.respond(
                payload,
                hint=("" if attempt == 1 else "上次输出非法，只输出合法 JSON。"),
            )
        except VerifierRuntimeError as exc:
            if attempt == 2:
                error = f"{exc.kind}: {exc}"
            continue
        except Exception as exc:  # noqa: BLE001
            if attempt == 2:
                error = f"provider: {exc}"
            continue
        try:
            parsed = parse_verifier_json(text)
            verdict = _normalize_verdict(parsed.get("verdict"))
            confidence = parsed.get("confidence")
            if confidence is not None and not isinstance(confidence, (int, float)):
                raise ValueError("confidence 非数值")
            confidence = float(confidence) if confidence is not None else None
            rationale = (
                str(parsed.get("rationale") or "")[:DEFAULT_RATIONALE_MAX] or None
            )
            # deterministic acceptance policy：low confidence → ABSTAIN（系统 policy）
            if (
                confidence is not None
                and threshold is not None
                and confidence < threshold
                and verdict != SemanticVerdict.ABSTAIN.value
            ):
                verdict = SemanticVerdict.ABSTAIN.value
            outcome = {
                "verdict": verdict,
                "rationale": rationale,
                "confidence": confidence,
            }
            break
        except (ValueError, json.JSONDecodeError):
            if attempt == 2:
                error = "malformed_output: 两次输出均无法解析为合法 verdict JSON"
    # 引入 timeout_ms 的确定性门（不真正阻塞：若 verifier 未实现超时，以 spec 记录）
    _ = timeout_ms

    if outcome is not None:
        _complete(
            store,
            vid,
            status=VerifyStatus.SUCCEEDED.value,
            verdict=outcome["verdict"],
            rationale=outcome.get("rationale"),
            confidence=outcome.get("confidence"),
        )
        return {
            "verification_id": vid,
            "reused": False,
            "status": "succeeded",
            "verdict": outcome["verdict"],
            "evidence_id": evidence_row["evidence_id"],
        }
    _complete(
        store,
        vid,
        status=VerifyStatus.FAILED.value,
        verdict=None,
        rationale=None,
        confidence=None,
        error=error,
    )
    return {
        "verification_id": vid,
        "reused": False,
        "status": "failed",
        "verdict": None,
        "error": error,
        "evidence_id": evidence_row["evidence_id"],
    }


def _candidate_evidence_ids(
    store, claim_row: dict, allowed_evidence_ids: Optional[list[str]]
) -> list[str]:
    """候选 = binding evidence；默认 cited 优先；allowed 显式覆盖（必须 ⊆ binding）。"""
    bound = _bound_evidence_ids(store, claim_row["claim_id"])
    if allowed_evidence_ids is not None:
        requested = list(dict.fromkeys(allowed_evidence_ids))
        invalid = [e for e in requested if e not in bound]
        if invalid:
            raise ValueError(
                f"allowed_evidence_ids 中存在未 binding 的 evidence: {invalid}"
            )
        return requested
    cited = _cited_evidence_ids(store, claim_row["run_id"], claim_row["claim_id"])
    ordered = [e for e in cited if e in bound] + [e for e in bound if e not in cited]
    return ordered


def _gate_check(run_id: str) -> None:
    report = validate_run(run_id)
    if report.errors:
        rules = sorted({e["rule"] for e in report.errors})
        raise VerificationGateError(
            f"run {run_id} 存在 F2 deterministic error {rules}，拒绝 semantic verification"
        )


def _build_verifier(spec: VerifierSpec) -> BaseVerifier:
    if spec.provider == "fake":
        return FakeVerifier()
    return RealLLMVerifier(spec)


# ---------------------------------------------------------------------------
# claim / run 级入口
# ---------------------------------------------------------------------------
def semantic_verify_claim(
    claim_id: str,
    *,
    spec: Optional[VerifierSpec] = None,
    allowed_evidence_ids: Optional[list[str]] = None,
    budget: Optional[VerifyBudget] = None,
    verifier: Optional[BaseVerifier] = None,
    confidence_threshold: Optional[float] = DEFAULT_CONFIDENCE_THRESHOLD,
    timeout_ms: Optional[int] = None,
) -> dict[str, Any]:
    store = _get_store()
    claim_row = _require_claim(store, claim_id)
    run_id = claim_row["run_id"]
    _gate_check(run_id)
    budget = budget or VerifyBudget()
    if not budget.include_opinion and claim_row.get("claim_type") == "OPINION":
        return {
            "claim_id": claim_id,
            "run_id": run_id,
            "skipped": True,
            "items": [],
            "aggregate": {"aggregate": "unverified", "skipped": "opinion"},
        }
    spec = spec or DEFAULT_SPEC
    verifier = verifier or _build_verifier(spec)
    counter = AttemptCounter()
    candidates = _candidate_evidence_ids(store, claim_row, allowed_evidence_ids)[
        : budget.max_evidence_per_claim
    ]
    items = []
    for eid in candidates:
        items.append(
            _verify_pair(
                store,
                run_id,
                claim_row,
                _evidence_row(store, eid),
                spec,
                verifier,
                counter,
                confidence_threshold,
                timeout_ms,
            )
        )
    agg = aggregate_claim_verdict(claim_id)
    return {
        "claim_id": claim_id,
        "run_id": run_id,
        "items": items,
        "aggregate": agg,
        "llm_calls": counter.calls,
    }


def semantic_verify_run(
    run_id: str,
    *,
    spec: Optional[VerifierSpec] = None,
    budget: Optional[VerifyBudget] = None,
    verifier: Optional[BaseVerifier] = None,
    confidence_threshold: Optional[float] = DEFAULT_CONFIDENCE_THRESHOLD,
    timeout_ms: Optional[int] = None,
) -> dict[str, Any]:
    """run 级批量验证；返回 batch 层 ExecutionSummary（可含 partial 统计，单条无 partial）。"""
    store = _get_store()
    _gate_check(run_id)
    budget = budget or VerifyBudget()
    spec = spec or DEFAULT_SPEC
    verifier = verifier or _build_verifier(spec)
    counter = AttemptCounter()

    rows = store.execute(
        "SELECT DISTINCT c.* FROM claims c JOIN claim_evidences ce ON ce.claim_id = c.claim_id "
        "WHERE c.run_id = %s ORDER BY c.created_at, c.claim_id",
        (run_id,),
    )
    claims = [
        r for r in rows if budget.include_opinion or r.get("claim_type") != "OPINION"
    ]
    if budget.max_claims_per_run is not None:
        claims = claims[: budget.max_claims_per_run]

    summary = {
        "run_id": run_id,
        "succeeded": 0,
        "failed": 0,
        "skipped": 0,
        "budget_exhausted": False,
        "claims": [],
    }
    for claim_row in claims:
        if budget.max_llm_calls is not None and counter.calls >= budget.max_llm_calls:
            summary["budget_exhausted"] = True
            break
        claim_budget = VerifyBudget(
            max_evidence_per_claim=budget.max_evidence_per_claim,
            include_opinion=True,
        )
        candidates = _candidate_evidence_ids(store, claim_row, None)
        # run 级 calls 上限在 _verify_pair 前检查
        evs = candidates[: claim_budget.max_evidence_per_claim]
        claim_summary: dict[str, Any] = {
            "claim_id": claim_row["claim_id"],
            "succeeded": 0,
            "failed": 0,
            "items": [],
        }
        for eid in evs:
            if (
                budget.max_llm_calls is not None
                and counter.calls >= budget.max_llm_calls
            ):
                summary["budget_exhausted"] = True
                break
            result = _verify_pair(
                store,
                run_id,
                claim_row,
                _evidence_row(store, eid),
                spec,
                verifier,
                counter,
                confidence_threshold,
                timeout_ms,
            )
            claim_summary["items"].append(result)
            if result["status"] == "succeeded":
                claim_summary["succeeded"] += 1
            else:
                claim_summary["failed"] += 1
        claim_summary["aggregate"] = aggregate_claim_verdict(claim_row["claim_id"])
        summary["claims"].append(claim_summary)
        summary["succeeded"] += claim_summary["succeeded"]
        summary["failed"] += claim_summary["failed"]
    summary["skipped"] = max(0, len(rows) - len(claims))
    summary["llm_calls"] = counter.calls
    summary["partial"] = summary["failed"] > 0 or summary["budget_exhausted"]
    return summary


# ---------------------------------------------------------------------------
# 只读聚合（不写 claims）与 conflict 标记
# ---------------------------------------------------------------------------
def _verdict_counts(
    store, run_id: str, claim_id: str
) -> tuple[list[str], dict[str, int]]:
    rows = store.execute(
        "SELECT verdict FROM verifications WHERE run_id=%s AND claim_id=%s "
        "AND status=%s AND verdict IS NOT NULL",
        (run_id, claim_id, VerifyStatus.SUCCEEDED.value),
    )
    verdicts = [r["verdict"] for r in rows]
    counts = {v: 0 for v in SemanticVerdict}
    for v in verdicts:
        counts.setdefault(v, 0)
        counts[v] += 1
    return verdicts, counts


def aggregate_claim_verdict(claim_id: str) -> dict[str, Any]:
    """claim 级只读聚合：supported/contested/partially_supported/unverified（不裁决 winner）。"""
    store = _get_store()
    claim_row = _require_claim(store, claim_id)
    verdicts, counts = _verdict_counts(store, claim_row["run_id"], claim_id)
    s = set(verdicts)
    if not s:
        aggregate = "unverified"
    elif SemanticVerdict.CONTRADICTS.value in s:
        aggregate = "contested"  # 检测并标记；不选 winner
    elif s == {SemanticVerdict.SUPPORTS.value}:
        aggregate = "supported"
    else:
        aggregate = "partially_supported"
    return {
        "claim_id": claim_id,
        "aggregate": aggregate,
        "contested": aggregate == "contested",
        "counts": {k: v for k, v in counts.items()},
        "verified_evidence_ids": _verified_evidence_ids(
            store, claim_row["run_id"], claim_id
        ),
    }


def _verified_evidence_ids(store, run_id: str, claim_id: str) -> list[str]:
    rows = store.execute(
        "SELECT DISTINCT evidence_id FROM verifications "
        "WHERE run_id=%s AND claim_id=%s AND status=%s AND verdict IS NOT NULL",
        (run_id, claim_id, VerifyStatus.SUCCEEDED.value),
    )
    return [r["evidence_id"] for r in rows]


# ---------------------------------------------------------------------------
# 只读查询 / provenance
# ---------------------------------------------------------------------------
def list_verifications(
    run_id: str, *, claim_id: Optional[str] = None, verdict: Optional[str] = None
) -> list[dict[str, Any]]:
    store = _get_store()
    sql = "SELECT * FROM verifications WHERE run_id = %s"
    params: list[Any] = [run_id]
    if claim_id:
        sql += " AND claim_id = %s"
        params.append(claim_id)
    if verdict:
        sql += " AND verdict = %s"
        params.append(verdict)
    sql += " ORDER BY created_at, verification_id"
    rows = store.execute(sql, tuple(params))
    return [_verification_dict(r) for r in rows]


def verification_chain(claim_id: str) -> Optional[dict[str, Any]]:
    """Claim → Verification → verifier/model/prompt_version/fingerprint（provenance）。"""
    store = _get_store()
    claim_row = _require_claim(store, claim_id)
    rows = store.execute(
        "SELECT * FROM verifications WHERE claim_id = %s "
        "ORDER BY created_at, verification_id",
        (claim_id,),
    )
    return {
        "claim_id": claim_id,
        "run_id": claim_row["run_id"],
        "verifications": [
            {
                "verification_id": r["verification_id"],
                "evidence_id": r["evidence_id"],
                "verdict": r.get("verdict"),
                "status": r["status"],
                "verifier_spec": _maybe_json(r.get("verifier_spec")) or {},
                "verifier_fingerprint": r["verifier_fingerprint"],
                "completed_at": _iso(r.get("completed_at")),
            }
            for r in rows
        ],
    }


def _verification_dict(row: dict) -> dict[str, Any]:
    return {
        "verification_id": row["verification_id"],
        "run_id": row["run_id"],
        "claim_id": row["claim_id"],
        "evidence_id": row["evidence_id"],
        "verifier_spec": _maybe_json(row.get("verifier_spec")) or {},
        "verifier_fingerprint": row["verifier_fingerprint"],
        "verdict": row.get("verdict"),
        "rationale": row.get("rationale"),
        "confidence": row.get("confidence"),
        "status": row["status"],
        "error": row.get("error"),
        "created_at": _iso(row["created_at"]),
        "completed_at": _iso(row.get("completed_at")),
        "metadata": _maybe_json(row.get("metadata")) or {},
    }
