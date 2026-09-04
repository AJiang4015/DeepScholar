"""
R3 测试：Agent Runtime Event Model（envelope / correlation / ordering / async-safe delivery）

覆盖（Spec §10 / 用户裁决清单）：
1. envelope 字段完整性        2. event_id 唯一        3. seq 单调/唯一
4. thread_id 显式进 payload   5. run_id 显式进 payload  6. 不同 thread/run 隔离
7. 跨线程 emit（seq 全序唯一） 8. per-thread queue FIFO  9. task_started
10. terminal exactly-once（RunTerminalGuard）  11. cancel terminal  12. error terminal
13. 真实 sync @tool → tool.ainvoke → executor 线程 → 正确 thread_id（AC-6）
14~16. 生命周期顺序 / 断开与重连生命周期 / 双线程互不阻塞

说明：
- main_agent 级全链路（真实 Agent + LLM）无法在本环境 import（deepagents 缺失），
  其 terminal exactly-once 控制逻辑已下沉为可单测的 RunTerminalGuard + 事件序列断言；
- 运行方式同仓库约定：`python -m pytest tests/ -q`（langgraph-checkpoint-sqlite 无关）。
"""

import asyncio
import builtins
import contextvars
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.api.context import set_thread_context
from app.api.monitor import (
    ConnectionManager,
    RunTerminalGuard,
    get_run_context,
    monitor,
    set_run_context,
)

try:  # langchain_core 工具路径测试所需（缺省跳过，符合 TESTING.md §1）
    from langchain_core.tools import tool as lg_tool

    HAS_LANGCHAIN_TOOLS = True
except Exception:  # pragma: no cover - 依赖缺失
    HAS_LANGCHAIN_TOOLS = False

needs_langchain_tools = pytest.mark.skipif(
    not HAS_LANGCHAIN_TOOLS, reason="langchain_core 不可用"
)

# monitor 每个事件都会 print 到控制台，测试期屏蔽噪音（全局 builtins.print 隔离于 pytest 捕获）
@pytest.fixture(autouse=True)
def _silence_monitor_prints(monkeypatch):
    monkeypatch.setattr(builtins, "print", lambda *a, **k: None)
    # 每个测试独立起止：不残留 manager/上下文，避免测试间污染
    monitor.set_websocket_manager(None)
    yield
    monitor.set_websocket_manager(None)


# ---------------------------------------------------------------------------
# 测试工具
# ---------------------------------------------------------------------------


class CaptureManager:
    """monitor 的伪 manager：enqueue 直接记录 payload（envelope/关联测试用）。"""

    def __init__(self) -> None:
        self.loop = None
        self.records: dict[str, list[dict]] = {}

    def enqueue(self, payload: dict, thread_id: str) -> None:
        self.records.setdefault(thread_id, []).append(payload)


class FakeWebSocket:
    """真实 ConnectionManager 消费者用的伪 websocket（记录 send_json）。"""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


def _run_scope(thread_id, run_id, fn):
    """在隔离的 contextvars 上下文中设置 thread/run 后执行 fn（自动恢复，无跨测试污染）。"""
    ctx = contextvars.copy_context()

    def inner():
        set_thread_context(thread_id)
        set_run_context(run_id)
        return fn()

    return ctx.run(inner)


async def _drain_until(ws, count, timeout=5.0):
    deadline = time.monotonic() + timeout
    while len(ws.sent) < count and time.monotonic() < deadline:
        await asyncio.sleep(0.005)
    assert len(ws.sent) >= count, f"只收到 {len(ws.sent)}/{count} 条事件"


def _assert_envelope(payload: dict, expected_event: str):
    assert payload["type"] == "monitor_event"
    assert payload["event"] == expected_event
    assert isinstance(payload["event_id"], str) and payload["event_id"]
    assert isinstance(payload["thread_id"], str)
    assert isinstance(payload["run_id"], str)
    assert isinstance(payload["seq"], int) and payload["seq"] >= 1
    assert isinstance(payload["timestamp"], str) and payload["timestamp"]
    assert isinstance(payload["message"], str)
    assert isinstance(payload["data"], dict)


# ---------------------------------------------------------------------------
# 1~3. envelope 完整性 / event_id 唯一 / seq 单调
# ---------------------------------------------------------------------------


