-- 0003_runtime_health（P2-2，postgres 方言）——Runtime Health Plane（governance 迁移族 additive）。
-- 契约：P2-2_SPEC_v2.md Rev 2.2 §3/§12 + DECISION.md D-Phase2-P2-2-001 / -007 / -015 / -017。
-- 与 sqlite 方言同形表结构；差异仅时间列类型（TIMESTAMPTZ）。
-- heartbeat = Runtime Health Telemetry（**非** lease / fencing）；与 governance_tasks 逻辑关联、不建 FK；
-- 0001 / 0002 零改动；lifecycle 状态禁止由本表路径写入（LA-1）。

CREATE TABLE IF NOT EXISTS governance_runtime_health (
    task_id            TEXT PRIMARY KEY,
    thread_id          TEXT NOT NULL,
    run_id             TEXT,
    owner_instance     TEXT NOT NULL,
    last_heartbeat_at  TIMESTAMPTZ NOT NULL,
    beat_count         INTEGER NOT NULL DEFAULT 1,
    created_at         TIMESTAMPTZ NOT NULL,
    updated_at         TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runtime_health_heartbeat
    ON governance_runtime_health(last_heartbeat_at);
