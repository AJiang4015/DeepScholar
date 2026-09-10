# P2-2 Pending Decision Proposal（D3 / D6 / D7 / D8 / D9 / D11 / D12）

- 阶段：**P2-2 Decision Closure — 提案（proposed / awaiting approval）**
- 定位：本文件是**待批准提案**，不是裁决、不是规范来源；批准后按 **append-only** 追加至 `DECISION.md`（拟 ID `D-Phase2-P2-2-011…017`）
- 分析基础：`P2-2_REMAINING_DECISION_REVIEW.md`（同批分析，不构成并行权威）、`P2-2_SPEC_v2.md` **Rev 2.1**、`DECISION.md`（`D-Phase2-P2-2-001…010`）、F8 Spec（§5.3/§6.1/§7.2/§7.3/§10.2）、`PROJECT_CONTEXT.md` §4/§8、`AGENTS.md` §4/§11、当前代码与前端
- 本轮约束遵守：**未修改 Spec 正文**、**未修改 `DECISION.md`**、未改代码/migration/测试/frontend、未建 branch、未 commit/push/merge、未进入 Implementation Planning、**未自行裁决**
- **Implementation Readiness = NOT READY**（批准 D7/D9 并确认其余处置后才可重评）

---

## 0. 提案总览

| 拟 Decision ID | Gate | 议题 | 推荐（待批准） | 影响 F8/F9 冻结 | Blocking Planning |
|---|---|---|---|---|---|
| `D-Phase2-P2-2-011` | **D3** | 双阶段确认次数 | **2 次**（下限 1，需显式 opt-in） | 否（F8 未冻结此参数） | 否 |
| `D-Phase2-P2-2-012` | **D6** | `flush_pending` 是否接线 | **本批不接线**（shutdown flush 记为后续候选） | 否（不改 pending 冻结语义，仅决定调用点） | 否 |
| `D-Phase2-P2-2-013` | **D7** | startup sweep 时序 | **方案 A：yield 前有界 sweep**（batch ≤200、预算 ≤2s、fail-open、store 不可用则跳过） | 否（直接实现 F8 §10.2 e；须写明与 §7.3 c3 的取舍） | **是** |
| `D-Phase2-P2-2-014` | **D8** | scanner interval | **15s + ±20% 抖动**（空闲跳过；30s 亦可接受） | 否（F8 示例值即 15s，无强制） | 否 |
| `D-Phase2-P2-2-015` | **D9** | heartbeat PG 写约束 | **逐拍节流 + 抖动；不引连接池、不加依赖、单语句短事务**（批量写列为应急候选） | 否（须遵守 F8 §7.2 control/observation 分离） | **是（护栏）** |
| `D-Phase2-P2-2-016` | **D11** | Problem Registry 时机 | **本轮不登记；Implementation 起点登记正式 Problem**（题名/Type 现已固定） | 否（Harness 层，与 F8/F9 无关） | 否 |
| `D-Phase2-P2-2-017` | **D12** | frontend terminal 文案策略 | **本批零 frontend 改动**（并更正「文案缺口」表述；reason 展示为可选后续项） | 否（仅呈现层，不触 WS/HTTP schema） | 否 |

---

## 1. D3 — 双阶段确认次数（拟 `D-Phase2-P2-2-011`）

**当前问题**
周期扫描对 stale 任务是否要求「连续 N 次判定」才允许 reclaim？该值决定「误回收防护强度」与「收敛延迟」的权衡；启动期 sweep 为单遍执行，无法套用确认次数，须由证据谓词（`liveness_evidence` / `deadline_evidence`）承担同等职责。

**可选方案**

| 方案 | 内容 |
|---|---|
| A | **2 次连续确认**（周期路径）；单次仅记 stale 视图 + 日志 |
| B | 1 次（最低延迟） |
| C | 3 次（最保守） |
| D | 自适应（registry 脱节 2 次 / heartbeat stale 1 次） |

**推荐方案**：**A（2 次）**；`RUNTIME_STALE_CONFIRMATIONS` 下限 1，取 1 须显式 opt-in 并在日志标注 `aggressive`；不采用 D（两套阈值带来复杂度，收益不明确）。

