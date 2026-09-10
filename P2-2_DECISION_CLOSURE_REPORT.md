# P2-2 Decision Closure Report

- 阶段：**P2-2 Spec v2 Decision Closure**（只读审核 + 经允许的 DECISION.md 追加登记；**未进入 Implementation Planning**）
- 输入：`P2-2_SPEC_v2.md`（含 `### Pending Decision Closure`）、`DECISION.md`、F8 Runtime Governance Spec（§5.3/§6.1/§10.1/§10.2）、F9/Phase-2 冻结条款（`PROJECT_CONTEXT.md` §4/§8）、P2-1 决策与实现、当前 runtime governance 代码
- 本轮约束遵守：未建 branch、未改代码/migration/test/frontend、未 commit/push/merge、未生成 Implementation Plan、未提前规划代码文件

---

## Decision Status

### Blocking

#### D5 — 终态映射（restart vs orphan）· **Approved**

- **理由**：F8 §6.1/§10.2 已冻结两种语义：进程 shutdown/restart 收敛 = `aborted`；台账与实际执行者脱节 = `orphan_reclaimed`。二者语义确实不同（进程世代问题 vs 台账/执行者不一致），合并为单一终态会丢失可审计语义，且偏离冻结文字。P2-1 语境中「统一复用 `orphan_reclaimed`」的表述与 F8 冲突，按 AGENTS §1 显式裁决为采用 F8 拆分。
- **不构成冲突的部分**：P2-1 语境所称「复用既有终态、不新增 TaskStatus」依然成立（`aborted`/`orphan_reclaimed` 均为既有终态）。
- **已登记**：`D-Phase2-P2-2-003`。

### Architecture

#### D13 — 心跳首拍时机 · **Approved**

- **理由**：heartbeat 语义 = 「执行者存活」。`submit_task` 仅表示受理，其后才 `create_task`/`ensure_future`/注册 handle；若在 submit 写首拍，则 `execute()` 早期失败（store 读失败 → `GovernanceControllerError`）留下的 running 行会伪装成「有心跳」，且把 stale 基准前移到尚未开始执行的时刻。执行起点（handle 注册后）写首拍使「心跳存在」成为「曾进入 governed 执行」的证据，与 D18 证据条件互补。
- **已登记**：`D-Phase2-P2-2-004`。

#### D14 — RuntimeHealthScanner 归属与抽象 · **Approved**

- **理由**：扫描/分类/收敛/清理逻辑内联 `server.py` 会使 API 层承担 Runtime 逻辑、不可注入 clock、不可复用；抽象为独立 runtime 组件后 server 仅保留生命周期接线（构造 → startup sweep → periodic task → shutdown cancel）。`SC-1`–`SC-4`（必须复用 `get_controller()` 进程级单例、禁止新建 controller、store 不可用即停用）为硬约束：handle registry 是进程内权威，新建 controller 会导致 `_handles` 恒空 → 同进程全量误回收（🔴 高危）。
- **边界保留**：文件/模块命名属 Planning 决策，**本次 Closure 不冻结**（不提前规划代码文件）。

#### D15 — owner 三分法中 CURRENT 的收敛条件 · **Need Revision**

- **问题**：v2 对 `SAME_HOST_PREV`（同主机、异 PID ⇒ 上一世代）采用「可立即收敛」，仅对 `CURRENT` 叠加心跳陈旧条件。该设计在**蓝绿/滚动部署重叠窗口**下不安全：旧进程仍在运行且持续写心跳（owner 为旧 PID、同主机），新进程 startup sweep 会立即把它标记为上一世代并 abort → **误杀在途任务**。
- **要求的修订（最小、复用既有参数，不新增能力）**：`SAME_HOST_PREV` 收敛前同样**必须**满足 liveness 证据：
  `now - last_heartbeat_at > max(3 × RUNTIME_HEARTBEAT_INTERVAL, 15s)`；
  无健康行（legacy）→ 回退 `now - max(started_at, created_at) > 同阈值`。
  `CURRENT` 维持 v2（需执行 deadline 阈值 `wall_clock + grace`，因 env 常量 owner 场景无法与活进程区分）。
