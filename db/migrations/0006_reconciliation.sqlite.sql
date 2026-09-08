-- 0006_reconciliation（F6，sqlite 方言）
-- 仅新增 reconciliations 表；零 ALTER F1–F5。
-- 契约：docs/spec/2026-09-12-f6-conflict-reconciliation.md rev2
-- F6 explains/registers conflict state, it does not adjudicate truth.
-- status 仅 complete|failed；outcome 三态（SAME_ORIGIN_CONTRADICTION / DETAIL_INCONSISTENCY / GENUINE_CONTESTED）
-- identity=(conflict_id, method_fingerprint)；conflict_id FK ON DELETE CASCADE（沿用 F4 删除语义）。

CREATE TABLE IF NOT EXISTS reconciliations (
    reconciliation_id   TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL REFERENCES research_runs(run_id) ON DELETE CASCADE,
    claim_id            TEXT NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
    conflict_id         TEXT NOT NULL REFERENCES conflicts(conflict_id) ON DELETE CASCADE,
    method_spec         TEXT NOT NULL,
    method_fingerprint  TEXT NOT NULL,
    status              TEXT NOT NULL,
    outcome             TEXT,
    detail              TEXT,
    error               TEXT,
    computed_at         TEXT NOT NULL,
    metadata            TEXT NOT NULL DEFAULT '{}',
    UNIQUE (conflict_id, method_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_reconciliations_run ON reconciliations(run_id);
CREATE INDEX IF NOT EXISTS idx_reconciliations_claim ON reconciliations(run_id, claim_id);
CREATE INDEX IF NOT EXISTS idx_reconciliations_conflict ON reconciliations(conflict_id);
