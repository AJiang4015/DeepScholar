"""Research Artifact ID 生成（F1）。

一律服务端生成 uuid4 hex（32 字符），禁止由 LLM 生成或传递任何 artifact id。
"""

from __future__ import annotations

import uuid


def new_run_id() -> str:
    return uuid.uuid4().hex


def new_sub_question_id() -> str:
    return uuid.uuid4().hex


def new_query_id() -> str:
    return uuid.uuid4().hex


def new_source_id() -> str:
    return uuid.uuid4().hex


def new_evidence_id() -> str:
    return uuid.uuid4().hex


def new_claim_id() -> str:
    return uuid.uuid4().hex


def new_binding_id() -> str:
    return uuid.uuid4().hex


def new_citation_id() -> str:
    return uuid.uuid4().hex


def new_verification_id() -> str:
    return uuid.uuid4().hex


def new_conflict_id() -> str:
    return uuid.uuid4().hex


def new_corroboration_id() -> str:
    return uuid.uuid4().hex


def new_reconciliation_id() -> str:
    return uuid.uuid4().hex
