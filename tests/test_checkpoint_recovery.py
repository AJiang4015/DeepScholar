"""
R2 演进后恢复测试：官方 AsyncSqliteSaver（backend 抽象，sqlite 缺省）的
跨进程 restart recovery（非 mock）+ 进程内 async resume + 配置/工厂单测。

背景（见 docs/spec/2026-09-03-postgres-checkpoint-migration.md）：
- 原 AsyncBridgeSqliteSaver 已退役，改官方 AsyncSqliteSaver（aiosqlite）；
- 官方 AsyncSaver 的 sync 方法仅限“异线程”使用（同 loop 抛 InvalidStateError），
  因此跨进程 runner 由 sync invoke 改为 asyncio.run + ainvoke；
- 恢复语义判据保持原 R2 不变：随机 marker_a 证明进程 B 未重跑 step1；
- checkpoint 后端由 env 选择：缺省 sqlite（本文件全部用例）；PG 用例见
  test_checkpoint_postgres.py / test_checkpoint_replay_verify.py。

环境要求：langgraph-checkpoint-sqlite + aiosqlite 已安装（缺省整文件 skip）；
不依赖 deepagents / LLM / 网络 / MySQL / RAGFlow。
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import TypedDict

import pytest

pytest.importorskip(
    "langgraph.checkpoint.sqlite", reason="需要 langgraph-checkpoint-sqlite 包"
)
pytest.importorskip("aiosqlite", reason="需要 aiosqlite 包")

from app.runtime import checkpoint as cp  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST_TMP = _REPO_ROOT / "_testtmp"


def _new_tmp_dir(prefix: str) -> Path:
    """在工作区 _testtmp/ 下建一个独立临时目录（默认权限，沙箱可写）。"""
    d = _TEST_TMP / f"{prefix}-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def r2_tmp():
    d = _new_tmp_dir("r2")
    yield d
    try:
        shutil.rmtree(d)
    except OSError:
        pass


@pytest.fixture(autouse=True)
def _close_holder_after_each_test():
    """每个用例后关闭模块级 holder（aiosqlite 工作线程需随连接关闭，
    否则 asyncio.run 返回后解释器退出会被非守护线程阻塞）。"""
    yield
    asyncio.run(cp.close_checkpointer())


# ---------------------------------------------------------------------------
# 子进程 runner：async 写法（asyncio.run + ainvoke）
# 进程 A（write）→ interrupt 后退出；进程 B（read/resume）同 DB 找回/续跑。
# ---------------------------------------------------------------------------
RESTART_RUNNER = """
import asyncio
import json
import sys
import uuid
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.runtime.checkpoint import get_checkpointer


class AgentState(TypedDict, total=False):
    log: list
    counter: int
    marker_a: str
    marker_b: str


def step1(state):
    return {
        "log": state.get("log", []) + ["step1 ran"],
        "counter": state.get("counter", 0) + 1,
        "marker_a": uuid.uuid4().hex,
    }


def step2(state):
    resumed = interrupt("resume-please")
    return {
        "log": state["log"] + ["step2 ran resumed=" + str(resumed)],
        "marker_b": str(resumed),
    }


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("step1", step1)
    g.add_node("step2", step2)
    g.add_edge(START, "step1")
    g.add_edge("step1", "step2")
    g.add_edge("step2", END)
    return g  # 未 compile；main 中绑定 saver 后 compile


async def amain(phase: str):
    saver = await get_checkpointer()  # backend=sqlite（env），loop 内创建
    graph = build_graph().compile(checkpointer=saver)
    config = {"configurable": {"thread_id": "A"}}
    if phase == "write":
        await graph.ainvoke({"log": [], "counter": 0}, config)
        snap = await graph.aget_state(config)
        print(json.dumps({
            "marker_a": snap.values["marker_a"],
            "log": snap.values["log"],
            "counter": snap.values["counter"],
        }))
    elif phase == "read":
        snap = await graph.aget_state(config)
        print(json.dumps({"values": snap.values, "next": list(snap.next)}))
    elif phase == "resume":
        final = await graph.ainvoke(Command(resume="resumed-by-B"), config)
        print(json.dumps(final))
    else:
        raise SystemExit("unknown phase: " + phase)


def main():
    phase = sys.argv[1]

    async def amain_with_close():
        await amain(phase)
        from app.runtime.checkpoint import close_checkpointer

        await close_checkpointer()  # 关闭 aiosqlite 连接，避免进程退出阻塞

    asyncio.run(amain_with_close())


