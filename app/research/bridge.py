"""F7 Research State Bridge（docs/spec/2026-09-13-f7-research-state-bridge.md rev2）。

定位：Research State Bridge — orchestration layer（不实现 F3/F4/F5/F6 算法）。
- 在真实 run terminal finalization 阶段物化 Candidate Claims（ClaimExtractor 只提取候选，
  不判支持；verdict 仅 F3 可产生）；
- invalid anchor → claim 仍落库 + unanchored（不建 ClaimEvidence/Citation）；
- F2 structural 必须执行；F3/F4/F5/F6 仅 enabled 时调用既有 public API，否则 skipped_off
  （不伪造 artifact；disabled ≠ verified/no-conflict/independent/reconciled）；
- finalization identity = sha256({extractor_spec, stages:{f3..f6 spec}, final_content_hash,
  evidence_universe_hash})；同代复用，内容/evidence 变化 → 新 generation；
- 全程 fail-open：任何异常不冒泡（main_agent 接线点仍额外 try/except）。

禁止：自行验证 / 自行 detect conflict / 自行聚类 / 自行 reconciliation / 复制 F3–F6 私有逻辑。
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

from app.research import provenance, registry, store as _store, validate
from app.research.conflict import (
    DEFAULT_DETECTOR_SPEC,
    detect_run_conflicts,
)
from app.research.corroboration import (
    DEFAULT_METHOD as DEFAULT_CORROBORATION_METHOD,
    compute_run_corroborations,
)
from app.research.extractor import (
    BaseExtractor,
    build_evidence_envelope,
    validate_candidates_payload,
)
from app.research.reconciliation import (
    DEFAULT_METHOD as DEFAULT_RECONCILIATION_METHOD,
    reconcile_run_conflicts,
    run_unresolved,
)
from app.research.schemas import ClaimStatus
from app.research.store import json_param
from app.research.verify import (
    DEFAULT_SPEC as DEFAULT_VERIFY_SPEC,
    aggregate_claim_verdict,
    semantic_verify_run,
)

logger = logging.getLogger("deepsearch.research.bridge")

BRIDGE_VERSION = "research-bridge.v1"

#: research_runs.metadata 中 finalization 代际记录键（只写该键，不改其它键语义）
FINALIZATIONS_META_KEY = "research_finalizations"


def _utcnow_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


@dataclass
class FinalizeBudget:
    """finalizer 预算（无新增基础设施）。"""

    max_claims_per_run: int = 12
    max_evidence_bindings: int = 8
    evidence_universe_max: int = 20
    evidence_snippet_max: int = 4000
    final_content_max: int = 20000


@dataclass
class StageConfig:
    """单阶段使能 + 既有 spec/method 快照（进入 fingerprint；不含 secret/实例）。"""

    enabled: bool
    spec_dict: Optional[dict[str, Any]] = None

    def identity(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "spec": self.spec_dict if self.spec_dict is not None else "default",
        }


# ---------------------------------------------------------------------------
# 内部只读：run / evidence universe
# ---------------------------------------------------------------------------
def _run_row(store, run_id: str) -> Optional[dict]:
    rows = store.execute("SELECT * FROM research_runs WHERE run_id=%s", (run_id,))
    return rows[0] if rows else None


def _evidence_rows(store, run_id: str) -> list[dict]:
    return store.execute(
        "SELECT e.evidence_id, e.run_id, e.source_id, e.sub_question_id, e.content, "
        "e.locator, e.created_at, s.title, s.canonical_url, s.source_type "
        "FROM evidences e LEFT JOIN sources s ON s.source_id = e.source_id "
        "WHERE e.run_id=%s ORDER BY e.created_at, e.evidence_id",
        (run_id,),
    )


def _evidence_universe_hash(rows: list[dict]) -> str:
    """evidence universe hash：evidence_id + content sha（内容变化 → hash 变化）。"""
    payload = sorted(
        (
            {
                "evidence_id": r["evidence_id"],
                "content_sha": _sha256(str(r.get("content") or "")),
            }
            for r in rows
        ),
        key=lambda x: (x["evidence_id"], x["content_sha"]),
    )
    return _sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False))


def _root_sub_question_id(store, run_id: str) -> Optional[str]:
    rows = store.execute(
        "SELECT sub_question_id FROM sub_questions WHERE run_id=%s "
        "ORDER BY position LIMIT 1",
        (run_id,),
    )
    return rows[0]["sub_question_id"] if rows else None


# ---------------------------------------------------------------------------
# materialization（F2 registry 写；bridge 只编排 + 锚点守卫，不判定支持）
# ---------------------------------------------------------------------------
def _materialize_claim(
    store,
    run_id: str,
    sub_question_id: str,
    candidate: dict,
    universe: dict[str, dict],
    budget: FinalizeBudget,
    extractor_name: str,
) -> dict[str, Any]:
    """把单个 candidate claim 物化；返回 {claim_id, anchored, bindings, citations}。

    语义（rev2 §5.2/§12）：
    - 先解析 anchors（不依赖 claim 行）：evidence_id 必须在 run universe；quote（若有）必须是
      content 连续子串；locator（若有）必须合法（≤200 且无控制字符）。任一违规 → 该 anchor 无效
      （不静默改写；不建 ClaimEvidence/Citation）。
    - 全部 anchor 无效/缺失 → claim 仍落库（final_content 中真实断言）且 metadata.unanchored=true，
      不创建 binding/citation → F3 对该 claim 无 evidence verification。
    - 否则建 claim → 为每个有效 anchor bind_claim_evidence（cap=max_evidence_bindings）；quote 有效
      时 create_citation（R2 binding 已满足；locator 已校验）。
    """
    statement = (candidate.get("statement") or "").strip()
    ctype = candidate.get("claim_type")
    if not statement or ctype is None:
        return {"skipped": True, "reason": "invalid_statement_or_type"}

    # 1) 锚点解析（与 claim 行无关，先于创建；避免 unanchored 需要二次写）
    valid_anchors: list[tuple[str, Optional[str], Optional[str]]] = []
    for anchor in candidate.get("evidence") or []:
        eid = anchor.get("evidence_id")
        ev = universe.get(eid)
        if ev is None:
            continue  # 不在 run universe（cross-run / 缺失）→ anchor 无效
        quote = anchor.get("quote")
        locator = anchor.get("locator")
        if quote is not None and quote not in (ev.get("content") or ""):
            continue  # quote 非 content 连续子串 → anchor 无效（不静默改写）
        if locator is not None:
            if not isinstance(locator, str) or len(locator) > 200:
                continue
            if any(ord(c) < 32 or c == "\x7f" for c in locator):
                continue
        valid_anchors.append((eid, quote, locator))
        if len(valid_anchors) >= budget.max_evidence_bindings:
            break

    anchored = bool(valid_anchors)
    claim_meta: dict[str, Any] = {
        "origin": "research_finalizer",
        "extractor": extractor_name,
    }
    if not anchored:
        claim_meta["unanchored"] = True
    claim_id = registry.create_claim(
        run_id, sub_question_id, statement, ctype, metadata=claim_meta
    )
    if claim_id is None:
        return {"skipped": True, "reason": "registry_create_claim_failed"}

    if not anchored:
        return {
            "claim_id": claim_id,
            "anchored": False,
            "bindings": [],
            "citations": [],
        }

    binding_ids: list[str] = []
    citation_ids: list[str] = []
    for eid, quote, locator in valid_anchors:
        binding_id = registry.bind_claim_evidence(
            run_id, claim_id, eid, metadata={"origin": "research_finalizer"}
        )
        if binding_id is not None:
            binding_ids.append(binding_id)
        if quote is not None:
            cit = registry.create_citation(
                run_id,
                claim_id,
                eid,
                quote=quote,
                locator=locator,
                metadata={"origin": "research_finalizer"},
            )
            if cit is not None:
                citation_ids.append(cit)
    return {
        "claim_id": claim_id,
        "anchored": True,
        "bindings": binding_ids,
        "citations": citation_ids,
    }


def _finalization_fingerprint(
    extractor: Optional[BaseExtractor],
    f2_cfg: StageConfig,
    f3_cfg: StageConfig,
    f4_cfg: StageConfig,
    f5_cfg: StageConfig,
    f6_cfg: StageConfig,
    final_content: str,
    evidence_rows: list[dict],
) -> str:
    canonical = {
        "bridge_version": BRIDGE_VERSION,
        "extractor": extractor.spec() if extractor is not None else {"provider": "off"},
        "stages": {
            "f2": f2_cfg.identity(),
            "f3": f3_cfg.identity(),
            "f4": f4_cfg.identity(),
            "f5": f5_cfg.identity(),
            "f6": f6_cfg.identity(),
        },
        "final_content_hash": _sha256(final_content or ""),
        "evidence_universe_hash": _evidence_universe_hash(evidence_rows),
    }
    return _sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False))


def _meta_finalizations(store, run_id: str) -> list[dict]:
    run = _run_row(store, run_id)
    if run is None:
        return []
    meta = _maybe_json(run.get("metadata")) or {}
    entries = meta.get(FINALIZATIONS_META_KEY) or []
    return entries if isinstance(entries, list) else []


def record_finalization_generation(store, run_id: str, record: dict) -> None:
    """追加一代 finalization 记录到 research_runs.metadata[research_finalizations]。

    只写该键；不触碰其它 metadata/列（registry/F1 语义不变）。
    """
    run = _run_row(store, run_id)
    if run is None:
        return
    meta = _maybe_json(run.get("metadata")) or {}
    entries = meta.get(FINALIZATIONS_META_KEY) or []
    if not isinstance(entries, list):
        entries = []
    entries.insert(0, record)
    meta[FINALIZATIONS_META_KEY] = entries
    with store.transaction() as tx:
        tx.execute(
            "UPDATE research_runs SET metadata=%s WHERE run_id=%s",
            (json_param(meta, store.dialect), run_id),
        )


# ---------------------------------------------------------------------------
# 主入口（orchestration；全链路 fail-open 由调用方/内部 try 兜底）
# ---------------------------------------------------------------------------
def finalize_run(
    run_id: str,
    final_content: str = "",
    *,
    extractor: Optional[BaseExtractor] = None,
    f3_enabled: bool = False,
    f3_spec=None,
    f3_verifier=None,
    f4_enabled: bool = False,
    f4_spec=None,
    f4_detector=None,
    f5_enabled: bool = False,
    f5_method=None,
    f6_enabled: bool = False,
    f6_method=None,
    f6_reviewer=None,
    budget: Optional[FinalizeBudget] = None,
) -> dict[str, Any]:
    """在真实 run terminal finalization 阶段执行（幂等，fail-open，不进入 Agent-visible）。

    Returns run-level research state summary（含 generation/state；异常时返回 noop 摘要而非抛错）。
    """
    budget = budget or FinalizeBudget()
    empty = {
        "run_id": run_id,
        "ok": False,
        "reason": "",
        "generation": None,
        "state": _empty_state(run_id),
    }
    try:
        store = _store.get_store()
        if store is None:
            empty["reason"] = "research store 不可用"
            return empty
        run = _run_row(store, run_id)
        if run is None:
            empty["reason"] = "research run 不存在"
            return empty

        # stages 配置（enabled 且带既有 spec/method 快照；未显式给 spec 时用该阶段模块默认值，
        # 使 fingerprint 反映真实调用语义）；f2 总是 enabled（deterministic structural）
        def _stage_spec_dict(obj) -> dict[str, Any]:
            return obj.to_dict()

        f2_cfg = StageConfig(enabled=True)
        f3_spec_effective = f3_spec if f3_spec is not None else DEFAULT_VERIFY_SPEC
        f4_spec_effective = f4_spec if f4_spec is not None else DEFAULT_DETECTOR_SPEC
        f5_method_effective = (
            f5_method if f5_method is not None else DEFAULT_CORROBORATION_METHOD
        )
        f6_method_effective = (
            f6_method if f6_method is not None else DEFAULT_RECONCILIATION_METHOD
        )
        f3_cfg = StageConfig(
            enabled=f3_enabled,
            spec_dict=_stage_spec_dict(f3_spec_effective) if f3_enabled else None,
        )
        f4_cfg = StageConfig(
            enabled=f4_enabled,
            spec_dict=_stage_spec_dict(f4_spec_effective) if f4_enabled else None,
        )
        f5_cfg = StageConfig(
            enabled=f5_enabled,
            spec_dict=_stage_spec_dict(f5_method_effective) if f5_enabled else None,
        )
        f6_cfg = StageConfig(
            enabled=f6_enabled,
            spec_dict=_stage_spec_dict(f6_method_effective) if f6_enabled else None,
        )

        evidence_rows = _evidence_rows(store, run_id)
        fp = _finalization_fingerprint(
            extractor,
            f2_cfg,
            f3_cfg,
            f4_cfg,
            f5_cfg,
            f6_cfg,
            final_content,
            evidence_rows,
        )
        # 同代复用（idempotency identity）
        for existing in _meta_finalizations(store, run_id):
            if existing.get("fingerprint") == fp:
                state = build_run_research_state(run_id)
                state["reused_generation"] = True
                return {
                    "run_id": run_id,
                    "ok": True,
                    "reason": "",
                    "generation": existing,
                    "state": state,
                }

        pipeline: dict[str, dict[str, Any]] = {
            "f2": {"status": "pending"},
            "f3": {"status": "pending"},
            "f4": {"status": "pending"},
            "f5": {"status": "pending"},
            "f6": {"status": "pending"},
        }
        meta: dict[str, Any] = {}
        claims_materialized: list[dict[str, Any]] = []

        # --- extraction（semantic；extractor 缺省 → off，不伪造 claims）---
        candidates: list[dict[str, Any]] = []
        extractor_identity: dict[str, Any] = {"provider": "off"}
        if extractor is not None:
            extractor_identity = extractor.spec()
            try:
                envelope = build_evidence_envelope(
                    evidence_rows,
                    snippet_max=budget.evidence_snippet_max,
                    universe_max=budget.evidence_universe_max,
                )
                raw = extractor.extract(
                    final_content[: budget.final_content_max], envelope
                )
                validated = validate_candidates_payload(raw)
                candidates = validated["claims"]
                meta["extractor_ok"] = True
            except Exception as exc:  # noqa: BLE001 - fail-open
                meta["extractor_ok"] = False
                meta["extractor_error"] = str(exc)[:500]
                logger.warning(
                    "[research-bridge] extractor failed (fail-open): %s", exc
                )

        # --- materialization：claims（提取的候选；空 → 无物化）---
        universe = {r["evidence_id"]: r for r in evidence_rows}
        subq_id = _root_sub_question_id(store, run_id)
        created_claim_ids: list[str] = []
        unanchored_claim_ids: list[str] = []
        if subq_id is None:
            meta["materialization_error"] = "root sub_question 缺失"
        else:
            for candidate in candidates[: budget.max_claims_per_run]:
                out = _materialize_claim(
                    store,
                    run_id,
                    subq_id,
                    candidate,
                    universe,
                    budget,
                    extractor_identity.get("provider", "unknown"),
                )
                if out.get("skipped"):
                    meta.setdefault("claim_downgraded", []).append(
                        {
                            "statement": candidate.get("statement", "")[:200],
                            "reason": out["reason"],
                        }
                    )
                    continue
                claims_materialized.append(out)
                if out.get("claim_id"):
                    created_claim_ids.append(out["claim_id"])
                if not out.get("anchored"):
                    unanchored_claim_ids.append(out["claim_id"])
        meta["claims_candidates"] = len(candidates)
        meta["claims_materialized"] = len(claims_materialized)
        meta["claims_unanchored"] = len(unanchored_claim_ids)
        meta["materialized_claim_ids"] = created_claim_ids
        meta["truncated"] = len(candidates) > budget.max_claims_per_run

        # --- F2 structural validation（必须执行；deterministic）---
        try:
            report = validate.validate_run(run_id)
            updated = registry.apply_validation_outcome(run_id, report)
            pipeline["f2"] = {
                "status": "executed",
                "errors": len(report.errors),
                "warnings": len(report.warnings),
                "validated_updated": updated,
            }
        except Exception as exc:  # noqa: BLE001 - fail-open
            pipeline["f2"] = {"status": "failed", "error": str(exc)[:500]}
            logger.warning("[research-bridge] f2 failed (fail-open): %s", exc)

        # --- F3/F4/F5/F6（enabled → 既有 public API；disabled → skipped_off）---
        if f3_enabled:
            try:
                result = semantic_verify_run(run_id, spec=f3_spec, verifier=f3_verifier)
                pipeline["f3"] = {
                    "status": "executed",
                    "succeeded": result.get("succeeded"),
                    "failed": result.get("failed"),
                    "skipped": result.get("skipped"),
                }
            except Exception as exc:  # noqa: BLE001
                pipeline["f3"] = {"status": "failed", "error": str(exc)[:500]}
                logger.warning("[research-bridge] f3 failed (fail-open): %s", exc)
        else:
            pipeline["f3"] = {"status": "skipped_off"}

        if f4_enabled:
            try:
                result = detect_run_conflicts(
                    run_id, spec=f4_spec, detector=f4_detector
                )
                pipeline["f4"] = {
                    "status": "executed",
                    "confirmed": result.get("confirmed"),
                    "rejected": result.get("rejected"),
                    "failed": result.get("failed"),
                    "candidates": result.get("candidates"),
                }
            except Exception as exc:  # noqa: BLE001
                pipeline["f4"] = {"status": "failed", "error": str(exc)[:500]}
                logger.warning("[research-bridge] f4 failed (fail-open): %s", exc)
        else:
            pipeline["f4"] = {"status": "skipped_off"}

        if f5_enabled:
            try:
                result = compute_run_corroborations(run_id, method=f5_method)
                pipeline["f5"] = {
                    "status": "executed",
                    "complete": result.get("complete"),
                    "failed": result.get("failed"),
                    "claims": len(result.get("claims", [])),
                }
            except Exception as exc:  # noqa: BLE001
                pipeline["f5"] = {"status": "failed", "error": str(exc)[:500]}
                logger.warning("[research-bridge] f5 failed (fail-open): %s", exc)
        else:
            pipeline["f5"] = {"status": "skipped_off"}

        if f6_enabled:
            try:
                result = reconcile_run_conflicts(
                    run_id, method=f6_method, reviewer=f6_reviewer
                )
                pipeline["f6"] = {
                    "status": "executed",
                    "complete": result.get("complete"),
                    "failed": result.get("failed"),
                    "conflicts": result.get("conflicts"),
                }
            except Exception as exc:  # noqa: BLE001
                pipeline["f6"] = {"status": "failed", "error": str(exc)[:500]}
                logger.warning("[research-bridge] f6 failed (fail-open): %s", exc)
        else:
            pipeline["f6"] = {"status": "skipped_off"}

        generation = {
            "fingerprint": fp,
            "bridge_version": BRIDGE_VERSION,
            "computed_at": _utcnow_iso(),
            "extractor": extractor_identity,
            "final_content_hash": _sha256(final_content or ""),
            "evidence_universe_hash": _evidence_universe_hash(evidence_rows),
            "pipeline": pipeline,
            "meta": meta,
        }
        record_finalization_generation(store, run_id, generation)

        state = build_run_research_state(run_id)
        return {
            "run_id": run_id,
            "ok": True,
            "reason": "",
            "generation": generation,
            "state": state,
        }
    except Exception as exc:  # noqa: BLE001 - fail-open：绝不把 finalizer 异常抛给 run_deep_agent
        logger.warning("[research-bridge] finalize_run failed (fail-open): %s", exc)
        empty["reason"] = str(exc)[:500]
        return empty


def _empty_state(run_id: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "finalization_fingerprint": None,
        "materialized_at": None,
        "extractor": None,
        "claims_total": None,
        "claims_validated": None,
        "claims_unanchored": None,
        "verified_claims": None,
        "contested_claims": None,
        "unresolved_claims": None,
        "pipeline": None,
    }


def build_run_research_state(run_id: str) -> dict[str, Any]:
    """派生 run-level research state（只读，不落表）。

    disabled / 未计算阶段 → null / not_computed（绝不写成 0，防把 skipped 当结论）。
    """
    store = _store.get_store()
    if store is None:
        return _empty_state(run_id)
    run = _run_row(store, run_id)
    if run is None:
        return _empty_state(run_id)

    claims = provenance.list_claims(run_id)
    validated = sum(1 for c in claims if c.status == ClaimStatus.VALIDATED.value)
    unanchored = sum(1 for c in claims if c.metadata.get("unanchored"))

    # pipeline/指纹/提取器 取最新一代（若有）
    generations = _meta_finalizations(store, run_id)
    latest = generations[0] if generations else None
    pipeline: Optional[dict] = None
    verified: Optional[int] = None
    contested: Optional[int] = None
    unresolved: Optional[int] = None
    if latest is not None:
        pipeline = latest.get("pipeline")
        f3_status = (pipeline or {}).get("f3", {}).get("status")
        f6_status = (pipeline or {}).get("f6", {}).get("status")
        if f3_status == "executed":
            v = c = 0
            for claim in claims:
                agg = aggregate_claim_verdict(claim.claim_id)
                if agg.get("aggregate") == "supported":
                    v += 1
                elif agg.get("aggregate") == "contested":
                    c += 1
            verified, contested = v, c
        if f6_status == "executed":
            unresolved = len(run_unresolved(run_id).get("unresolved_claims", []))
    return {
        "run_id": run_id,
        "finalization_fingerprint": latest.get("fingerprint") if latest else None,
        "materialized_at": latest.get("computed_at") if latest else None,
        "extractor": latest.get("extractor") if latest else None,
        "claims_total": len(claims),
        "claims_validated": validated,
        "claims_unanchored": unanchored,
        "verified_claims": verified,
        "contested_claims": contested,
        "unresolved_claims": unresolved,
        "pipeline": pipeline,
    }


def get_run_research_state(run_id: str) -> Optional[dict[str, Any]]:
    """便捷只读入口；run 不存在/无状态 → None（claims 全空且无代际记录时仍返回 summary）。"""
    store = _store.get_store()
    if store is None:
        return None
    if _run_row(store, run_id) is None:
        return None
    return build_run_research_state(run_id)


def run_finalization_history(run_id: str) -> list[dict[str, Any]]:
    """只读：代际记录（fingerprint/computed_at/extractor/pipeline/meta）。"""
    store = _store.get_store()
    if store is None:
        return []
    return _meta_finalizations(store, run_id)
