# P007 — Runtime 缺少健康平面：进程退出 / 执行器失联后任务永久 RUNNING 且不可诊断

## Type

Runtime（主类；同时属于 Architecture constraint —— 契约缺口：无存活信号、无 stale 判定、无收敛路径）

## Status

Open（追踪中；由 P2-2 Runtime Heartbeat / Stale Detection / Reclaim 实现关闭）

## Severity

High

## Created / Last Updated

2026-10-01 / 2026-10-01

## Path

`app/runtime/governance/`（controller / store / models / events）；关联 `app/api/server.py` lifespan；
上游分析 `RUNTIME_OBSERVABILITY_AUDIT_REPORT.md`；设计与决策 `P2-2_SPEC_v2.md`（Rev 2.2）、
`DECISION.md`（D-Phase2-P2-2-001 ~ 017）

## Summary

Runtime 在执行期没有任何存活信号（heartbeat）：`governance_tasks` 行自 `running` 建立后，
除 `started_at` 外不再变化；一旦进程异常退出、执行器（事件循环 / 线程）长时间失联，
或底层调用静默挂起，就没有任何机制能把该行收敛为终态，也没有任何数据可以判定「它是否还活着」。

后果：任务永久停留 `running`，前端按 durable 状态永久显示「研搜中」；既无 stale 判定、
无启动期 / 周期收敛路径，也无诊断字段（最后心跳、最后事件、失联原因）。
同时 `orphan_reclaimed` / `aborted` 两个既有终态从未被任何生产路径产出，
`flush_pending()` 无生产调用方。

## Impact

- **正确性 / 运维**：遗留 `running` 行使 session 归档守卫（无 running 才可归档）长期失败，
  前端与使用者无法区分「仍在执行」与「已失联」。
- **可诊断性**：事故后只能看到「无终态、无异常日志」，无法证明构成（缺少持久化证据）。
- **架构**：Runtime 缺少「进程 / 执行器存活」这一层观测面，任何上层恢复能力（reclaim、管理诊断、
  历史时间线）都缺少输入。

## Root Cause

Runtime Health Plane 缺失：heartbeat（存活遥测）不存在 ⇒ stale 判定无输入 ⇒ reclaim 无触发依据 ⇒
终态收敛只能依赖「执行协程自身返回或抛异常」这一唯一路径；进程退出或静默挂起时该路径永不发生。

## Evidence

- `RUNTIME_OBSERVABILITY_AUDIT_REPORT.md`：现场任务（会话 `c345e0c6308e4bbaa1461567f20b16d6`）
  在工具事件后永久停留「研搜中」，无终态 / 无失败 / 无异常日志。
- `app/runtime/governance/controller.py`：watchdog 仅在进程内可 tick 时有效；无 heartbeat 写入点；
  `_execute_bare` / `_execute_governed` 的终态收敛完全依赖协程返回或抛出异常。
- `app/runtime/governance/models.py`：`orphan_reclaimed` / `aborted` 已定义但无生产产出路径。
- `app/runtime/governance/controller.py::flush_pending`：存在但无生产调用方（`grep` 仅测试引用）。
- `db/governance_migrations/0001_governance.*.sql`：无 heartbeat 列 / 健康表。
- `PROJECT_CONTEXT.md` §8：sweeper / startup 自动恢复（orphan reclaim 产品化）明确列为未做。

## Scope

### In Scope

- P2-2 引入 Runtime Health Plane：heartbeat（健康表 + 写路径）、stale 派生判定、reclaim
  （startup sweep + periodic scanner）、健康行清理与 `RUNTIME_RECLAIM_MODE` 三模式。

### Out of Scope

- 多实例 lease / fencing、断点续跑（resume）、自动重试；
- 新增 TaskStatus / 修改 F8 lifecycle authority / funnel / CAS / events schema；
- P2-3 durable timeline、P2-4 admin runtime 端点、P2-5 会话标题；
- 前端改动与运行期调参（Runtime Policy Tuning）。

## Constraints

- 必须保持 F8 冻结语义：终态收敛只经 `controller.finalize_with_event`；health plane 不得写
  `governance_tasks` 的 lifecycle 字段；不新增 TaskStatus。
- heartbeat 为**单实例健康遥测**，**非** lease / fencing，不改变 F8 §5.3 单实例假设。
- 不新增依赖、不引入连接池；心跳写必须 fail-open 且不得侵蚀 control path。

## Known Failure Modes

- 心跳写失败被误当作「执行已停」→ 由 grace + 双阶段确认 + 最小年龄 + handle 存活检查共同兜底。
- 滚动 / 蓝绿部署重叠期间误杀仍在心跳的旧进程任务 → `SAME_HOST_PREV` 必须叠加 liveness 证据。
- 健康行无界增长 → 清理责任归 scanner；`off` 模式停写心跳。
- PG 短连接写抖动拖慢事件循环 → 节流 + 抖动相位、单语句短事务（批量 writer 仅作应急候选）。

## Candidate Solutions

1. **独立健康表 + 首拍接线 + 双路径 reclaim（采纳）**：见 `P2-2_SPEC_v2.md` Rev 2.2。
2. 在 `governance_tasks` 加 heartbeat 列（**拒绝**：触碰冻结 lifecycle 字段权威 + 宽行写放大）。
3. 无持久化心跳、仅依赖 owner/handle（**拒绝**：无法判定执行器失联，且破坏 D15 安全证据）。

## Resolution

未解决。关闭条件：P2-2 实现完成并通过验证（heartbeat / stale / reclaim 全链路 +
PG 门控 + 回归证据），届时 Status → Resolved 并指向实现报告与 Decision 证据。

## Related Decisions

- `D-Phase2-P2-2-001`（Runtime Health Plane；heartbeat = 健康遥测）
- `D-Phase2-P2-2-002`（阈值与配置默认值）
- `D-Phase2-P2-2-010`（`SAME_HOST_PREV` liveness 证据）
- `D-Phase2-P2-2-011 … 017`（确认次数 / `flush_pending` / startup 时序 / interval / PG 约束 /
  Problem 时机 / frontend 策略）

## Related Architecture

- `P2-2_SPEC_v2.md` Rev 2.2（§4 Heartbeat / §5 Stale / §6 Reclaim / §7 Startup / §8 Periodic /
  §12 Schema / §13 Config）
- F8 Runtime Governance Spec（§5.3 单实例、§6.1 终态语义、§7.2 control/observation 分离、§10.2 sweeper）
- `ARCHITECTURE.md`（Runtime 边界）

## Related Tests

- `tests/test_runtime_heartbeat.py`（S1 数据层；S2 追加 writer / 首拍接线）
- `tests/test_runtime_reclaim.py`（S4/S5：owner 分类、证据谓词、startup sweep、periodic、清理、模式矩阵）
- `tests/test_runtime_health_postgres.py`（S7 PG 门控）
- 冻结回归：`tests/test_runtime_policy_enforcement.py`、`tests/test_runtime_governance*.py`、`tests/test_sessions_*.py`

## Remaining Risks

- 默认 wall-clock（600s）与 grace（30s）对超长合法研究任务偏紧 → 由显式 policy 覆盖；
  调参属后续独立批次（不在 P2-2）。
- 跨进程重复 terminal 事件为已知限制（单实例 supported envelope；多实例不支持）。