**风险**
- 低延迟场景（希望尽快收敛）会多等 1 个扫描周期（15–30s）——方向安全。
- 误判概率不会因确认次数降为零：极端情况下「心跳写长期失败 + 无 handle」仍可能触发；由 P1（handle 存活）/P2（内存裁决）/P3（最小年龄）/证据谓词共同约束。
- 启动路径与周期路径的**严格度不对称**（单遍 vs 双确认）需在 Spec 中保持明示，避免读者误以为启动 sweep 也做双确认。

**是否影响 F8/F9 冻结**：**否**。F8 §10.2 只要求 sweeper 存在、判定确定、回收幂等，未规定确认次数；不触碰 funnel/CAS/终态语义。F9 无关。

**拟追加 Decision 草案**：确认次数默认 2（下限 1，1 需 opt-in）；确认计数为进程内状态、重启清零（更保守）；启动 sweep 不适用确认次数，改由证据谓词约束。

---

## 2. D6 — `flush_pending` 是否接线（拟 `D-Phase2-P2-2-012`）

**当前问题**
`controller.flush_pending()`（P2-1 前既有方法，无生产调用方）是否在 P2-2 接线？抉择点：进程内 pending 裁决（内存已裁决、DB 写失败）如何被最终持久化。

**可选方案**

| 方案 | 内容 | 关键事实 |
|---|---|---|
| A | **本批不接线** | pending 的 DB 行在进程死亡后由 startup/periodic sweep 收敛（可能为 `aborted`/`orphan_reclaimed`，非原裁决） |
| B | startup 顺带 flush | **在新进程中恒为空操作**：`_pending_order`/`_decisions` 均为内存态，随进程消失 |
| C | shutdown 收尾有界 flush | 唯一有实效的变体：给内存裁决最后一次落库机会（需硬超时 + fail-open） |
| D | 周期 reconciler（如 15s） | 进入 F8 §10.2 文档化 future 区域，新增与 scanner 平行的周期组件 → scope 外扩 |

**推荐方案**：**A（本批不接线）**；将 **C** 登记为 P2-3/P2-4 候选（若用户希望在 P2-2 内一并覆盖，应以显式 scope 例外批准，并限定「硬超时 + fail-open + 复用既有 retry 阶梯」）。

**风险**
- 不接线的降级结果 = **终态语义漂移**（原本可能 `completed` 的裁决最终被收敛为 `aborted`）；方向保守、可审计（`error` 字符串含 reclaim trigger），但丢失原裁决原因。
- 若接线 C：shutdown 期 DB 不可用可能延长关闭过程 → 必须有硬超时，否则影响部署/回滚时延。
- 方案 B 若被误选，会形成「看似已接线、实际无效」的假安全感 → 明确拒绝。

**是否影响 F8/F9 冻结**：**否**（仅决定调用点）。必须遵守 F8 §7.2：任何 flush 调用点为 observation/durability 行为，失败不得阻断 control；pending 的内存裁决权威/retry 阶梯/degraded 标记语义均不改。F9 无关。

**拟追加 Decision 草案**：本批不接线 `flush_pending`；记录「pending → sweep 收敛导致终态语义漂移」为已知限制；shutdown flush 作为后续批次候选（需独立批准）。

---

## 3. D7 — startup sweep 时序（拟 `D-Phase2-P2-2-013`）

**当前问题**
启动期收敛遗留 `running` 行，应发生在 `lifespan` 的 `yield` 之前（阻塞 readiness）还是之后（后台执行）？

**可选方案**

| 方案 | 内容 |
|---|---|
| A | `init（migration/store）→ startup sweep（有界 + 预算）→ yield` |
| B | `init → yield → background startup sweep` |
| C | 混合：yield 前极小快速通道（≤50 行 / ≤500ms），余量后台 |

**推荐方案**：**A**（`RUNTIME_SCAN_BATCH_LIMIT=200`、`RUNTIME_STARTUP_SWEEP_BUDGET=2s`、fail-open、store 不可用则跳过并记日志）。C 作为未来规模化的演进选项（当前遗留行数 ≈ 崩溃时在途任务数，通常 <10）。

