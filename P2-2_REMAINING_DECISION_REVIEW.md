# P2-2 Remaining Decision Review（D3 / D6 / D7 / D8 / D9 / D11 / D12）

- 阶段：**P2-2 Remaining Decision Gate Closure — Review**（只读分析；等待用户逐项批准）
- 范围：仅 D3、D6、D7、D8、D9、D11、D12（其余 Gate 已于上一轮关闭：D5/D13/D14/D15/D16/D17/D18/D19 + D1/D2/D4/D10 推论关闭）
- 本轮约束遵守：**未修改 `DECISION.md`**；未改代码 / migration / 测试 / frontend；未建 branch；未 commit/push/merge；未进入 Implementation Planning
- **Implementation Readiness = NOT READY**（状态保持；阻塞项见 §8）
- 证据来源：`P2-2_SPEC_v2.md`（Rev 2.1）、`DECISION.md`（D-Phase2-P2-2-001…010）、F8 Spec（§5.3/§6.1/§7.2/§10.2）、`PROJECT_CONTEXT.md` §4/§8、当前代码（`app/api/server.py` lifespan、`app/runtime/governance/controller.py` `flush_pending`/`_watchdog_loop`/`_start_governed_task`、`app/runtime/governance/store.py` PG 短连接）、frontend（`taskStatus.ts`、`ConversationThread.tsx`）、`AGENTS.md`（§4/§11）

---

## 0. 总览（推荐与阻塞性）

| Gate | 议题 | 推荐 | 与 F8/F9 冻结关系 | Blocking Implementation Planning? |
|---|---|---|---|---|
| **D3** | 双阶段确认次数 | **2 次**（下限 1，需显式 opt-in） | 补充 F8 §10.2 的 sweeper 判定严格度；不改冻结语义 | **否**（默认取最保守值，参数可配） |
| **D6** | `flush_pending` 交互范围 | **A：本批不接线**（shutdown flush 记为后续候选） | 触及 F8 §10.2「sweeper 周期 reconciler」文档化 future；本批不进入 | **否** |
| **D7** | startup sweep 时序 | **方案 A：yield 前有界 sweep**（预算 ≤2s、fail-open） | 直接实现 F8 §10.2 e；决定 F8 restart 收敛的确定性 | **是（架构语义）** |
| **D8** | scanner interval + jitter | **15s + ±20% 抖动**（空闲跳过；30s 亦可接受） | F8 §10.2 示例值即为 15s；无强制值 | **否**（纯参数，env 可覆盖） |
| **D9** | heartbeat PG write 约束 | **A：逐拍节流 + 抖动，不引连接池、不加依赖**（批量写列为应急候选） | 受 F8 §7.2 control/observation 分离约束（不得拖慢 control path） | **是（架构护栏）** |
| **D11** | Problem Registry 时机 | **A′：本轮不登记；在 P2-2 Implementation 起点（或 Readiness 通过时）登记正式 Problem**（内容/Type 现在固定） | Harness 层（AGENTS §11），与 F8/F9 无关 | **否** |
| **D12** | frontend 终态文案范围 | **A：本批不改 frontend**（并修正 Spec 表述：标签/提示已存在；仅「回收原因展示」为可选后续项） | 仅前端呈现层，不触 WS/事件 schema（AGENTS §4） | **否** |

---

## 1. D3 — 双阶段确认次数（`RUNTIME_STALE_CONFIRMATIONS`）

**当前候选方案**

| 方案 | 内容 |
|---|---|
| **A（v2 推荐）** | 2 次连续扫描均 stale 才进入 reclaim（周期路径）；单次仅记 stale 视图 |
| B | 1 次（低延迟 / 激进） |
| C | 3 次（最保守） |
| D | 自适应：注册表脱节 2 次、heartbeat stale 1 次（因已叠加 `deadline_evidence`） |

