"""Research Artifact pydantic 模型（F1）。

实体语义见 docs/spec/2026-09-07-research-artifact-foundation.md §3。
本文件只定义结构与稳定枚举；持久化列名与字段名保持一致（snake_case）。
"""

from __future__ import annotations

import datetime as _dt
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


def _utcnow_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


class ResearchRunStatus(str, Enum):
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"
    CANCELLED = "cancelled"
    # planned 为未来 Planner 预留下游枚举值（F1 创建即 running）
    PLANNED = "planned"


class SubQuestionStatus(str, Enum):
    PLANNED = "planned"
    ASKED = "asked"
    ANSWERED = "answered"
    SKIPPED = "skipped"
    FAILED = "failed"


class AgentType(str, Enum):
    NETWORK_SEARCH = "network_search"
    DATABASE = "database"
    RAGFLOW = "ragflow"
    MAIN = "main"


class SourceType(str, Enum):
    WEB = "web"
    DB = "db"
    RAGFLOW = "ragflow"
    UPLOAD = "upload"


class ExtractionMethod(str, Enum):
    TOOL_RESULT_ITEM = "tool_result_item"
    WEB_RESULT = "web_result"
    DB_RESULT = "db_result"
    RAG_ANSWER = "rag_answer"
    UPLOAD_PARSER = "upload_parser"


class ResearchRun(BaseModel):
    run_id: str
    thread_id: str
    question: str
    status: str
    started_at: str
    finished_at: Optional[str] = None
    plan: Optional[Any] = None
    budget: Optional[Any] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SubQuestion(BaseModel):
    sub_question_id: str
    run_id: str
    parent_id: Optional[str] = None
    position: int
    question: str
    rationale: Optional[str] = None
    status: str
    assigned_agent: Optional[str] = None


class SearchQuery(BaseModel):
    query_id: str
    run_id: str
    sub_question_id: str
    query: str
    agent: str
    tool: str
    topic: Optional[str] = None
    seq: Optional[int] = None
    fetched_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class Source(BaseModel):
    source_id: str
    run_id: str
    query_id: str
    source_type: str
    title: str
    canonical_url: Optional[str] = None
    locator: str
    canonical_key: str
    fetched_at: str
    agent: str
    publisher: Optional[str] = None
    published_at: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Evidence(BaseModel):
    evidence_id: str
    run_id: str
    source_id: str
    sub_question_id: str
    content: str
    locator: str
    extraction_method: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None


# ---------------------------------------------------------------------------
# F2：Claim / ClaimEvidence / Citation（docs/spec/2026-09-08-claim-citation-binding.md rev2）
# ---------------------------------------------------------------------------
class ClaimType(str, Enum):
    """陈述类型（F2 仅存证；语义由后续 Verification 消费）。"""

    FACT = "FACT"
    STATISTIC = "STATISTIC"
    PREDICTION = "PREDICTION"
    COMPARISON = "COMPARISON"
    CAUSAL_CLAIM = "CAUSAL_CLAIM"
    OPINION = "OPINION"
    INFERENCE = "INFERENCE"


class ClaimStatus(str, Enum):
    DRAFTED = "drafted"
    VALIDATED = "validated"


class Claim(BaseModel):
    claim_id: str
    run_id: str
    sub_question_id: str
    statement: str
    statement_sha: str
    claim_type: str
    status: str
    created_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ClaimEvidence(BaseModel):
    binding_id: str
    run_id: str
    claim_id: str
    evidence_id: str
    created_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class Citation(BaseModel):
    citation_id: str
    run_id: str
    claim_id: str
    evidence_id: str
    quote: Optional[str] = None
    locator: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str


# ---------------------------------------------------------------------------
# F3：Semantic Verification（docs/spec/2026-09-09-semantic-verification.md rev2）
# ---------------------------------------------------------------------------
class SemanticVerdict(str, Enum):
    """语义判断结果（仅 5 种；无 ERROR——执行失败属 VerifyStatus）。"""

    SUPPORTS = "SUPPORTS"
    INSUFFICIENT = "INSUFFICIENT"
    CONTRADICTS = "CONTRADICTS"
    UNVERIFIABLE = "UNVERIFIABLE"
    ABSTAIN = "ABSTAIN"