class TestEnvelope:
    def test_report_methods_produce_full_envelope(self):
        """每个 report_* 都产出稳定信封（含新增 event_id/run_id/thread_id/seq）。"""
        cases = [
            (monitor.report_tool, ("工具X", {"k": 1}), "tool_start"),
            (monitor.report_assistant, ("助手X", {"description": "d"}), "assistant_call"),
            (monitor.report_task_started, ("问题?",), "task_started"),
            (monitor.report_task_result, ("结果",), "task_result"),
            (monitor.report_task_cancelled, (), "task_cancelled"),
            (monitor.report_error, ("出错了", {"error": "boom"}), "error"),
            (monitor.report_session_dir, ("/tmp/x",), "session_created"),
        ]
        for method, args, expected_event in cases:
            payload = method(*args)
            _assert_envelope(payload, expected_event)
            # 无上下文时 thread_id/run_id 为空串（不伪造关联）
            assert payload["thread_id"] == ""
            assert payload["run_id"] == ""

    def test_event_id_unique_and_seq_monotonic(self):
        """event_id 全局唯一；seq 严格 +1 单调（进程内 allocation 全序）。"""
        payloads = [
            monitor.report_tool("t", {"i": i})
            for i in range(20)
        ]
        ids = [p["event_id"] for p in payloads]
        seqs = [p["seq"] for p in payloads]
        assert len(set(ids)) == 20, "event_id 必须唯一"
        assert seqs == sorted(seqs) and len(set(seqs)) == 20, "seq 必须唯一"
        assert all(b - a == 1 for a, b in zip(seqs, seqs[1:])), "seq 必须连续单调"


# ---------------------------------------------------------------------------
# 4~5. thread_id / run_id 显式进入 payload
# ---------------------------------------------------------------------------


class TestCorrelation:
    def test_thread_and_run_explicit_in_payload(self, monkeypatch):
        cap = CaptureManager()
        monitor.set_websocket_manager(cap)

        def emit():
            monitor.report_tool("网络搜索工具", {"query": "q"})
            monitor.report_assistant("网络搜索助手", {"description": "d"})

        _run_scope("thread-A", "run-1", emit)
        records = cap.records.get("thread-A", [])
        assert len(records) == 2
        for payload in records:
            assert payload["thread_id"] == "thread-A"
            assert payload["run_id"] == "run-1"
        assert get_run_context() is None, "scope 结束后 run context 不应泄漏"

    def test_no_run_context_yields_empty_run_id(self):
        def emit():
            return monitor.report_task_started("q")

        payload = _run_scope("thread-A", None, emit)
        assert payload["thread_id"] == "thread-A"
        assert payload["run_id"] == ""


# ---------------------------------------------------------------------------
# 6. 不同 thread/run 隔离；7. 跨线程 emit（seq 全序唯一）
# ---------------------------------------------------------------------------


