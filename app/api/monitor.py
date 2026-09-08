"""
Agent 执行过程监控模块（R3：Agent Runtime Event Model）

负责把工具调用、子智能体调用、任务生命周期等事件统一包装成 **Agent Runtime Event**
（带 event_id / run_id / thread_id / seq 的统一信封）后推送给前端：

- 在 Web 服务中通过 WebSocket 按 thread_id 定向推送（每 thread 串行发送队列，保证
  enqueue 顺序 == WS send 顺序）；
- 在脚本调试场景中保留控制台输出；
- 可观测性失败 fail-open：WS/投递异常绝不反向破坏 Agent 主执行链路。

R3 事件信封（additive，前端既有字段 type/event/message/data/timestamp 语义不变）：
    type        "monitor_event"（稳定）
    event       事件类型（稳定取值见 report_* / RunTerminalGuard.TERMINALS）
    event_id    事件唯一身份（uuid4 hex）
    run_id      一次 run_deep_agent 执行的身份（无 run 上下文时为 ""）
    thread_id   会话/连接身份（无上下文时为 ""；仍是 WS 路由 key）
    seq         进程内全局单调递增（emit 时锁内分配；排序以 seq 为准，timestamp 不参与）
    timestamp   naive ISO（保持现状，前端 new Date 兼容）
    message     面向展示的事件说明
    data        事件私有结构化字段

排序语义：seq 是进程内 event allocation order 的全序，不是物理时间戳，不代表绝对因果时间；
同一 thread 的事件经 per-thread asyncio.Queue + 单消费者串行发送 → WS 到达顺序 == enqueue 顺序。
"""

import asyncio
import builtins
import datetime
import threading
import uuid
from contextvars import ContextVar, Token
from typing import Any, Optional

from fastapi import WebSocket

from app.api.context import get_thread_context

# ---------------------------------------------------------------------------
# run 级上下文（R3）：ContextVar 由本模块托管（本轮不改 app/api/context.py）
# thread_id 仍在 context.py；run_id 在一次 run_deep_agent 执行开始时 set，
# 结束时 reset。executor 线程内 emit 依赖 langchain 提交时复制调用方上下文
# （已实测）→ 工具内事件同样能快照到 run_id。
# ---------------------------------------------------------------------------
_run_id_ctx: ContextVar[Optional[str]] = ContextVar("run_id", default=None)


def set_run_context(run_id: str) -> Token[Optional[str]]:
    """设置当前执行链路的 run_id（一次 run_deep_agent = 一个 run_id）。"""
    return _run_id_ctx.set(run_id)


def get_run_context() -> Optional[str]:
    """读取当前执行链路的 run_id；未设置返回 None。"""
    return _run_id_ctx.get()


def reset_run_context(token: Token[Optional[str]]) -> None:
    """恢复 run_id 上下文，避免跨 run 泄漏。"""
    _run_id_ctx.reset(token)


# Batch 3（C）：task_id 作 additive correlation 字段 —— 来源为 governance execution context；
# bare（非 governed）执行恒为 None。不改变 thread_id/run_id/monitor_seq。
_task_id_ctx: ContextVar[Optional[str]] = ContextVar("task_id", default=None)


def set_task_context(task_id: Optional[str]) -> Token[Optional[str]]:
    """设置当前 execution 的 governance task_id（governed 时来自 GovernanceExecution.task_id）。"""
    return _task_id_ctx.set(task_id)


def get_task_context() -> Optional[str]:
    return _task_id_ctx.get()


def reset_task_context(token: Token[Optional[str]]) -> None:
    _task_id_ctx.reset(token)


# ---------------------------------------------------------------------------
# run 级终态一次性守卫（纯逻辑，可单测）
# ---------------------------------------------------------------------------
class RunTerminalGuard:
    """保证同一 run 内终态事件（task_result / task_cancelled / error）至多触发一次。

    使用方（run_deep_agent）在需要发终态事件前调用 may_emit(event_type)：
    返回 True 表示本次允许发出并已占用终态；返回 False 表示终态已发出或该事件
    不是终态（不产生副作用）。
    """

    TERMINALS = frozenset({"task_result", "task_cancelled", "error"})

    def __init__(self) -> None:
        self._terminal_emitted = False

    def may_emit(self, event_type: str) -> bool:
        if event_type not in self.TERMINALS:
            return False
        if self._terminal_emitted:
            return False
        self._terminal_emitted = True
        return True

    @property
    def emitted(self) -> bool:
        """是否已触发终态。"""
        return self._terminal_emitted