**架构影响**
- 仅影响 periodic scanner 的进程内计数状态（`dict[task_id] → consecutive_stale_count`），无持久化、无 schema、无 API；重启后计数清零（更保守方向）。
- **启动期 sweep 无法「确认两次」**（单遍执行）→ 启动路径必须依赖 owner 分类 + 证据谓词（`liveness_evidence` / `deadline_evidence`）而非确认次数；该不对称必须在 Spec 中保持明确（v2 已如此）。

**安全影响**
- heartbeat-stale 路径已要求 `deadline_evidence`（默认 ≈630s 无心跳）；在如此强的证据上再加确认，收益边际很小 → 主要价值在**弱证据路径**（registry 脱节，其 liveness 阈值仅 ≈15s）与「单次扫描读到瞬时异常」的防护。
- 代价仅是收敛延迟增加 1 个扫描周期（15–30s），方向安全（fail-closed 哲学：宁可不收敛，不误杀）。
- 与 P1（handle 存活）/P2（内存裁决）/P3（最小年龄）叠加后，误回收概率已在工程意义上趋零。

**与 F8/F9 冻结约束关系**：F8 §10.2 只定义 sweeper 的存在与幂等要求，未规定确认次数 → 属**未冻结的自由参数**；不触碰 funnel/CAS/终态语义。F9 无关。

**推荐方案**：**A（2 次）**，配置下限 1（`1` 需显式 opt-in 并在日志标注 `aggressive`）；不做 D（自适应复杂度高、收益不明确）。

**是否 blocking**：**否** —— 默认取最保守值即可安全实现；属参数，可在实现冻结前锁定。

---

## 2. D6 — `flush_pending` 交互范围

**当前候选方案**

| 方案 | 内容 |
|---|---|
| **A（v2 推荐）** | 本批不接线；`flush_pending()` 保持无生产调用方；DB 写失败的 pending 裁决在进程存活期由既有 retry 阶梯处理，进程死亡后由 startup/periodic sweep 收敛 |
| B | startup 顺带 `flush_pending()` |
| C | shutdown 收尾 flush（lifespan 退出前有界 flush） |
| D | 完整接线（周期 reconciler，如每 15s flush pending） |

**架构影响**
- `flush_pending()` 只对**进程内** pending 裁决有意义（`_pending_order` + `_decisions` 均为内存状态）→ **B 在新进程启动时恒为空操作**（内存已随进程消失），实为无效方案；除非同进程 reload 场景，但那不是生产重启模型。
- **D** 会新增一个与 scanner 平行的周期组件，进入 F8 §10.2「sweeper/reconciler（15s）」的文档化 future 区域 → 本批 scope 外扩。
- **C** 是唯一有实际价值的变体：给「已在内存裁决但 DB 未落」的裁决最后一次持久化机会，避免其在下次启动被 sweep 判为 `aborted`。成本：shutdown 期一次有界重试（可与既有 `TERMINAL_RETRY_DELAYS` 复用），需 fail-open 且有超时上限。

**安全影响**
- 不接线不会造成危险状态：pending 场景的降级结果是终态**语义漂移**（原本可能 `completed` 的行最终被收敛为 `aborted`/`orphan_reclaimed`）——方向保守、可审计（error 字符串含 reclaim trigger），但会丢失原裁决原因。
- 若接线 C，需要注意的是 shutdown 期 DB 不可用时**不得**延长关闭过程（必须有硬超时），否则会拖慢部署/回滚。

**与 F8/F9 冻结约束关系**：F8 已把 pending 语义（内存裁决权威 + retry 阶梯 + degraded_durability 标记）冻结；本批**不修改**这些语义，仅决定是否增加**调用点**。F8 §7.2 要求 observation 失败不阻断 control —— 因此任何 flush 调用点都必须 fail-open。F9 无关。

**推荐方案**：**A（本批不接线）**；将 **C（shutdown 有界 flush）** 记为 P2-3/P2-4 候选（或作为独立小批），并在 Spec 中显式记录「pending → sweep 收敛导致终态语义漂移」这一已知限制（v2 §6.5 已记录）。

