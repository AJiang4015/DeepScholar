-- 0003_semantic_verification（F3，sqlite 方言）
-- 仅新增 verifications 表；零 ALTER F1/F2。
-- 契约：docs/spec/2026-09-09-semantic-verification.md rev2
-- 单条状态仅 pending|succeeded|failed（partial 只属 batch/run 层，不落库）；
-- verdict 仅 status=succeeded 时非空；error 仅 failed。

CREATE TABLE IF NOT EXISTS verifications (
    verification_id      TEXT PRIMARY KEY,
    run_id               TEXT NOT NULL REFERENCES research_runs(run_id) ON DELETE CASCADE,
    claim_id             TEXT NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
    evidence_id          TEXT NOT NULL REFERENCES evidences(evidence_id) ON DELETE CASCADE,
    verifier_spec        TEXT NOT NULL,
    verifier_fingerprint TEXT NOT NULL,
    verdict              TEXT,
    rationale            TEXT,
    confidence           REAL,
    status               TEXT NOT NULL DEFAULT 'pending',
    error                TEXT,
    created_at           TEXT NOT NULL,
    completed_at         TEXT,
    metadata             TEXT NOT NULL DEFAULT '{}',
    UNIQUE (run_id, claim_id, evidence_id, verifier_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_verifications_run ON verifications(run_id);
CREATE INDEX IF NOT EXISTS idx_verifications_claim ON verifications(run_id, claim_id);
CREATE INDEX IF NOT EXISTS idx_verifications_evidence ON verifications(evidence_id);
