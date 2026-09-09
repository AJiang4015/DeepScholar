import {
  ApiOutlined,
  BranchesOutlined,
  CloudServerOutlined,
  CloseCircleOutlined,
  DatabaseOutlined,
  FileSearchOutlined,
  PlusOutlined,
  RollbackOutlined,
  ToolOutlined
} from "@ant-design/icons";
import { Button, Tooltip } from "antd";
import { API_BASE_URL, WS_BASE_URL } from "../lib/config";
import { formatTime } from "./ConversationThread";
import type { ConnectionState, SessionRuntimeSnapshot, SessionSummary } from "../types";

function connectionLabel(state: ConnectionState | undefined): string {
  const labels: Record<ConnectionState, string> = {
    connecting: "连接中",
    connected: "已连接",
    reconnecting: "重连中",
    closed: "已关闭",
  };
  return labels[state ?? "connecting"] ?? "已关闭";
}

interface SessionSidebarProps {
  sessions: SessionSummary[];
  sessionsLoading: boolean;
  sessionsError: string | null;
  currentSessionId: string | null;
  runtime: SessionRuntimeSnapshot | null;
  onRetrySessions: () => void;
  onCreateSession: () => void;
  onSwitchSession: (sessionId: string) => void;
  onArchiveSession: (sessionId: string) => void;
  onRestoreSession: (sessionId: string) => void;
}

