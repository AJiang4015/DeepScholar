CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_runs (
    run_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    question TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    plan JSONB,
    budget JSONB,
    metadata JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_research_runs_thread_id ON research_runs(thread_id);
CREATE INDEX IF NOT EXISTS idx_research_runs_status ON research_runs(status);

CREATE TABLE IF NOT EXISTS sub_questions (
    sub_question_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES research_runs(run_id) ON DELETE CASCADE,
    parent_id TEXT REFERENCES sub_questions(sub_question_id),
    position INTEGER NOT NULL,
    question TEXT NOT NULL,
    rationale TEXT,
    status TEXT NOT NULL,
    assigned_agent TEXT,
    UNIQUE (run_id, position)
);
CREATE INDEX IF NOT EXISTS idx_sub_questions_run_id ON sub_questions(run_id);

CREATE TABLE IF NOT EXISTS search_queries (
    query_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES research_runs(run_id) ON DELETE CASCADE,
    sub_question_id TEXT NOT NULL REFERENCES sub_questions(sub_question_id),
    query TEXT NOT NULL,
    agent TEXT NOT NULL,
    tool TEXT NOT NULL,
    topic TEXT,
    seq INTEGER,
    fetched_at TIMESTAMPTZ NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_search_queries_run_id ON search_queries(run_id);
CREATE INDEX IF NOT EXISTS idx_search_queries_subq_id ON search_queries(sub_question_id);

CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES research_runs(run_id) ON DELETE CASCADE,
    query_id TEXT NOT NULL REFERENCES search_queries(query_id),
    source_type TEXT NOT NULL,
    title TEXT NOT NULL,
    canonical_url TEXT,
    locator TEXT NOT NULL,
    canonical_key TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    agent TEXT NOT NULL,
    publisher TEXT,
    published_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}',
    UNIQUE (run_id, source_type, canonical_key)
);
CREATE INDEX IF NOT EXISTS idx_sources_run_type ON sources(run_id, source_type);
CREATE INDEX IF NOT EXISTS idx_sources_canonical ON sources(canonical_key);

CREATE TABLE IF NOT EXISTS evidences (
    evidence_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES research_runs(run_id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    sub_question_id TEXT NOT NULL REFERENCES sub_questions(sub_question_id),
    content TEXT NOT NULL,
    locator TEXT NOT NULL,
    extraction_method TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_evidences_run_source ON evidences(run_id, source_id);
CREATE INDEX IF NOT EXISTS idx_evidences_source_id ON evidences(source_id);
CREATE INDEX IF NOT EXISTS idx_evidences_subq_id ON evidences(sub_question_id);
