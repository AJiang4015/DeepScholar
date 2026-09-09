import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  cancelSessionTask,
  createSessionTask,
  getTask,
  listSessionFiles,
  uploadSessionFiles
} from "../lib/api";
import { WS_BASE_URL } from "../lib/config";
import {
  getStoredSessionPath,
  rememberSessionPath
} from "../lib/thread";
import type {
  ConnectionState,
  LatestTaskSummary,
  MonitorMessage,
  OutputFile,
  SocketMessage,
  TaskInfo,
  UploadedItem
} from "../types";
import { isTerminalTaskStatus } from "../types";

const MAX_EVENTS = 120;

// live 面无法区分的终态种类（真实语义只在 durable 面）：
// 出现这些终态时清除 live 的“任务已取消”等占位文案，改由 taskStatus 呈现真实语义
const OVERRIDE_RESULT_KINDS = new Set([
  "timed_out",
  "budget_exceeded",
  "aborted",
  "superseded",
  "orphan_reclaimed",
]);

function extractString(data: Record<string, unknown>, key: string): string | null {
  const value = data[key];
  return typeof value === "string" ? value : null;
}

/**
 * Session-scoped DeepAgent 会话 hook（Multi-Session）。
 *
 * 每个 SessionWorkspace 以 `key={sessionId}` 挂载本 hook 的一个实例：
 *  - WebSocket 绑定 `/ws/{sessionId}`（session_id == thread_id）
 *  - task 创建 / 取消 / 上传 / 文件全部 session-scoped
 *  - 组件卸载（Session 切换）时关闭 socket、清理 timer/polling/reconcile，
 *    从根上保证 Session A 的 runtime state 不泄漏到 Session B
 */
