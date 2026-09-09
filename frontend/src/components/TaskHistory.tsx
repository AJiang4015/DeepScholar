import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  FileSearchOutlined,
  PlayCircleOutlined,
  StopOutlined,
} from "@ant-design/icons";
import { statusLabel } from "../lib/taskStatus";
import { formatTime } from "./ConversationThread";
import type { SessionTask } from "../types";

function TaskStatusIcon({ status }: { status: string }) {
  if (status === "running") {
    return <PlayCircleOutlined aria-hidden />;
  }
  if (status === "completed") {
    return <CheckCircleOutlined aria-hidden />;
  }
  if (status === "cancelled") {
    return <StopOutlined aria-hidden />;
  }
  if (status === "failed" || status === "timed_out" || status === "budget_exceeded") {
    return <CloseCircleOutlined aria-hidden />;
  }
  return <ClockCircleOutlined aria-hidden />;
}

function formatShortId(taskId: string): string {
  return taskId.length > 8 ? taskId.slice(0, 8) : taskId;
}

export function TaskHistory({ tasks, loading }: { tasks: SessionTask[]; loading?: boolean }) {
  if (loading && tasks.length === 0) {
    return (
      <div className="task-history task-history--loading">
        <div className="task-history-head">
          <span className="panel-kicker">TASK HISTORY</span>
          <strong>…</strong>
        </div>
        <p className="task-history-note">正在加载会话任务历史…</p>
      </div>
    );
  }

  return (
    <div className="task-history">
      <div className="task-history-head">
        <span className="panel-kicker">TASK HISTORY</span>
        <strong>{tasks.length}</strong>
      </div>
      <ol className="task-history-list" aria-label="会话任务历史">
        {tasks.map((task) => (
          <li
            className={`task-history-item task-history-item--${task.status}`}
            key={task.task_id}
          >
            <span className="task-history-icon">
              <TaskStatusIcon status={task.status} />
            </span>
            <div className="task-history-copy">
              <div className="task-history-title">
                <strong>{task.query?.trim() ? task.query : `任务 ${formatShortId(task.task_id)}`}</strong>
                <code>{formatShortId(task.task_id)}</code>
              </div>
              <div className="task-history-meta">
                <span>{statusLabel(task.status)}</span>
                <time dateTime={task.created_at ?? undefined}>
                  {task.created_at ? formatTime(task.created_at) : "--:--"}
                </time>
                {task.finished_at ? (
                  <time dateTime={task.finished_at}>结束 {formatTime(task.finished_at)}</time>
                ) : null}
              </div>
            </div>
          </li>
        ))}
      </ol>
      <div className="task-history-note">
        <FileSearchOutlined aria-hidden />
        <span>刷新后仅保留任务级历史摘要；完整对话过程不会在刷新后恢复。</span>
      </div>
    </div>
  );
}
