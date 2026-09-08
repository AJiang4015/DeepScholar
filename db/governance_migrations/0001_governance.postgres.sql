-- 0001_governance（F8，postgres 方言）——governance 表族自管，独立于 checkpoint/research。
-- 契约：docs/spec/2026-09-14-f8-runtime-execution-governance.md rev2 + Plan Rev2 §2/§3。
-- governance_tasks = Task Lifecycle State；governance_events = Event History（append-only）。
-- seq = governance durable replay sequence（task-scoped），与 monitor_seq 不混用。
-- SQLite TEXT-JSON / PostgreSQL JSONB 保持同语义。

CREATE TABLE IF NOT EXISTS governance_tasks (
    task_id                       TEXT PRIMARY KEY,
    thread_id                     TEXT NOT NULL,
    run_id                        TEXT,
    status                        TEXT NOT NULL,
    terminal_reason               TEXT,
    error_kind                    TEXT,
    error                         TEXT,
    policy_snapshot               JSONB NOT NULL DEFAULT '{}',
    effective_limits              JSONB NOT NULL DEFAULT '{}',
    counters_snapshot             JSONB,
    superseded_by                 TEXT,
    parent_session                TEXT,
    underlying_linger_observed    BOOLEAN,
    owner_instance                TEXT NOT NULL,
    version                       INTEGER NOT NULL DEFAULT 0,
    created_at                    TIMESTAMPTZ NOT NULL,
    started_at                    TIMESTAMPTZ,
    finished_at                   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_governance_tasks_thread ON governance_tasks(thread_id, status);
CREATE INDEX IF NOT EXISTS idx_governance_tasks_status ON governance_tasks(status);
CREATE INDEX IF NOT EXISTS idx_governance_tasks_run ON governance_tasks(run_id);

CREATE TABLE IF NOT EXISTS governance_events (
    event_id    TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL REFERENCES governance_tasks(task_id) ON DELETE CASCADE,
    thread_id   TEXT NOT NULL,
    run_id      TEXT,
    seq         BIGINT NOT NULL,
    event_type  TEXT NOT NULL,
    payload     JSONB NOT NULL DEFAULT '{}',
    durable     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL,
    UNIQUE (task_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_governance_events_thread ON governance_events(thread_id, seq);
CREATE INDEX IF NOT EXISTS idx_governance_events_task ON governance_events(task_id, seq);
CREATE INDEX IF NOT EXISTS idx_governance_events_run ON governance_events(run_id);
