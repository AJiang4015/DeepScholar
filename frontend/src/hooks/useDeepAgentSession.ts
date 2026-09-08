import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  cancelTask,
  getTask,
  listSessionFiles,
  startTask,
  uploadSessionFiles
} from "../lib/api";
import { WS_BASE_URL } from "../lib/config";
import { createThreadId, getStoredThreadId, storeThreadId } from "../lib/thread";
import type {
  ConnectionState,
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

export function useDeepAgentSession() {
  const socketRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<number | undefined>(undefined);
  const heartbeatTimerRef = useRef<number | undefined>(undefined);
  const uploadedNameSetRef = useRef<Set<string>>(new Set());
  // 当前 governed 任务身份与终态追踪（供重连后校正，避免“研搜中”卡死 / 误判取消）
  const taskIdRef = useRef<string | null>(null);
  const runIdRef = useRef<string | null>(null);
  const isRunningRef = useRef(false);
  // 是否已见到本次 run 的终态（live 事件 / 主动取消 / 重连校正任一发生即置位）
  const terminalLiveRef = useRef(false);
  const taskStatusAppliedRef = useRef(false);
  const reconcileAttemptsRef = useRef(0);
  const prevConnectionRef = useRef<ConnectionState>("connecting");

  const [threadId, setThreadId] = useState(getStoredThreadId);
  const [connectionState, setConnectionState] = useState<ConnectionState>("connecting");
  const [events, setEvents] = useState<MonitorMessage[]>([]);
  const [files, setFiles] = useState<OutputFile[]>([]);
  const [sessionPath, setSessionPath] = useState("");
  const [result, setResult] = useState("");
  const [lastError, setLastError] = useState("");
  const [lastPongAt, setLastPongAt] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadedItems, setUploadedItems] = useState<UploadedItem[]>([]);
  const [taskId, setTaskId] = useState<string | null>(null);
  // 治理任务终态记录（GET /api/tasks/{task_id}）；仅“断线后由 durable 终态校正”时写入，
  // 正常 live 收尾不依赖它（live 事件已给出 result/cancelled/error）。
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
  }, []);

  const resetSession = useCallback(() => {
    const nextThreadId = createThreadId();
    storeThreadId(nextThreadId);
    setThreadId(nextThreadId);
    setEvents([]);
    setFiles([]);
    setSessionPath("");
    setResult("");
    setLastError("");
    setUploadedItems([]);
    uploadedNameSetRef.current.clear();
    setIsRunning(false);
    setIsCancelling(false);
    setTaskId(null);
    setTaskStatus(null);
    taskIdRef.current = null;
    runIdRef.current = null;
    isRunningRef.current = false;
    terminalLiveRef.current = false;
    taskStatusAppliedRef.current = false;
    reconcileAttemptsRef.current = 0;
  }, []);

  const refreshFiles = useCallback(async () => {
    if (!sessionPath) {
      return;
    }

    const response = await listSessionFiles(sessionPath);
    if (response.error) {
      throw new Error(response.error);
    }
    setFiles(response.files || []);
  }, [sessionPath]);

  // 一次性 durable 终态校正（幂等）：无论 live 是否已收尾，都以治理记录为准补正 UI 语义。
  // - 任务仍在运行 → 不做任何事（等 live / 下一次触发）；
  // - 终态为 timed_out / budget_exceeded / aborted / superseded / orphan_reclaimed → 清除 live 占位文案，
  //   让消息区显示真实结束语义（而非“已取消”）；
  // - 终态为 failed 且尚无错误提示 → 走既有 Alert 通道。
  const doReconcile = useCallback(async () => {
    const currentTaskId = taskIdRef.current;
    if (!currentTaskId || taskStatusAppliedRef.current) {
      return;
    }
    try {
      const info = await getTask(currentTaskId);
      if (!info || !isTerminalTaskStatus(info.status)) {
        // durable 终态尚未落库（live 终态先于 governance 写盘是正常时序）：
        // 限次快速重试，让「取消/超时/预算」等真实语义尽快收敛到 UI
        if (reconcileAttemptsRef.current < 20) {
          reconcileAttemptsRef.current += 1;
          window.setTimeout(() => {
            if (!taskStatusAppliedRef.current && taskIdRef.current) {
              void doReconcile();
            }
          }, 250);
        }
        return;
      }
      reconcileAttemptsRef.current = 0;
      taskStatusAppliedRef.current = true;
      terminalLiveRef.current = true;
      if (OVERRIDE_RESULT_KINDS.has(info.status)) {
        setResult("");
      }
      if (info.status === "failed") {
        setLastError((previous) => previous || info.error || "任务执行失败");
      }
      setIsRunning(false);
      setIsCancelling(false);
      setTaskStatus(info);
    } catch {
      // 网络异常 / 任务不存在：保留现状，静默
    }
  }, []);

  useEffect(() => {
    let disposed = false;

    function connect() {
      clearSocketTimers();
      const hadSocket = Boolean(socketRef.current);
      socketRef.current?.close();
      setConnectionState(hadSocket ? "reconnecting" : "connecting");

      const socket = new WebSocket(`${WS_BASE_URL}/ws/${encodeURIComponent(threadId)}`);
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

          // 同 thread 可能先后存在多个 run：丢弃迟到旧 run 的事件。
          // 注意：monitor 信封 run_id（main_agent 本地生成）≠ governance TaskRecord.run_id，
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
  }, [clearSocketTimers, threadId]);

  useEffect(() => {
    isRunningRef.current = isRunning;
  }, [isRunning]);

  // 断线重连后的 durable 终态校正：WS 掉线期间 live 事件会被服务端丢弃（monitor 不持久化），
  // 若任务恰在窗口内结束（完成/失败/超时/预算/取代/中止），页面会一直停在“研搜中”或误判取消。
  // 重连成功后按 task_id 查询治理记录，用真实终态校正 UI（区分 timed_out/budget_exceeded 等）。
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
        const response = await startTask(cleanQuery, threadId);
        if (response.thread_id && response.thread_id !== threadId) {
          storeThreadId(response.thread_id);
          setThreadId(response.thread_id);
        }
        // governed 任务身份：task_id 用于终态校正查询（run_id 因后端信封语义差异不作为过滤锚点）
        taskIdRef.current = response.task_id ?? null;
        setTaskId(response.task_id ?? null);
        return response;
      } catch (error) {
        setIsRunning(false);
        setIsCancelling(false);
        throw error;
      }
    },
    [threadId]
  );

  const cancelCurrentTask = useCallback(async () => {
    if (!isRunning) {
      throw new Error("当前没有正在执行的任务");
    }

    setIsCancelling(true);
    setLastError("");
    try {
      const response = await cancelTask(threadId);
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
  }, [isRunning, threadId]);

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
          threadId
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
    [threadId]
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
    connectionState,
    events,
    files,
    isCancelling,
    isRunning,
    isUploading,
    lastError,
    lastPongAt,
    refreshFiles,
    resetSession,
    result,
    sessionPath,
    stats,
    taskId,
    taskStatus,
    cancelCurrentTask,
    submitTask,
    threadId,
    uploadFiles,
    uploadedItems
  };
}
