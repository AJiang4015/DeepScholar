# P2-2 Spec Review Report

Review 对象：`P2-2_RUNTIME_HEARTBEAT_STALE_RECLAIM_SPEC.md`（Spec Review 阶段；**只读**：本报告不含代码/测试/schema/frontend 改动，未建 branch、未 commit）
Review 依据：P2-2 Spec 全文、F8 Runtime Governance Spec（§5.3/§6.1/§10.1/§10.2）、P2-1 Spec/Plan/Report/Merge Report、DECISION.md（仅 `D-Phase2-P2-1-001`）、当前 runtime governance 代码（`controller.py` / `store.py` / `models.py` / `events.py` / `service.py` / `server.py`）、既有测试（`test_runtime_governance*.py`、`test_sessions_*`）、Harness（AGENTS §10/§11、PROCESS §11、TESTING §1/§5）

---

## 1. Review Result

**PASS WITH CHANGES**

- 架构方向正确：独立 Runtime Health Plane + 派生 stale 态 + reclaim 复用冻结 terminal funnel，符合 F8 冻结语义与 `D-Phase2-P2-2-001/-002` 的既有裁决。
- 但存在 **6 项架构/安全精度缺口**（R1–R6 中的关键项：首拍时机、registry 脱节误判窗口、scanner 必须复用进程级 controller、跨进程事件不变量表述、startup 误杀路径、模块归属缺失）与 **5 项工程化缺口**（R7–R11），需在 Spec 中补全后方可进入 Implementation Planning。
- **Implementation Readiness = NOT READY**（见 §6）。

---

## 2. Architecture Findings

| # | 检查项 | 结论 | 证据 / 说明 |
|---|---|---|---|
| A1 | heartbeat 是否应为独立 Runtime Health Plane | ✅ **正确且必要** | F8 冻结 `governance_tasks` 为 lifecycle 字段权威（`tests/test_runtime_governance.py:43-63` 注释明文「不允许 Plan 之外的 lifecycle 字段」）；`D-Phase2-P2-2-001` 已裁决「stale 属 Runtime Health，不是业务生命周期状态」→ Spec §4.1 推荐方案 A（独立健康表）与之一致；方案 B（加列）会同时触碰字段权威与写放大，不推荐 |
| A2 | 是否违反 F8 lifecycle authority | ✅ 不违反（需**写死 3 条纪律**） | ① 心跳写只允许触碰健康表，禁止更新 `governance_tasks.status/version/terminal_*`；② reclaim 唯一入口 = `controller.finalize_with_event()`（P2-1 已冻结）；③ 健康态不落库到 `status`。三条须从「设计描述」升格为 Spec 的 MUST |
| A3 | stale 是否正确作为 health state | ✅ 正确 | Spec §9 明确 `healthy/stale/expired` 派生、不新增 TaskStatus；与 F8 §6.1 终态语义无冲突 |
| A4 | reclaim 是否正确复用 terminal funnel | ✅ 正确（**但一处不变量表述需限定**） | 代码复核：`terminalize()` 在同进程内二次调用经内存裁决短路（`controller.py:271-274`）→ `finalize_with_event` 提前返回不重发事件；DB 已终态时走 `_already_terminal_db_result`（L286-288）同样不重发。**跨进程** CAS 败者路径（L316→L319-324 retry 采纳 DB 事实）仍返回 `winner=True, already_terminal=False` → **会二次发布 terminal 事件**（多进程不在 F8 支持范围）→ Spec「每 task ≤1 terminal event」必须限定为单实例envelope（R3） |
| A5 | heartbeat 的语义定位需更强表述 | ⚠ 需补强 | F8 §5.3 明文把 `execution owner / lease / heartbeat` 排除在 v1 之外（为多实例互斥设计）。P2-2 的 heartbeat 是**健康遥测**而非租约；Spec 虽在 §2.2/§15 提到，但应在 §4 顶部加「**不改变 F8 §5.3 单实例假设；非 lease/fencing；不用于互斥**」的显式声明，避免后续误当分布式原语 |
| A6 | Scanner 模块归属（Review Focus C） | ⚠ 缺失 → 需补 | Spec §7/§8 把 startup+periodic 都放在 `server.lifespan`，未定义 RuntimeHealthScanner 抽象（详见 §4/§5 的 R6） |

---

## 3. Safety Findings

