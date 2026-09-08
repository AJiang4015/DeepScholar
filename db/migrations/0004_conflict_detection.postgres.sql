-- 0004_conflict_detection（F4，postgres 方言）
-- 仅新增 conflicts 表；零 ALTER F1/F2/F3。
-- 契约：docs/spec/2026-09-10-conflict-detection.md rev2
-- invariant：genuine/conflict_type 状态矩阵（§10c）与跨表 integrity（§10b）由唯一写路径+测试锁定
--（DDL 可移植：不使用跨表 CHECK/触发器）。
-- verification_a/b 删除策略：ON DELETE SET NULL（signal 快照写入 metadata）。

CREATE TABLE IF NOT EXISTS conflicts (
    conflict_id          TEXT PRIMARY KEY,
    run_id               TEXT NOT NULL REFERENCES research_runs(run_id) ON DELETE CASCADE,
    claim_id             TEXT NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
    evidence_a_id        TEXT NOT NULL REFERENCES evidences(evidence_id) ON DELETE CASCADE,
    evidence_b_id        TEXT NOT NULL REFERENCES evidences(evidence_id) ON DELETE CASCADE,
    verification_a_id    TEXT REFERENCES verifications(verification_id) ON DELETE SET NULL,
    verification_b_id    TEXT REFERENCES verifications(verification_id) ON DELETE SET NULL,
    detector_spec        JSONB NOT NULL,
    detector_fingerprint TEXT NOT NULL,
    candidate_source     TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'candidate',
    conflict_type        TEXT,
    genuine              BOOLEAN,
    rationale            TEXT,
    error                TEXT,
    created_at           TIMESTAMPTZ NOT NULL,
    completed_at         TIMESTAMPTZ,
    metadata             JSONB NOT NULL DEFAULT '{}',
    UNIQUE (run_id, claim_id, evidence_a_id, evidence_b_id, detector_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_conflicts_run ON conflicts(run_id);
CREATE INDEX IF NOT EXISTS idx_conflicts_claim ON conflicts(run_id, claim_id);
CREATE INDEX IF NOT EXISTS idx_conflicts_evidence_a ON conflicts(evidence_a_id);
CREATE INDEX IF NOT EXISTS idx_conflicts_evidence_b ON conflicts(evidence_b_id);
