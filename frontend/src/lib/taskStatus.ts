// 治理任务终态 → 用户可读标签/语义（governance TaskStatus；App 多组件共享，避免散落重复）。
// 语义色映射由调用方按 UI_PATTERNS 的 event/status 规则自行落到 class。

/** governance TaskStatus → 中文短标签（对应后端 TaskStatus / TerminalReason）。 */
export const TERMINAL_STATUS_LABELS: Record<string, string> = {
  running: "研搜中",
  completed: "已完成",
  failed: "执行失败",
  cancelled: "已取消",
  timed_out: "任务超时",
  budget_exceeded: "预算超限",
  superseded: "已被新任务取代",
  aborted: "任务中止",
  orphan_reclaimed: "已回收",
};

export function statusLabel(status: string): string {
  return TERMINAL_STATUS_LABELS[status] ?? `已结束（${status}）`;
}
