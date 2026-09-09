import { API_BASE_URL } from "./config";
import type {
  ArchiveSessionResponse,
  CancelTaskResponse,
  FileListResponse,
  RestoreSessionResponse,
  SessionDetailResponse,
  SessionListResponse,
  SessionSummary,
  SessionTaskListResponse,
  TaskInfo,
  TaskResponse,
  UploadResponse
} from "../types";

function apiUrl(path: string): string {
  return `${API_BASE_URL}${path}`;
}

async function requestJson<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
  const response = await fetch(input, init);
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const message =
      typeof payload === "object" && payload && "detail" in payload
        ? String(payload.detail)
        : `HTTP ${response.status}`;
    throw new Error(message);
  }

  return payload as T;
}

export async function startTask(query: string, threadId: string): Promise<TaskResponse> {
  return requestJson<TaskResponse>(apiUrl("/api/task"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      query,
      thread_id: threadId
    })
  });
}

export async function cancelTask(threadId: string): Promise<CancelTaskResponse> {
  return requestJson<CancelTaskResponse>(apiUrl(`/api/task/${encodeURIComponent(threadId)}/cancel`), {
    method: "POST"
  });
}

/** 治理任务详情（GET /api/tasks/{task_id}）：
 *  重连/刷新后校正任务终态（completed/failed/cancelled/timed_out/budget_exceeded/…）。 */
export async function getTask(taskId: string): Promise<TaskInfo> {
  return requestJson<TaskInfo>(apiUrl(`/api/tasks/${encodeURIComponent(taskId)}`));
}

export async function uploadSessionFiles(
  files: File[],
  threadId: string
): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("thread_id", threadId);
  files.forEach((file) => formData.append("files", file));

  return requestJson<UploadResponse>(apiUrl("/api/upload"), {
    method: "POST",
    body: formData
  });
}

export async function listSessionFiles(path: string): Promise<FileListResponse> {
  const url = new URL(apiUrl("/api/files"));
  url.searchParams.set("path", path);
  return requestJson<FileListResponse>(url);
}

export function getDownloadUrl(path: string): string {
  const url = new URL(apiUrl("/api/download"));
  url.searchParams.set("path", path);
  return url.toString();
}

// ---------------------------------------------------------------------------
// Session domain（契约 = MULTI_SESSION_API_SPEC.md；additive，既有端点/语义不变）
//   session_id == thread_id：新建 task 走 session-scoped；upload/files 以 session_id 作 thread_id
// ---------------------------------------------------------------------------

export async function createSession(options?: {
  title?: string;
  threadId?: string;
}): Promise<SessionSummary> {
  const body: Record<string, string> = {};
  if (options?.title) {
    body.title = options.title;
  }
  if (options?.threadId) {
    body.thread_id = options.threadId;
  }
  return requestJson<SessionSummary>(apiUrl("/api/sessions"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
}

export async function listSessions(options?: {
  includeArchived?: boolean;
  limit?: number;
  offset?: number;
}): Promise<SessionListResponse> {
  const url = new URL(apiUrl("/api/sessions"));
  if (options?.includeArchived) {
    url.searchParams.set("include_archived", "true");
  }
  if (options?.limit) {
    url.searchParams.set("limit", String(options.limit));
  }
  if (options?.offset) {
    url.searchParams.set("offset", String(options.offset));
  }
  return requestJson<SessionListResponse>(url);
}

export async function getSessionDetail(sessionId: string): Promise<SessionDetailResponse> {
  return requestJson<SessionDetailResponse>(
    apiUrl(`/api/sessions/${encodeURIComponent(sessionId)}`)
  );
}

export async function archiveSession(sessionId: string): Promise<ArchiveSessionResponse> {
  return requestJson<ArchiveSessionResponse>(
    apiUrl(`/api/sessions/${encodeURIComponent(sessionId)}`),
    { method: "DELETE" }
  );
}

export async function restoreSession(sessionId: string): Promise<RestoreSessionResponse> {
  return requestJson<RestoreSessionResponse>(
    apiUrl(`/api/sessions/${encodeURIComponent(sessionId)}`),
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: "active" })
    }
  );
}

/** 在当前 Session 内创建研究任务（archived → 409；同 thread 单活跃收敛语义同 POST /api/task）。 */
export async function createSessionTask(
  sessionId: string,
  query: string,
  policy?: Record<string, unknown>
): Promise<TaskResponse> {
  return requestJson<TaskResponse>(
    apiUrl(`/api/sessions/${encodeURIComponent(sessionId)}/tasks`),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, policy })
    }
  );
}

export async function listSessionTasks(sessionId: string): Promise<SessionTaskListResponse> {
  return requestJson<SessionTaskListResponse>(
    apiUrl(`/api/sessions/${encodeURIComponent(sessionId)}/tasks`)
  );
}

/** Session-scoped 取消（归属校验：task.thread_id == session_id；跨 Session → 404）。 */
export async function cancelSessionTask(
  sessionId: string,
  taskId: string
): Promise<CancelTaskResponse> {
  return requestJson<CancelTaskResponse>(
    apiUrl(
      `/api/sessions/${encodeURIComponent(sessionId)}/tasks/${encodeURIComponent(taskId)}/cancel`
    ),
    { method: "POST" }
  );
}
