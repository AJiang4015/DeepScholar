# P2-2 Decision Closure + Readiness Review

- 阶段：**P2-2 Decision Closure 完成 → Readiness Review**
- 批准输入：用户 2026-10 批准 D3/D6/D7/D8/D9/D11/D12 提案（并授权 append-only 登记 + Spec 同步）
- 本轮约束遵守：未进入 Implementation Planning；未建 branch；未改代码 / migration / 测试 / frontend；未 commit / push / merge
- 落盘：`DECISION.md`（**append-only** 追加 `D-Phase2-P2-2-011…017`，未改写任何历史条目）；`P2-2_SPEC_v2.md`（就地修订 → **v2 · Rev 2.2**，含 Change Log）

---

## 1. Decision Closure（全部闭合）

| 拟/实 ID | Gate | 决议 | 状态 |
|---|---|---|---|
| `D-Phase2-P2-2-001` | — | Runtime Health Plane；heartbeat = 健康遥测；stale 派生态；F8 §5.3 / F9 `lease-heartbeat` 冻结冲突裁决 | ✅ Accepted |
| `-002` | — | 阈值与配置默认值（600s / 30s；env 可覆盖，默认不变） | ✅ Accepted |
| `-003` | **D5** | 终态映射：restart→`aborted`；stale/registry→`orphan_reclaimed` | ✅ Accepted |
| `-004` | **D13** | 首拍 = `_execute_governed` 执行起点（handle 注册后）；submit 不写 | ✅ Accepted |
| `-005` | **D14** | `RuntimeHealthScanner` 组件边界 + `SC-1`–`SC-4`（必须复用 `get_controller()`） | ✅ Accepted |
| `-006` | **D18** | registry 脱节证据条件（MIN_AGE ∧ 证据 ∧ liveness 阈值） | ✅ Accepted |
| `-007` | **D17** | 健康行清理责任 = scanner；`off` 停写心跳 | ✅ Accepted |
| `-008` | **D16** | 诊断载体 = `error` 字符串（不改 events payload） | ✅ Accepted |
| `-009` | **D19** | 跨进程重复 terminal event = 已知限制，本批不硬化 | ✅ Accepted |
| `-010` | **D15** | `SAME_HOST_PREV` 必须叠加 liveness 证据（Rev 2.1） | ✅ Accepted |
| **`-011`** | **D3** | 双阶段确认默认 **2**（下限 1 需显式 opt-in 并标注 `aggressive`；启动 sweep 不适用） | ✅ **本轮登记** |
| **`-012`** | **D6** | `flush_pending` 本批不接线；`shutdown flush` 记后续候选；pending→sweep 语义漂移记为已知限制 | ✅ **本轮登记** |
| **`-013`** | **D7** | startup sweep = `yield` 前有界 sweep（batch ≤200 / 预算 ≤2s / fail-open）；显式记录对 F8 §7.3 c3 的取舍（不 fail-fast） | ✅ **本轮登记** |
| **`-014`** | **D8** | scanner interval 默认 **30s → 15s** + ±20% 抖动（单飞 + 空闲跳过；30s 亦可接受） | ✅ **本轮登记** |
| **`-015`** | **D9** | PG 心跳：逐拍节流 + 抖动；**不引连接池、不新增依赖**；单语句短事务；批量 writer 为应急候选 | ✅ **本轮登记** |
| **`-016`** | **D11** | Problem 登记时机 = P2-2 Implementation 起点（题名/Type/Severity/关联已固定） | ✅ **本轮登记** |
| **`-017`** | **D12** | 本批零 frontend 改动；含「文案缺口」表述的证据更正 | ✅ **本轮登记** |
| D1 / D2 / D4 / D10 | — | 推论关闭（存储平面 A / 逐任务阈值 / FOREIGN 只观测 / 清理责任） | ✅ Closed |

**结论：D1–D19 全部闭合；`Pending Decision Closure` 清空（无剩余待裁 Gate）。**

### 1.1 Spec 同步清单（Rev 2.2 变更点）

| 条款 | 变更 |
|---|---|
| 标题 / 版本表 / 修订记录 | v2 → **v2 · Rev 2.2**（新增 v2.2 Change Log 条目） |
| §5.2 | 追加批准注记（D3：2 次；下限 1 需 opt-in；启动 sweep 不适用确认次数） |
| §6.5 | 追加第 7 条（D6：不接线 `flush_pending`；shutdown flush 记候选；语义漂移为已知限制） |
| §7.2 | 方案 A 标注已批准（D7）+ c3 取舍记录 |
| §8 | 周期扫描默认 **15s** + ±20% 抖动（D8） |
| §9 | Frontend 行更正：`aborted`/`orphan_reclaimed` 标签与提示**已存在**；真实缺口仅为不展示 `error` 原因（D12） |
| §11 | PG 成本行更新（D9：不引连接池/依赖；单语句短事务；批量写应急候选；F8 §7.2 约束） |
| §13 | `RUNTIME_STALE_CONFIRMATIONS`（2 + opt-in 注记）、`RUNTIME_SCAN_INTERVAL`（**15**） |
| §20 / Pending Decision Closure | 7 项 Gate 状态改 ✅ ratified；pending 表清空并改为「已闭合」表 |
| §21 / §22 | 配置面行、前端行、Status/Readiness 段同步 |