- **效果**：真死进程（停止心跳）在 ~15s 级别即可收敛（远快于等 `wall_clock+grace` ≈ 630s）；活着的重叠进程因持续心跳而不会被误杀；`FOREIGN` 继续只观测。
- **未登记**：待 Spec 修订后作为 `D-Phase2-P2-2-010`（拟）提交批准。

#### D17 — 健康行清理责任方 · **Approved**

- **理由**：清理若放在 controller/funnel，会把 health 平面写操作引入 lifecycle 权威模块，违反 F8 字段/职责边界；由 scanner 清理（终态行 + 孤儿行，滞后 ≤ 1 周期）保持 lifecycle 模块单一职责。`off` 模式停写心跳 ⇒ 无清理者也不会增长（模式矩阵自洽）。
- **已登记**：`D-Phase2-P2-2-007`。

#### D18 — registry 脱节收敛证据条件 · **Approved**

- **理由**：仅凭「无 handle + age」会在事件循环被阻塞时误回收「已受理、尚未注册 handle」的任务；`start_task()` 失败非致命（`started_at` 可能为空），故必须用「心跳行存在 **或** `started_at` 非空」的析取条件 + 超时倍数。该条件同时关闭「心跳写长期失败」造成的误判，且对同进程活跃任务另有 P1（handle 存活）兜底。
- **已登记**：`D-Phase2-P2-2-006`。

### Non-blocking

#### D16 — reclaim 诊断载体 · **Approved**

- **理由**：扩展 `events.lifecycle_event` 的 payload 会触碰冻结 observation 模块；`error` 字符串（≤800 字符）已足够承载 trigger/owner_class/last_heartbeat/threshold/age，且不破坏 replay 契约。结构化诊断留 P2-4 另行决策。
- **已登记**：`D-Phase2-P2-2-008`。

#### D19 — 跨进程重复 terminal event · **Approved（作为「本批不硬化」的范围裁决）**

- **理由**：单实例 supported envelope（F8 §5.3）内 exactly-once 已由内存裁决 + CAS 保证；跨进程 CAS 败者重复发布事件属**不支持场景**，硬化需改动冻结 controller（`controller.py` L316→L319-326 采纳路径标记）。本批不做、显式记录为已知限制，避免越界修改冻结面。
- **已登记**：`D-Phase2-P2-2-009`。

### Resolution Summary

| Gate | 状态 | 登记 |
|---|---|---|
| D5 | **Approved** | `D-Phase2-P2-2-003` |
| D13 | **Approved** | `D-Phase2-P2-2-004` |
| D14 | **Approved** | `D-Phase2-P2-2-005` |
| D15 | **Need Revision** | 未登记（待修订后作为拟 `D-Phase2-P2-2-010`） |
| D17 | **Approved** | `D-Phase2-P2-2-007` |
| D18 | **Approved** | `D-Phase2-P2-2-006` |
| D16 | **Approved** | `D-Phase2-P2-2-008` |
| D19 | **Approved**（不硬化） | `D-Phase2-P2-2-009` |
| D1/D2/D4/D10 | **Resolved as corollary**（见下） | 由 001/002/003 覆盖（待 ratify） |
| D3/D6/D7/D8/D9/D11/D12 | **Awaiting explicit confirmation**（本轮未裁决） | 未登记 |

**Corollary 说明（不做隐蔽裁决，仅陈述推论）**：
- `D1`（存储平面）：`D-Phase2-P2-2-001` 已批准「独立 Runtime Health Plane」⇒ 存储不得落在 `governance_tasks`（否则违反 F8 字段权威）⇒ 方案 A 为其必然实现；方案 B 与 001 冲突。
- `D2`（阈值基准）：`D-Phase2-P2-2-002` 已批准 `wall_clock + grace` ⇒ 逐任务 `effective_limits` 为主基准（全局常量会在显式长任务下误回收）。
- `D4`（foreign owner）：D15 的 `FOREIGN = 只观测不收敛` 已覆盖（F8 §10.2 不得误杀其它实例）。
- `D10`（健康行清理）：D17 已裁决（scanner 负责，即「终态/孤儿即删」），D10 随之关闭。

