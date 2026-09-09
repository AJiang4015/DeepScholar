import { CheckCircleOutlined, BranchesOutlined } from "@ant-design/icons";
import { Alert, App as AntApp, Select } from "antd";
import { useEffect, useRef, useState } from "react";
import { getSessionDetail } from "../lib/api";
import { useDeepAgentSession } from "../hooks/useDeepAgentSession";
import { ChatComposer } from "./ChatComposer";
import { ConversationThread } from "./ConversationThread";
import { TaskHistory } from "./TaskHistory";
import type { ChatTurn } from "./ConversationThread";
import type {
  SessionRuntimeSnapshot,
  SessionStatus,
  SessionSummary,
  SessionTask,
  UploadedItem
} from "../types";

interface SessionWorkspaceProps {
  sessionId: string;
  /** Browser-lifetime 内存缓存（App 持有）：仅用于切换回来后还原，不是 durable transcript。 */
  initialTurns: ChatTurn[];
  sessionStatus: SessionStatus | string | null;
  /** 当前 session 在 App 列表中的展示信息（如已失效则为 null）。 */
  currentSessionMeta: SessionSummary | null;
  /** 全部 session（供 ≤980px 移动端切换 Select 使用）。 */
  sessions: SessionSummary[];
  onTurnsChange: (sessionId: string, turns: ChatTurn[]) => void;
  onRuntimeSnapshot: (sessionId: string, snapshot: SessionRuntimeSnapshot) => void;
  onSwitchSession: (sessionId: string) => void;
  onCreateSession: () => void;
  onRestoreSession: (sessionId: string) => void;
}

