// 会话身份持久化（Multi-Session：session_id == thread_id，1:1）。
// deepsearch.thread_id 同时承载「当前 Session id」——不建立第二套 Session ID 持久化机制。

const STORAGE_KEY = "deepsearch.thread_id";
const SESSION_PATH_PREFIX = "deepsearch.session_path.";

export function createThreadId(): string {
  if (crypto.randomUUID) {
    return crypto.randomUUID();
  }

  return `manual-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** 读取当前 thread/session id；不存在则生成并落盘（兼容旧前端行为）。 */
export function getStoredThreadId(): string {
  const existing = window.localStorage.getItem(STORAGE_KEY);
  if (existing) {
    return existing;
  }

  const threadId = createThreadId();
  window.localStorage.setItem(STORAGE_KEY, threadId);
  return threadId;
}

/** 只读探测：是否存在已持久化的 thread id（boot 协调用，避免无谓生成）。 */
export function peekStoredThreadId(): string | null {
  return window.localStorage.getItem(STORAGE_KEY);
}

export function storeThreadId(threadId: string): void {
  window.localStorage.setItem(STORAGE_KEY, threadId);
}

/** 会话输出目录绝对路径缓存（服务端 /api/files 按绝对路径取值；live session_created 时写入）。 */
export function rememberSessionPath(sessionId: string, path: string): void {
  if (!path) {
    return;
  }
  try {
    window.localStorage.setItem(`${SESSION_PATH_PREFIX}${sessionId}`, path);
  } catch {
    // 配额/隐私模式等：缓存失败不影响主流程
  }
}

export function getStoredSessionPath(sessionId: string): string | null {
  try {
    return window.localStorage.getItem(`${SESSION_PATH_PREFIX}${sessionId}`);
  } catch {
    return null;
  }
}