**冻结约束冲突的显式裁决（AGENTS §1）**：
- F8 §5.3 将「execution owner / lease / heartbeat 机制」排除在 v1 之外；`PROJECT_CONTEXT` §4 与 §8 亦出现 `lease-heartbeat`/multi-instance 禁止项。
- 裁决：该禁令针对**多实例 ownership/互斥（lease/fencing）**；P2-2 的 heartbeat 是**单实例健康遥测**，不提供 lease/fencing/所有权仲裁，不改变 F8 §5.3 single-instance 假设，不新增第二 Runtime 控制面（F8 controller 仍为唯一 lifecycle 权威）。该裁决已写入 `D-Phase2-P2-2-001` 的 Conflict Resolution 字段，待用户 ratify。

---

## Spec Impact

### 已冻结条款（本轮 Closure 后不再改动，除非新决策）

| Spec 条款 | 冻结内容 |
|---|---|
| §4.0（R1） | heartbeat = Runtime Health Telemetry；非 lease/fencing；不改 F8 §5.3 |
| §4.6（R2） | `LA-1`–`LA-4`：只写 health plane / reclaim 只经 `finalize_with_event` / health 不进 status / 清理不属 controller |
| §4.3(1)（D13） | 首拍 = `_execute_governed` handle 注册后；submit 路径不写 |
| §5.4（P10）+ §6.1（D18） | registry 脱节证据条件（MIN_AGE ∧（心跳 ∨ started_at）∧ 超时倍数） |
| §5.5（D15，部分） | 三分法分类定义（CURRENT / SAME_HOST_PREV / FOREIGN）与 `FOREIGN = 只观测`；**SAME_HOST_PREV 收敛条件待修订** |
| §6.1/§6.2（D5） | 终态映射：restart→`aborted`；stale/registry→`orphan_reclaimed` |
| §6.3（D19） | exactly-once 限定单实例 envelope；跨进程重复事件为已知限制 |
| §7.0（SC-1…SC-4） | scanner MUST 复用 `get_controller()`；禁止新建 controller；不可用即停用 |
| §7.1（D14） | 组件边界（scanner 独立、server 仅接线、controller 不依赖 scanner）；**文件命名不冻结** |
| §12.2/§13.1（D17） | 健康行清理责任 = scanner；模式矩阵（off 停写心跳、detect 只判、enforce 全功能） |
| §5.3/§10（D16） | 可注入 clock；诊断载体 = `error` 字符串（不改 events payload） |

### 后续 Implementation 必须遵守的 Invariant

| ID | Invariant |
|---|---|
| I1 | F8 controller 仍是**唯一** lifecycle/terminal 权威；health 平面不参与 lifecycle 决策 |
| I2 | heartbeat 写入只触碰 health plane；`governance_tasks` 的 `status/version/terminal_*/finished_at/counters_snapshot` 不被 health 路径写 |
| I3 | 一切 reclaim 收敛必须经 `controller.finalize_with_event(...)`；禁止直调 `terminal_update` 或任何绕过 funnel 的写 |
| I4 | health 态（healthy/stale/expired）永不写入 `TaskStatus`/`status`，仅派生只读视图 |
| I5 | scanner 必须来自 `get_controller()` 单例；同一进程内不得存在第二个 controller 实例参与判定 |
| I6 | 收敛前置防护 P1–P11 全部成立（含最小年龄、双阶段确认、证据条件、每周期上限、单飞）；任一不成立 → 保持 running 并记录 |
| I7 | owner 分类三分法；`FOREIGN` 永不收敛；`CURRENT` 与（修订后）`SAME_HOST_PREV` 必须叠加 liveness/deadline 证据 |
| I8 | 终态选择由触发源决定（startup→`aborted`；stale/registry→`orphan_reclaimed`），不由调用方自由选择 |
| I9 | exactly-once event 声明只能覆盖单实例 envelope；跨进程重复事件作为已知限制如实记录 |
| I10 | 新增模块必须可注入 clock；时间判定一律 Python 侧（避免方言差异） |
| I11 | 健康行清理仅由 scanner 执行；`off` 模式不写心跳 |
| I12 | 不新增 TaskStatus、不改 default policy 语义/默认阈值、不新增 HTTP API、不动 frontend 与 `app/agent/**`、`app/research/**` |