---

## 2. Readiness Review

### 2.1 两层判定（避免混用）

| 层级 | 判定 | 依据 |
|---|---|---|
| **Ready for Implementation Planning** | ✅ **READY** | 全部 19 项 Decision Gate 已闭合并登记；Spec Rev 2.2 文本自洽（含 7 项批准落地）；无遗留架构/参数待裁项 |
| **Ready for Implementation** | ❌ **NOT READY** | L3 流程尚未走完：① Implementation Plan 未产出（需单独批准进入 Planning）；② `feature/runtime-p2-2-…` 分支未创建（Implementation 起点动作）；③ D11 的 Problem 登记尚未执行（非阻塞，但计划中需列为动作） |

### 2.2 进入 Implementation Planning 的前置（已满足 / 待办）

| # | 前置 | 状态 |
|---|---|---|
| 1 | Spec Review PASS WITH CHANGES → 反馈回写（v2） | ✅ |
| 2 | Decision Closure（D5/D13–D19） | ✅ |
| 3 | D15 修订 + 登记 | ✅ |
| 4 | 剩余 7 项 Gate 批准 + 登记（011–017） | ✅ |
| 5 | Spec Rev 2.2 与 Decision 一致 | ✅ |
| 6 | **用户单独批准进入 Implementation Planning** | ⏳ 待批准（本报告不代替该批准） |

### 2.3 Planning 阶段应纳入的待办（非 Decision 类，供下一步使用）

1. **D11 Problem 登记**：Implementation 起点执行（`docs/problem/P0NN-*.md` + `PROBLEM.md` 索引行）——文档变更需单独批准；
2. **Spec 冻结基线**：P2-2 实现须以 `P2-2_SPEC_v2.md` **Rev 2.2** 为唯一权威输入；
3. **迁移影响清单**（Spec §12.3）：4 处 `applied_migration_versions` 断言需随 `0003` 更新；方案 A 下 `TASK_COLUMNS`/PG 列类型断言**不动**；
4. **实现顺序建议**（Planning 阶段细化，不在本报告展开）：heartbeat/health 平面 → 首拍接线 → owner 分类 + 证据谓词 → startup sweep → periodic scanner → 清理/模式矩阵 → 测试与回归；
5. **后续候选（不在 P2-2 范围）**：`shutdown flush`（D6 候选）、前端 reclaim reason 展示（D12 候选）、PG 批量心跳 writer（D9 应急）、跨进程重复事件硬化（D19，仅多实例议题重启时评估）、Runtime Policy Tuning（独立批次）。

### 2.4 已冻结的 P2-2 invariant（实现必须遵守）

I1–I12（Spec §"后续 Implementation 必须遵守的 Invariant"）保持有效；本次新增/强化者：

- **I13**（D3）：启动 sweep 不适用确认次数，其防护由证据谓词 + owner 分类 + 最小年龄承担；
- **I14**（D6）：任何未来 flush 调用点必须 fail-open 且不得阻断 control；
- **I15**（D7）：startup sweep 有界（batch/预算）+ fail-open；store 不可用 → 跳过（不 fail-fast，取舍已记录）；
- **I16**（D8）：间隔与抖动为进程随机相位；验收需写明最坏检出延迟公式（registry ≈ min_age + 2×interval）；
- **I17**（D9）：心跳写不引连接池/依赖、单语句短事务、fail-open；
- **I18**（D12）：本批零 frontend 改动。

---

## 3. 本轮 Scope 确认与证据

| 项 | 结果 |
|---|---|
| 代码 / migration / 测试 / frontend | ✅ 零改动（`git diff --name-only -- app db tests frontend` = 空） |
| branch | ✅ 未创建（仍 `main`） |
| commit / push / merge | ✅ 未执行（HEAD 仍 `98e4e5a`） |
| `DECISION.md` | ✅ **仅 append**（011–017；历史条目 D001–D019 与 `-001…-010` 未改写） |
| `P2-2_SPEC_v2.md` | ✅ 就地修订至 Rev 2.2（无并行权威文件；v1 仍为历史基线） |
| Implementation Planning | ✅ 未进入（未产出 Implementation Plan） |
| P2-3 / P2-4 / P2-5 | ✅ 未触碰 |

> 提示：本轮所有变更仍在 **未提交** 状态；commit / push / merge 均需单独批准（AGENTS §10.3/§10.6、PROCESS §12.4）。

---

## 4. 结论

- **P2-2 Decision Closure：COMPLETE**（19/19 Gate 闭合，决策已 append-only 登记，Spec Rev 2.2 同步完成）。
- **Ready for Implementation Planning：READY**（等待用户单独批准开启 Planning）。
- **Ready for Implementation：NOT READY**（Planning → Implementation → Verification → Freeze 尚未开始）。
