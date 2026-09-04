"""
R2 测试：Checkpointer 工厂单测 + 真实跨进程 restart recovery（非 mock）。

核心验收载体（Spec §9.1）：用**真实子进程**模拟进程重启——
  进程 A（subprocess）用 thread_id=A 执行最小 LangGraph 图，step1 后 interrupt
  （checkpoint 落盘 SQLite）→ 进程退出；
  进程 B（全新 subprocess，全新 Checkpointer 实例）打开同一 SQLite DB，
  thread_id=A 找回状态（get_state）并从 checkpoint 续跑（step2 → END）。

如何证明是 resume 而非重跑（AC-4）：
- step1 每次执行写入**随机** marker_a（uuid4）；若进程 B 从 START 重跑，
  step1 会再次执行并生成新 marker_a → 与进程 A 的值必然不同；
- 断言：进程 B 结束后 counter==1、log 中 step1 只出现一次、marker_a 与进程 A
  完全一致、marker_b 为进程 B 注入值 → 证明 step2 才继续执行、step1 未重跑。

临时目录约定：与 R1 测试一致（本沙箱禁止 pytest tmp_path 的 0o700 目录写文件），
统一建在工作区 `_testtmp/` 下（gitignore 已忽略 `_testtmp/`），每个用例独立子目录。

环境要求：langgraph-checkpoint-sqlite 已安装（缺省时整文件 skip，符合 TESTING.md §1）；
测试不依赖 deepagents / LLM / 网络 / MySQL / RAGFlow。
"""

import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path
from typing import TypedDict

import pytest

pytest.importorskip(
    "langgraph.checkpoint.sqlite", reason="需要 langgraph-checkpoint-sqlite 包"
)

from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402
from langgraph.types import Command, interrupt  # noqa: E402

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
    """独立临时目录 fixture，teardown 尽力清理（清理失败残留可忽略，已 gitignore）。"""
    d = _new_tmp_dir("r2")
    yield d
    try:
        shutil.rmtree(d)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 子进程 runner：真实进程 A（write）/ 进程 B（read = AC-3 找回 / resume = AC-4 续跑）。
# 通过 env AGENT_CHECKPOINT_DB 指向临时 DB，并走 app.runtime.checkpoint 真实工厂，
# 从而把"工厂 + SqliteSaver + 落盘 + 跨进程恢复"整条链路都纳入验证。
# ---------------------------------------------------------------------------
RESTART_RUNNER = '''
import json
import sys
import uuid
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.runtime.checkpoint import get_sqlite_checkpointer


class AgentState(TypedDict, total=False):
    log: list
    counter: int
    marker_a: str
    marker_b: str


def step1(state):
    # 每次执行生成随机值：若进程 B 重跑 step1，marker_a 必然与进程 A 不同（可证伪 resume）
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
    return (
        StateGraph(AgentState)
        .add_node("step1", step1)
        .add_node("step2", step2)
        .add_edge(START, "step1")
        .add_edge("step1", "step2")
        .add_edge("step2", END)
        .compile(checkpointer=get_sqlite_checkpointer())
    )


def main():
    thread = sys.argv[1]
    phase = sys.argv[2]
    graph = build_graph()
    config = {"configurable": {"thread_id": thread}}
    if phase == "write":
        # 进程 A：step1 执行后 interrupt → LangGraph 在 step2 前落 checkpoint → 正常退出
        graph.invoke({"log": [], "counter": 0}, config)
        snap = graph.get_state(config)
        print(json.dumps({
            "marker_a": snap.values["marker_a"],
            "log": snap.values["log"],
            "counter": snap.values["counter"],
        }))
    elif phase == "read":
        # 进程 B：全新 Checkpointer 打开同一 DB，thread_id=A 找回之前状态（AC-3）
        snap = graph.get_state(config)
        print(json.dumps({"values": snap.values, "next": list(snap.next)}))
    elif phase == "resume":
        # 进程 B：从 checkpoint 继续执行剩余节点 step2（AC-4）
        final = graph.invoke(Command(resume="resumed-by-B"), config)
        print(json.dumps(final))
    else:
        raise SystemExit("unknown phase: " + phase)


if __name__ == "__main__":
    main()
'''