| # | 风险 | 评估 | 处置 |
|---|---|---|---|
| S1 | **registry 脱节路径的误判窗口**（Spec §6.1 第三行仅要求 `age > MIN_AGE`） | ⚠ 真实风险：`submit_task` 先 `create_task(running)`，**随后**才 `asyncio.ensure_future` + 注册 handle（`controller.py:624-632`）；若事件循环被其它任务阻塞 > `MIN_AGE(10s)`，刚提交但尚未注册 handle 的任务会被判「registry 脱节」→ 误回收 | R2：该路径追加**证据条件** —— 要求「存在心跳行 或 `started_at` 非空」且 `now - max(last_beat, started_at) > heartbeat_timeout_multiplier`（建议 ≥3× 心跳间隔且 ≥15s）；`started_at` 可为空（`start_task` 失败非致命，L618-620），故不能单独依赖 |
| S2 | **首拍时机**（Review Focus A） | ⚠ 需冻结 | 结论：**应在 `_execute_governed` 起点、handle 注册后立即写首拍**；`submit_task` 路径**不写**心跳。理由：① 语义上 heartbeat = 执行者存活，submit 仅代表受理；② 若 submit 写首拍，则 `execute()` 早期失败（store 读失败 → `GovernanceControllerError`，L472-479）留下的 running 行会显示「有心跳」，掩盖 registry 脱节；③ 避免把 stale 基准前移到「尚未开始执行」的时刻。→ 新 Gate **D13** |
| S3 | **scanner 必须复用进程级 controller** | 🔴 关键安全前提 | handle registry 是**进程内**权威（`_handles`，L186/L308）。若 scanner 自建 controller 实例，则该实例 `_handles` 恒空 → 同进程中所有活跃任务都将满足「无 handle」→ **大规模误回收**。Spec 须写死：scanner MUST 由 `get_controller()`（L1011-1028 进程级单例）构造；拿不到 controller（store 不可用）→ scanner 停用 |
| S4 | CAS 是否足以保证 exactly-once | ✅ 单实例成立 / ⚠ 多进程否 | DB 迁移恰一次由 `WHERE status='running' AND version=?`（`store.py:315-356`）+ rowcount==1 保证；事件恰一次在单实例内由内存裁决保证。跨进程存在重复事件可能（见 A4）→ 限定 + 可选硬化（新 Gate **D19**） |
| S5 | restart recovery 误杀风险 | ⚠ 存在一条危险路径（Spec P6 只做主机名二分） | 危险场景：① 运维显式设 `GOVERNANCE_OWNER_INSTANCE`（owner 字符串跨重启**不变**）→ 旧行 owner == 当前 owner → 落入「本实例 registry 脱节」路径；② 若同机误起第二个进程（同 owner 字符串），新进程的 startup sweep 会把**另一进程正在跑的任务**判为脱节并 abort → 误杀。处置：owner 分类升级为**三分法**（R5）+ owner==current 时要求心跳陈旧才收敛（Gate **D15**） |
| S6 | PG 心跳写成本 / 事件循环阻塞 | ⚠ 需具体化 | PG store 每次 `transaction()` 开短连接（`store.py:155-170`），而心跳由 watchdog tick 在**事件循环内同步**触发 → N 个并发任务每 5s 产生 N 次同步连接；50 任务 ≈ 10 conn/s，可观测 jitter。P2-2 建议：逐任务节流 + 抖动相位（避免 herd）；批量写作为 P2-4 前评估项（Gate **D9** 细化） |
| S7 | 健康行清理责任方（Spec §4.3(3)/D10 = 终态即删，写在 funnel 内） | ⚠ 建议改 | 在 controller funnel 内删除健康行会给 lifecycle 权威模块引入 health 平面写操作；改为**scanner 侧清理**（reclaim/终态后清理 + 孤儿健康行清理），controller 保持「只做 lifecycle」→ R8 + 修订 **D10/D17** |
| S8 | kill switch 与心跳写耦合 | ⚠ 需定义 | 若 `RUNTIME_RECLAIM_MODE=off` 仍持续写心跳，而清理责任在 scanner → 健康行无界增长。建议：`off` ⇒ 心跳写亦停（健康平面整体关闭）；`detect` ⇒ 写心跳 + 判定 + 清理，但不改 DB 终态 → R9 |
| S9 | 双阶段确认在重启后清零 | ✅ 安全方向 | 只会「更保守」，不会误回收；需在 Spec 文档化（不新增机制） |
| S10 | 时钟注入可测性 | ⚠ 缺失 | Spec §16 要求边界/回拨等用例，但未要求可注入 clock。实现需 `clock`/`now_fn` 依赖注入（scanner + heartbeat writer），否则时间用例只能 sleep → R10 |
| S11 | 与 normal completion / cancel / watchdog 的 race | ✅ 安全 | 三者都先写终态；scanner 读到终态即采纳/跳过（`terminalize` L286-288）；内存裁决存在时 P2 防护跳过 |
| S12 | shutdown 期扫描 | ✅ 安全（需写死） | periodic task 在 lifespan finally 中 cancel + await（与 watchdog 同纪律）；startup sweep 仅在启动期执行，不在 shutdown 触发 |

