# P2-2 D15 Revision Report（Spec Rev 2.1 + Decision Closure 更新）

- 阶段：**P2-2 Spec v2 · Rev 2.1（Gate D15 liveness 修订）**；Decision Closure 已按批准更新
- 批准输入：用户 2026-10「接受 D15 修订方案；仅回写 Spec；不进入 Implementation Planning；不建 branch；不改代码/migration/test/frontend」
- 约束遵守：**零代码改动、零 migration、零测试改动、零 frontend 改动、零 branch、零 commit/push/merge**；P2-3/P2-4/P2-5 未触碰
- 落盘位置：`P2-2_SPEC_v2.md`（就地更新为 **v2 · Rev 2.1**，单一权威 Spec）；`DECISION.md`（**append-only** 追加 `D-Phase2-P2-2-010`）；本报告为过程留痕（非权威契约）

---

## 1. Spec 修订报告

### 1.1 版本与惯例

| 项 | 值 |
|---|---|
| 文件 | `P2-2_SPEC_v2.md` |
| 版本标识 | **v2 · Rev 2.1**（标题、版本表、修订记录三处同步） |
| 惯例 | 就地修订 + Rev/Change Log（仓库既有 Spec revision convention，先例：F9-P0 spec Rev1）；**未新建 v3**，避免并行权威 |
| v1 文件 | `P2-2_RUNTIME_HEARTBEAT_STALE_RECLAIM_SPEC.md` 仅作已 Review 基线留痕 |

### 1.2 修订记录（Spec 内 Change Log）

- **v2.1（当前权威版；Gate D15 liveness 修订）**：`SAME_HOST_PREV` 取消「立即收敛」，改为必须满足 liveness 证据 `now - last_heartbeat_at > max(3 × RUNTIME_HEARTBEAT_INTERVAL, 15s)`（无健康行 → legacy 回退 `now - max(started_at, created_at) > 同阈值`）；`CURRENT` 维持 deadline 阈值；`FOREIGN` 维持只观测。同步更新 §5.5 / §6.1 / §6.2 / §7.3 / §14 F11 / §20 / §21 / §22；决策登记 `D-Phase2-P2-2-010`（append-only）。动机：防 rolling / blue-green overlap 误杀。
- v2：整合 Review 报告 R1–R11 + Gate D13–D19。
- v1：初版（Review：PASS WITH CHANGES / NOT READY）。

### 1.3 条款级差异（修订前 → 修订后）

| 条款 | 修订前（v2） | 修订后（Rev 2.1） |
|---|---|---|
| §5.5 分类行为表 · `SAME_HOST_PREV` | 「**可立即收敛**（受 P3 最小年龄约束）」 | 「**需满足 `liveness_evidence`**（不再因 owner generation 不同而立即收敛；另受 P3 最小年龄约束）」 |
| §5.5 新增谓词块 | 无 | 冻结 `liveness_evidence(row)`（有健康行 / legacy 回退）与 `deadline_evidence(row)`；**两分类共用同一参数，不新增配置** |
| §5.5 安全不变量 | 无 | 新增 4 条（① overlap 期间不得误杀活旧进程；② 真死 `SAME_HOST_PREV` ~15s 级收敛；③ 不新增 TaskStatus / 不改 F8 lifecycle authority；④ heartbeat 非 lease/fencing） |
| §6.1 Startup sweep 行 | 「owner 分类 ∈ {SAME_HOST_PREV, CURRENT+心跳陈旧超阈}」 | 「`SAME_HOST_PREV` → **`liveness_evidence`**；`CURRENT` → **`deadline_evidence`**；`FOREIGN` → 不参与」+ 新增 Rev 2.1 修订说明段 |
| §6.1 Periodic — heartbeat stale 行 | 「age 超阈」 | 「`deadline_evidence`」（措辞与谓词统一） |
| §6.2 状态转换图 | `startup: SAME_HOST_PREV / CURRENT+心跳陈旧` | `startup: SAME_HOST_PREV+liveness / CURRENT+deadline` |
| §7.3 startup scope 行 | 「仅 SAME_HOST_PREV / CURRENT（后者需叠加心跳陈旧）」 | 「`SAME_HOST_PREV` 需 `liveness_evidence`；`CURRENT` 需 `deadline_evidence`；`FOREIGN` 只统计不收敛」+ 新增「liveness 阈值」行与「误杀防护」行 |
| §14 F11 | 「同机误起双进程（owner 相同）」 | 扩为「rolling / blue-green overlap、同机双进程或 PID 复用」，缓解措施 = liveness（SAME_HOST_PREV）/ deadline（CURRENT）/ FOREIGN 永不收敛 / CAS 唯一 winner |
| §20 / Pending Decision Closure | 全部 awaiting approval | 逐 Gate 标注 ratified / resolved / awaiting；列出 7 项仍 pending |
| §21 / §22 | 「v2 设计（D5/D15/D18 待裁决）」/「ready for approval」 | 「v2.1 设计（…D15 已修订 → `D-Phase2-P2-2-010`）」/「rev 2.1 权威；Readiness = NOT READY + 阻塞项」 |