if __name__ == "__main__":
    main()
"""


def _run_phase(runner, env, phase):
    proc = subprocess.run(
        [sys.executable, str(runner), phase],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=_REPO_ROOT,
        timeout=180,
    )
    assert proc.returncode == 0, (
        f"phase '{phase}' failed rc={proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    last = proc.stdout.strip().splitlines()[-1]
    return json.loads(last)


def _point_env(monkeypatch, db_path: Path) -> None:
    """把配置指向指定 sqlite DB 文件（sqlite 后端缺省，仅覆盖路径）。"""
    monkeypatch.delenv(cp.CHECKPOINT_BACKEND_ENV, raising=False)
    monkeypatch.setenv(cp.CHECKPOINT_DB_ENV, str(db_path))


# ---------------------------------------------------------------------------
# 配置解析 / 工厂单测
# ---------------------------------------------------------------------------
class TestCheckpointConfig:
    def test_default_backend_and_path(self, monkeypatch):
        monkeypatch.delenv(cp.CHECKPOINT_BACKEND_ENV, raising=False)
        monkeypatch.delenv(cp.CHECKPOINT_DB_ENV, raising=False)
        cfg = cp.parse_checkpoint_config()
        assert cfg.backend == "sqlite"
        assert cfg.db_path == cp.DEFAULT_CHECKPOINT_DB_PATH

    def test_env_override_path(self, monkeypatch, r2_tmp):
        target = r2_tmp / "custom" / "cp.sqlite"
        _point_env(monkeypatch, target)
        cfg = cp.parse_checkpoint_config()
        assert cfg.backend == "sqlite"
        assert cfg.db_path == target.resolve()
        assert cp.get_checkpoint_db_path() == str(target.resolve())

    def test_invalid_backend_rejected(self, monkeypatch):
        monkeypatch.setenv(cp.CHECKPOINT_BACKEND_ENV, "mongo")
        with pytest.raises(ValueError, match="AGENT_CHECKPOINT_BACKEND 取值非法"):
            cp.parse_checkpoint_config()

    def test_postgres_requires_dsn(self, monkeypatch):
        monkeypatch.setenv(cp.CHECKPOINT_BACKEND_ENV, "postgres")
        monkeypatch.delenv(cp.CHECKPOINT_DSN_ENV, raising=False)
        with pytest.raises(ValueError, match="AGENT_CHECKPOINT_DSN"):
            cp.parse_checkpoint_config()

    def test_postgres_with_dsn(self, monkeypatch):
        monkeypatch.setenv(cp.CHECKPOINT_BACKEND_ENV, "postgres")
        monkeypatch.setenv(cp.CHECKPOINT_DSN_ENV, "postgresql://u:p@h:5432/db")
        cfg = cp.parse_checkpoint_config()
        assert cfg.backend == "postgres"
        assert cfg.dsn == "postgresql://u:p@h:5432/db"


class TestSqliteFactory:
    def test_creates_db_with_tables(self, monkeypatch, r2_tmp):
        target = r2_tmp / "nested" / "dirs" / "checkpoints.sqlite"
        _point_env(monkeypatch, target)

        async def scenario():
            saver = await cp.get_checkpointer()
            assert saver is not None
            return saver

        asyncio.run(scenario())
        assert target.exists(), "DB 文件应被创建"
        import sqlite3

        with sqlite3.connect(target) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        assert {"checkpoints", "writes"} <= tables

    def test_fail_fast_when_db_parent_is_a_file(self, monkeypatch, r2_tmp):
        blocker = r2_tmp / "not_a_dir"
        blocker.write_text("i am a file", encoding="utf-8")
        _point_env(monkeypatch, blocker / "db.sqlite")

        async def scenario():
            with pytest.raises(RuntimeError, match="无法初始化 SQLite checkpointer"):
                await cp.get_checkpointer()

        asyncio.run(scenario())

    def test_fail_fast_when_db_path_is_a_directory(self, monkeypatch, r2_tmp):
        target = r2_tmp / "adir.sqlite"
        target.mkdir()
        _point_env(monkeypatch, target)

        async def scenario():
            with pytest.raises(RuntimeError, match="无法初始化 SQLite checkpointer"):
                await cp.get_checkpointer()

        asyncio.run(scenario())

    def test_same_loop_same_path_reused(self, monkeypatch, r2_tmp):
        target = r2_tmp / "one.sqlite"
        _point_env(monkeypatch, target)

        async def scenario():
            first = await cp.get_checkpointer()
            second = await cp.get_checkpointer()
            return first, second

        first, second = asyncio.run(scenario())
        assert first is second


# ---------------------------------------------------------------------------
# 真实跨进程 restart recovery（async runner）
# ---------------------------------------------------------------------------
class TestRestartRecovery:
    def test_restart_recovery_real_subprocesses(self, monkeypatch, r2_tmp):
        db = r2_tmp / "checkpoints.sqlite"
        runner = r2_tmp / "recovery_runner.py"
        runner.write_text(RESTART_RUNNER, encoding="utf-8")

        env = dict(os.environ)
        _point_env(monkeypatch, db)
        env[cp.CHECKPOINT_BACKEND_ENV] = "sqlite"
        env[cp.CHECKPOINT_DB_ENV] = str(db)
        env.pop(cp.CHECKPOINT_DSN_ENV, None)
        env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")

        # 进程 A：step1 执行后 interrupt → checkpoint 落盘 → 退出
        out_a = _run_phase(runner, env, "write")
        assert out_a["log"] == ["step1 ran"]
        assert out_a["counter"] == 1
        assert out_a["marker_a"], "进程 A 的 step1 应写入随机 marker_a"

        # 落盘证据：DB 文件存在 + checkpoints 表存在 thread_id=A 记录
        assert db.exists(), "SQLite checkpoint 文件应已落盘"
        import sqlite3

        with sqlite3.connect(db) as conn:
            (count,) = conn.execute(
                "SELECT count(*) FROM checkpoints WHERE thread_id = ?", ("A",)
            ).fetchone()
        assert count >= 1

        # 进程 B（全新进程）找回状态
        out_read = _run_phase(runner, env, "read")
        assert out_read["values"]["counter"] == 1
        assert out_read["values"]["log"] == ["step1 ran"]
        assert out_read["values"]["marker_a"] == out_a["marker_a"]
        assert "step2" in out_read["next"]

        # 进程 B 从 checkpoint 续跑（step2 → END），未重跑 step1
        out_b = _run_phase(runner, env, "resume")
        assert out_b["counter"] == 1, "step1 不应被重跑"
        assert out_b["log"] == [
            "step1 ran",
            "step2 ran resumed=resumed-by-B",
        ]
        assert out_b["marker_a"] == out_a["marker_a"], (
            "随机 marker_a 跨进程存活 => resume 而非重跑"
        )
        assert out_b["marker_b"] == "resumed-by-B"


# ---------------------------------------------------------------------------
# 进程内 async resume（生产路径 astream 兼容性，官方 AsyncSqliteSaver）
# ---------------------------------------------------------------------------
class TestAsyncAstreamCompatibility:
    def test_factory_saver_serves_astream_with_interrupt_resume(
        self, monkeypatch, r2_tmp
    ):
        db = r2_tmp / "async.sqlite"
        _point_env(monkeypatch, db)

        from langgraph.graph import END, START, StateGraph
        from langgraph.types import Command, interrupt

        class _AsyncSt(TypedDict, total=False):
            log: list
            counter: int
            done: str

        def step1(s):
            return {
                "log": s.get("log", []) + ["step1"],
                "counter": s.get("counter", 0) + 1,
            }

        def step2(s):
            resumed = interrupt("resume-please")
            return {"log": s["log"] + [f"step2:{resumed}"], "done": str(resumed)}

        async def scenario():
            saver = await cp.get_checkpointer()
            graph = (
                StateGraph(_AsyncSt)
                .add_node("step1", step1)
                .add_node("step2", step2)
                .add_edge(START, "step1")
                .add_edge("step1", "step2")
                .add_edge("step2", END)
                .compile(checkpointer=saver)
            )
            cfg = {"configurable": {"thread_id": "async-thread"}}
            async for _chunk in graph.astream({"log": [], "counter": 0}, cfg):
                pass
            snap = await graph.aget_state(cfg)
            assert snap.values["counter"] == 1
            assert "step2" in snap.next
            return await graph.ainvoke(Command(resume="resumed"), cfg)

        final = asyncio.run(scenario())
        assert final["counter"] == 1, "async resume 不应重跑 step1"
        assert final["log"] == ["step1", "step2:resumed"]
        assert final["done"] == "resumed"

        # 落盘证据
        import sqlite3

        with sqlite3.connect(db) as conn:
            (count,) = conn.execute(
                "SELECT count(*) FROM checkpoints WHERE thread_id = ?",
                ("async-thread",),
            ).fetchone()
        assert count >= 1