### 需随 D15 修订同步的 Spec 位置（仅列条款，不含实现规划）

- §5.5 分类行为表（`SAME_HOST_PREV` 行 → 追加 liveness 证据条件）
- §6.1 触发条件表（startup 行）
- §7.3 startup sweep 参数（沿用 liveness 阈值）
- §14 F11（同机双进程/滚动部署误杀 → 缓解措施更新）

---

## DECISION.md Impact

- **是否需要登记**：**需要**（Blocking + Architecture + Non-blocking 均已裁决；P2-2 属 runtime lifecycle/schema 级变更，按 DECISION.md 纪律必须留痕）。
- **本轮执行**：**仅追加**（append-only），未修改任何历史 Decision（D001–D019、`D-Phase2-P2-1-001` 全文保持原样）。
- **已追加记录**：

| 记录 ID | 内容 | 来源 Gate |
|---|---|---|
| `D-Phase2-P2-2-001` | Runtime Health Plane：heartbeat = 健康遥测；stale 派生态；含 F8 §5.3 / F9 `lease-heartbeat` 冻结冲突的显式裁决 | 用户既有裁决 + 本 Closure |
| `D-Phase2-P2-2-002` | 阈值与配置默认值（600s / 30s / env 可覆盖，默认不变） | 用户既有裁决 |
| `D-Phase2-P2-2-003` | 终态映射：restart→`aborted`；stale/registry→`orphan_reclaimed` | **D5** |
| `D-Phase2-P2-2-004` | 首拍时机 = `_execute_governed` 执行起点 | **D13** |
| `D-Phase2-P2-2-005` | `RuntimeHealthScanner` 组件边界 + `SC-1`–`SC-4` | **D14** |
| `D-Phase2-P2-2-006` | registry 脱节收敛证据条件 | **D18** |
| `D-Phase2-P2-2-007` | 健康行清理责任 = scanner；`off` 停写心跳 | **D17** |
| `D-Phase2-P2-2-008` | 诊断载体 = `error` 字符串（不改 events payload） | **D16** |
| `D-Phase2-P2-2-009` | 跨进程重复 terminal event = 已知限制，本批不硬化 | **D19** |

- **未登记（有意）**：
  - **D15**（Need Revision）——待 Spec 修订并经批准后，作为拟 `D-Phase2-P2-2-010` 追加；
  - **D3 / D6 / D7 / D8 / D9 / D11 / D12**——仍处 awaiting explicit confirmation，未裁决即不登记。
- **未修改**：`DECISION.md` 历史条目、`PROBLEM.md`（D11 未决）、冻结 Spec 文档、任何代码/测试/schema。
- **后续**：`D-Phase2-P2-2-001`–`009` 的状态标注为「Accepted（随本报告等待用户 ratify）」；用户 ratify 后即为最终记录（如需再改，仍走 append-only 新条目）。

---

## Implementation Readiness

**NOT READY**

阻塞原因（逐条可验证）：

1. **D15 = Need Revision**：`SAME_HOST_PREV` 的 liveness 证据条件尚未写入 Spec；滚动部署重叠窗口下的误杀风险未闭合（Architecture Gate 未完成）。
2. **Spec 未同步修订**：D15 修订需要回写 §5.5 / §6.1 / §7.3 / §14（本报告仅指出条款，未改 Spec 内容）。
3. **仍有 Gate 未裁决**：`D3`（确认次数）、`D6`（`flush_pending` 交互）、`D7`（startup sweep 时序）、`D8`（扫描间隔）、`D9`（PG 心跳写约束）为 implementation contract 的参数/行为项；`D11`/`D12` 为收尾项。上述均**未被批准**（本报告未自行裁决）。
4. **流程前置未满足**：P2-2 属 L3（runtime lifecycle + additive schema/migration），按 PROCESS §11.3 需 Readiness Review + Decision Closure 通过后方可进入 Implementation Planning；且 Implementation Planning 需**单独批准**（本轮未申请）。

> 已闭合项（供后续引用）：D5、D13、D14、D16、D17、D18、D19 已 Approved；D1/D2/D4/D10 作为已批准决策的必然推论关闭（待 ratify）。当前 Readiness 状态不因这些闭合项改变——**NOT READY**。