**是否 blocking**：**否** —— 不接线是安全默认；接线与否不影响 P2-2 主体契约。

---

## 3. D7 — startup sweep 时序

**当前候选方案**

| 方案 | 内容 |
|---|---|
| **A（v2 推荐）** | `store/migration init → startup sweep（有界 + 预算）→ yield` |
| B | `init → yield → background startup sweep` |
| C | 混合：yield 前做极小快速通道（如 ≤50 行 / ≤500ms），余量交 background |

**架构影响**
- 影响 `app/api/server.py` lifespan 的时序与失败语义（当前 lifespan：`manager.set_loop → init_checkpoint_lifespan → get_main_agent prewarm → yield → shutdown_checkpoint_lifespan`，**没有任何 governance hook**）。
- A 使「首请求前账本干净」，并让启动收敛**确定性**（不与新请求竞争）；B 会在窗口内让旧 `running` 行对前端可见（「研搜中」假态）并与新 submit 竞争——届时旧行可能被 `_start_governed_task` 的 `cancel_governed` 收敛为 `cancelled`，而非 F8 §6.1 规定的 restart 语义 `aborted`，并可能触发 session archive 守卫 409。
- A 的 readiness 成本有界（批量上限 + 时间预算）；对比现有 lifespan 已 `await get_main_agent()`（组装 agent + checkpointer，量级远大于一次有界 sweep），A 的开销可忽略。

**安全影响**
- A：store 不可用/超时 → **跳过 + 日志**（不 fail-fast），不阻塞服务；无并发收敛竞争。
- B：存在「用户看到假 running」「旧行终态非确定性」两类问题；若 sweep 迟到，用户可能在同 thread 提交新任务而触发对旧行的 cancel 语义。
- C：兼顾，但增加一条「快速/慢速」双路径判定逻辑（复杂度换延迟）。

**与 F8/F9 冻结约束关系**：直接实现 F8 §10.2 e（startup sweep → `aborted`，不自动 resume）；受 F8 §5.3 单实例假设约束（FOREIGN 不收敛）。F8 §7.3 c3 提到「startup governance store 不可用 → fail-fast」，但该条针对**能否自举治理**；本批选择「跳过 sweep 但服务继续（提交仍由 c1 返回 503）」是对现状的最小改动 —— 这一取舍必须在 Spec 中写明（v2 §7.2 D7 已列）。F9 无关。

**推荐方案**：**A**（有界 batch 200 + 预算 ≤2s + fail-open + store 不可用则跳过）；C 作为未来规模化时的演进选项（当前遗留行数 ≈ 崩溃时在途任务数，通常 <10，无需混合路径）。

**是否 blocking**：**是** —— 决定 lifespan 时序、readiness 语义与 F8 restart 收敛的确定性；实现计划必须基于明确选择。

---

## 4. D8 — scanner interval + jitter

**当前候选方案**

| 方案 | 内容 | 说明 |
|---|---|---|
| A（v2 推荐） | 30s + ±20% 抖动 | 扫描开销更低 |
| **B（本 Review 推荐）** | 15s + ±20% 抖动（空闲跳过） | 对齐 F8 §10.2 示例值，「如 15s」 |
| C | 60s + ±20% | 开销最低、收敛最慢 |
| D | 自适应（无 running 任务时退避） | 额外复杂度 |

**架构影响**
- 每周期工作 = 1 次 `status='running'` 索引查询 + N 行 Python 侧判定 + 健康行清理（终态/孤儿）。N = 在途任务数（通常 <10）。15s 与 30s 的差异在亚毫秒级查询层面，无架构差异。
- 「无 running 任务则跳过（并顺带跳过清理？不——清理仍需执行，只是查询结果为空）」→ 空闲成本 ≈ 0，因此缩短间隔几乎无代价。
- 抖动：每进程相位随机（±20%），避免多实例/多进程同步尖峰（即便多实例不支持，抖动也降低与其它周期任务叠加的概率）。

