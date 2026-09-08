"""F8 Runtime Governance — Step 1：governance schema/migration（SQLite）。

覆盖：fresh migration、upgrade 0000→0001、幂等 apply、UNIQUE/FK/index、models ↔ SQL 字段
一致性、sqlite TEXT-JSON 语义、dual JSON/时间归一（models.from_row）。

Plan Rev2 §2/§3 为单一字段权威：DDL 与 models 须与本测试的字段清单一致。
"""

import json
import shutil
import sqlite3
import uuid
from pathlib import Path

import pytest

from app.runtime.governance import migrations as gov_migrations
from app.runtime.governance import store as gov_store
from app.runtime.governance.models import (
    ErrorKind,
    GovernanceEvent,
    TaskRecord,
    TaskStatus,
    TerminalReason,
    terminal_reason_for,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST_TMP = _REPO_ROOT / "_testtmp"


@pytest.fixture
def gov_tmp():
    d = _TEST_TMP / f"gov-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    try:
        shutil.rmtree(d)
    except OSError:
        pass


#: Plan §2/§3 字段权威清单（governance_tasks 不允许 Plan 之外的 lifecycle 字段）
TASK_COLUMNS = [
    "task_id",
    "thread_id",
    "run_id",
    "status",
    "terminal_reason",
    "error_kind",
    "error",
    "policy_snapshot",
    "effective_limits",
    "counters_snapshot",
    "superseded_by",
    "parent_session",
    "underlying_linger_observed",
    "owner_instance",
    "version",
    "created_at",
    "started_at",
    "finished_at",
]

EVENT_COLUMNS = [
    "event_id",
    "task_id",
    "thread_id",
    "run_id",
    "seq",
    "event_type",
    "payload",
    "durable",
    "created_at",
]


def _fresh_store(gov_tmp):
    db = str(gov_tmp / "gov-fresh.sqlite")
    store = gov_store._GovernanceSqliteStore(db)
    return store, db


def _sqlite_connect(db):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


class TestGovernanceStoreSqlite:
    def test_fresh_migration_0000_to_0001(self, gov_tmp):
        """fresh DB：ensure_schema 后版本表 + 0001 存在，且两表结构齐全。"""
        store, db = _fresh_store(gov_tmp)
        assert gov_migrations.applied_migration_versions(store) == []
        gov_migrations.ensure_schema(store)
        assert gov_migrations.applied_migration_versions(store) == ["0001"]
        store.close()

    def test_upgrade_migration_0000_to_0001(self, gov_tmp):
        """upgrade 验证：先建空版本表（0000 状态），再 apply 0001。"""
        db = str(gov_tmp / "gov-upgrade.sqlite")
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS governance_schema_migrations ("
            "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        conn.commit()
        conn.close()
        store = gov_store._GovernanceSqliteStore(db)
        assert gov_migrations.applied_migration_versions(store) == []
        gov_migrations.ensure_schema(store)
        assert gov_migrations.applied_migration_versions(store) == ["0001"]
        store.close()

    def test_migration_idempotent(self, gov_tmp):
        store, _ = _fresh_store(gov_tmp)
        gov_migrations.ensure_schema(store)
        gov_migrations.ensure_schema(store)  # 幂等
        assert gov_migrations.applied_migration_versions(store) == ["0001"]
        store.close()

    def test_tasks_table_columns_and_indexes(self, gov_tmp):
        store, db = _fresh_store(gov_tmp)
        gov_migrations.ensure_schema(store)
        conn = sqlite3.connect(db)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(governance_tasks)")}
        idx = {r[1] for r in conn.execute("PRAGMA index_list(governance_tasks)")}
        conn.close()
        assert TASK_COLUMNS == sorted(cols) or set(TASK_COLUMNS) == cols
        # 不允许 Plan 之外的 lifecycle 字段（超集即失败）
        assert set(TASK_COLUMNS) == cols
        assert "idx_governance_tasks_thread" in idx
        assert "idx_governance_tasks_status" in idx
        assert "idx_governance_tasks_run" in idx
        store.close()

    def test_events_table_unique_fk_and_indexes(self, gov_tmp):
        store, db = _fresh_store(gov_tmp)
        gov_migrations.ensure_schema(store)
        conn = sqlite3.connect(db)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(governance_events)")}
        assert set(EVENT_COLUMNS) == cols
        idx = {r[1] for r in conn.execute("PRAGMA index_list(governance_events)")}
        assert "idx_governance_events_thread" in idx
        conn.close()
        # UNIQUE(task_id, seq) 约束生效
        task_id = "t1"
        with store.transaction() as tx:
            tx.execute(
                "INSERT INTO governance_tasks (task_id, thread_id, owner_instance, status,"
                " created_at) VALUES (%s,%s,%s,%s,%s)",
                (task_id, "th1", "inst1", "running", "2026-01-01T00:00:00+00:00"),
            )
        with store.transaction() as tx:
            tx.execute(
                "INSERT INTO governance_events (event_id, task_id, thread_id, seq,"
                " event_type, payload, durable, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    "e1",
                    task_id,
                    "th1",
                    1,
                    "task_created",
                    json.dumps({}),
                    1,
                    "2026-01-01T00:00:00+00:00",
                ),
            )
        with pytest.raises(Exception):  # UNIQUE(task_id, seq)
            with store.transaction() as tx:
                tx.execute(
                    "INSERT INTO governance_events (event_id, task_id, thread_id, seq,"
                    " event_type, payload, durable, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        "e2",
                        task_id,
                        "th1",
                        1,
                        "task_created",
                        json.dumps({}),
                        1,
                        "2026-01-01T00:00:00+00:00",
                    ),
                )
        # FK：不存在 task 的事件被拒绝
        with pytest.raises(Exception):
            with store.transaction() as tx:
                tx.execute(
                    "INSERT INTO governance_events (event_id, task_id, thread_id, seq,"
                    " event_type, payload, durable, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        "e3",
                        "no-such-task",
                        "th1",
                        2,
                        "x",
                        json.dumps({}),
                        1,
                        "2026-01-01T00:00:00+00:00",
                    ),
                )
        store.close()

    def test_task_insert_read_roundtrip_json(self, gov_tmp):
        """models ↔ SQL 一致性：policy/limits JSON 写入后可经 TaskRecord.from_row 还原。"""
        store, _ = _fresh_store(gov_tmp)
        gov_migrations.ensure_schema(store)
        policy = {"wall_clock_timeout": 600, "max_llm_calls": 120}
        limits = {"llm_calls": 120, "agent_steps": 200}
        with store.transaction() as tx:
            tx.execute(
                "INSERT INTO governance_tasks (task_id, thread_id, owner_instance, status,"
                " run_id, policy_snapshot, effective_limits, version, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    "task-1",
                    "th-1",
                    "inst-1",
                    "running",
                    "run-1",
                    gov_store.json_param(policy, "sqlite"),
                    gov_store.json_param(limits, "sqlite"),
                    0,
                    "2026-01-01T00:00:00+00:00",
                ),
            )
        row = store.execute(
            "SELECT * FROM governance_tasks WHERE task_id=%s", ("task-1",)
        )[0]
        rec = TaskRecord.from_row(row)
        assert rec.task_id == "task-1"
        assert rec.status == TaskStatus.RUNNING.value
        assert rec.policy_snapshot == policy
        assert rec.effective_limits == limits
        assert rec.is_terminal is False
        assert rec.version == 0
        store.close()

    def test_terminal_helpers(self):
        assert terminal_reason_for(TaskStatus.COMPLETED) == TerminalReason.COMPLETED
        assert terminal_reason_for(TaskStatus.ABORTED) == TerminalReason.ABORTED
        with pytest.raises(ValueError):
            terminal_reason_for(TaskStatus.RUNNING)
        assert ErrorKind.AGENT_FAILURE.value == "agent_failure"
        assert TaskStatus.ORPHAN_RECLAIMED.value == "orphan_reclaimed"

    def test_event_row_roundtrip(self, gov_tmp):
        store, _ = _fresh_store(gov_tmp)
        gov_migrations.ensure_schema(store)
        with store.transaction() as tx:
            tx.execute(
                "INSERT INTO governance_tasks (task_id, thread_id, owner_instance, status,"
                " created_at) VALUES (%s,%s,%s,%s,%s)",
                ("t-ev", "th-ev", "inst1", "running", "2026-01-01T00:00:00+00:00"),
            )
            tx.execute(
                "INSERT INTO governance_events (event_id, task_id, thread_id, seq,"
                " event_type, payload, durable, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    "ev-1",
                    "t-ev",
                    "th-ev",
                    1,
                    "task_status_change",
                    gov_store.json_param({"status": "completed"}, "sqlite"),
                    1,
                    "2026-01-02T00:00:00+00:00",
                ),
            )
        row = store.execute(
            "SELECT * FROM governance_events WHERE event_id=%s", ("ev-1",)
        )[0]
        ev = GovernanceEvent.from_row(row)
        assert ev.event_id == "ev-1"
        assert ev.task_id == "t-ev"
        assert ev.seq == 1
        assert ev.payload == {"status": "completed"}
        assert ev.durable is True
        store.close()

    def test_governance_store_env_parsing(self, monkeypatch):
        # 默认（env=None 读 os.environ）：GOVERNANCE_BACKEND 缺省 sqlite
        monkeypatch.delenv("GOVERNANCE_BACKEND", raising=False)
        monkeypatch.delenv("GOVERNANCE_DB", raising=False)
        monkeypatch.delenv("GOVERNANCE_DSN", raising=False)
        monkeypatch.delenv("AGENT_CHECKPOINT_DSN", raising=False)
        cfg = gov_store.parse_governance_config()
        assert cfg["backend"] == "sqlite"
        assert cfg["db_path"].endswith("governance.sqlite")
        # postgres 但 DSN 缺失 → fail-closed
        monkeypatch.setenv("GOVERNANCE_BACKEND", "postgres")
        with pytest.raises(gov_store.GovernanceStoreError):
            gov_store.parse_governance_config()
        # AGENT_CHECKPOINT_DSN 兜底
        monkeypatch.setenv("AGENT_CHECKPOINT_DSN", "postgresql://x")
        cfg2 = gov_store.parse_governance_config()
        assert cfg2 == {"backend": "postgres", "dsn": "postgresql://x"}
        # 非法 backend
        monkeypatch.setenv("GOVERNANCE_BACKEND", "mongo")
        with pytest.raises(gov_store.GovernanceStoreError):
            gov_store.parse_governance_config()