**风险**
- A：增加启动延迟（有界）；若 store 初始化慢或迁移大事务，预算机制必须真正生效（超时即转后台/放弃本遍），否则会拖慢 readiness。
- B：窗口内旧行对前端可见（「研搜中」假态），且新 submit 会同 thread 竞争 → 旧行可能被 `cancel_governed` 收敛为 `cancelled`，偏离 F8 §6.1 的 restart 语义 `aborted`，并可能触发 session archive 守卫 409。
- C：双路径判定增加实现与测试复杂度。
- 与现状耦合：当前 `lifespan` 已 `await get_main_agent()`（组装 agent + checkpointer，重）→ 说明启动期存在重量级操作是既有模式；A 的相对开销可忽略。

**是否影响 F8/F9 冻结**：**否**（实现 F8 既定语义 §10.2 e：startup sweep → `aborted`、不自动 resume）。需显式记录一项取舍：F8 §7.3 (c3) 规定「startup governance store 不可用 → fail-fast」，而本方案选择「跳过 sweep、服务继续（提交侧由 c1 返回 503）」。**建议在批准文本中明确该取舍**（若用户要求完全对齐 c3，则改为启动 fail-fast，属可选项）。F9 无关。

**拟追加 Decision 草案**：startup sweep 于 `yield` 前执行，有界（batch/预算）+ fail-open；store 不可用 → 跳过 + 日志（不 fail-fast，记录为对 §7.3 c3 的显式取舍）；`FOREIGN` 不参与。

---

## 4. D8 — scanner interval（拟 `D-Phase2-P2-2-014`）

**当前问题**
周期扫描间隔取多少、是否加抖动？（影响检出延迟、扫描开销、健康行清理滞后。）

**可选方案**

| 方案 | 内容 |
|---|---|
| A | 30s + ±20%（Spec v2 当前默认） |
| B | **15s + ±20%**（对齐 F8 §10.2 示例） |
| C | 60s + ±20% |
| D | 自适应退避（无 running 时拉长） |

**推荐方案**：**B（15s + ±20% 抖动，空闲跳过）**。理由：每周期仅 1 次索引查询 + N 行（N=在途任务，通常 <10）Python 侧判定，15s 与 30s 的开销差异可忽略；与 D3 相乘后总延迟 30s（优于 30s×2=60s）；与 F8 示例值一致。**注：此建议相对 Spec v2 默认值（30s）为变更**，若批准需在后续 Spec append-only 修订中同步 §13 默认值。若运维偏好更少扫描，30s 亦安全可接受；D 不采用（复杂度换收益不明确）。

**风险**
- 间隔过短的主要风险是日志/事件噪声（每周期统计行）与 DB 轮询频率；由「空闲跳过 + 单飞 + 上限」约束。
- 间隔与 D3 相乘决定最坏检出延迟，需在验收标准中写明（例如 registry 脱节路径 ≈ min_age + 2×interval）。
- 抖动必须按进程随机相位（避免多进程同步尖峰）。

**是否影响 F8/F9 冻结**：**否**。F8 §10.2 明示「如 15s」为示例、非强制；判定条件与证据门槛不因间隔变化。F9 无关。

**拟追加 Decision 草案**：`RUNTIME_SCAN_INTERVAL` 默认 15s、±20% 抖动、单飞、空闲跳过；env 可覆盖；30s 亦为可接受配置。

---

## 5. D9 — heartbeat PostgreSQL 写约束（拟 `D-Phase2-P2-2-015`）

**当前问题**
PG 后端下如何写心跳，才能既保证 D15 的 liveness 证据可用，又不拖慢事件循环（watchdog/agent 调度同处一个 loop）与不引入越界依赖？

**可选方案**

| 方案 | 内容 | 判定 |
|---|---|---|
| A | **逐拍 UPSERT + 逐任务节流（5s）+ 抖动相位；不引连接池、不加依赖、单语句短事务** | 满足要求 |
| B | 引入连接池（`psycopg_pool`） | 新增依赖/基础设施 → AGENTS §4 越界 |
| C | 批量写（单 writer task 聚合为一次事务多行 UPSERT） | 连接数降至 ≈1/interval；新增平行组件 → 列为应急候选 |
| D | PG 场景心跳仅存内存 | **破坏 D15 安全证据**（`SAME_HOST_PREV` 的 liveness 依赖 DB 心跳）→ 拒绝 |