**安全影响**
- 间隔越短 ⇒ 检出/收敛越快（registry 脱节路径延迟 ≈ 2×interval + min_age；heartbeat 路径由 `deadline_evidence` 主导，间隔影响小）；缩短间隔**不增加误判**（判定条件与证据门槛不变）。
- 与 D3 交互：总延迟 ≈ interval × confirmations → 15s×2 = 30s 最坏，优于 30s×2 = 60s。

**与 F8/F9 冻结约束关系**：F8 §10.2 明示「sweeper（周期，如 15s）」→ 15s 是**与 F8 文字一致**的选择；无强制值，无冻结冲突。F9 无关。

**推荐方案**：**B（15s + ±20% 抖动）**，env 可覆盖（`RUNTIME_SCAN_INTERVAL`）；同时记录：若运维偏好更少扫描（例如低功耗环境），30s 亦安全可接受。**注意：此为对 v2 默认值的调整建议**（v2 = 30s），若批准需在 Spec append-only 修订中更新 §13 默认值与 §22。

**是否 blocking**：**否** —— 纯参数，且两个取值均安全；但需在实现冻结前锁定默认值以保持文档一致。

---

## 5. D9 — heartbeat PG write 约束

**当前候选方案**

| 方案 | 内容 |
|---|---|
| **A（v2 推荐）** | 逐拍 UPSERT + 逐任务节流（默认 5s）+ 抖动相位；**不引入连接池、不新增依赖** |
| B | 引入连接池（`psycopg_pool`）复用连接 |
| C | 批量写（单 writer task 聚合，同一事务内多条 UPSERT） |
| D | PG 场景心跳仅存内存（不落库） |

**架构影响**
- 现状：`_GovernancePostgresStore` **每次调用一个短连接**（`transaction()`/`execute()` 各自 `connect()`），且 store 调用在**事件循环内同步执行**（P2-1 前既有模式：`start_task`/`terminalize` 同样如此）。
- 心跳频率 5s/任务 ⇒ 连接数 ≈ N/5 每秒（N=50 → 10 conn/s）；每次 connect 的毫秒级开销会表现为事件循环抖动，而同一循环承载 watchdog tick 与 agent 调度。
- **B** 需新增依赖（`psycopg_pool` 已在 checkpoint 侧使用，但 governance store 刻意保持短连接）→ 属基础设施/依赖面变更（AGENTS §4：禁止无关依赖升级），超出 P2-2 scope。
- **C** 把连接数降到 ≈1/interval，是把开销压到最小的工程解，但引入一个独立 writer 组件（与 scanner 平行）→ 可作**应急/Phase-2b** 候选，不作为本批默认。
- **D** 使 heartbeat 不可跨进程/跨重启观测 → **破坏 D15 的安全证据**（`SAME_HOST_PREV` 的 `liveness_evidence` 依赖 DB 心跳；退化为 `started_at` 回退后，活跃旧进程会被误判），故拒绝。

**安全影响**
- A 保证了 D15 安全不变量可判定（DB 心跳存在），且写失败 fail-open，不影响执行/budget/cancel/terminal。
- 需要明确护栏：心跳写**必须**保持单语句短事务；**不得**在同一调用内追加额外查询/锁，避免控制路径（watchdog tick）被拖慢 —— 这对应 F8 §7.2「control-path 不因 observation 失败或开销而关闭/退化」。
- C 若未来启用，仍需保证 writer 故障不影响 control（fail-open、有界队列）。

**与 F8/F9 冻结约束关系**：F8 §7.2（control/observation 分离 + 不得静默关闭 hard control）、§5.3（单实例；不得借此引入 lease）为核心约束；F8 §11 允许 provider/telemetry 开销但要求 fail-open。F9 无关。

