-- 0002_sessions（Multi-Session，sqlite 方言）——sessions 表族（governance 迁移族 additive）。
-- 契约：docs/plan/2026-10-03-multi-session-backend-architecture-analysis-and-plan.md + Decision Closure。
-- sessions = Session 容器元数据（对话容器；session_id == thread_id，见 Decision #1）。
-- 归属为派生关联：sessions.session_id == governance_tasks.thread_id == research_runs.thread_id（不建 FK，
-- 允许既有遗留 thread 无 session 行；Task 可无 Session = 旧行为保留）。
-- 0001 表族与索引零改动；幂等 apply（governance_schema_migrations 版本表由 migration runner 管理）。

CREATE TABLE IF NOT EXISTS sessions (
    session_id  TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    description TEXT,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_status_updated ON sessions(status, updated_at);
