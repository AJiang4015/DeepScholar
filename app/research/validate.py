"""F2 Validator：validate_run —— 纯函数 / 只读，R1–R10（rev2 contract）。

纪律（rev2）：
- validate_run(run_id) -> ViolationReport：只计算，**零 DB side effect**，不更新 claim.status，
  不调用 LLM；
- claim.status 演进仅经 registry.apply_validation_outcome（finalizer workflow 唯一写入口）。

规则定义见 docs/spec/2026-09-08-claim-citation-binding.md §7/§8：
R1 dangling citation；R2 引用无支撑绑定；R3 unsupported（无 binding）；
R4 invalid evidence reference（source 缺失）；R5 evidence/source 跨 run；R6 claim/evidence 不同 run；
R7 locator 不合法；R8 quote 非 evidence.content 连续子串；R9 duplicate claim（防御性）；
R10 unreferenced bound claim（有 binding 无 citation，coverage warning）。
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from app.research import store as _store

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


@dataclass
class ViolationReport:
    """一次 validate_run 的结果（纯数据，无写路径依赖）。"""

    run_id: str
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    cited_claim_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "errors": self.errors,
            "warnings": self.warnings,
            "counts": self.counts,
            "cited_claim_ids": self.cited_claim_ids,
        }


def _iso(value: Any) -> Any:
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    return value


def validate_run(run_id: str) -> ViolationReport:
    """对单个 run 执行 R1–R10 确定性校验（只读）。"""
    report = ViolationReport(run_id=run_id)
    store = _store.get_store()
    if store is None:
        report.errors.append(
            {"rule": "STORE", "message": "research store 不可用（disabled/故障）"}
        )
        return report

    def _fetch(table: str) -> list[dict]:
        try:
            return store.execute(f"SELECT * FROM {table} WHERE run_id = %s", (run_id,))
        except Exception:  # noqa: BLE001 - 只读兜底：表/库异常按整体失败报告
            report.errors.append(
                {"rule": "DB", "table": table, "message": "读取失败（只读异常）"}
            )
            return []

    claims = {c["claim_id"]: c for c in _fetch("claims")}
    run_evidences = {e["evidence_id"]: e for e in _fetch("evidences")}
    sources = {s["source_id"]: s for s in _fetch("sources")}
    citations = _fetch("citations")
    bindings = _fetch("claim_evidences")

    if not claims and not citations and not bindings:
        # run 不存在或无任何 F2 数据
        report.counts = {"claims": 0, "unsupported": 0, "unreferenced": 0, "cited": 0}
        return report

    # 跨 run 引用查找：binding/citation 引用的 evidence 可能属于其它 run
    # （语义非法但 FK 合法，正是 R6 要检出的情形）——按 id 精确补取。
    _ref_evidence_ids = {b["evidence_id"] for b in bindings} | {
        c["evidence_id"] for c in citations
    }
    missing_ev = _ref_evidence_ids - set(run_evidences)
    evidences: dict[str, dict] = dict(run_evidences)
    if missing_ev:
        placeholders = ",".join(["%s"] * len(missing_ev))
        try:
            extra = store.execute(
                f"SELECT evidence_id, run_id, source_id, content FROM evidences "
                f"WHERE evidence_id IN ({placeholders})",
                tuple(sorted(missing_ev)),
            )
            evidences.update({r["evidence_id"]: r for r in extra})
        except Exception:  # noqa: BLE001 - 只读兜底
            report.errors.append(
                {"rule": "DB", "message": "跨 run evidence 引用读取失败"}
            )

    bound_by_claim: dict[str, list[str]] = {}
    for b in bindings:
        bound_by_claim.setdefault(b["claim_id"], []).append(b["evidence_id"])

    cited_by_claim: dict[str, list[str]] = {}
    for c in citations:
        cited_by_claim.setdefault(c["claim_id"], []).append(c["evidence_id"])

    def add(kind: str, rule: str, message: str, **refs: Optional[str]) -> None:
        entry: dict[str, Any] = {"rule": rule, "message": message}
        for k, v in refs.items():
            if v is not None:
                entry[k] = v
        getattr(report, kind).append(entry)

    # R1 dangling citation（FK 兜底后的读端审计）
    for c in citations:
        if c["claim_id"] not in claims or c["evidence_id"] not in evidences:
            add(
                "errors",
                "R1",
                "dangling citation：claim/evidence 不存在",
                citation_id=c["citation_id"],
            )
    # R2 citation 无支撑绑定
    binding_pairs = {(b["claim_id"], b["evidence_id"]) for b in bindings}
    for c in citations:
        if (c["claim_id"], c["evidence_id"]) not in binding_pairs:
            add(
                "errors",
                "R2",
                "citation 缺少 claim_evidences binding",
                citation_id=c["citation_id"],
            )

    # R3 unsupported claim（无任何 binding）
    for cid, c in claims.items():
        if not bound_by_claim.get(cid):
            add(
                "warnings",
                "R3",
                "claim 无任何 evidence binding（unsupported）",
                claim_id=cid,
                claim_type=c.get("claim_type"),
            )

    # R4/R5：本 run 证据的 source 存在性（R4）与归属（R5）。
    # 需要区分"source 完全不存在"(R4) 与"source 属于其它 run"(R5)：
    # 对 run-scope 内找不到的 source_id 做一次全局存在性补查。
    _missing_source_ids = {
        e["source_id"]
        for e in run_evidences.values()
        if e.get("source_id") and e["source_id"] not in sources
    }
    if _missing_source_ids:
        placeholders = ",".join(["%s"] * len(_missing_source_ids))
        try:
            extra_sources = store.execute(
                f"SELECT source_id, run_id FROM sources "
                f"WHERE source_id IN ({placeholders})",
                tuple(sorted(_missing_source_ids)),
            )
            sources.update({r["source_id"]: r for r in extra_sources})
        except Exception:  # noqa: BLE001 - 只读兜底
            report.errors.append({"rule": "DB", "message": "source 引用读取失败"})

    for eid, e in run_evidences.items():
        s = sources.get(e.get("source_id")) if e.get("source_id") else None
        if e.get("source_id") and s is None:
            add("errors", "R4", "evidence 引用不存在的 source", evidence_id=eid)
        elif s is not None and s.get("run_id") != e.get("run_id"):
            add("errors", "R5", "evidence 与 source 不属于同一 run", evidence_id=eid)

    # R6 claim/evidence 不同 run（binding 对 + citation 对）
    for b in bindings:
        cl = claims.get(b["claim_id"])
        ev = evidences.get(b["evidence_id"])
        if cl is not None and ev is not None and cl.get("run_id") != ev.get("run_id"):
            add(
                "errors",
                "R6",
                "claim 与 evidence 不属于同一 run",
                claim_id=b["claim_id"],
                evidence_id=b["evidence_id"],
            )
    for c in citations:
        cl = claims.get(c["claim_id"])
        ev = evidences.get(c["evidence_id"])
        if cl is not None and ev is not None and cl.get("run_id") != ev.get("run_id"):
            add(
                "errors",
                "R6",
                "citation 关联的 claim 与 evidence 不属于同一 run",
                citation_id=c["citation_id"],
            )

    # R7 locator 不合法
    for c in citations:
        loc = c.get("locator")
        if loc is not None and (len(loc) > 200 or bool(_CONTROL_CHARS.search(loc))):
            add(
                "errors",
                "R7",
                "locator 不合法（长度>200 或含控制字符）",
                citation_id=c["citation_id"],
            )

    # R8 quote 非 evidence.content 连续子串
    for c in citations:
        q = c.get("quote")
        if q:
            ev = evidences.get(c["evidence_id"])
            if ev is not None and q not in ev.get("content", ""):
                add(
                    "warnings",
                    "R8",
                    "quote 不是 evidence.content 的连续子串",
                    citation_id=c["citation_id"],
                )

    # R9 duplicate claim（防御性：唯一键防写入重复；若库内出现同 run 同 sha 多行则报错）
    sha_groups: dict[str, list[str]] = {}
    for cid, c in claims.items():
        sha_groups.setdefault(c.get("statement_sha", ""), []).append(cid)
    for sha, ids_ in sha_groups.items():
        if len(ids_) > 1:
            add(
                "errors",
                "R9",
                "同 run 内重复 statement（statement_sha 冲突）",
                claim_id=ids_[0],
            )

    # R10 unreferenced bound claim（有 binding 无 citation → coverage warning）
    for cid, c in claims.items():
        if bound_by_claim.get(cid) and not cited_by_claim.get(cid):
            add(
                "warnings",
                "R10",
                "claim 有 evidence binding 但未被任何 citation 覆盖",
                claim_id=cid,
            )

    unsupported_ids = {
        w["claim_id"]
        for w in report.warnings
        if w["rule"] == "R3" and w.get("claim_id")
    }
    unreferenced_ids = {
        w["claim_id"]
        for w in report.warnings
        if w["rule"] == "R10" and w.get("claim_id")
    }
    report.counts = {
        "claims": len(claims),
        "unsupported": len(unsupported_ids),
        "unreferenced": len(unreferenced_ids),
        "cited": len({cid for cid in cited_by_claim if cid in claims}),
    }
    report.cited_claim_ids = sorted({cid for cid in cited_by_claim if cid in claims})
    return report