### 1.4 与批准约束的逐条一致性核验

| 批准约束 | Spec Rev 2.1 实际文本 | 一致 |
|---|---|---|
| `SAME_HOST_PREV` 必须有 liveness evidence：`now - last_heartbeat_at > max(3 × RUNTIME_HEARTBEAT_INTERVAL, 15s)` | §5.5 `liveness_evidence` 定义（有健康行分支）逐字一致 | ✅ |
| 无 health row → `now - max(started_at, created_at) > 同一阈值` | §5.5 legacy fallback 分支逐字一致 | ✅ |
| `FOREIGN` 永不收敛 | §5.5 / §6.1 / §7.3 / §14 F11 四处一致（「只观测、禁止 reclaim」） | ✅ |
| `CURRENT` 保持 deadline 语义 | §5.5 `deadline_evidence` = `effective_limits.wall_clock_timeout + RUNTIME_STALE_GRACE_PERIOD`；§6.1 同步 | ✅ |

---

## 2. D15 修订后的 Decision Closure

| Gate | 状态 | 依据 / 记录 |
|---|---|---|
| **D5** 终态映射（原阻塞项） | ✅ **Closed (ratified)** | F8 §6.1/§10.2 拆分 → `D-Phase2-P2-2-003` |
| **D13** 首拍时机 | ✅ Closed (ratified) | 执行起点（handle 注册后）；submit 不写 → `-004` |
| **D14** Scanner 归属与抽象 | ✅ Closed (ratified) | `RuntimeHealthScanner` + `SC-1`–`SC-4`；文件命名留 Planning → `-005` |
| **D15** owner 三分法收敛条件 | ✅ **Closed (resolved by Rev 2.1)** | `SAME_HOST_PREV` 需 `liveness_evidence`；`CURRENT` 需 `deadline_evidence`；`FOREIGN` 只观测 → `D-Phase2-P2-2-010` |
| **D16** 诊断载体 | ✅ Closed (ratified) | 仅 `error` 字符串；不改 events payload → `-008` |
| **D17** 健康行清理责任 | ✅ Closed (ratified) | scanner 负责；`off` 停写心跳 → `-007` |
| **D18** registry 脱节证据 | ✅ Closed (ratified) | `MIN_AGE ∧（心跳 ∨ started_at）∧ liveness 阈值` → `-006` |
| **D19** 跨进程重复事件 | ✅ Closed (ratified, 不硬化) | 已知限制记录 → `-009` |
| D1 / D2 / D4 / D10 | ✅ Closed（已批准决策的必然推论） | 分别由 `-001` / `-002` / `-001`·D15 / `-007` 覆盖 |
| **D3** 双阶段确认次数 | ⏳ **awaiting explicit confirmation** | 未裁决、未登记 |
| **D6** `flush_pending` 交互 | ⏳ awaiting | 未裁决、未登记 |
| **D7** startup sweep 时序 | ⏳ awaiting | 未裁决、未登记 |
| **D8** 扫描间隔 | ⏳ awaiting | 未裁决、未登记 |
| **D9** PG 心跳写约束 | ⏳ awaiting | 未裁决、未登记 |
| **D11** Problem Registry 时机 | ⏳ awaiting | 未裁决、未登记 |
| **D12** frontend 终态文案 | ⏳ awaiting | 未裁决、未登记 |

**当前冻结的 D15 语义（Rev 2.1 生效，实现必须遵守）**

```text
SAME_HOST_PREV 收敛前置：liveness_evidence(row) 成立
    有 health row ： now - last_heartbeat_at > max(3 × RUNTIME_HEARTBEAT_INTERVAL, 15s)
    无 health row ： now - max(started_at, created_at) > max(3 × RUNTIME_HEARTBEAT_INTERVAL, 15s)
CURRENT 收敛前置：deadline_evidence(row) 成立
    now - max(last_heartbeat_at?, started_at, created_at)
        > effective_limits.wall_clock_timeout + RUNTIME_STALE_GRACE_PERIOD
FOREIGN：永不收敛（只统计/日志）
终态映射不变：startup → aborted；stale/registry → orphan_reclaimed
```

---

## 3. `D-Phase2-P2-2-010`（拟追加内容 = 已登记内容）

> **透明说明**：该条目在**上一轮**（D15 修订执行时）已以 **append-only** 方式追加到 `DECISION.md`
> （未改写任何历史条目）。本轮核验其内容与你批准的约束逐字一致，故**不再重复追加**（避免重复
> Decision）。如需任何调整，仍以 append-only 新条目更正。

**`## D-Phase2-P2-2-010 — owner 三分法收敛条件：SAME_HOST_PREV 必须叠加 liveness 证据（Gate D15 修订）`**