export function useDeepAgentSession(sessionId: string) {
  const socketRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<number | undefined>(undefined);
  const heartbeatTimerRef = useRef<number | undefined>(undefined);
  // durable reconcile 的重试定时器必须随卸载清理（Session 切换后不得继续轮询旧 task）
  const reconcileTimerRef = useRef<number | undefined>(undefined);
  const uploadedNameSetRef = useRef<Set<string>>(new Set());
  // 当前 governed 任务身份与终态追踪（供重连/恢复后校正，避免“研搜中”卡死 / 误判取消）
  const taskIdRef = useRef<string | null>(null);
  const runIdRef = useRef<string | null>(null);
  const isRunningRef = useRef(false);
  // 是否已见到本次 run 的终态（live 事件 / 主动取消 / durable 校正任一发生即置位）
  const terminalLiveRef = useRef(false);
  const taskStatusAppliedRef = useRef(false);
  const reconcileAttemptsRef = useRef(0);
  const prevConnectionRef = useRef<ConnectionState>("connecting");

  const [connectionState, setConnectionState] = useState<ConnectionState>("connecting");
  const [events, setEvents] = useState<MonitorMessage[]>([]);
  const [files, setFiles] = useState<OutputFile[]>([]);
  // 会话输出目录：优先恢复本 session 已缓存路径（历史产物可见性），live session_created 时刷新
  const [sessionPath, setSessionPath] = useState<string>(
    () => getStoredSessionPath(sessionId) ?? ""
  );
  const [result, setResult] = useState("");
  const [lastError, setLastError] = useState("");
  const [lastPongAt, setLastPongAt] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadedItems, setUploadedItems] = useState<UploadedItem[]>([]);
  const [taskId, setTaskId] = useState<string | null>(null);
  // 治理任务终态记录（GET /api/tasks/{task_id}）；断线窗口内由 durable 校正时写入
  const [taskStatus, setTaskStatus] = useState<TaskInfo | null>(null);

  const clearSocketTimers = useCallback(() => {
    if (reconnectTimerRef.current) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = undefined;
    }
    if (heartbeatTimerRef.current) {
      window.clearInterval(heartbeatTimerRef.current);
      heartbeatTimerRef.current = undefined;
    }
    if (reconcileTimerRef.current) {
      window.clearTimeout(reconcileTimerRef.current);
      reconcileTimerRef.current = undefined;
    }
  }, []);

  const refreshFiles = useCallback(async () => {
    if (!sessionPath) {
      return;
    }

    const response = await listSessionFiles(sessionPath);
    if (response.error) {
      // 目录不存在（历史会话输出目录被清理 / 尚未产生 session_created）：优雅降级为空文件
      if (response.error.includes("目录不存在")) {
        setFiles([]);
        return;
      }
      throw new Error(response.error);
    }
    setFiles(response.files || []);
  }, [sessionPath]);

  /** 把 durable TaskInfo 收敛到 UI（共用：reconcile 校正 + 刷新后恢复共用同一语义）。 */
  const applyDurableInfo = useCallback((info: TaskInfo) => {
    taskStatusAppliedRef.current = true;
    terminalLiveRef.current = true;
    reconcileAttemptsRef.current = 0;
    if (OVERRIDE_RESULT_KINDS.has(info.status)) {
      setResult("");
    }
    if (info.status === "failed") {
      setLastError((previous) => previous || info.error || "任务执行失败");
    }
    setIsRunning(false);
    setIsCancelling(false);
    setTaskStatus(info);
  }, []);

  // durable 终态校正（幂等）：以治理记录为准补正 UI 语义。
  // - 任务仍在运行 → 按 allowRetry 决定是否继续限次重试（live 终态晚于 governance 写盘属正常时序）；
  // - 终态 → 统一走 applyDurableInfo（清除占位文案 / failed 走 Alert / 停止运行态）。
  const doReconcile = useCallback(
    async (options?: { allowRetry?: boolean }) => {
      const allowRetry = options?.allowRetry ?? true;
      const currentTaskId = taskIdRef.current;
      if (!currentTaskId || taskStatusAppliedRef.current) {
        return;
      }
      try {
        const info = await getTask(currentTaskId);
        if (!info || !isTerminalTaskStatus(info.status)) {
          if (allowRetry && reconcileAttemptsRef.current < 20) {
            reconcileAttemptsRef.current += 1;
            reconcileTimerRef.current = window.setTimeout(() => {
              reconcileTimerRef.current = undefined;
              if (!taskStatusAppliedRef.current && taskIdRef.current) {
                void doReconcile({ allowRetry: true });
              }
            }, 250);
          }
          return;
        }
        applyDurableInfo(info);
      } catch {
        // 网络异常 / 任务不存在：保留现状，静默
      }
    },
    [applyDurableInfo]
  );

  useEffect(() => {
    let disposed = false;

    function connect() {
      clearSocketTimers();
      const hadSocket = Boolean(socketRef.current);
      socketRef.current?.close();
      setConnectionState(hadSocket ? "reconnecting" : "connecting");

      const socket = new WebSocket(`${WS_BASE_URL}/ws/${encodeURIComponent(sessionId)}`);
      socketRef.current = socket;

      socket.onopen = () => {
        if (disposed) {
          return;
        }
        setConnectionState("connected");
        setLastError("");
        heartbeatTimerRef.current = window.setInterval(() => {
          if (socket.readyState === WebSocket.OPEN) {
            socket.send("ping");
          }
        }, 25000);
      };

      socket.onmessage = (event) => {
        if (socketRef.current !== socket) {
          return;
        }
        try {
          const payload = JSON.parse(event.data) as SocketMessage;
          if (payload.type === "pong") {
            setLastPongAt(new Date().toISOString());
            return;
          }

          if (payload.type !== "monitor_event") {
            // governance_replay / governance_error 等帧：本页面用 REST 校正终态，不消费
            return;
          }

          // 双保险：信封携带 thread_id 时，只接受属于本 session 的事件
          if (payload.thread_id && payload.thread_id !== sessionId) {
            return;
          }

          // 同 session（thread）可能先后存在多个 run：丢弃迟到旧 run 的事件。
          // monitor 信封 run_id（main_agent 本地生成）≠ governance TaskRecord.run_id，
          // 因此以事件流自身的 task_started.run_id 为「本 run 锚点」（每次新 run 自愈重建）。
          const payloadRunId =
            typeof payload.run_id === "string" ? payload.run_id : "";
          if (payload.event === "task_started" && payloadRunId) {
            runIdRef.current = payloadRunId;
          }
          if (payloadRunId && runIdRef.current && payloadRunId !== runIdRef.current) {
            return;
          }

          setEvents((previous) => [...previous, payload].slice(-MAX_EVENTS));

          if (payload.event === "session_created") {
            const path = extractString(payload.data, "path");
            if (path) {
              setSessionPath(path);
              rememberSessionPath(sessionId, path);
            }
          }

          if (payload.event === "task_result") {
            const finalResult = extractString(payload.data, "result");
            setResult(finalResult || payload.message);
            setIsRunning(false);
            setIsCancelling(false);
            terminalLiveRef.current = true;
            void doReconcile();
          }

          if (payload.event === "task_cancelled") {
            setResult((previous) => previous || payload.message);
            setIsRunning(false);
            setIsCancelling(false);
            terminalLiveRef.current = true;
            void doReconcile();
          }

          if (payload.event === "error") {
            setLastError(payload.message);
            setIsRunning(false);
            setIsCancelling(false);
            terminalLiveRef.current = true;
            void doReconcile();
          }
        } catch (error) {
          setLastError(error instanceof Error ? error.message : "WebSocket 消息解析失败");
        }
      };

      socket.onerror = () => {
        if (!disposed && socketRef.current === socket) {
          setLastError("WebSocket 连接异常，请确认后端服务已启动");
        }
      };

      socket.onclose = () => {
        if (socketRef.current !== socket) {
          return;
        }
        clearSocketTimers();
        if (disposed) {
          setConnectionState("closed");
          return;
        }
        setConnectionState("reconnecting");
        reconnectTimerRef.current = window.setTimeout(connect, 2000);
      };
    }

    connect();

    return () => {
      disposed = true;
      clearSocketTimers();
      socketRef.current?.close();
    };
  }, [clearSocketTimers, doReconcile, sessionId]);

  useEffect(() => {
    isRunningRef.current = isRunning;
  }, [isRunning]);

  // 断线重连后的 durable 终态校正：WS 掉线期间 live 事件会被服务端丢弃（monitor 不持久化），
  // 若任务恰在窗口内结束，页面会一直停在“研搜中”或误判取消。
  useEffect(() => {
    const previous = prevConnectionRef.current;
    prevConnectionRef.current = connectionState;
    if (connectionState === "connected" && previous === "reconnecting") {
      void doReconcile();
    }
  }, [connectionState, doReconcile]);

  useEffect(() => {
    if (!sessionPath) {
      return;
    }

    refreshFiles().catch((error: unknown) => {
      setLastError(error instanceof Error ? error.message : "文件列表刷新失败");
    });

    const timer = window.setInterval(() => {
      refreshFiles().catch((error: unknown) => {
        setLastError(error instanceof Error ? error.message : "文件列表刷新失败");
      });
    }, isRunning ? 2500 : 6000);

    return () => window.clearInterval(timer);
  }, [isRunning, refreshFiles, sessionPath]);

  const submitTask = useCallback(
    async (query: string) => {
      const cleanQuery = query.trim();
      if (!cleanQuery) {
        throw new Error("请输入研搜任务");
      }

      setIsRunning(true);
      setIsCancelling(false);
      setEvents([]);
      setResult("");
      setLastError("");
      setTaskStatus(null);
      taskStatusAppliedRef.current = false;
      terminalLiveRef.current = false;
      reconcileAttemptsRef.current = 0;
      // 新 run 开始：清除旧 run 锚点，等新 run 的 task_started 事件重建锚点
      runIdRef.current = null;
      try {
        const response = await createSessionTask(sessionId, cleanQuery);
        // governed 任务身份：task_id 用于终态校正查询与 session-scoped cancel
        taskIdRef.current = response.task_id ?? null;
        setTaskId(response.task_id ?? null);
        return response;
      } catch (error) {
        setIsRunning(false);
        setIsCancelling(false);
        throw error;
      }
    },
    [sessionId]
  );

  /**
   * 刷新 / 重挂载后的 durable 恢复：session detail 给出最新 task 时采纳其真实状态。
   * - 仍 running：恢复运行态（不伪造 live run 锚点），随后等待 WS live 事件与终态收敛；
   * - 已终态：直接以 durable TaskInfo 收敛（断线窗口内完成/失败/取消等不再卡“研搜中”）。
   */
  const adoptDurableTask = useCallback(
    async (task: LatestTaskSummary) => {
      if (!task?.task_id) {
        return;
      }
      taskIdRef.current = task.task_id;
      setTaskId(task.task_id);
      runIdRef.current = null; // 不伪造 live run 锚点；等待真实 task_started 事件
      taskStatusAppliedRef.current = false;
      terminalLiveRef.current = false;
      reconcileAttemptsRef.current = 0;
      setLastError("");

      if (task.status === "running") {
        setIsRunning(true);
        setIsCancelling(false);
        try {
          const info = await getTask(task.task_id);
          if (info && isTerminalTaskStatus(info.status)) {
            applyDurableInfo(info); // 查询瞬间已终态（detail 与 durable 竞态）
          }
        } catch {
          // 保持 running，等 live / 后续 reconcile
        }
        return;
      }

      // durable 已终态：停止运行态并以真实语义收敛
      setIsRunning(false);
      setIsCancelling(false);
      try {
        const info = await getTask(task.task_id);
        if (info) {
          applyDurableInfo(info);
        }
      } catch {
        // 网络异常：至少不进入“研搜中”假态
        setTaskStatus({
          task_id: task.task_id,
          thread_id: sessionId,
          status: task.status,
          terminal_reason: task.terminal_reason ?? null
        });
      }
    },
    [applyDurableInfo, sessionId]
  );

  /** 会话级错误上报（如历史加载失败）：复用既有顶部 Alert 通道。 */
  const reportError = useCallback((messageValue: string) => {
    if (messageValue) {
      setLastError(messageValue);
    }
  }, []);

  const cancelCurrentTask = useCallback(async () => {
    if (!isRunning) {
      throw new Error("当前没有正在执行的任务");
    }
    const currentTaskId = taskIdRef.current;
    if (!currentTaskId) {
      throw new Error("任务标识尚未就绪，请稍候再取消");
    }

    setIsCancelling(true);
    setLastError("");
    try {
      const response = await cancelSessionTask(sessionId, currentTaskId);
      if (response.status === "cancelled") {
        setIsRunning(false);
        setIsCancelling(false);
        setResult((previous) => previous || "任务已取消");
        terminalLiveRef.current = true;
        void doReconcile();
      }
      return response;
    } catch (error) {
      setIsCancelling(false);
      throw error;
    }
  }, [doReconcile, isRunning, sessionId]);

  const uploadFiles = useCallback(
    async (items: UploadedItem[]) => {
      if (items.length === 0) {
        throw new Error("请选择要上传的文件");
      }

      const nextItems = items.filter((item) => !uploadedNameSetRef.current.has(item.name));

      if (nextItems.length === 0) {
        return {
          status: "uploaded",
          files: Array.from(uploadedNameSetRef.current)
        };
      }

      setIsUploading(true);
      setLastError("");
      try {
        const response = await uploadSessionFiles(
          nextItems.map((item) => item.raw),
          sessionId
        );
        setUploadedItems((previous) => {
          const names = new Set(previous.map((item) => item.name));
          const next = [...previous];
          nextItems.forEach((item) => {
            if (!names.has(item.name)) {
              names.add(item.name);
              uploadedNameSetRef.current.add(item.name);
              next.push(item);
            }
          });
          return next;
        });
        return response;
      } finally {
        setIsUploading(false);
      }
    },
    [sessionId]
  );

  const stats = useMemo(() => {
    const toolEvents = events.filter((event) => event.event === "tool_start").length;
    const assistantEvents = events.filter((event) => event.event === "assistant_call").length;
    const errorEvents = events.filter((event) => event.event === "error").length;

    return {
      toolEvents,
      assistantEvents,
      errorEvents,
      fileCount: files.length
    };
  }, [events, files.length]);

  return {
    threadId: sessionId,
    sessionId,
    connectionState,
    events,
    files,
    isCancelling,
    isRunning,
    isUploading,
    lastError,
    lastPongAt,
    refreshFiles,
    reportError,
    result,
    sessionPath,
    stats,
    taskId,
    taskStatus,
    adoptDurableTask,
    cancelCurrentTask,
    submitTask,
    uploadFiles,
    uploadedItems
  };
}