class VerifyStatus(str, Enum):
    """单条 verification 执行状态（仅 3 种；partial 只属 batch/run 层）。"""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Verification(BaseModel):
    verification_id: str
    run_id: str
    claim_id: str
    evidence_id: str
    verifier_spec: dict[str, Any] = Field(default_factory=dict)
    verifier_fingerprint: str
    verdict: Optional[str] = None
    rationale: Optional[str] = None
    confidence: Optional[float] = None
    status: str
    error: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# F4：Conflict Detection（docs/spec/2026-09-10-conflict-detection.md rev2）
# ---------------------------------------------------------------------------
class ConflictType(str, Enum):
    """语义分类（genuine=true 仅前两类）。"""

    CONTRADICTION = "CONTRADICTION"
    INCONSISTENCY = "INCONSISTENCY"
    CONTEXTUAL_DIFFERENCE = "CONTEXTUAL_DIFFERENCE"
    TEMPORAL_DIFFERENCE = "TEMPORAL_DIFFERENCE"
    SCOPE_DIFFERENCE = "SCOPE_DIFFERENCE"
    NO_CONFLICT = "NO_CONFLICT"


class ConflictStatus(str, Enum):
    """单条 conflict artifact 状态（4 态，无 partial）。"""

    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    FAILED = "failed"


class Conflict(BaseModel):
    conflict_id: str
    run_id: str
    claim_id: str
    evidence_a_id: str
    evidence_b_id: str
    verification_a_id: Optional[str] = None
    verification_b_id: Optional[str] = None
    detector_spec: dict[str, Any] = Field(default_factory=dict)
    detector_fingerprint: str
    candidate_source: str
    status: str
    conflict_type: Optional[str] = None
    genuine: Optional[bool] = None
    rationale: Optional[str] = None
    error: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# F5：Independent Evidence Corroboration（docs/spec/2026-09-11-...-rev2）
# ---------------------------------------------------------------------------
class CorroborationStatus(str, Enum):
    """corroboration 单状态 artifact（无 partial；review 失败不影响 complete）。"""

    COMPLETE = "complete"
    FAILED = "failed"


class Corroboration(BaseModel):
    corroboration_id: str
    run_id: str
    claim_id: str
    method_spec: dict[str, Any] = Field(default_factory=dict)
    method_fingerprint: str
    status: str
    error: Optional[str] = None
    computed_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    global_clusters: list[dict[str, Any]] = Field(default_factory=list)
    support: dict[str, Any] = Field(default_factory=dict)
    contradict: dict[str, Any] = Field(default_factory=dict)
    conflicts_independence: list[dict[str, Any]] = Field(default_factory=list)
    source_profile: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# F6：Conflict Reconciliation（docs/spec/2026-09-12-f6-conflict-reconciliation.md rev2）
# ---------------------------------------------------------------------------
class ReconciliationStatus(str, Enum):
    """单条 reconciliation artifact 状态（无 partial，同 F5）。"""

    COMPLETE = "complete"
    FAILED = "failed"


class ReconciliationOutcome(str, Enum):
    """conflict 级 reconciliation outcome（rev2 锁死，无 UNRESOLVABLE_SEMANTIC）。

    SAME_ORIGIN_CONTRADICTION — 冲突两侧 cluster 交集非空（同源自我矛盾，非独立证据冲突）；
    DETAIL_INCONSISTENCY — 独立 + F4 INCONSISTENCY（仅登记 F4 已识别类别，不裁决影响）；
    GENUINE_CONTESTED — 独立 + F4 CONTRADICTION（真争点；证据面默认不可消解，登记保留）。
    """

    SAME_ORIGIN_CONTRADICTION = "SAME_ORIGIN_CONTRADICTION"
    DETAIL_INCONSISTENCY = "DETAIL_INCONSISTENCY"
    GENUINE_CONTESTED = "GENUINE_CONTESTED"


class Reconciliation(BaseModel):
    reconciliation_id: str
    run_id: str
    claim_id: str
    conflict_id: str
    method_spec: dict[str, Any] = Field(default_factory=dict)
    method_fingerprint: str
    status: str
    outcome: Optional[str] = None
    detail: Optional[str] = None
    error: Optional[str] = None
    computed_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)