class TestIsolationAndCrossThread:
    def test_concurrent_runs_do_not_cross_contaminate(self):
        """thread-A/run-A 与 thread-B/run-B 并发 emit → 事件集互不串扰。"""
        cap = CaptureManager()
        monitor.set_websocket_manager(cap)

        def worker(thread_id, run_id, n):
            def emit():
                for i in range(n):
                    monitor.report_tool(f"tool-{thread_id}", {"i": i})
                    monitor.report_task_started(f"q-{i}")

            _run_scope(thread_id, run_id, emit)

        threads = [
            threading.Thread(target=worker, args=("thread-A", "run-A", 15)),
            threading.Thread(target=worker, args=("thread-B", "run-B", 15)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        for thread_id, run_id in [("thread-A", "run-A"), ("thread-B", "run-B")]:
            records = cap.records[thread_id]
            assert len(records) == 30
            assert all(p["thread_id"] == thread_id for p in records)
            assert all(p["run_id"] == run_id for p in records)

    def test_cross_thread_emit_seq_global_unique(self):
        """多个工作线程并发 emit → seq 进程内全序唯一（锁正确性）。"""

        def worker(seed):
            def emit():
                return [monitor.report_tool("t", {"seed": seed, "i": i})["seq"] for i in range(25)]

            return _run_scope(f"thread-{seed}", f"run-{seed}", emit)

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(worker, range(4)))
        all_seqs = [s for r in results for s in r]
        assert len(all_seqs) == 100
        assert len(set(all_seqs)) == 100, "跨线程 seq 必须全局唯一"


# ---------------------------------------------------------------------------
# 9~12. 生命周期事件 + terminal exactly-once（RunTerminalGuard）
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_lifecycle_sequence_ordered(self, monkeypatch):
        """task_started → session_created → tool_start/assistant_call → 终态，
        事件序列按 seq 有序、可归属 run。"""
        cap = CaptureManager()
        monitor.set_websocket_manager(cap)

        def run():
            guard = RunTerminalGuard()
            monitor.report_task_started("问题?")
            monitor.report_session_dir("/app/output/session_x")
            monitor.report_tool("网络搜索工具", {"query": "q"})
            monitor.report_assistant("网络搜索助手", {"description": "d"})
            if guard.may_emit("task_result"):
                monitor.report_task_result("最终答案")
            # 第二次终态被守卫抑制
            assert guard.may_emit("task_result") is False
            assert guard.may_emit("error") is False

        _run_scope("thread-L", "run-L", run)
        events = [p["event"] for p in cap.records["thread-L"]]
        assert events == [
            "task_started",
            "session_created",
            "tool_start",
            "assistant_call",
            "task_result",
        ]
        seqs = [p["seq"] for p in cap.records["thread-L"]]
        assert seqs == sorted(seqs)

    def test_task_started_event_exists(self):
        payload = monitor.report_task_started("query")
        assert payload["event"] == "task_started"
        assert payload["data"]["query"] == "query"

    def test_cancel_terminal_once(self):
        """取消终态：第一次允许，随后任何终态被抑制（模拟 run 内唯一终态）。"""
        guard = RunTerminalGuard()
        assert guard.may_emit("task_cancelled") is True
        assert guard.may_emit("task_result") is False
        assert guard.may_emit("error") is False

        payload = monitor.report_task_cancelled()
        assert payload["event"] == "task_cancelled"

    def test_error_terminal_once(self):
        """错误终态：report_error 公共化、含结构化 error 字段；守卫一次性。"""
        guard = RunTerminalGuard()
        assert guard.may_emit("error") is True
        assert guard.may_emit("task_result") is False

        payload = monitor.report_error("执行异常", {"error": "boom"})
        assert payload["event"] == "error"
        assert payload["data"]["error"] == "boom"

    def test_non_terminal_event_does_not_consume_guard(self):
        """非终态事件不消耗终态配额（tool_start 之类不得占用终态）。"""
        guard = RunTerminalGuard()
        assert guard.may_emit("tool_start") is False
        assert guard.may_emit("assistant_call") is False
        assert guard.emitted is False
        assert guard.may_emit("task_result") is True


# ---------------------------------------------------------------------------
# 8/11/13. per-thread 串行发送队列：FIFO / 生命周期 / 双线程不互相阻塞
# ---------------------------------------------------------------------------


class TestPerThreadQueue:
    def _fresh_manager_in_loop(self):
        cm = ConnectionManager()
        cm.set_loop(asyncio.get_running_loop())
        return cm

    def test_fifo_single_source_enqueue_order_equals_send_order(self):
        """同 thread 单来源：enqueue 顺序 == WS send 顺序（FIFO）。"""

        async def scenario():
            cm = self._fresh_manager_in_loop()
            ws = FakeWebSocket()
            await cm.connect(ws, "T1")
            payloads = [
                {"type": "monitor_event", "event": "tool_start", "seq": i, "x": i}
                for i in range(1, 16)
            ]
            for p in payloads:
                cm.enqueue(p, "T1")
            await _drain_until(ws, 15)
            sent_seqs = [p["seq"] for p in ws.sent]
            assert sent_seqs == [p["seq"] for p in payloads]
            cm.disconnect(ws, "T1")

        asyncio.run(scenario())

    def test_worker_thread_enqueue_delivered_in_order(self):
        """sync 线程（工具 executor 同款路径）经 call_soon_threadsafe 入队 → 仍按序送达。"""

        async def scenario():
            cm = self._fresh_manager_in_loop()
            ws = FakeWebSocket()
            await cm.connect(ws, "T1")

            def worker():
                _run_scope("T1", "run-Q", lambda: [monitor.report_tool("t", {"i": i}) for i in range(10)])

            th = threading.Thread(target=worker)
            th.start()
            th.join(timeout=10)
            await _drain_until(ws, 10)
            sent = ws.sent
            assert all(p["thread_id"] == "T1" for p in sent)
            assert all(p["run_id"] == "run-Q" for p in sent)
            seqs = [p["seq"] for p in sent]
            assert seqs == sorted(seqs), "单来源 worker 事件必须按 seq 顺序送达"
            cm.disconnect(ws, "T1")

        asyncio.run(scenario())

    def test_two_threads_independent_not_blocking(self):
        """不同 thread 独立队列：并发不互相阻塞、事件不串扰。"""

        async def scenario():
            cm = self._fresh_manager_in_loop()
            ws_a, ws_b = FakeWebSocket(), FakeWebSocket()
            await cm.connect(ws_a, "TA")
            await cm.connect(ws_b, "TB")

            def worker(thread_id, run_id, n):
                _run_scope(
                    thread_id,
                    run_id,
                    lambda: [monitor.report_tool(f"tool-{thread_id}", {"i": i}) for i in range(n)],
                )

            threads = [
                threading.Thread(target=worker, args=("TA", "run-A", 12)),
                threading.Thread(target=worker, args=("TB", "run-B", 12)),
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)
            await _drain_until(ws_a, 12)
            await _drain_until(ws_b, 12)
            assert all(p["thread_id"] == "TA" for p in ws_a.sent)
            assert all(p["thread_id"] == "TB" for p in ws_b.sent)
            cm.disconnect(ws_a, "TA")
            cm.disconnect(ws_b, "TB")

        asyncio.run(scenario())

    def test_disconnect_stops_consumer_and_drops_late_events(self):
        """断开后消费者退出、队列丢弃：不再发送、不泄漏 task。"""

        async def scenario():
            cm = self._fresh_manager_in_loop()
            ws = FakeWebSocket()
            await cm.connect(ws, "T1")
            consumer = cm._consumers["T1"]
            cm.enqueue({"seq": 1}, "T1")
            await _drain_until(ws, 1)
            cm.disconnect(ws, "T1")
            assert "T1" not in cm._consumers
            assert "T1" not in cm._queues
            # 断开后的入队是 no-op（事件本就无接收方，fail-open 不抛）
            cm.enqueue({"seq": 2}, "T1")
            await asyncio.sleep(0.05)
            assert len(ws.sent) == 1
            # 消费者 task 已取消（无泄漏）
            await asyncio.sleep(0)
            assert consumer.done() and consumer.cancelled()

        asyncio.run(scenario())

    def test_reconnect_last_wins_and_stale_guard(self):
        """同 thread 重连：last-connect-wins；旧连接 consumer 取消，新连接收后续事件。"""

        async def scenario():
            cm = self._fresh_manager_in_loop()
            ws1, ws2 = FakeWebSocket(), FakeWebSocket()
            await cm.connect(ws1, "T1")
            cm.enqueue({"seq": 1}, "T1")
            await _drain_until(ws1, 1)

            await cm.connect(ws2, "T1")  # 重连：ws1 的消费者被取消
            cm.enqueue({"seq": 2}, "T1")
            cm.enqueue({"seq": 3}, "T1")
            await _drain_until(ws2, 2)
            assert [p["seq"] for p in ws2.sent] == [2, 3]
            # 旧 ws 收到的是重连前的那一条，之后不再收到
            assert [p["seq"] for p in ws1.sent] == [1]
            # 旧 ws 断开：stale guard 不误删新连接
            cm.disconnect(ws1, "T1")
            assert "T1" in cm.active_connections
            cm.disconnect(ws2, "T1")
            assert "T1" not in cm.active_connections

        asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 13. 真实 sync @tool → tool.ainvoke → executor 线程 → 正确 thread_id（AC-6）
# ---------------------------------------------------------------------------


@needs_langchain_tools
class TestSyncToolAsyncCorrelation:
    def test_sync_tool_emit_reaches_correct_thread(self):
        """真实 langchain 执行路径：async 图内 sync 工具在工作线程 emit，
        事件经 per-thread 队列回到正确 thread_id/run_id（非 mock）。"""

        async def scenario():
            cm = ConnectionManager()
            cm.set_loop(asyncio.get_running_loop())
            ws = FakeWebSocket()
            await cm.connect(ws, "thread-tool")

            set_thread_context("thread-tool")
            set_run_context("run-tool")
            # asyncio.run 自带独立 context：scenario 内 set 的 context 在 run 结束后整体丢弃，
            # 不会泄漏到进程上下文（无需手动 reset，与 run_deep_agent 的 finally reset 是两回事）

            @lg_tool
            def probe_tool(x: str) -> str:
                """R3 probe tool."""
                monitor.report_tool("probe_tool", {"x": x})
                return f"echo:{x}"

            result = await probe_tool.ainvoke({"x": "hello"})
            assert result == "echo:hello"

            await _drain_until(ws, 1)
            payload = ws.sent[0]
            assert payload["event"] == "tool_start"
            assert payload["thread_id"] == "thread-tool"
            assert payload["run_id"] == "run-tool"
            assert payload["data"]["tool_name"] == "probe_tool"
            cm.disconnect(ws, "thread-tool")

        asyncio.run(scenario())
