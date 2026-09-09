import { Alert, App as AntApp } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import { SessionSidebar } from "./components/SessionSidebar";
import { SessionWorkspace } from "./components/SessionWorkspace";
import type { ChatTurn } from "./components/ConversationThread";
import {
  archiveSession,
  createSession,
  listSessions,
  restoreSession
} from "./lib/api";
import {
  peekStoredThreadId,
  storeThreadId
} from "./lib/thread";
import type {
  SessionRuntimeSnapshot,
  SessionSummary
} from "./types";

export default function App() {
  const { message } = AntApp.useApp();
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(true);
  const [sessionsError, setSessionsError] = useState<string | null>(null);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [booting, setBooting] = useState(true);
  const [bootError, setBootError] = useState<string | null>(null);
  // Browser-lifetime 内存缓存（非 durable transcript）：session → turns
  const [turnsBySession, setTurnsBySession] = useState<Record<string, ChatTurn[]>>({});
  // 当前 Session 的展示元数据（权威 status 来自 GET /api/sessions/{id}；列表刷新失败时保留旧值）
  const [currentMeta, setCurrentMeta] = useState<SessionSummary | null>(null);
  // 当前 Session 的轻量运行态快照（仅展示用；runtime authority 在 SessionWorkspace hook）
  const [runtime, setRuntime] = useState<SessionRuntimeSnapshot | null>(null);

  const refreshSessions = useCallback(async () => {
    setSessionsLoading(true);
    setSessionsError(null);
    try {
      // Sidebar 需要同时呈现 active + archived（archived 用于恢复入口），一次拉全量后分组
      const data = await listSessions({ includeArchived: true });
      setSessions(data.sessions);
      return data.sessions;
    } catch (error) {
      const text = error instanceof Error ? error.message : "加载会话列表失败";
      setSessionsError(text);
      return [];
    } finally {
      setSessionsLoading(false);
    }
  }, []);

  /** 本地乐观更新 session 状态（归档/恢复成功后立即生效，避免等待列表刷新）。 */
  function patchSessionStatus(sessionId: string, status: SessionSummary["status"]) {
    setSessions((previous) =>
      previous.map((item) =>
        item.session_id === sessionId ? { ...item, status } : item
      )
    );
  }

  // boot：恢复 currentSessionId（含 legacy thread 自动注册为 Session）
  useEffect(() => {
    let disposed = false;

    async function boot() {
      setBooting(true);
      try {
        // 先确认服务端可达（顺带全量会话列表）
        const data = await listSessions({ includeArchived: true });
        if (disposed) {
          return;
        }
        const allSessions = data.sessions;
        setSessions(allSessions);

        const targetId = peekStoredThreadId();
        let chosenId: string | null = null;
        if (targetId) {
          const registered = allSessions.find((item) => item.session_id === targetId);
          if (!registered) {
            // legacy thread 未注册 → POST /api/sessions {thread_id} 注册为 Session
            try {
              const created = await createSession({ threadId: targetId });
              chosenId = created.session_id;
            } catch (registerError) {
              // 已注册（并发/上次注册成功但列表未刷）→ 以既有 thread 作为 current
              chosenId = targetId;
            }
          } else {
            chosenId = targetId;
          }
        } else {
          // 无任何 thread：创建默认 Session
          const created = await createSession();
          chosenId = created.session_id;
        }
        if (chosenId) {
          storeThreadId(chosenId);
          setCurrentSessionId(chosenId);
        }
        // 统一以最新列表收尾（无论注册/创建成功与否），保证 current 一定在 sessions 中
        const fresh = await listSessions({ includeArchived: true });
        if (disposed) {
          return;
        }
        setSessions(fresh.sessions);
      } catch (error) {
        if (!disposed) {
          setBootError(error instanceof Error ? error.message : "初始化会话失败");
        }
      } finally {
        if (!disposed) {
          setBooting(false);
          setSessionsLoading(false);
        }
      }
    }

    void boot();
    return () => {
      disposed = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // currentSessionId / sessions 变化时同步 currentMeta（找不到则保留旧值，避免 workspace 闪失）
  useEffect(() => {
    if (!currentSessionId) {
      setCurrentMeta(null);
      return;
    }
    const found = sessions.find((item) => item.session_id === currentSessionId);
    if (found) {
      setCurrentMeta(found);
    }
  }, [currentSessionId, sessions]);

  const currentSessionIdRef = useRef<string | null>(null);
  currentSessionIdRef.current = currentSessionId;

  function handleRuntimeSnapshot(sessionId: string, snapshot: SessionRuntimeSnapshot) {
    // 只接受"当前 session"的快照：切换后旧 workspace 卸载，A 的运行时数据不得残留到 B
    if (sessionId !== currentSessionIdRef.current) {
      return;
    }
    setRuntime(snapshot);
  }

  const handleSwitchSession = useCallback(
    (sessionId: string) => {
      if (sessionId === currentSessionId) {
        return;
      }
      storeThreadId(sessionId);
      setCurrentSessionId(sessionId);
      // 清空上一 session 的运行态快照，避免 A 状态短暂显示在 B 侧栏
      setRuntime(null);
    },
    [currentSessionId]
  );

  const handleCreateSession = useCallback(async () => {
    try {
      const created = await createSession();
      storeThreadId(created.session_id);
      setSessions((previous) => [
        created,
        ...previous.filter((item) => item.session_id !== created.session_id)
      ]);
      setCurrentSessionId(created.session_id);
      setRuntime(null);
      void refreshSessions();
      message.success("已创建新会话");
    } catch (error) {
      message.error(error instanceof Error ? error.message : "创建会话失败");
    }
  }, [message, refreshSessions]);

  const handleArchiveSession = useCallback(
    async (sessionId: string) => {
      try {
        const response = await archiveSession(sessionId);
        if (!response.already_archived) {
          patchSessionStatus(sessionId, "archived");
          message.success("会话已归档");
        }
        void refreshSessions();
      } catch (error) {
        // 409：会话存在运行中的任务，需先取消；其余错误原样呈现
        message.error(error instanceof Error ? error.message : "归档会话失败");
      }
    },
    [message, refreshSessions]
  );

  const handleRestoreSession = useCallback(
    async (sessionId: string) => {
      try {
        const response = await restoreSession(sessionId);
        if (!response.already_active) {
          patchSessionStatus(sessionId, "active");
          message.success("会话已恢复");
        }
        void refreshSessions();
      } catch (error) {
        message.error(error instanceof Error ? error.message : "恢复会话失败");
      }
    },
    [message, refreshSessions]
  );

  const handleTurnsChange = useCallback((sessionId: string, turns: ChatTurn[]) => {
    setTurnsBySession((previous) => {
      const next = { ...previous };
      if (turns.length === 0) {
        delete next[sessionId];
      } else {
        next[sessionId] = turns;
      }
      return next;
    });
  }, []);

  if (booting) {
    return (
      <div className="chat-app-shell min-h-dvh">
        <main className="chat-main chat-main--center">
          <span className="panel-kicker">DEEPSEARCH</span>
          <p className="boot-status">正在恢复会话…</p>
        </main>
      </div>
    );
  }

  if (bootError) {
    return (
      <div className="chat-app-shell min-h-dvh">
        <main className="chat-main chat-main--center">
          <Alert
            className="chat-alert"
            message={bootError}
            showIcon
            type="error"
          />
          <p className="boot-status">
            无法连接后端服务，请确认 FastAPI 已启动（默认 http://localhost:8000）。
          </p>
        </main>
      </div>
    );
  }

  return (
    <div className="chat-app-shell min-h-dvh">
      <SessionSidebar
        currentSessionId={currentSessionId}
        onCreateSession={() => {
          void handleCreateSession();
        }}
        onArchiveSession={(sessionId) => {
          void handleArchiveSession(sessionId);
        }}
        onRestoreSession={(sessionId) => {
          void handleRestoreSession(sessionId);
        }}
        onRetrySessions={() => {
          void refreshSessions();
        }}
        onSwitchSession={handleSwitchSession}
        runtime={runtime}
        sessions={sessions}
        sessionsError={sessionsError}
        sessionsLoading={sessionsLoading}
      />
      {currentSessionId && currentMeta ? (
        <SessionWorkspace
          currentSessionMeta={currentMeta}
          initialTurns={turnsBySession[currentSessionId] ?? []}
          key={currentSessionId}
          onCreateSession={() => {
            void handleCreateSession();
          }}
          onRestoreSession={(sessionId) => {
            void handleRestoreSession(sessionId);
          }}
          onRuntimeSnapshot={handleRuntimeSnapshot}
          onSwitchSession={handleSwitchSession}
          onTurnsChange={handleTurnsChange}
          sessionId={currentSessionId}
          sessionStatus={currentMeta.status}
          sessions={sessions}
        />
      ) : null}
    </div>
  );
}
