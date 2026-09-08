-- 0002_claim_citation（F2，postgres 方言）
-- 仅新增 claims / claim_evidences / citations；零 ALTER F1 五张表。
-- 契约：docs/spec/2026-09-08-claim-citation-binding.md（rev2）
-- 注意：citations 不含任何呈现编号（citation_key）列；citation_id 为稳定 artifact identity。

CREATE TABLE IF NOT EXISTS claims (
    claim_id        TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES research_runs(run_id) ON DELETE CASCADE,
    sub_question_id TEXT NOT NULL REFERENCES sub_questions(sub_question_id),
    statement       TEXT NOT NULL,
    statement_sha   TEXT NOT NULL,
    claim_type      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'drafted',
    created_at      TIMESTAMPTZ NOT NULL,
    metadata        JSONB NOT NULL DEFAULT '{}',
    UNIQUE (run_id, statement_sha)
);
CREATE INDEX IF NOT EXISTS idx_claims_run ON claims(run_id);
CREATE INDEX IF NOT EXISTS idx_claims_subq ON claims(sub_question_id);

CREATE TABLE IF NOT EXISTS claim_evidences (
    binding_id  TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES research_runs(run_id) ON DELETE CASCADE,
    claim_id    TEXT NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
    evidence_id TEXT NOT NULL REFERENCES evidences(evidence_id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL,
    metadata    JSONB NOT NULL DEFAULT '{}',
    UNIQUE (claim_id, evidence_id)
);
CREATE INDEX IF NOT EXISTS idx_claim_evidences_run ON claim_evidences(run_id);
CREATE INDEX IF NOT EXISTS idx_claim_evidences_evidence ON claim_evidences(evidence_id);

CREATE TABLE IF NOT EXISTS citations (
    citation_id TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES research_runs(run_id) ON DELETE CASCADE,
    claim_id    TEXT NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
    evidence_id TEXT NOT NULL REFERENCES evidences(evidence_id) ON DELETE CASCADE,
    quote       TEXT,
    locator     TEXT,
    metadata    JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL,
    UNIQUE (run_id, claim_id, evidence_id)
);
CREATE INDEX IF NOT EXISTS idx_citations_claim ON citations(run_id, claim_id);