class ToolMonitor:
    """
    工具和助手调用的统一监控入口

    业务工具只需要导入全局 monitor，并调用 report_tool/report_assistant 等方法
    具体是通过 WebSocket 推送，还是输出到脚本运行时，由本类内部统一处理
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ToolMonitor, cls).__new__(cls)
            cls._instance.websocket_manager = None
            # seq 为进程内全局单调递增的 event allocation 全序
            cls._instance._seq = 0
            cls._instance._seq_lock = threading.Lock()
        return cls._instance

    def set_websocket_manager(self, manager: "ConnectionManager") -> None:
        """绑定 FastAPI WebSocket 连接管理器"""
        self.websocket_manager = manager

    def _emit(
        self,
        event_type: str,
        message: str,
        data: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        构造统一监控事件（R3 envelope），并尝试投递到当前 thread_id 对应的前端连接

        :param event_type: 事件类型（见 report_* 与 RunTerminalGuard.TERMINALS）
        :param message: 面向前端展示的事件说明
        :param data: 附加结构化数据
        :return: 构造出的 payload（供测试/调用方检查）
        """
        thread_id = get_thread_context()
        run_id = get_run_context()
        with self._seq_lock:
            self._seq += 1
            seq = self._seq

        payload = {
            "type": "monitor_event",
            "event": event_type,
            "event_id": uuid.uuid4().hex,
            "run_id": run_id or "",
            "thread_id": thread_id or "",
            "task_id": _task_id_ctx.get(),  # additive：governed 时=TaskRecord.task_id，bare=None
            "seq": seq,
            "message": message,
            "data": data or {},
            "timestamp": datetime.datetime.now().isoformat(),
        }

        # WS 定向投递：per-thread 串行队列（enqueue 顺序 == send 顺序）。
        # 任何投递异常都只影响可观测性（fail-open），绝不反向破坏 Agent 执行。
        if self.websocket_manager and thread_id:
            try:
                self.websocket_manager.enqueue(payload, thread_id)
            except Exception as e:
                print(f"[Monitor] WebSocket enqueue failed: {e}")

        # DeepAgents 脚本调试时，如果运行时暴露了 stream_writer，也同步写入流式输出
        if hasattr(builtins, "runtime") and hasattr(builtins.runtime, "stream_writer"):
            try:
                builtins.runtime.stream_writer(payload)
            except Exception:
                pass

        # 控制台保底输出，便于无前端场景下观察执行过程
        print(f"\n[Monitor:{event_type}] {message}")

        return payload

    def report_tool(
        self,
        tool_name: str,
        args: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """报告开始执行某个工具（R3 只保证 tool_start 的可关联/有序；
        tool completed/failed 属 R3.1，不伪造）。"""
        return self._emit(
            "tool_start",
            f"开始执行工具: {tool_name}",
            {"tool_name": tool_name, "args": args},
        )

    def report_assistant(
        self,
        assistant_name: str,
        args: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """报告正在调用某个子智能体"""
        return self._emit(
            "assistant_call",
            f"正在调用助手: {assistant_name}",
            {"assistant_name": assistant_name, "args": args},
        )

    def report_task_started(self, query: str) -> dict[str, Any]:
        """报告任务开始（R3 新增：task 生命周期起点）"""
        return self._emit("task_started", "任务开始执行", {"query": query})

    def report_task_result(self, result: str) -> dict[str, Any]:
        """报告任务最终结果（run 级一次性终态，配合 RunTerminalGuard 使用）"""
        return self._emit("task_result", "任务执行完成", {"result": result})

    def report_task_cancelled(self) -> dict[str, Any]:
        """报告任务已被用户取消（终态）"""
        return self._emit("task_cancelled", "任务已取消")

    def report_error(
        self, message: str, data: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        """报告 run 级错误（终态；公共入口，取代对私有 _emit 的直调）"""
        payload_data = dict(data or {})
        return self._emit("error", message, payload_data)

    def report_session_dir(self, path: str) -> dict[str, Any]:
        """报告当前任务工作目录"""
        return self._emit("session_created", f"工作目录已创建: {path}", {"path": path})


monitor = ToolMonitor()


class ConnectionManager:
    """
    WebSocket 连接管理器（R3：per-thread 串行发送队列）

    active_connections 使用 thread_id 作为 key，保证监控事件只推送给对应任务的前端连接。
    每个已连接 thread 配一个 asyncio.Queue + 单消费者 task：monitor.enqueue 只入队
    （同 loop put_nowait / 跨线程 call_soon_threadsafe），消费者按队列顺序串行 send_json
    → 同一 thread 的 enqueue 顺序 == WS send 顺序；不同 thread 队列互相独立、不阻塞。
    """

    def __init__(self) -> None:
        self.active_connections: dict[str, WebSocket] = {}
        # WebSocket 发送必须回到创建连接的事件循环，因此启动时需要显式绑定 loop
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        # per-thread 串行发送队列与消费者 task
        self._queues: dict[str, "asyncio.Queue[dict[str, Any]]"] = {}
        self._consumers: dict[str, "asyncio.Task[Any]"] = {}

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """绑定 FastAPI 主事件循环，并同步注册到 monitor"""
        self.loop = loop
        monitor.set_websocket_manager(self)
        print(f"[Monitor] ConnectionManager manually bound to loop: {id(self.loop)}")

    async def connect(self, websocket: WebSocket, thread_id: str) -> None:
        """接受 WebSocket 连接，并按 thread_id 保存 + 启动该 thread 的发送消费者"""
        await websocket.accept()
        # 同 thread 重连：先清掉旧连接状态（旧消费者取消、旧队列丢弃），last-connect-wins
        self._drop_thread_state(thread_id)
        self.active_connections[thread_id] = websocket
        queue: "asyncio.Queue[dict[str, Any]]" = asyncio.Queue()
        self._queues[thread_id] = queue
        self._consumers[thread_id] = asyncio.create_task(
            self._consumer(thread_id, queue)
        )
        print(f"Client connected: {thread_id}")

    async def _consumer(
        self,
        thread_id: str,
        queue: "asyncio.Queue[dict[str, Any]]",
    ) -> None:
        """单消费者：按 enqueue 顺序串行 send_json；send 失败 fail-open（不抛给 Agent）。"""
        try:
            while True:
                payload = await queue.get()
                try:
                    websocket = self.active_connections.get(thread_id)
                    if websocket is None:
                        # 连接已消失（正常断开流程会 cancel 本消费者；此处兜底丢弃）
                        continue
                    await websocket.send_json(payload)
                except Exception as e:
                    # WS 发送失败只影响可观测性，不反向破坏 Agent 执行
                    print(f"[Monitor] WebSocket send failed (thread={thread_id}): {e}")
                finally:
                    queue.task_done()
        except asyncio.CancelledError:
            raise

    def enqueue(self, payload: dict[str, Any], thread_id: str) -> None:
        """把事件投递到 thread 的发送队列（线程安全）。

        同 loop：put_nowait；跨线程（工具 executor 等）：call_soon_threadsafe 让
        put_nowait 回到 manager loop 执行 → asyncio.Queue 不会被非 loop 线程直接触碰。
        队列不存在（该 thread 未连接/已断开）→ 丢弃（事件本就无接收方，不阻断）。
        """
        queue = self._queues.get(thread_id)
        if queue is None:
            return
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        try:
            if current_loop is not None and current_loop is self.loop:
                queue.put_nowait(payload)
            elif self.loop is not None:
                # 从任意线程安全入队：call_soon_threadsafe 是跨线程投递到 loop 的标准方式
                self.loop.call_soon_threadsafe(queue.put_nowait, payload)
            else:
                # manager 尚未绑定 loop（服务未启动完成）→ 无可投递目标，丢弃
                pass
        except Exception as e:
            print(f"[Monitor] enqueue failed (thread={thread_id}): {e}")

    def _drop_thread_state(self, thread_id: str) -> None:
        """清理 thread 的连接状态：取消消费者、丢弃队列与连接（不关闭 ws 本身）。"""
        consumer = self._consumers.pop(thread_id, None)
        if consumer is not None and not consumer.done():
            consumer.cancel()
        self._queues.pop(thread_id, None)
        self.active_connections.pop(thread_id, None)

    def disconnect(self, websocket: WebSocket, thread_id: str) -> None:
        """移除已经断开的 WebSocket 连接（仅当仍是最新连接时清理，避免误删重连后的新连接）"""
        if self.active_connections.get(thread_id) is websocket:
            self._drop_thread_state(thread_id)
            print(f"Client disconnected: {thread_id}")
        else:
            print(f"Stale websocket disconnected, current connection kept: {thread_id}")

    async def send_personal_message(self, message: str, websocket: WebSocket) -> None:
        """向指定 WebSocket 发送纯文本消息"""
        await websocket.send_text(message)


manager = ConnectionManager()