**推荐方案**：**A**。并冻结两条护栏：① **不引入连接池、不新增依赖**；② 心跳写**必须**为单语句短事务，禁止在同一调用内追加查询/锁/重试风暴（避免拖慢 control path）。**C 作为应急候选**：仅当实测表明 PG 写抖动影响 watchdog 及时性时，以独立小批引入（需另行批准）。

**风险**
- 现状：governance PG store 每次调用开**短连接**且**同步执行**于事件循环内；N 个在途任务 ≈ N/5 次连接/秒（N=50 → 10 conn/s），表现为毫秒级 loop 抖动。
- 若实现者自行引入连接池或批量组件 → scope/依赖面漂移（本提案的护栏正是为阻止该情况）。
- 心跳写失败必须 fail-open（否则会反向影响执行）——已由 `D-Phase2-P2-2-001`（LA-1/LA-4）与 Spec §4.5 约束。

**是否影响 F8/F9 冻结**：**否，但受其约束**。F8 §7.2 要求 control-path 不因 observation 失败或开销而退化 → 本提案的护栏是该条款的直接落实；F8 §5.3 单实例假设不变（不得借此引入 lease）。F9 无关。

**拟追加 Decision 草案**：PG 心跳采用逐拍节流 + 抖动；**不引入连接池、不新增依赖**；心跳写必须单语句短事务、fail-open；批量 writer 列为应急候选（需独立批准）。

---

## 6. D11 — Problem Registry 登记时机（拟 `D-Phase2-P2-2-016`）

**当前问题**
「Runtime 无健康平面 ⇒ 遗留 RUNNING 不可诊断/无收敛」是否登记为正式 Problem？何时登记？

**可选方案**

| 方案 | 内容 |
|---|---|
| A | 本轮不改 `PROBLEM.md`；实现阶段评估 |
| A′ | **本轮不登记；Implementation 起点（或 Readiness 通过时）登记正式 Problem**（题名/Type 现在固定） |
| B | 现在登记正式 Problem（`docs/problem/P0NN-*.md` + 索引行） |
| C | 现在登记 Problem Candidate（`docs/problem/candidates/`） |
| D | 不登记（视为 Phase-2 路线已覆盖） |

**推荐方案**：**A′**。理由：AGENTS §11.3 的登记义务发生在**任务完成前**（当前仍处 Spec/Decision Closure 阶段）；但触发条件（可复现、长期价值、契约缺口、未来 Agent 易重复）均已成立，故应固定内容与时机而非弃置。D 会丢失稳定 ID 与检索入口，不推荐。

**风险**
- 不登记（或延迟过久）→ 未来 Agent 重复踩坑（AGENTS §11.1 明示风险）。
- 过早登记 → 与 P2-2 实现进度脱节，可能需二次更新；故将登记时机绑定到 Implementation 起点。
- 登记动作本身是文档变更（`PROBLEM.md` + `docs/problem/`），需单独批准（本提案不执行）。

**拟登记内容（供批准后使用）**
- 题名（拟）：「Runtime 缺少健康平面：进程退出/执行器失联后任务永久 RUNNING 且不可诊断」
- Type（拟）：Runtime behavior / Architecture constraint；Severity：High；预期状态：Open → 由 P2-2 实现关闭
- 关联：`RUNTIME_OBSERVABILITY_AUDIT_REPORT.md`、`P2-2_SPEC_v2.md` Rev 2.1、`D-Phase2-P2-2-001`

**是否影响 F8/F9 冻结**：**否**（Harness 层：`AGENTS.md` §11 / `PROBLEM.md` 登记门槛；与 F8/F9 运行语义无关）。

**拟追加 Decision 草案**：Problem 登记时机 = P2-2 Implementation 起点；题名/Type/Severity/关联如前；本轮不改 `PROBLEM.md`。

---

## 7. D12 — frontend terminal 文案策略（拟 `D-Phase2-P2-2-017`）

**当前问题**
P2-2 首次让 `aborted` / `orphan_reclaimed` 成为**生产可见终态**，前端是否需要改动？

