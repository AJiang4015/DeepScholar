"""F7 ClaimExtractor（docs/spec/2026-09-13-f7-research-state-bridge.md rev2 §5.1/§5.2/§12）。

语义边界（rev2 锁定）：
- ClaimExtractor 只负责从 final_content 提取 **Candidate Claim**（statement/claim_type）与
  **Candidate Evidence Anchor**（evidence_id/quote/locator 候选）；
- **不得**判断 "Evidence 是否真正支持 Claim"（那是 F3 的职责）；
- 输出 schema 无任何 verdict/support/confidence 字段；出现即视为 malformed；
- 核心不变量：Extractor 选择 Evidence ≠ Evidence 支持 Claim。

通道纪律（沿用 F3–F6）：FakeExtractor 默认用于测试 / Gate；
RealLLMExtractor 仅在 `VERIFY_REAL_LLM=1` 且存在 OPENAI_API_KEY 时允许构造（受控，不进自动化 Gate）。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Callable, Optional

from app.research.schemas import ClaimType

#: extractor 输出中被禁止的语义键（verdict/support/confidence 族）——出现即 malformed
_FORBIDDEN_KEYS = {
    "verdict",
    "verdicts",
    "confidence",
    "support",
    "supports",
    "contradicts",
    "insufficient",
    "unverifiable",
    "abstain",
    "verified",
    "supported",
    "contested",
}

_ALLOWED_CLAIM_KEYS = {"statement", "claim_type", "evidence"}
_ALLOWED_EVIDENCE_KEYS = {"evidence_id", "quote", "locator"}

CLAIM_STATEMENT_MAX = 2000
CLAIM_TYPE_VALUES = {t.value for t in ClaimType}
DEFAULT_EVIDENCE_UNIVERSE_MAX = 20
DEFAULT_EVIDENCE_SNIPPET_MAX = 4000
DEFAULT_EXTRACTOR_TIMEOUT_MS = 30000
_DEFAULT_MODEL = "qwen-max"


class ExtractorError(Exception):
    """extractor 通道失败（provider/timeout/malformed/缺配置）。"""

    def __init__(self, kind: str, message: str = "") -> None:
        super().__init__(message or kind)
        self.kind = kind


class MalformedExtractionError(ExtractorError):
    """输出违反契约（含 verdict 键 / 结构非法）→ 该批判 malformed。"""

    def __init__(self, message: str = "") -> None:
        super().__init__("malformed_output", message)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _clean_code_fence(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


@dataclass
class EvidenceEnvelopeItem:
    """注入 extractor 的最小 evidence 条目（不得带验证/冲突等语义状态）。"""

    evidence_id: str
    title: str = ""
    url: str = ""
    content_snippet: str = ""
    source_type: str = "unknown"
    locator: str = ""


def build_evidence_envelope(
    evidence_rows: list[dict[str, Any]],
    *,
    snippet_max: int = DEFAULT_EVIDENCE_SNIPPET_MAX,
    universe_max: int = DEFAULT_EVIDENCE_UNIVERSE_MAX,
) -> list[EvidenceEnvelopeItem]:
    """把 run evidence rows 折叠为只含 identification + content 摘要的最小信封。"""
    items = []
    for row in evidence_rows[:universe_max]:
        items.append(
            EvidenceEnvelopeItem(
                evidence_id=str(row["evidence_id"]),
                title=str(row.get("title") or "")[:300],
                url=str(row.get("canonical_url") or row.get("locator") or "")[:1000],
                content_snippet=str(row.get("content") or "")[:snippet_max],
                source_type=str(row.get("source_type") or "unknown"),
                locator=str(row.get("locator") or "")[:1000],
            )
        )
    return items


class BaseExtractor:
    """协议：extract(final_content, evidence_envelope) -> {"claims": [...]}（候选，无 verdict）。"""

    def spec(self) -> dict[str, Any]:
        """方法快照（进入 finalization fingerprint；不得含 secret）。"""
        return {"provider": "base"}

    def extract(
        self, final_content: str, evidence_envelope: list[EvidenceEnvelopeItem]
    ) -> dict[str, Any]:
        raise NotImplementedError


def validate_candidates_payload(payload: Any) -> dict[str, Any]:
    """结构 + 禁词校验（deterministic）。

    通过 → 返回 {"claims": [...]}（每条仅 statement/claim_type/evidence，evidence 仅
    evidence_id/quote/locator 候选）；失败 → MalformedExtractionError。
    """
    if not isinstance(payload, dict):
        raise MalformedExtractionError("extractor 输出不是 JSON object")
    top_forbidden = set(payload) & _FORBIDDEN_KEYS
    if top_forbidden:
        raise MalformedExtractionError(
            f"extractor 输出含被禁止的语义键 {sorted(top_forbidden)}（verdict 仅 F3 可产生）"
        )
    claims = payload.get("claims")
    if not isinstance(claims, list):
        raise MalformedExtractionError("extractor 输出缺少 claims 数组")
    cleaned: list[dict[str, Any]] = []
    for idx, raw in enumerate(claims):
        if not isinstance(raw, dict):
            raise MalformedExtractionError(f"claims[{idx}] 不是 object")
        bad = set(raw) & _FORBIDDEN_KEYS
        if bad:
            raise MalformedExtractionError(
                f"claims[{idx}] 含被禁止的语义键 {sorted(bad)}（verdict 仅 F3 可产生）"
            )
        statement = raw.get("statement")
        claim_type = raw.get("claim_type")
        if not isinstance(statement, str) or not statement.strip():
            raise MalformedExtractionError(f"claims[{idx}].statement 缺失/非 string")
        if claim_type not in CLAIM_TYPE_VALUES:
            raise MalformedExtractionError(
                f"claims[{idx}].claim_type 非法: {claim_type!r}（claim_type 结构非法 → "
                "该 candidate claim 不入库的唯一被拒通道）"
            )
        evidence_raw = raw.get("evidence", [])
        if evidence_raw is None:
            evidence_raw = []
        if not isinstance(evidence_raw, list):
            raise MalformedExtractionError(f"claims[{idx}].evidence 非数组")
        evidence_clean: list[dict[str, Any]] = []
        for eidx, ev in enumerate(evidence_raw):
            if not isinstance(ev, dict):
                raise MalformedExtractionError(
                    f"claims[{idx}].evidence[{eidx}] 非 object"
                )
            eid = ev.get("evidence_id")
            if not isinstance(eid, str) or not eid:
                raise MalformedExtractionError(
                    f"claims[{idx}].evidence[{eidx}].evidence_id 缺失"
                )
            entry: dict[str, Any] = {"evidence_id": eid}
            if isinstance(ev.get("quote"), str):
                entry["quote"] = ev["quote"]
            if isinstance(ev.get("locator"), str):
                entry["locator"] = ev["locator"]
            extra = set(ev) - _ALLOWED_EVIDENCE_KEYS
            if extra & _FORBIDDEN_KEYS:
                raise MalformedExtractionError(
                    f"claims[{idx}].evidence[{eidx}] 含被禁止键 {sorted(extra & _FORBIDDEN_KEYS)}"
                )
            evidence_clean.append(entry)
        claim: dict[str, Any] = {
            "statement": statement.strip()[:CLAIM_STATEMENT_MAX],
            "claim_type": claim_type,
            "evidence": evidence_clean,
        }
        cleaned.append(claim)
    return {"claims": cleaned}


class FakeExtractor(BaseExtractor):
    """确定性通道（Gate/测试）：返回预设候选；可注入异常（provider/timeout/malformed）。"""

    def __init__(
        self,
        claims: Optional[list[dict[str, Any]]] = None,
        resolver: Optional[Callable[[str, list[EvidenceEnvelopeItem]], Any]] = None,
        raise_error: Optional[BaseException] = None,
        name: str = "fake.v1",
    ) -> None:
        self.claims = list(claims or [])
        self.resolver = resolver
        self.raise_error = raise_error
        self.name = name
        self.calls: list[tuple[str, int]] = []

    def spec(self) -> dict[str, Any]:
        # candidates 的 sha 进入 fingerprint：同一 run/content/universe 但不同候选 → 不同 fingerprint
        return {
            "provider": "fake",
            "name": self.name,
            "candidates_sha": _sha256(
                json.dumps(self.claims, ensure_ascii=False, sort_keys=True)
            ),
        }

    def extract(
        self, final_content: str, evidence_envelope: list[EvidenceEnvelopeItem]
    ) -> dict[str, Any]:
        self.calls.append((final_content, len(evidence_envelope)))
        if self.raise_error is not None:
            raise self.raise_error
        if self.resolver is not None:
            return validate_candidates_payload(
                self.resolver(final_content, evidence_envelope)
            )
        return validate_candidates_payload({"claims": self.claims})


class RealLLMExtractor(BaseExtractor):
    """受控 real-LLM 通道（VERIFY_REAL_LLM=1 + OPENAI_API_KEY；不进自动化 Gate）。

    输入信封仅 final_content + evidence envelope（identification + content 摘要 ≤4k/条）；
    不接 Agent history / checkpoint / monitor / 其它 run / 凭据；不输出 verdict。
    """

    def __init__(
        self, model: Optional[str] = None, prompt_version: str = "2026-09-13.extract.v1"
    ) -> None:
        if os.getenv("VERIFY_REAL_LLM", "0") != "1":
            raise RuntimeError("real-LLM extractor 需显式设置 VERIFY_REAL_LLM=1")
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("real-LLM extractor 需要 openai SDK") from exc
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("real-LLM extractor 需要 OPENAI_API_KEY")
        kwargs: dict[str, Any] = {"api_key": api_key}
        base_url = os.getenv("OPENAI_BASE_URL")
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)
        self.model = model or os.getenv("LLM_QWEN_MAX") or _DEFAULT_MODEL
        self.prompt_version = prompt_version

    def spec(self) -> dict[str, Any]:
        return {
            "provider": "real",
            "model": self.model,
            "prompt_version": self.prompt_version,
        }

    def extract(
        self, final_content: str, evidence_envelope: list[EvidenceEnvelopeItem]
    ) -> dict[str, Any]:
        envelope = [
            {
                "evidence_id": it.evidence_id,
                "title": it.title,
                "url": it.url,
                "source_type": it.source_type,
                "content": it.content_snippet,
            }
            for it in evidence_envelope
        ]
        system = (
            "你是研究断言提取器。从给定研究报告 final 文本中提取候选研究断言（Claim）。"
            "规则："
            "1) 只提取 final 文本中实际出现的事实/统计/推断类断言（claim_type 取 "
            f"{sorted(CLAIM_TYPE_VALUES)} 之一）；观点(OPINION)仅在明确出现时保留；"
            "2) 每个断言最多给出你认为相关的候选证据（从给定 evidence 列表中选 evidence_id）；"
            "3) 你**不能**判断证据是否支持该断言——那由后续验证模块负责；不要输出任何 verdict/"
            "support/confidence 字段；"
            '4) 只输出 JSON：{"claims": [{"statement": "…", "claim_type": "…", '
            '"evidence": [{"evidence_id": "…", "quote": "可选且须与 evidence content '
            '完全一致的连续子串"}]}]}。'
            f"（prompt_version={self.prompt_version}）"
        )
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "final_content": (final_content or "")[:20000],
                            "evidence": envelope,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            temperature=0.0,
            # openai>=2.x 请求级超时参数为 timeout（秒）；与 F3–F6 real adapter 的 timeout_ms
            # 意图一致（30s），但遵守本 SDK 签名（F3–F6 冻结，不改）。
            timeout=DEFAULT_EXTRACTOR_TIMEOUT_MS / 1000.0,
        )
        content = (resp.choices[0].message.content or "").strip()
        if not content:
            raise ExtractorError("provider", "empty response")
        try:
            payload = json.loads(_clean_code_fence(content))
        except (ValueError, TypeError) as exc:
            raise MalformedExtractionError(f"extractor 输出非法 JSON: {exc}") from exc
        return validate_candidates_payload(payload)