def _run_phase(runner, env, thread, phase):
    proc = subprocess.run(
        [sys.executable, str(runner), thread, phase],
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
    """把工厂指向指定 DB 文件（env 覆盖）。"""
    monkeypatch.setenv(cp.CHECKPOINT_DB_ENV, str(db_path))


# ---------------------------------------------------------------------------
# Checkpointer 工厂单测（Spec §9.2）
# ---------------------------------------------------------------------------


class TestCheckpointFactory:
    def test_default_path_resolution(self, monkeypatch):
        """未配置 env 时解析到 app/runtime/checkpoints.sqlite（相对模块文件，不依赖 CWD）。"""
        monkeypatch.delenv(cp.CHECKPOINT_DB_ENV, raising=False)
        assert cp.get_checkpoint_db_path() == str(cp.DEFAULT_CHECKPOINT_DB_PATH)
        expected = Path(cp.__file__).resolve().parent / "checkpoints.sqlite"
        assert cp.get_checkpoint_db_path() == str(expected)

    def test_env_override_path_resolution(self, monkeypatch, r2_tmp):
        """AGENT_CHECKPOINT_DB 覆盖默认路径。"""
        target = r2_tmp / "custom" / "cp.sqlite"
        _point_env(monkeypatch, target)
        assert cp.get_checkpoint_db_path() == str(target.resolve())

    def test_default_used_when_env_unset_creates_db_with_tables(self, monkeypatch, r2_tmp):
        """env 未配置 → 用默认路径（monkeypatch 到临时目录避免污染仓库）→ 建库建表。"""
        monkeypatch.delenv(cp.CHECKPOINT_DB_ENV, raising=False)
        target = r2_tmp / "nested" / "dirs" / "checkpoints.sqlite"
        monkeypatch.setattr(cp, "DEFAULT_CHECKPOINT_DB_PATH", target)
        saver = cp.get_sqlite_checkpointer()
        assert saver is not None
        assert target.exists(), "DB 文件应被创建"
        with sqlite3.connect(target) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        assert {"checkpoints", "writes"} <= tables, f"实际表: {tables}"

    def test_env_override_creates_db_and_parent_dirs(self, monkeypatch, r2_tmp):
        """env 覆盖 + 父目录自动创建 + DB 文件真实落盘。"""
        db_path = r2_tmp / "a" / "b" / "c" / "custom.sqlite"
        _point_env(monkeypatch, db_path)
        saver = cp.get_sqlite_checkpointer()
        assert saver is not None
        assert db_path.exists()
        assert db_path.parent.exists()

    def test_singleton_same_path_reused(self, monkeypatch, r2_tmp):
        """同一进程内同一路径复用同一 SqliteSaver 实例（单例）。"""
        db_path = r2_tmp / "one.sqlite"
        _point_env(monkeypatch, db_path)
        first = cp.get_sqlite_checkpointer()
        second = cp.get_sqlite_checkpointer()
        assert first is second

    def test_fail_fast_when_db_parent_is_a_file(self, monkeypatch, r2_tmp):
        """DB 父路径不可建目录 → 抛 RuntimeError（fail-fast），不静默退化内存。"""
        blocker = r2_tmp / "not_a_dir"
        blocker.write_text("i am a file, not a directory", encoding="utf-8")
        _point_env(monkeypatch, blocker / "db.sqlite")
        with pytest.raises(RuntimeError, match="无法初始化|AGENT_CHECKPOINT_DB"):
            cp.get_sqlite_checkpointer()

    def test_fail_fast_when_db_path_is_a_directory(self, monkeypatch, r2_tmp):
        """DB 路径本身是目录（sqlite 无法打开）→ 抛 RuntimeError。"""
        db_dir = r2_tmp / "adir.sqlite"
        db_dir.mkdir()
        _point_env(monkeypatch, db_dir)
        with pytest.raises(RuntimeError, match="无法初始化|AGENT_CHECKPOINT_DB"):
            cp.get_sqlite_checkpointer()


# ---------------------------------------------------------------------------
# 真实 restart recovery（Spec §9.1 / AC-2 ~ AC-4）
# ---------------------------------------------------------------------------


class TestRestartRecovery:
    def test_restart_recovery_real_subprocesses(self, r2_tmp):
        db = r2_tmp / "checkpoints.sqlite"
        runner = r2_tmp / "recovery_runner.py"
        runner.write_text(RESTART_RUNNER, encoding="utf-8")

        env = dict(os.environ)
        env["AGENT_CHECKPOINT_DB"] = str(db)
        env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")

        # AC-2：进程 A 执行 step1 后中断，checkpoint 落盘 SQLite，随后正常退出
        out_a = _run_phase(runner, env, thread="A", phase="write")
        assert out_a["log"] == ["step1 ran"]
        assert out_a["counter"] == 1
        assert out_a["marker_a"], "进程 A 的 step1 应写入随机 marker_a"

        # 落盘证据：DB 文件存在 + checkpoints 表存在 thread_id=A 记录
        assert db.exists(), "SQLite checkpoint 文件应已落盘"
        with sqlite3.connect(db) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert "checkpoints" in tables
            (count,) = conn.execute(
                "SELECT count(*) FROM checkpoints WHERE thread_id = ?", ("A",)
            ).fetchone()
        assert count >= 1, "checkpoints 表应有 thread_id=A 的 checkpoint 记录"

        # AC-3：全新进程 B（新 Checkpointer 实例，同一 DB）找回之前状态（get_state 非空）
        out_read = _run_phase(runner, env, thread="A", phase="read")
        assert out_read["values"]["counter"] == 1
        assert out_read["values"]["log"] == ["step1 ran"]
        assert out_read["values"]["marker_a"] == out_a["marker_a"]
        assert "step2" in out_read["next"], "恢复的 checkpoint 应停在 step2 之前（中间态）"

        # AC-4：进程 B 从 checkpoint 续跑（step2 → END），且未重跑 step1
        out_b = _run_phase(runner, env, thread="A", phase="resume")
        assert out_b["counter"] == 1, "step1 不应被重跑（重跑则 counter==2）"
        assert out_b["log"] == [
            "step1 ran",
            "step2 ran resumed=resumed-by-B",
        ], "step1 只执行一次，step2 在进程 B 续跑"
        assert out_b["marker_a"] == out_a["marker_a"], (
            "随机 marker_a 跨进程存活 ⇒ 是从 checkpoint resume，而非从 START 重跑"
        )
        assert out_b["marker_b"] == "resumed-by-B", "最终状态包含进程 B 写入的结果"


# ---------------------------------------------------------------------------
# main_agent 生产路径同构验证（进程内、真实 LangGraph 异步机制，非 mock）
# ---------------------------------------------------------------------------


class _AsyncSt(TypedDict, total=False):
    log: list
    counter: int
    done: str


class TestAsyncAstreamCompatibility:
    def test_factory_saver_serves_astream_with_interrupt_resume(self, monkeypatch, r2_tmp):
        """factory 返回的 checkpointer 必须能支撑 main_agent 的 astream 用法。

        背景（真实探测）：官方同步 SqliteSaver 的 a* 方法（aget_tuple/aput/aput_writes）
        一律抛 NotImplementedError，无法用于 astream；InMemorySaver 能用是因为实现了
        异步方法。R2 因此让 factory 返回 AsyncBridgeSqliteSaver（SqliteSaver 子类），
        本测试锁定该行为：async astream 全流程 + interrupt 后 async resume 均落盘 SQLite。
        """
        db = r2_tmp / "async.sqlite"
        _point_env(monkeypatch, db)
        saver = cp.get_sqlite_checkpointer()
        assert isinstance(saver, SqliteSaver), "factory 返回值必须是 SqliteSaver 子类"
        assert type(saver).__name__ == "AsyncBridgeSqliteSaver"

        def step1(s):
            return {"log": s.get("log", []) + ["step1"], "counter": s.get("counter", 0) + 1}

        def step2(s):
            resumed = interrupt("resume-please")
            return {"log": s["log"] + [f"step2:{resumed}"], "done": str(resumed)}

        graph = (
            StateGraph(_AsyncSt)
            .add_node("step1", step1)
            .add_node("step2", step2)
            .add_edge(START, "step1")
            .add_edge("step1", "step2")
            .add_edge("step2", END)
            .compile(checkpointer=saver)
        )

        async def scenario():
            cfg = {"configurable": {"thread_id": "async-thread"}}
            async for _chunk in graph.astream({"log": [], "counter": 0}, cfg):
                pass
            # interrupt 在 step2 处触发：stream 结束且状态停留在 step2 之前
            snap = graph.get_state(cfg)
            assert snap.values["counter"] == 1
            assert "step2" in snap.next
            return await graph.ainvoke(Command(resume="resumed"), cfg)

        final = asyncio.run(scenario())
        assert final["counter"] == 1, "async resume 不应重跑 step1"
        assert final["log"] == ["step1", "step2:resumed"]
        assert final["done"] == "resumed"

        # 落盘证据：该 thread 的 checkpoint 记录真实存在
        with sqlite3.connect(db) as conn:
            (count,) = conn.execute(
                "SELECT count(*) FROM checkpoints WHERE thread_id = ?",
                ("async-thread",),
            ).fetchone()
        assert count >= 1