export function SessionSidebar({
  sessions,
  sessionsLoading,
  sessionsError,
  currentSessionId,
  runtime,
  onRetrySessions,
  onCreateSession,
  onSwitchSession,
  onArchiveSession,
  onRestoreSession,
}: SessionSidebarProps) {
  const online = runtime?.connectionState === "connected";
  const current = sessions.find((item) => item.session_id === currentSessionId) ?? null;
  const activeSessions = sessions.filter((item) => item.status !== "archived");
  const archivedSessions = sessions.filter((item) => item.status === "archived");

  const renderSessionItem = (sessionItem: SessionSummary) => {
    const isCurrent = sessionItem.session_id === currentSessionId;
    const isArchived = sessionItem.status === "archived";
    const isRunning = (sessionItem.running_tasks ?? 0) > 0;
    return (
      <li
        aria-current={isCurrent ? "true" : undefined}
        className={`session-item${isCurrent ? " session-item--active" : ""}${
          isArchived ? " session-item--archived" : ""
        }`}
        key={sessionItem.session_id}
      >
        <button
          aria-label={`打开会话 ${sessionItem.title || "未命名会话"}`}
          className="session-item-main"
          onClick={() => onSwitchSession(sessionItem.session_id)}
          type="button"
        >
          <span className="session-item-title">
            {sessionItem.title || "未命名会话"}
            {isRunning ? <i className="session-running-dot" aria-hidden /> : null}
          </span>
          <span className="session-item-meta">
            <code>{sessionItem.session_id.slice(0, 8)}</code>
            <time>{sessionItem.updated_at ? formatTime(sessionItem.updated_at) : ""}</time>
            {isRunning ? <em>研搜中</em> : null}
            {isArchived ? <em>已归档</em> : null}
          </span>
        </button>
        <span className="session-item-actions">
          {isArchived ? (
            <Tooltip title="恢复会话">
              <Button
                aria-label={`恢复会话 ${sessionItem.title || "未命名会话"}`}
                className="session-action session-action--restore"
                icon={<RollbackOutlined />}
                onClick={() => onRestoreSession(sessionItem.session_id)}
                shape="circle"
                size="small"
              />
            </Tooltip>
          ) : (
            <Tooltip title="归档会话">
              <Button
                aria-label={`归档会话 ${sessionItem.title || "未命名会话"}`}
                className="session-action session-action--archive"
                danger
                icon={<CloseCircleOutlined />}
                onClick={() => onArchiveSession(sessionItem.session_id)}
                shape="circle"
                size="small"
              />
            </Tooltip>
          )}
        </span>
      </li>
    );
  };

  return (
    <aside className="chat-sidebar" aria-label="会话列表">
      <div className="sidebar-brand">
        <span className="panel-kicker">DEEPSEARCH</span>
        <h1>深度研搜</h1>
        <p>对话式多智能体研究台</p>
      </div>

      <Button className="new-chat-button" block icon={<PlusOutlined />} onClick={onCreateSession}>
        新建研搜
      </Button>

      <div className="sidebar-section session-section">
        <div className="session-section-head">
          <span className="sidebar-label">SESSIONS</span>
          {sessionsLoading ? <span className="session-section-meta">同步中</span> : null}
        </div>

        {sessionsError ? (
          <div className="session-error" role="alert">
            <span>{sessionsError}</span>
            <button type="button" onClick={onRetrySessions}>
              重试
            </button>
          </div>
        ) : sessions.length === 0 && !sessionsLoading ? (
          <div className="session-empty">
            <span>暂无会话，点击「新建研搜」开始。</span>
          </div>
        ) : (
          <>
            {activeSessions.length > 0 ? (
              <ul className="session-list" aria-label="研搜会话列表">
                {activeSessions.map(renderSessionItem)}
              </ul>
            ) : null}
            {archivedSessions.length > 0 ? (
              <div className="session-archived-group">
                <span className="sidebar-label session-archived-label">ARCHIVED</span>
                <ul className="session-list" aria-label="已归档会话列表">
                  {archivedSessions.map(renderSessionItem)}
                </ul>
              </div>
            ) : null}
          </>
        )}
      </div>

      <div className="sidebar-section session-detail">
        <span className="sidebar-label">CURRENT SESSION</span>
        <strong className="thread-id" title={currentSessionId ?? ""}>
          {currentSessionId ? currentSessionId.slice(0, 8) : "—"}
        </strong>
        {current ? (
          <span
            className={`session-detail-status${
              current.status === "archived" ? " session-detail-status--archived" : ""
            }`}
          >
            {current.status === "archived" ? "已归档" : "进行中"}
          </span>
        ) : null}
      </div>

      <div className="sidebar-status-list">
        <div className={`sidebar-status ${online ? "sidebar-status--online" : "sidebar-status--warn"}`}>
          <ApiOutlined aria-hidden />
          <span>WebSocket</span>
          <strong>{connectionLabel(runtime?.connectionState)}</strong>
        </div>
        <div className="sidebar-status">
          <BranchesOutlined aria-hidden />
          <span>助手调度</span>
          <strong>{runtime?.stats.assistantEvents ?? 0}</strong>
        </div>
        <div className="sidebar-status">
          <ToolOutlined aria-hidden />
          <span>工具调用</span>
          <strong>{runtime?.stats.toolEvents ?? 0}</strong>
        </div>
        <div
          className={
            (runtime?.stats.errorEvents ?? 0) > 0 ? "sidebar-status sidebar-status--error" : "sidebar-status"
          }
        >
          <CloseCircleOutlined aria-hidden />
          <span>异常</span>
          <strong>{runtime?.stats.errorEvents ?? 0}</strong>
        </div>
      </div>

      <div className="sidebar-section">
        <span className="sidebar-label">AGENTS</span>
        <ul className="agent-mini-list">
          <li>
            <CloudServerOutlined aria-hidden />
            网络搜索助手
          </li>
          <li>
            <DatabaseOutlined aria-hidden />
            数据库查询助手
          </li>
          <li>
            <FileSearchOutlined aria-hidden />
            RAGFlow 助手
          </li>
        </ul>
      </div>

      <div className="sidebar-section sidebar-endpoints">
        <span className="sidebar-label">ENDPOINTS</span>
        <code>{API_BASE_URL}</code>
        <code>{WS_BASE_URL}</code>
      </div>
    </aside>
  );
}