- **Status**: Accepted（用户 2026-10 ratify P2-2 Decision Closure 并批准执行 D15 Spec 修订；登记为 append-only 追加）
- **Context**: v2 设计对 `SAME_HOST_PREV`（同主机、异 PID ⇒ 上一世代）允许「立即收敛」，仅对 `CURRENT` 叠加心跳陈旧条件。该设计在 **rolling / blue-green 部署重叠窗口**下不安全：旧进程仍在运行且持续写心跳（owner 为旧 PID、同主机），新进程 startup sweep 会立即把它标记为上一世代并 `aborted` → **误杀在途任务**。
- **Decision**: `SAME_HOST_PREV` **不得**因 owner generation 不同而立即 reclaim；收敛前必须满足 **liveness 证据**：`now - last_heartbeat_at > max(3 × RUNTIME_HEARTBEAT_INTERVAL, 15s)`；若无 heartbeat health row（legacy）→ 回退 `now - max(started_at, created_at) > 同阈值`。`CURRENT` 维持既有规则（`deadline_evidence` = `wall_clock_timeout + stale grace`）；`FOREIGN` 继续只观测、**禁止 reclaim**。三分法分类本身（CURRENT / SAME_HOST_PREV / FOREIGN）保持不变。
- **核心安全不变量**: ① 活跃的旧进程在 rolling / blue-green overlap 期间**不得**被新进程 startup sweep 误杀；② 真正停止心跳的 `SAME_HOST_PREV` 任务仍可在 **~15s 级**进入 reclaim；③ 不新增 TaskStatus；④ 不改变 F8 lifecycle authority；⑤ heartbeat 仍然只是 Runtime Health Telemetry，**不是 lease / fencing**。
- **Alternatives（拒）**: 维持「立即收敛」（重叠部署误杀）；统一要求 `deadline_evidence`（真死进程需等 ~630s，恢复过慢）；引入 lease/心跳互斥（超出 P2-2 范围且违反 F8 §5.3）。
- **Consequences**: restart 场景遗留任务收敛延迟由「立即」变为「最后一次心跳 + ~15s」；换来重叠部署期间不误杀在途任务。`liveness` 阈值复用 D18 已有参数，**不新增配置项**。
- **Constraints Created**: `SAME_HOST_PREV` 的 startup/periodic 收敛均须先判定 `liveness_evidence`；该证据同时受 P3 最小年龄守卫约束；`FOREIGN` 永远只统计。
- **Related Problems**: 无新增
- **Related Architecture**: `P2-2_SPEC_v2.md` **Rev 2.1** §5.5 / §6.1 / §6.2 / §7.3 / §14 F11；F8 §5.3/§10.2 e。
- **Ratification Note**: 用户已于 2026-10 ratify `D-Phase2-P2-2-001`–`009`（append-only：不改写既有条目状态字段）；`D3/D6/D7/D8/D9/D11/D12` 仍 awaiting explicit confirmation，未登记。

---

## 4. 更新后的 Implementation Readiness

**NOT READY**

保持不变的原因（D15 已关闭，但以下仍未闭合）：

| # | 阻塞项 | 说明 |
|---|---|---|
| 1 | **D3** 双阶段确认次数 | false-positive 防护强度参数未裁决 |
| 2 | **D6** `flush_pending` 交互 | 是否接线未裁决（当前默认「不接线」仅为本报告建议） |
| 3 | **D7** startup sweep 时序 | 方案 A（yield 前，有界 + 预算 + fail-open）vs 方案 B 未裁决（影响 readiness/一致性语义） |
| 4 | **D8** 扫描间隔 | 30s + 抖动未裁决（检出延迟/开销） |
| 5 | **D9** PG 心跳写约束 | 节流 + 抖动、不引连接池未裁决（PG 事件循环开销） |
| 6 | **D11** Problem Registry 时机 | 未裁决（本轮未改 PROBLEM.md） |
| 7 | **D12** frontend 终态文案 | 未裁决（本批禁改 frontend） |
| 8 | **流程前置** | P2-2 属 L3：需 Readiness Review + Decision Closure 全部闭合，且 **Implementation Planning 需单独批准**（本轮未申请） |

> 已闭合：D5、D13、D14、D15、D16、D17、D18、D19（+ D1/D2/D4/D10 推论关闭）。
> 一旦 D3/D6/D7/D8/D9/D11/D12 裁决完成，可在**单独批准**下以 append-only 登记缺失的 Decision（拟 `D-Phase2-P2-2-011…`）并进入 Implementation Planning。

---

## 5. 本轮 Scope 确认

- ✅ 零代码改动（`app/**` diff 空）；✅ 零 migration（`db/**` 未触碰、未创建 0003）
- ✅ 零测试改动（`tests/**` 空）；✅ 零 frontend 改动；`app/agent/**`、`app/research/**` 未触碰
- ✅ 零 branch 创建（分支仍 `main`）；✅ 零 commit / push / merge（HEAD 仍 `98e4e5a`）
- 本轮写入仅文档：`P2-2_D15_REVISION_REPORT.md`（本报告）；`P2-2_SPEC_v2.md`（Rev 2.1，上一轮已完成）；`DECISION.md`（`D-Phase2-P2-2-010`，上一轮 append-only 已登记）
- 未进入 Implementation Planning；未生成 Implementation Plan；未提前规划代码文件