**推荐方案**：**A**（逐拍 + 节流 + 抖动；**不引连接池、不加依赖**）；**C 列为应急候选**（若实测 PG 写抖动影响 watchdog 及时性，再以独立小批引入批量 writer）；B 拒绝（依赖/基础设施越界）；D 拒绝（破坏 D15 证据）。

**是否 blocking**：**是（架构护栏）** —— 必须显式冻结「不引连接池/不加依赖」与「单语句短事务」两条，否则实现者可能自行引入连接池或批量组件，造成 scope 与依赖面漂移。

---

## 6. D11 — Problem Registry 时机

**当前候选方案**

| 方案 | 内容 |
|---|---|
| A（v2 推荐） | 本轮不改 `PROBLEM.md`；实现阶段评估登记 |
| **A′（本 Review 推荐）** | 本轮不登记；**在 P2-2 Implementation 起点（或 Readiness 通过时）登记正式 Problem**，题名/Type 现在固定 |
| B | 现在登记正式 Problem（`docs/problem/P0NN-*.md` + `PROBLEM.md` 索引行） |
| C | 现在登记 Problem Candidate（`docs/problem/candidates/`） |
| D | 不登记（视为已被 Phase-2 路线覆盖） |

**架构影响**：无代码/架构影响；属 Harness 知识沉淀（`PROBLEM.md` 与 `docs/problem/`）。

**安全影响**：无直接安全影响；不登记的风险是「未来 Agent 重复踩坑」（AGENTS §11.1 触发条件之一）。

**与 F8/F9 冻结约束关系**：与 F8/F9 无关；受 `AGENTS.md` §11（Problem Capture：触发条件 + 完成前三问 + 克制原则）与 `PROBLEM.md` 登记门槛约束。审计已记录该问题（`RUNTIME_OBSERVABILITY_AUDIT_REPORT.md`）且 `PROJECT_CONTEXT` §8 将其列为已知缺口，因此**具备长期价值但尚未登记**。

**推荐方案**：**A′** —— 现在固定 Problem 内容与类型，待 Implementation 起点（或 Readiness 通过）再执行登记（届时属文档变更，需单独批准）；不建议 D（会丢失稳定 ID 与检索入口）。

建议登记内容（供后续使用，本轮不写文件）：
- 题名（拟）：「Runtime 缺少健康平面：进程退出/执行器失联后任务永久 RUNNING 且不可诊断」
- Type（拟）：Runtime behavior / Architecture constraint；Severity：High；Status：Open → 由 P2-2 实现关闭
- 关联：`RUNTIME_OBSERVABILITY_AUDIT_REPORT.md`、`P2-2_SPEC_v2.md` Rev 2.1、`D-Phase2-P2-2-001`

**是否 blocking**：**否**。

---

## 7. D12 — frontend 终态文案范围

**重要修正（证据）**：Spec v2 §9/§21 与 P2-2 Closure Report 中「`aborted`/`orphan_reclaimed` 文案缺口」的表述**不准确** —— 前端**已具备**两者的标签与断线提示文案：

| 证据 | 内容 |
|---|---|
| `frontend/src/lib/taskStatus.ts:13-14` | `aborted: "任务中止"`、`orphan_reclaimed: "已回收"` |
| `frontend/src/components/ConversationThread.tsx:323-326` | `aborted → "任务已中止（连接中断期间结束）。"`、`orphan_reclaimed → "任务已被回收（连接中断期间结束）。"` |
| `frontend/src/lib/taskStatus.ts:17-18` | 未命中时回退 `已结束（${status}）`（本批新增终态不会走到该分支） |

**因此 D12 的真实范围** = 是否在本批**展示 reclaim 原因（`error` 字符串）**——当前只有 `failed` 分支渲染 `error`（`ConversationThread.tsx:311-314`），`aborted`/`orphan_reclaimed` 分支为固定文案。

**当前候选方案**

