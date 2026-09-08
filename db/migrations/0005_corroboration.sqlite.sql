-- 0005_corroboration（F5，sqlite 方言）
-- 仅新增 corroborations 表；零 ALTER F1–F4。
-- 契约：docs/spec/2026-09-11-f5-audit-and-independent-corroboration.md rev2
-- F5 measures independence, it does not score trust.
-- status 仅 complete|failed（review 失败不改变 complete）；clusters v1 不拆表。

CREATE TABLE IF NOT EXISTS corroborations (
    corroboration_id       TEXT PRIMARY KEY,
    run_id                 TEXT NOT NULL REFERENCES research_runs(run_id) ON DELETE CASCADE,
    claim_id               TEXT NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
    method_spec            TEXT NOT NULL,
    method_fingerprint     TEXT NOT NULL,
    status                 TEXT NOT NULL,
    error                  TEXT,
    computed_at            TEXT NOT NULL,
    metadata               TEXT NOT NULL DEFAULT '{}',
    global_clusters        TEXT NOT NULL DEFAULT '[]',
    support                TEXT NOT NULL DEFAULT '{}',
    contradict             TEXT NOT NULL DEFAULT '{}',
    conflicts_independence TEXT NOT NULL DEFAULT '[]',
    source_profile         TEXT NOT NULL DEFAULT '{}',
    UNIQUE (run_id, claim_id, method_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_corroborations_run ON corroborations(run_id);
CREATE INDEX IF NOT EXISTS idx_corroborations_claim ON corroborations(run_id, claim_id);
