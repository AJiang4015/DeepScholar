-- 0003_runtime_health（P2-2，sqlite 方言）——Runtime Health Plane（governance 迁移族 additive）。
-- 契约：P2-2_SPEC_v2.md Rev 2.2 §3/§12 + DECISION.md D-Phase2-P2-2-001 / -007 / -015 / -017。
-- heartbeat = Runtime Health Telemetry（**非** lease / fencing）；本表仅承载存活遥测，
-- 与 governance_tasks 为**逻辑关联、不建 FK**（沿用 0002 sessions 的「派生关联」纪律）。
-- 0001（governance_tasks/governance_events）与 0002（sessions）零改动；
-- lifecycle 状态（status/version/terminal_*）**禁止**由本表路径写入（LA-1）。

CREATE TABLE IF NOT EXISTS governance_runtime_health (
    task_id            TEXT PRIMARY KEY,
    thread_id          TEXT NOT NULL,
    run_id             TEXT,
    owner_instance     TEXT NOT NULL,
    last_heartbeat_at  TEXT NOT NULL,
    beat_count         INTEGER NOT NULL DEFAULT 1,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runtime_health_heartbeat
    ON governance_runtime_health(last_heartbeat_at);