---

## 4. Decision Gate Resolution

| Gate | 议题 | Review 结论 | 需用户裁决？ |
|---|---|---|---|
| D1 | 心跳存储平面 | **批准方案 A**（独立健康表）；B 作为理论备选保留但明确不推荐（触碰 F8 字段权威 + 写放大） | 建议确认 A |
| D2 | stale 阈值基准 | **批准逐任务 `effective_limits.wall_clock_timeout` + grace**；全局常量方案在长任务（如 1800s）下必误回收，否决 | 建议确认 |
| D3 | 双阶段确认次数 | **批准默认 2 次**（可配 1）；确认计数为进程内状态（S9 文档化） | 建议确认 |
| D4 | foreign owner 行 | **批准「只统计不收敛」**（F8 §10.2 明文不得误杀其它实例） | 建议确认 |
| D5 | 终态映射 | **批准 F8 拆分**（restart→`aborted`；stale/registry→`orphan_reclaimed`）；与 P2-1 Spec 语境中「统一 orphan_reclaimed」的旧表述存在**冲突**，须以 F8 文字裁决（AGENTS §1：不得静默选择） | **需裁决（冲突项）** |
| D6 | `flush_pending` 交互 | **批准「本批不接线」**；但其语义空白需在 Spec 显式记录（DB 写失败 pending 裁决在重启后由 sweep 收敛为 aborted/orphan_reclaimed，非原裁决） | 建议确认 |
| D7 | startup sweep 时机 | **批准方案 A（yield 前）**，但追加：**有界**（batch 上限）+ **时间预算**（如 ≤2s）+ **fail-open**（store 不可用/超时 → log + 交由 periodic）；理由见 §5-b 分析 | 建议确认 |
| D8 | scanner 间隔 | **批准 30s + ±20% 抖动**（可配；15s 为 F8 示例值非强制） | 建议确认 |
| D9 | PG 心跳写成本 | **有条件批准**：逐拍短连接 + 逐任务节流 + 抖动相位；批量写列为后续评估（不是 P2-2 交付物） | 建议确认 |
| D10 | 健康行清理 | **修订**：清理责任移出 controller → 由 scanner 负责（终态清理 + 孤儿行清理），见 R8/D17 | 建议确认（修订版） |
| D11 | Problem Registry | **批准「本轮不改 PROBLEM.md」**；实现阶段或本轮后经批准再登记（「无健康平面导致遗留 RUNNING 不可诊断」，AGENTS §11 触发条件满足） | 建议确认 |
| D12 | 前端边界 | **批准本批不改 frontend**；`aborted`/`orphan_reclaimed` 文案缺口记为后续项（需在 Spec 的后续项清单中保留） | 建议确认 |
| **D13（新）** | **心跳首拍时机** | 推荐：`_execute_governed` handle 注册后立即写首拍；submit 路径不写（S2） | **需裁决** |
| **D14（新）** | **Scanner 归属与抽象** | 推荐：新增 `RuntimeHealthScanner`（startup sweep / periodic scan / reclaim coordination / health 只读视图），落在 `app/runtime/governance/`；`server.lifespan` 仅接线（≈10 行） | **需裁决** |
| **D15（新）** | **owner==current 行的 startup 收敛条件** | 推荐：owner 分类三分法（current / same-host-different-incarnation / foreign）；`same-host-different-incarnation` 可立即收敛（`aborted`）；`owner==current` 必须叠加「心跳陈旧超阈」才收敛（安全优先，防同机双进程误杀） | **需裁决** |
| **D16（新）** | **reclaim 诊断信息载体** | 推荐 P2-2 **只用 `error` 字符串**（≤800 字符，`_clip_error` 既有约束），**不改** `events.lifecycle_event` 的 payload 构造（避免触碰冻结 observation 模块）；payload 扩展留 P2-4 | **需裁决** |
| **D17（新）** | **健康行清理责任方** | 推荐 scanner 侧（与 D10 修订一致）；`off` 模式下停写心跳 | **需裁决** |
| **D18（新）** | **registry 脱节收敛条件** | 推荐：`age>MIN_AGE` **且**（心跳行存在 **或** `started_at` 非空）**且** `now-max(beat,started_at) > max(3×心跳间隔, 15s)`；不满足则仅记 stale/ diagnostic | **需裁决** |
| **D19（新）** | **跨进程重复事件硬化（可选）** | 现状：单实例 envelope 内 ≤1 事件成立；跨进程 CAS 败者仍会发布事件。可选硬化（在 `terminalize` 采纳 DB 事实时标记 `adopted_from_db` 并让 `finalize_with_event` 跳过发布）——属对冻结 controller 的改动，建议**本批不做**、仅在 Spec 记录为已知限制 | 建议确认（保守：不做） |