function createTurn(content: string, isRunning: boolean): ChatTurn {
  return {
    id: crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}`,
    content,
    events: [],
    files: [],
    isRunning,
    result: "",
    taskStatus: null,
    timestamp: new Date().toISOString()
  };
}

export function SessionWorkspace({
  sessionId,
  initialTurns,
  sessionStatus,
  currentSessionMeta,
  sessions,
  onTurnsChange,
  onRuntimeSnapshot,
  onSwitchSession,
  onCreateSession,
  onRestoreSession,
}: SessionWorkspaceProps) {
  const { message } = AntApp.useApp();
  const session = useDeepAgentSession(sessionId);
  const [query, setQuery] = useState("");
  const [stagedItems, setStagedItems] = useState<UploadedItem[]>([]);
  // 本 Session 的对话 turns：以 App 缓存为初值，运行期只在本组件内演进
  const [turns, setTurns] = useState<ChatTurn[]>(initialTurns);
  // durable task 摘要（刷新后 / 无内存 turns 时的历史视图数据）
  const [tasks, setTasks] = useState<SessionTask[]>([]);
  const [tasksLoading, setTasksLoading] = useState(false);

  // App 回调引用（避免每轮渲染重建造成的 effect 重入）
  const onTurnsChangeRef = useRef(onTurnsChange);
  onTurnsChangeRef.current = onTurnsChange;
  const onRuntimeRef = useRef(onRuntimeSnapshot);
  onRuntimeRef.current = onRuntimeSnapshot;

  const isArchived = sessionStatus === "archived";

  // 本实例"拥有的活动 turn"：只有本 workspace 发起的任务（submit）或刷新后按 durable query
  // 恢复的活动任务，才允许把 hook 状态写回 turns；避免重挂载后空 hook 状态污染缓存/跨会话。
  const activeTurnIdRef = useRef<string | null>(null);
  // 曾渲染的"待恢复运行 turn"id（durable 已终态或 query 缺失时不会创建）
  const recoveredRunningRef = useRef(false);

  // 加载 durable task 历史（mount 及 sessionId 变化时）；running 任务采纳 durable 状态恢复
  useEffect(() => {
    let disposed = false;
    setTasksLoading(true);
    getSessionDetail(sessionId)
      .then((detail) => {
        if (disposed) {
          return;
        }
        const latestTasks = detail.tasks ?? [];
        setTasks(latestTasks);
        const latest = detail.latest_task;
        if (latest && latest.status === "running") {
          // 恢复 durable lifecycle：不伪造 run_id / transcript，只恢复运行态并接收后续 live
          void session.adoptDurableTask(latest);
          // 若内存缓存里残留"离开时仍在运行"的旧 turn（其 live 过程不可恢复），
          // 从展示序列剔除，避免冻结/伪造运行态；随后按 durable query 决定是否重建活动 turn
          setTurns((previous) => {
            const last = previous[previous.length - 1];
            if (last && last.isRunning) {
              return previous.slice(0, -1);
            }
            return previous;
          });
        } else if (latest) {
          // durable 已终态：同样剔除缓存中"仍在运行"的旧 turn（不可恢复的 live 尾巴）
          setTurns((previous) => {
            const last = previous[previous.length - 1];
            if (last && last.isRunning) {
              return previous.slice(0, -1);
            }
            return previous;
          });
        }
      })
      .catch((error: unknown) => {
        if (!disposed) {
          // 会话级错误通道（顶部 Alert），与单会话现状一致
          session.reportError(
            error instanceof Error ? error.message : "加载会话历史失败"
          );
        }
      })
      .finally(() => {
        if (!disposed) {
          setTasksLoading(false);
        }
      });
    return () => {
      disposed = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  // 若 durable 存在 running task、且 turns 为空（刷新场景）：当 backend query 可用时按真实
  // 提问重建一个活动 turn，用于承接重连后到达的新 live events；query 缺失则保持 TaskHistory，
  // 不伪造 transcript（contract：task.query 可能为 null）。
  useEffect(() => {
    if (!session.isRunning || turns.length > 0 || recoveredRunningRef.current) {
      return;
    }
    const runningTask = tasks.find((task) => task.status === "running");
    const queryText = runningTask?.query?.trim();
    if (!queryText) {
      return;
    }
    recoveredRunningRef.current = true;
    const recovered = createTurn(queryText, true);
    activeTurnIdRef.current = recovered.id;
    setTurns((previous) => [...previous, recovered]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.isRunning, tasks, turns.length]);

  // 无活动 turn（刷新恢复的 passive 视图）且运行结束后：刷新 TaskHistory，让 durable 终态呈现
  useEffect(() => {
    if (session.isRunning || activeTurnIdRef.current || !session.taskStatus) {
      return;
    }
    let disposed = false;
    getSessionDetail(sessionId)
      .then((detail) => {
        if (!disposed) {
          setTasks(detail.tasks ?? []);
        }
      })
      .catch(() => {
        // 静默：TaskHistory 维持旧数据
      });
    return () => {
      disposed = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.isRunning, session.taskStatus, sessionId]);

  // 向 App 上报运行态快照（仅展示用；runtime authority 在本 hook）
  useEffect(() => {
    onRuntimeRef.current(sessionId, {
      connectionState: session.connectionState,
      isRunning: session.isRunning,
      stats: session.stats,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, session.connectionState, session.isRunning, session.stats]);

  // 运行期状态回填"本实例发起的活动 turn"（承接原 App.tsx 的镜像逻辑）
  useEffect(() => {
    const activeTurnId = activeTurnIdRef.current;
    if (!activeTurnId) {
      return;
    }
    // 空载荷守卫：非运行且无事件/结果/files/taskStatus 时，不覆盖 turn（保留 submit 失败的文案等）
    const hasPayload =
      session.isRunning ||
      session.events.length > 0 ||
      Boolean(session.result) ||
      session.files.length > 0 ||
      session.taskStatus !== null;
    if (!hasPayload) {
      return;
    }
    setTurns((previous) => {
      const index = previous.findIndex((turn) => turn.id === activeTurnId);
      if (index === -1) {
        return previous;
      }
      const latestTurn = previous[index];
      const sessionStatusView = session.taskStatus
        ? {
            status: session.taskStatus.status,
            error: session.taskStatus.error ?? null,
            terminalReason: session.taskStatus.terminal_reason ?? null
          }
        : null;
      const nextLatestTurn: ChatTurn = {
        ...latestTurn,
        events: session.events,
        files: session.files,
        isRunning: session.isRunning,
        result: session.result,
        taskStatus: sessionStatusView
      };
      const next = [...previous];
      next[index] = nextLatestTurn;
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    session.events,
    session.files,
    session.isRunning,
    session.result,
    session.taskStatus
  ]);

  // turns 演进后写回 App 内存缓存（turnsBySession）
  useEffect(() => {
    onTurnsChangeRef.current(sessionId, turns);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [turns]);

  async function handleSubmit() {
    const cleanQuery = query.trim();
    if (!cleanQuery) {
      message.warning("请输入研搜任务");
      return;
    }
    if (isArchived) {
      message.warning("会话已归档，无法创建新任务");
      return;
    }

    const nextTurn = createTurn(cleanQuery, true);
    activeTurnIdRef.current = nextTurn.id;
    setTurns((previous) => [...previous, nextTurn]);
    setQuery("");

    try {
      await session.submitTask(cleanQuery);
      message.success("任务已启动，执行过程会显示在对话中");
    } catch (error) {
      setTurns((previous) =>
        previous.map((turn) =>
          turn.id === nextTurn.id
            ? {
                ...turn,
                isRunning: false,
                result: error instanceof Error ? error.message : "任务启动失败"
              }
            : turn
        )
      );
      message.error(error instanceof Error ? error.message : "任务启动失败");
    }
  }

  async function handleCancel() {
    try {
      const response = await session.cancelCurrentTask();
      message.info(
        response.status === "cancelling" ? "取消请求已发送，正在等待当前调用结束" : "任务已取消"
      );
    } catch (error) {
      message.error(error instanceof Error ? error.message : "取消任务失败");
    }
  }

  async function handleUpload(items: UploadedItem[]) {
    try {
      const response = await session.uploadFiles(items);
      setStagedItems([]);
      message.success(`已上传 ${response.files.length} 个文件`);
    } catch (error) {
      message.error(error instanceof Error ? error.message : "上传失败");
    }
  }

  function renderStreamContent() {
    if (turns.length > 0) {
      return <ConversationThread onUseExample={setQuery} turns={turns} />;
    }
    if (tasksLoading || tasks.length > 0) {
      // 历史视图（加载中或存在 durable task 摘要；不伪造 transcript）
      return <TaskHistory loading={tasksLoading} tasks={tasks} />;
    }
    if (isArchived) {
      return (
        <div className="conversation-empty archived-empty">
          <p>该会话已归档，暂无历史任务。</p>
        </div>
      );
    }
    // 全新会话空态：示例入口（复用既有 ConversationThread 空态）
    return <ConversationThread onUseExample={setQuery} turns={turns} />;
  }

  // ≤980px 移动端会话切换入口（AntD Select，样式最小侵入）
  const switcherOptions = sessions.map((item) => ({
    value: item.session_id,
    label: item.session_id === currentSessionMeta?.session_id
      ? `${item.title || "未命名会话"}`
      : item.title || "未命名会话"
  }));

  return (
    <main className="chat-main">
      <header className="chat-topbar">
        <div className="chat-topbar-copy">
          <span className="panel-kicker">CHAT WORKSPACE</span>
          <h2>深度研搜对话</h2>
          <span className="chat-topbar-session">
            {isArchived ? "已归档" : "当前会话"} · {currentSessionMeta?.title || "未命名会话"}
          </span>
        </div>
        <div className="chat-topbar-actions">
          <Select
            aria-label="切换会话"
            className="session-switcher session-switcher--mobile"
            onChange={(value) => onSwitchSession(String(value))}
            options={switcherOptions}
            popupMatchSelectWidth={false}
            size="middle"
            value={sessionId}
          />
          <div className={`run-indicator ${session.isRunning ? "run-indicator--live" : ""}`}>
            {session.isRunning ? <BranchesOutlined aria-hidden /> : <CheckCircleOutlined aria-hidden />}
            {session.isRunning ? "研搜中" : "待命"}
          </div>
        </div>
      </header>

      {session.lastError ? (
        <Alert className="chat-alert" message={session.lastError} showIcon type="error" />
      ) : null}

      <section className="chat-stream-panel">
        {isArchived && (
          <div className="archived-notice" role="note">
            <span className="panel-kicker">ARCHIVED</span>
            <p>
              该会话已归档，仅可查看历史任务与产物。如需继续研究，请先
              <button type="button" onClick={() => onRestoreSession(sessionId)}>
                恢复会话
              </button>
              ，或
              <button type="button" onClick={onCreateSession}>
                新建研搜
              </button>
              。
            </p>
          </div>
        )}

        {renderStreamContent()}
      </section>

      <ChatComposer
        isArchived={isArchived}
        isCancelling={session.isCancelling}
        isRunning={session.isRunning}
        isUploading={session.isUploading}
        cancelEnabled={Boolean(session.taskId)}
        onCancel={handleCancel}
        onNewSession={onCreateSession}
        onQueryChange={setQuery}
        onStagedItemsChange={setStagedItems}
        onSubmit={handleSubmit}
        onUpload={handleUpload}
        query={query}
        stagedItems={stagedItems}
        uploadedItems={session.uploadedItems}
      />
    </main>
  );
}