**事实更正（证据）**：Spec v2 §9/§21 与 P2-2 Closure Report 中「`aborted`/`orphan_reclaimed` 文案缺口」的表述**不成立**：

| 证据 | 内容 |
|---|---|
| `frontend/src/lib/taskStatus.ts:13-14` | `aborted: "任务中止"`、`orphan_reclaimed: "已回收"` |
| `frontend/src/components/ConversationThread.tsx:323-326` | `aborted → "任务已中止（连接中断期间结束）。"`、`orphan_reclaimed → "任务已被回收（连接中断期间结束）。"` |
| `frontend/src/components/ConversationThread.tsx:311-314` | 仅 `failed` 分支拼接 `error` 文本 |

**可选方案**

| 方案 | 内容 |
|---|---|
| A | **本批零 frontend 改动**（标签/提示已覆盖；reason 展示记为后续项） |
| B | 同批小改（≤5 行：在 `aborted`/`orphan_reclaimed` 分支拼接 `error`，与 `failed` 对齐）——需显式 scope 例外（放行 frontend 改动） |
| C | 归入 P2-3（durable timeline UI 一并呈现过程与原因） |
| D | 永不改动（接受固定文案） |

**推荐方案**：**A**（保持 P2-2 批次边界：零 frontend 改动）；若用户希望「一次到位」，可选 **B**（限定上述文件与 ≤5 行，且不触碰 WS/HTTP 契约）。同时建议：在后续 Spec **append-only** 修订中更正 §9/§21 的「文案缺口」表述（本轮**不修改 Spec 正文**）。

**风险**
- A：reclaim 原因（`error` 字符串）在 UI 不可见，用户只看到「任务已中止/已回收」；诊断需查 `GET /api/tasks/{id}` 或事件/日志。
- B：放行 frontend 改动会打破本批「零 frontend」约束，需明确 scope 例外与最小 diff 边界。
- 展示 `error` 时必须遵守 `D-Phase2-P2-2-008`（不得含凭据/prompt 正文）。

**是否影响 F8/F9 冻结**：**否**。终态集合为 F8 冻结、前端仅呈现；方案 B 不触碰公共 API/WS schema（AGENTS §4 约束满足）。F9 无关。

**拟追加 Decision 草案**：P2-2 不做 frontend 改动；`aborted`/`orphan_reclaimed` 标签与提示沿用现状；reclaim reason 展示列为后续项（P2-3 或独立小批）；Spec §9/§21 表述在下次 append-only 修订中更正。

---

## 8. 批准与后续动作（批准后执行，本轮不执行）

1. **用户逐项批准**（可直接引用 Gate 号；未批准的保持 `awaiting explicit confirmation`）。
2. **append-only 追加 Decision**：按上表 ID（`D-Phase2-P2-2-011…017`）追加至 `DECISION.md`，不修改任何历史条目（需单独批准该文档变更）。
3. **Spec append-only 修订**（若与 v2 文本不一致）：
   - §13 配置表：`RUNTIME_SCAN_INTERVAL` 默认 30s → **15s**（若 D8 采用 B）；
   - §22 Status 与 §20 Gate 状态：标注 7 项 closed；
   - §9/§21：更正「frontend 文案缺口」表述（D12 证据）。
4. **Problem 登记**（D11 A′ 生效时）：文档变更需单独批准。
5. **Readiness 重评**：D7/D9 获批准 + 其余处置确认后，Readiness 可由 NOT READY 重评为下一步（仍**不含** Implementation Planning 批准；该批准需单独申请）。

---

## 9. 本轮 Scope 确认

- ✅ **未修改 Spec 正文**（`P2-2_SPEC_v2.md` 保持 Rev 2.1 原样）
- ✅ **未修改 `DECISION.md`**
- ✅ 零代码改动（`app/**`）；✅ 零 migration（`db/**`）；✅ 零测试改动（`tests/**`）；✅ 零 frontend 改动
- ✅ 未建 branch（仍 `main`）；✅ 无 commit / push / merge
- ✅ 未进入 Implementation Planning；未生成 Implementation Plan
- 本轮唯一写入：`P2-2_PENDING_DECISION_PROPOSAL.md`（本文件，待批准）