---

## 5. Required Spec Changes（进入 Implementation Planning 前必须落到 Spec）

| # | 位置 | 要求 |
|---|---|---|
| R1 | §4 顶部 | 增加显式声明：heartbeat = **health telemetry**；不改变 F8 §5.3 单实例假设；非 lease/fencing/互斥原语 |
| R2 | §5.4 / §6.1 | registry 脱节收敛条件按 **D18** 加固（证据条件 + 超时倍数），关闭 submit→handle 注册窗口 |
| R3 | §6.3 / §10 | 「每 task ≤1 terminal event」限定为**单实例 supported envelope**；记录跨进程重复事件为已知限制（D19） |
| R4 | §7 / §8 前置 | 写死：scanner MUST 复用 `get_controller()` 进程级单例（handle registry 权威）；store 不可用 → scanner 停用 |
| R5 | §5.4 P6 / §6.1 / §7 | owner 分类升级为三分法（**D15**），并明确 `GOVERNANCE_OWNER_INSTANCE` 环境变量导致的 owner 跨重启不变场景 |
| R6 | §7 / §8 | 引入 `RuntimeHealthScanner` 模块归属与接口（**D14**）；`server.lifespan` 仅接线（startup sweep 调用 + periodic task + finally cancel）；心跳 writer 归属（controller watchdog 内节流调用，或独立 `heartbeat.py`）需二选一并写明 |
| R7 | §13 / §14 | PG 写成本具体化（节流 + 抖动相位；批量写列为后续评估）；把「事件循环内同步 DB 写」列为已知开销 |
| R8 | §4.3 / §12 | 健康行清理责任移到 scanner（**D10/D17** 修订版）；`off` 模式停写心跳 |
| R9 | §13 | 三模式语义表（`off`/`detect`/`enforce`）× （写心跳 / 判定 / 收敛 / 清理）四列矩阵，消除歧义 |
| R10 | §16 | 要求实现提供可注入 clock（`now_fn`）与可注入 scan 触发器，否则时间类用例不可确定性验证 |
| R11 | §12 / §16 | 迁移影响清单补充：`test_runtime_governance.py` 的迁移版本断言、`test_sessions_store.py`、`test_sessions_postgres.py`、`test_runtime_governance_postgres.py`；方案 B 另需 `TASK_COLUMNS` 与 PG 列类型断言（若最终选 A 则无需） |

**Review Focus 三项结论小结**：
- **A. 首拍时机**：选 **`_execute_governed` 起点（handle 注册后）**；`submit_task` 不写心跳；需冻结为 D13（R1/R2 配套）。
- **B. Startup sweep 时序**：选 **方案 A（yield 前）**——readiness 影响有界（可加时间预算 ≤2s；且现有 lifespan 已 await checkpoint init + agent prewarm，重量级远大于一次有界 sweep）；数据一致性最优（首请求前账本干净，避免「遗留 running + 新 submit/归档守卫」的不确定收敛顺序）；failure behavior 明确（跳过 + log，不 fail-fast）。
- **C. Scanner 归属**：**应当抽象 `RuntimeHealthScanner`**（见 D14/R6），避免 `server.py` 膨胀且提升可测性（fake clock/fake store），并为 P2-4 提供复用点。

---

## 6. Implementation Readiness Decision

**NOT READY for Implementation Planning。**

后续顺序（本轮不执行）：

1. **更新 Spec**：落地 R1–R11，并把 D13–D19 写入 Open Questions/Decision Gates；
2. **用户裁决**：D5（F8 映射冲突）为阻塞项；D13–D18 需明确选择；其余建议项确认；
3. **Decision 登记**：仅登记经批准的 Decision（`D-Phase2-P2-2-001…-00x`）——**需单独批准修改 DECISION.md**（本轮未改）；
4. **单独批准 Implementation Planning**：P2-2 属 L3（runtime lifecycle + additive schema/migration）→ 按 PROCESS §11.3 走 Readiness Review + Decision Closure，之后方可创建 `feature/runtime-p2-2-heartbeat-stale-reclaim` 分支进入实现。

本轮确认：
- ✅ zero code changes（`app/**` diff 为空）
- ✅ zero migration changes（`db/**` 未触碰）
- ✅ zero frontend changes
- ✅ zero test changes
- ✅ zero implementation branch（分支仍 `main`）
- ✅ 未 commit / push / merge
- ✅ P2-3 / P2-4 / P2-5 未触碰