| 方案 | 内容 |
|---|---|
| **A（v2 推荐，本 Review 维持）** | 本批不改 frontend（标签已覆盖；「回收原因展示」记为后续项） |
| B | 同批小改（≤5 行：为 `aborted`/`orphan_reclaimed` 分支增加 `error` 拼接） |
| C | 归入 P2-3（durable timeline UI 一并呈现过程与原因） |
| D | 永不改动（接受固定文案） |

**架构影响**
- A/C：无（零 frontend 改动）。
- B：前端本地标签映射/文案变更，属于呈现层；**不触碰 WS/monitor 事件 schema 或 HTTP 契约**（`error` 已由 `GET /api/tasks/{id}` 返回，无后端改动）。

**安全影响**：无。展示 `error` 字符串需注意不包含凭据/prompt 正文 —— 该要求已由 `D-Phase2-P2-2-008`（error 字符串不得含凭据）约束。

**与 F8/F9 冻结约束关系**：终态集合为 F8 冻结；前端只是呈现 → 无契约变更。`AGENTS.md` §4 禁止无需求地修改公共 API/WS schema（方案 B 满足该约束）。F9 无关。

**推荐方案**：**A**（本批零 frontend 改动，维持批次边界）；若用户希望「一次到位」，则 **B** 可作为**显式 scope 例外**（需用户书面批准放行 frontend 改动，且限于上述 ≤5 行）。同时**建议在下次 Spec 修订中以 append-only 方式更正 §9/§21 的「文案缺口」表述**（改为「标签已覆盖；reason 展示为可选后续项」）。

**是否 blocking**：**否**。

---

## 8. Implementation Readiness（状态保持）

**NOT READY**

| 类别 | Gate | 说明 |
|---|---|---|
| **阻塞（架构）** | **D7** | startup sweep 时序未裁决 → lifespan 时序/readiness 语义/重启收敛确定性未定 |
| **阻塞（架构护栏）** | **D9** | PG 心跳写约束未裁决 → 存在实现者擅自引入连接池/批量组件的风险 |
| 非阻塞（建议锁定默认值） | D3（2 次）、D8（15s 或 30s） | 参数；两者均有安全默认，且 env 可覆盖 |
| 非阻塞（范围处置） | D6（不接线）、D11（实现起点登记）、D12（零 frontend 改动） | 处置明确即可，不影响主体契约 |
| 流程前置 | — | D7/D9 裁决 + 上述各项确认后，仍需 Readiness Review 通过，且 **Implementation Planning 需单独批准**（P2-2 属 L3，PROCESS §11.3） |

**批准后需要同步的动作（不在本轮执行）**：
1. 若 D8 采用 15s（或 D3/D6/D9/D12 任一项与 v2 文本不一致）→ **Spec append-only 修订**更新 §13 配置表默认值 / §22 状态；
2. D7/D9 及其余 Gate 裁决 → 以 **append-only** 方式追加 `D-Phase2-P2-2-011…`（届时需单独批准修改 `DECISION.md`）；
3. D11 的 Problem 正式登记（文档变更，需单独批准）；
4. Spec §9/§21 的文案缺口表述更正（append-only）。

---

## 9. 本轮 Scope 确认

- ✅ **未修改 `DECISION.md`**（其内容仍为上一轮已登记的 `D-Phase2-P2-2-001…010`，本轮零改动）
- ✅ 零代码改动（`app/**` diff 空）；✅ 零 migration（`db/**` 未触碰）；✅ 零测试改动（`tests/**` 空）
- ✅ 零 frontend 改动；`app/agent/**`、`app/research/**` 未触碰
- ✅ 零 branch 创建（分支仍 `main`）；✅ 零 commit / push / merge
- ✅ 未进入 Implementation Planning；未生成 Implementation Plan；未提前规划代码文件
- 本轮唯一写入：`P2-2_REMAINING_DECISION_REVIEW.md`（本报告）
