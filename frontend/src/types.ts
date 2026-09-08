export type ConnectionState = "connecting" | "connected" | "reconnecting" | "closed";

export type MonitorEventName =
  | "session_created"
  | "task_started"
  | "tool_start"
  | "assistant_call"
  | "task_result"
  | "task_cancelled"
  | "error"
  | string;

/** R3 monitor_event 信封（后端 app/api/monitor.py:136-146）。
 *  event_id：跨 live/durable/replay 唯一身份（可用于去重）；
 *  run_id：一次 run_deep_agent 执行的身份（用于隔离同 thread 的迟到旧事件）；
 *  seq：进程内全局单调递增的事件分配序（不是物理时间戳）。 */
export interface MonitorMessage {
  type: "monitor_event";
  event: MonitorEventName;
  message: string;
  data: Record<string, unknown>;
  timestamp: string;
  event_id?: string;
  run_id?: string;
  thread_id?: string;
  seq?: number;
}

export interface PongMessage {
  type: "pong";
  message: string;
}

export type SocketMessage = MonitorMessage | PongMessage;

export interface TaskResponse {
  status: "started" | string;
  thread_id: string;
  task_id?: string;
  run_id?: string;
}

export interface CancelTaskResponse {
  status: "cancelled" | "cancelling" | string;
  thread_id: string;
  message?: string;
  task_id?: string;
  already_terminal?: boolean;
}

/** GET /api/tasks/{task_id} 返回的治理任务记录子集（app/runtime/governance/service.py:90-109）。 */
export interface TaskInfo {
  task_id: string;
  thread_id: string;
  run_id?: string | null;
  status: string;
  terminal_reason?: string | null;
  error_kind?: string | null;
  error?: string | null;
  counters_snapshot?: Record<string, unknown> | null;
  policy_snapshot?: Record<string, unknown>;
  effective_limits?: Record<string, unknown>;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
}

/** TaskInfo.status 的治理终态（app/runtime/governance/models.py:15-40）。 */
export const TERMINAL_TASK_STATUSES = [
  "completed",
  "failed",
  "cancelled",
  "timed_out",
  "budget_exceeded",
  "superseded",
  "aborted",
  "orphan_reclaimed",
] as const;

export type TerminalTaskStatus = (typeof TERMINAL_TASK_STATUSES)[number];

export function isTerminalTaskStatus(
  status: string | undefined | null
): status is TerminalTaskStatus {
  return Boolean(status) && (TERMINAL_TASK_STATUSES as readonly string[]).includes(status as string);
}

export interface UploadResponse {
  status: "uploaded" | string;
  files: string[];
}

export interface OutputFile {
  name: string;
  type: "file" | string;
  path: string;
  size: number;
  mtime: number;
}

export interface FileListResponse {
  files?: OutputFile[];
  error?: string;
}

export interface UploadedItem {
  uid: string;
  name: string;
  size: number;
  raw: File;
}
