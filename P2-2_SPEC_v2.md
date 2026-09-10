# P2-2 Runtime Heartbeat / Stale Detection / Reclaim — Spec v2 · Rev 2.2

Phase-2 Runtime Reliability — Batch P2-2（**当前权威版；Rev 2.2 = 剩余 7 项 Gate 批准落地（D3/D6/D7/D8/D9/D11/D12）；本轮零代码改动**）

| 项 | 值 |
|---|---|
| 版本 | **v2 · Rev 2.2**（v2 = 整合 `P2-2_SPEC_REVIEW_REPORT.md` 全部 R1–R11 + Gate D13–D19；**Rev 2.1 = Gate D15 liveness 修订**；**Rev 2.2 = D3/D6/D7/D8/D9/D11/D12 批准落地**，见修订记录） |
| v1（已 Review 基线） | `P2-2_RUNTIME_HEARTBEAT_STALE_RECLAIM_SPEC.md`（保留为历史基线，不再修改） |
| 权威性 | **本文件（v2）为当前权威 Spec**；v1 仅作 Review 留痕 |
| 阶段 | P2-2 Spec（等待批准 → 之后另行批准 Implementation Planning） |
| 基线 | `main` = `origin/main`；P2-1 merge `342483c` / impl `dd7af9a`；另有 harness 文档 commit `98e4e5a` |
| 本轮禁止 | 不改代码 / 不改 migration / 不改测试 / 不改 frontend / 不建 branch / 不 commit·push·merge / 不改 DECISION.md / 不进入 P2-3·P2-4·P2-5 / 不新增 HTTP API / 不新增 TaskStatus |

**修订记录**

- **v2.2（当前权威版；剩余 7 项 Gate 批准落地）**：D3（确认次数 2，下限 1 需 opt-in）、D6（`flush_pending`
  本批不接线）、D7（startup sweep 采方案 A：yield 前有界 + 预算 + fail-open，并显式记录对 F8 §7.3 c3 的
  取舍）、D8（scanner interval 默认 **30s → 15s** + ±20% 抖动）、D9（PG 心跳：节流 + 抖动，**不引连接池、
  不新增依赖**，单语句短事务）、D11（Problem 登记时机 = Implementation 起点）、D12（**本批零 frontend
  改动**，并更正 §9/§21「文案缺口」表述）→ 对应 `D-Phase2-P2-2-011…017`。同步更新
  §5.2 / §6.5 / §7.2 / §8 / §9 / §11 / §13 / §20 / §21 / §22。
- **v2.1（Gate D15 liveness 修订）**：`SAME_HOST_PREV` **取消「立即收敛」**，改为必须满足 liveness 证据
  `now - last_heartbeat_at > max(3 × RUNTIME_HEARTBEAT_INTERVAL, 15s)`（无健康行 → legacy 回退
  `now - max(started_at, created_at) > 同阈值`）；`CURRENT` 维持 deadline 阈值 `wall_clock + grace`；
  `FOREIGN` 维持只观测。同步更新 §5.5 / §6.1 / §6.2 / §7.3 / §14 F11 / §20 / §21 / §22；
  决策登记 `D-Phase2-P2-2-010`（append-only）。动机：防止 rolling / blue-green overlap 期间新进程
  startup sweep 误杀仍在心跳的旧进程任务。
- v2（本文件）：吸收 Review 报告 R1–R11；新增架构组件 `RuntimeHealthScanner`（R6）；细化首拍时机（R2/D13）、registry 脱节证据（R2'/D18）、owner 三分法（R5/D15）、模式矩阵（R9）、可注入 clock（R10）、PG 成本约束（R7）、清理责任迁移（R8）、迁移/冻结断言影响清单（R11）；新增 `### Pending Decision Closure`（D5、D13–D19，状态 proposed / awaiting approval）。
- v1：初版（已 Review，结果 PASS WITH CHANGES、Implementation Readiness = NOT READY）。

---

## 0. INSPECTION 证据清单（读了什么、为什么读）

| 对象 | 关键结论（对本 Spec 的作用） |
|---|---|
| `app/runtime/governance/controller.py` | `terminalize()` 唯一终态 funnel：同进程内存裁决短路（L271-274）→ 不重发事件；DB 已终态走 `_already_terminal_db_result`（L286-288）；**跨进程 CAS 败者**（L316→L319-324 采纳 DB 事实）仍返回 `winner=True` → 可重复发事件；`_execute_governed` 顺序 = `start_task`（非致命）→ ctx → `ensure_future` → **handle 注册（L624-632）** → watchdog；`get_controller()` 为进程级单例（L1004-1028）；`_default_owner_instance()` = `f"{hostname}-{pid}"`（L147-151）；`flush_pending/pending_snapshot/lifecycle()` 存在但无生产调用方 |
| `app/runtime/governance/models.py` | `TaskStatus` running + 8 终态（含 `aborted` / `orphan_reclaimed`）；`TaskRecord` 字段为唯一权威（无 health 字段） |
| `app/runtime/governance/store.py` | 函数式 store；`terminal_update` = `WHERE task_id=? AND status='running' AND version=?`（rowcount==1 证据）；sqlite 单连接+RLock+WAL、PG 每 `transaction()` 一短连接（L155-170） |
| `db/governance_migrations/0001_governance.{sqlite,postgres}.sql` | sqlite 时间列 TEXT / PG TIMESTAMPTZ + JSONB；迁移版本表 `governance_schema_migrations`；additive 先例 = 0002（新表，不动 0001） |
| `app/runtime/governance/events.py` | 单写者 + `(task_id,seq)` + PK 幂等 + fail-open；`orphan_reclaimed → task_orphan_reclaimed`、`aborted → task_aborted` 映射**已存在** |
| `app/runtime/governance/service.py`（P2-1 后） | `submit_task`：normalize → `create_task(running)` → `lifecycle_event(task_started)` → `asyncio.create_task(controller.execute(...))` |
| `app/api/server.py`（P2-1 后） | `lifespan`：checkpoint init → `get_main_agent()` prewarm → `yield` → shutdown（**无 governance hook**；scanner 接线点） |
| `docs/spec/2026-09-14-f8-runtime-execution-governance.md` | §5.3 单实例假设（owner/lease/heartbeat 排除在 v1 外）；§6.1 终态语义（`aborted`=restart 收敛、`orphan_reclaimed`=台账与执行者脱节）；§10.1 orphan 三源；§10.2 sweeper/startup sweep（**不得误杀其它实例**） |
| `tests/test_runtime_governance.py` | `TASK_COLUMNS` 冻结字段清单（禁止 Plan 外 lifecycle 字段）＋ `applied_migration_versions == ["0001","0002"]` 断言 |
| `tests/test_sessions_store.py` / `test_sessions_postgres.py` / `test_runtime_governance_postgres.py` | 迁移版本断言（0001+0002）与 PG 列类型断言 → 见 §12.3 影响清单 |
| `PROJECT_CONTEXT.md` §8 | sweeper / startup 自动恢复未做；multi-instance lease/heartbeat 未支持 |
| `P2-2_SPEC_REVIEW_REPORT.md` | R1–R11、D1–D12 结论、D13–D19 新增 Gate、Safety S1–S12 |
| `AGENTS.md` §10/§11、`PROCESS.md` §11、`TESTING.md` §1/§5 | Git/Problem/分级/证据约束 |

---

## 1. Problem Statement

P2-1 已消除「生产任务无治理裸跑」根因，但**进程级/执行器级失联**仍未解决：

1. **进程异常退出遗留 RUNNING**：重启后内存裁决/handle registry 归零，DB 行永久 `running`，无启动期收敛（F8 文档化 future，未实现）。
2. **执行器长时间无心跳**：执行期无任何持久化存活信号；事件循环阻塞、线程卡死、宿主冻结时无从判定。
3. **不可诊断**：无 `last_heartbeat`、无 stale 判定、无 reclaim 记录（现场：永久「研搜中」>30min）。
4. **无确定性收敛路径**：`orphan_reclaimed` / `aborted` 从未被生产路径产出；`flush_pending` 无调用方；无 sweeper。
5. **收敛安全性未定义**：误杀防护、并发恰一次、双后端一致性、重启恢复均无契约。

> P2-2 引入 **Runtime Health Plane**（health telemetry → stale detection → reclaim），经冻结 funnel 收敛。

---

## 2. Goals / Non-goals

### 2.1 Goals

- G1 Heartbeat 持久化存活信号（字段/更新点/频率/事务纪律），**只写 health plane**。
- G2 Stale 确定性判定（公式/grace/时钟/双阶段确认/false-positive 防护）。
- G3 Reclaim 经冻结 funnel 收敛为既有终态，**单实例 envelope 内恰一次、幂等、可审计**。
- G4 Startup Scanner 收敛上一世代遗留 running（F8 §10.2 e）。
- G5 Periodic Scanner 周期检测 stale 并回收（F8 §10.2 c/d）。
- G6 Health 只读派生视图（函数级，**不新增 HTTP API**）。
- G7 SQLite / PostgreSQL 行为一致。
- G8 阈值/间隔/模式可配置，**默认值保持 M-Spec §7.4 与 D-Phase2-P2-2-002**。

### 2.2 Non-goals（明确不做）

- ❌ P2-3 durable timeline / conversation transcript；❌ P2-4 admin runtime 端点；❌ P2-5 标题生成；
- ❌ checkpoint resume / 断点续跑 / 自动重试（reclaim = 收敛，不是恢复）；
- ❌ 多实例 lease / fencing / 共享 PG 互斥（F8 §5.3；见 §4.0 R1）；
- ❌ 新增 TaskStatus 枚举值；❌ 修改默认 policy 语义/默认阈值数值；
- ❌ 改 frontend、`app/agent/**`、`app/research/**`；❌ 新增 HTTP/WS 协议（仅复用既有 `governance_terminal` 帧）。

---

## 3. Current Runtime State

```text
submit（P2-1 后）
  └─ gov_service.submit_task(policy=normalize) → create_task(running) [+ task_started event]
       └─ controller.execute(policy≠None) → _execute_governed
            ├─ start_task(started_at)（失败非致命）
            ├─ BudgetCounter + GovernanceExecution(ctx) + recursion_limit
            ├─ ensure_future → handle 注册 → watchdog（tick 1s，deadline=start+wall_clock）
            └─ 终态：completed / failed / cancelled / timed_out / budget_exceeded（funnel + event）

缺口：① 执行期无持久化存活信号；② 进程退出 → 内存状态消失、DB 行永久 running、无启动收敛；
     ③ 循环阻塞时 watchdog 自身亦不 tick；④ orphan_reclaimed/aborted 无产出路径；
     ⑤ 无 stale 公式 / reclaim 并发幂等契约 / 双后端一致性约定
```

**与 P2-1 governed execution 的关系**：watchdog = 进程内 deadline 执行者（能 tick 才有效）；scanner = 账本侧兜底（进程死亡/循环阻塞时生效）；两者都只能经冻结 funnel 收敛，竞争由「内存裁决 + CAS rowcount」定唯一 winner；P2-1 的 `effective_limits`（含逐任务 `wall_clock_timeout`）是 stale 阈值权威依据。

---

## 4. Heartbeat Model

### 4.0 【v2 新增 · R1】Runtime Health Telemetry 定位（MUST）

- Heartbeat **IS**：Runtime Health **Telemetry**（存活观测信号），仅用于 ① 单实例 stale 判定输入；② 诊断（P2-4 后续消费）。
- Heartbeat **IS NOT**：lease、分布式锁、fencing token、实例所有权仲裁、任务所有权转移原语。
- **MUST NOT** 改变 F8 §5.3 的 single runtime instance / single active backend execution owner 假设；**MUST NOT** 被用作启用多实例的前置条件（多实例仍需独立 Spec）。
- 语义边界：heartbeat 仅表达「所属执行进程在其自身上下文内仍在推进」，不表达「该任务在全集群唯一有效」；**不提供 fencing 能力**。

### 4.1 存储方案（含 Decision Gate）

| 方案 | 设计 | 优点 | 缺点 |
|---|---|---|---|
| **A（推荐）独立健康表** `governance_runtime_health` | `task_id PK / thread_id / run_id / owner_instance / last_heartbeat_at / beat_count / created_at / updated_at` | 保持 F8 lifecycle 字段权威；与「stale 属 health 不属 lifecycle」一致；写放大隔离；P2-4 可复用 | 新增表；需清理策略 |
| B 在 `governance_tasks` 加列 | additive `ADD COLUMN last_heartbeat_at` | 实现最少 | 触碰冻结字段权威（`TASK_COLUMNS` 断言）、宽行写放大、语义混淆 |
| C 无持久化心跳 | 仅 owner/handle + watchdog | 零 schema | **不满足 P2-2 目标**（无心跳即无法判定执行器失联、无诊断数据） |

**推荐 A**；C 仅作 Review 若否决新增表时的降级路径。→ **D1**（awaiting approval）。

### 4.2 字段语义（方案 A）

| 列 | 语义 |
|---|---|
| `task_id` | PK；与 `governance_tasks.task_id` 逻辑关联（**不建 FK**，沿用 0002「派生关联」纪律） |
| `thread_id` / `run_id` | 冗余，便于 thread 维度诊断 |
| `owner_instance` | 写入者身份 = `controller.owner_instance`（`hostname-pid` 或 env 覆盖） |
| `last_heartbeat_at` | 最近心跳（sqlite TEXT ISO-8601 UTC；PG TIMESTAMPTZ；读回归一） |
| `beat_count` | 心跳计数（诊断；**非** fencing） |
| `created_at` / `updated_at` | 首/末写时间（审计） |

### 4.3 更新点

1. **首拍（v2 修订 · R2/D13）**：**`_execute_governed` 执行起点、handle 注册成功后立即写首拍**；**`submit_task` 路径 MUST NOT 写首拍**（理由：heartbeat 语义 = 执行者存活；submit 写会掩盖 `execute()` 早期失败留下的 running 行，并把 stale 基准前移到「尚未开始执行」的时刻）。若首拍写失败 → fail-open（不阻断执行），由 stale/registry 路径兜底。
2. **周期拍（主）**：`controller._watchdog_loop` 每次 tick 检查节流窗口（默认 5s）→ 写一次（仅 governed 执行；bare/dev 不写）。
3. **终态清理（v2 修订 · R8/D17）**：**MUST NOT** 在 controller/funnel 内删除健康行；由 **scanner** 负责清理（见 §4.6/§12.2）。
4. **明确不做**：不在工具/LLM 回调里写心跳（属 P2-3 事件时间线范畴）。

### 4.4 更新频率与抖动（【v2 细化 · R7】）

- 写节流 `RUNTIME_HEARTBEAT_INTERVAL` 默认 **5s**（下限 1s；tick 粒度沿用 1s）。
- **MUST** 加入**抖动相位**：每任务首次节流相位由 `hash(task_id) % interval` 决定（或用 `RUNTIME_HEARTBEAT_JITTER` 百分比），避免 N 个任务同一 tick 齐拍造成的写尖峰。
- 成本量级：N 个并发任务 ≈ N / interval 次写/秒（50 任务 ≈ 10 写/秒）。

### 4.5 Transaction / Persistence 纪律

- **单语句 UPSERT**（双方言同形）：`INSERT ... ON CONFLICT(task_id) DO UPDATE SET last_heartbeat_at=…, beat_count=beat_count+1, updated_at=…`（`events.py` 有 `ON CONFLICT` 先例），包在 `store.transaction()`。
- 心跳写失败 = **fail-open**（health plane；不影响执行/budget/cancel/terminal）。
- **【v2 · R7】MUST NOT 在本批引入连接池**（不新增基础设施/依赖）；PG 仍按每拍短连接，成本以节流 + 抖动控制；批量写留后续评估。

### 4.6 【v2 新增 · R2】Lifecycle Authority MUST 约束（写死不变量）

| 编号 | 约束（MUST） |
|---|---|
| LA-1 | heartbeat 写入**只能**落在 health plane（独立表 UPSERT）；**MUST NOT** 更新 `governance_tasks` 的 `status / terminal_reason / error_kind / error / counters_snapshot / finished_at / version`（实现上以「只含 health UPSERT 语句的专用 store 函数」保证） |
| LA-2 | reclaim 终态收敛**只能**经 `controller.finalize_with_event(...)`；**MUST NOT** 直调 `gov_store.terminal_update` 或任何绕过 funnel 的写路径 |
| LA-3 | health state（healthy / stale / expired）**MUST NOT** 写入 `TaskStatus` / `governance_tasks.status`；仅作为**派生只读视图**（函数）存在 |
| LA-4 | health 行的清理属 scanner 职责，controller/funnel **MUST NOT** 触碰 health 表（除 LA-1 的心跳 UPSERT 外） |

---

## 5. Stale Detection Model

### 5.1 判定公式（逐任务基准）

```text
reference_ts = max( last_heartbeat_at (若存在),
                    started_at (若存在),
                    created_at )
per_task_wall_clock = effective_limits.wall_clock_timeout（P2-1 已落库）
                      └─ 缺失/非法（legacy）→ 回退 RUNTIME_WALL_CLOCK_TIMEOUT（默认 600）
threshold      = per_task_wall_clock + RUNTIME_STALE_GRACE_PERIOD（默认 30s）
age            = now_utc - reference_ts          # 负值/不可解析 → 0（不 stale）
stale_candidate := status='running' AND age > threshold
```

- 逐任务阈值为**权威**（全局常量在显式 1800s 长任务下必然误回收）→ **D2**。
- `max(...)` 兜底 legacy 无心跳行；不因心跳缺失而跳过收敛。

### 5.2 Grace 与双阶段确认

- `RUNTIME_STALE_GRACE_PERIOD` 默认 **30s**（D-Phase2-P2-2-002，env 可覆盖）。
- **双阶段确认**：连续 `RUNTIME_STALE_CONFIRMATIONS`（默认 **2**）次扫描均 stale 才进入 reclaim；单次仅记 stale 视图 + 日志。
- 确认计数为**进程内状态**；重启后清零（更保守，不产生误回收）→ 文档化，不新增机制。
- **Rev 2.2 批准（`D-Phase2-P2-2-011`）**：默认 **2** 次；配置下限 1，取 1 须显式 opt-in 并在日志标注 `aggressive`；**启动 sweep 不适用确认次数**（单遍执行），其误判防护由证据谓词 + owner 分类 + 最小年龄承担。

### 5.3 Clock Semantics（【v2 · R10】可注入）

| 用途 | 时钟 |
|---|---|
| 进程内 deadline（watchdog，P2-1） | `time.monotonic()`（不可持久化） |
| stale 判定（P2-2） | UTC 墙钟；DB 存 ISO-8601（sqlite TEXT / PG TIMESTAMPTZ） |
| 比较位置 | **Python 侧**（避免 `now()/interval` 方言差异） |

- **【v2 · R10】MUST**：新增模块（heartbeat writer / scanner / stale 公式）**必须接受可注入 clock**（`clock: Callable[[], datetime]`，默认 `lambda: datetime.now(timezone.utc)`）；**禁止**在新增模块内散落隐式 `datetime.now()`。理由：时间边界/回拨/前跳用例必须确定性可测（不靠 sleep）。
- `age < 0` → 0（时钟回拨绝不触发 stale）；不可解析时间 → 跳过该行并记 diagnostic。
- 单主机假设（F8 §5.3）；跨主机漂移不在支持范围；NTP 前跳由 grace + 双阶段吸收。
- 秒级精度（沿用 `_now_iso()` `timespec="seconds"`）。

### 5.4 False-positive 防护（全部成立才可 reclaim）

| # | 防护 | 说明 |
|---|---|---|
| P1 | in-process handle 存活 | `ctl.has_active_handle(task_id)` 且未 done → **绝不 reclaim** |
| P2 | 内存终态裁决存在 → 跳过 | `ctl.terminal_decision(task_id) is not None` |
| P3 | 最小年龄守卫 | `now - created_at < RUNTIME_RECLAIM_MIN_AGE`（默认 = max(2×心跳, 10s)）→ 跳过 |
| P4 | grace + 双阶段确认 | §5.2 |
| P5 | 非 running 跳过 | 含终态与无行 |
| **P6（v2 修订 · R5/D15）** | **owner 三分法**（见 §5.5） | 各分类行为不同；FOREIGN 永不收敛 |
| P7 | 模式开关 | `RUNTIME_RECLAIM_MODE`（见 §13 矩阵） |
| P8 | 每周期上限 | `RUNTIME_RECLAIM_MAX_PER_CYCLE`（默认 20） |
| P9 | 扫描单飞 | 上周期未完 → 跳过本周期 |
| **P10（v2 新增 · R2'/D18）** | **registry 脱节证据条件** | 见 §6.1（不改仅凭 `age>MIN_AGE` 触发） |
| **P11（v2 新增 · R4）** | **scanner 必须复用进程级 controller** | 见 §7.0（防同进程全量误回收） |

### 5.5 【v2 新增 · R5/D15】owner 三分法（确定性分类与行为）

```text
CURRENT          : owner_instance == controller.owner_instance（字符串相等）
                   → 本实例（含 env 显式设置 owner 且跨重启不变的场景）
SAME_HOST_PREV   : owner 可解析为 <host>-<pid>（rsplit('-',1)）且 host == 本机 hostname，
                   且 owner != current owner → 上一世代（重启遗留）
FOREIGN          : 其余（其它主机 / 无法解析且不等于 current owner / 未知格式）
```

**证据谓词（Rev 2.1 冻结定义；两分类共用同一参数，不新增配置）**

```text
liveness_evidence(row) :=                      # 「执行者已停止心跳」证据
    有健康行 ： now - last_heartbeat_at > max(3 × RUNTIME_HEARTBEAT_INTERVAL, 15s)
    无健康行（legacy fallback）：
              now - max(started_at, created_at) > max(3 × RUNTIME_HEARTBEAT_INTERVAL, 15s)

deadline_evidence(row) := now - max(last_heartbeat_at?, started_at, created_at)
                          > effective_limits.wall_clock_timeout + RUNTIME_STALE_GRACE_PERIOD
```

| 分类 | startup sweep | periodic scan | 终态 | 备注 |
|---|---|---|---|---|
| CURRENT | 需满足 **`deadline_evidence`**（维持 v2；env 常量 owner 场景无法与活进程区分）→ `aborted` | registry 脱节路径（需 P10 证据）→ `orphan_reclaimed`；heartbeat stale（`deadline_evidence` + 双阶段确认）→ `orphan_reclaimed` | 见左 | 防「同机误起第二进程且 owner 字符串相同」时误杀活任务 |
| SAME_HOST_PREV | **需满足 `liveness_evidence`**（**Rev 2.1**：不再因 owner generation 不同而立即收敛；另受 P3 最小年龄约束）→ `aborted` | `liveness_evidence` 或 `deadline_evidence` → `aborted` | `aborted` | F8 §10.2 e restart 语义；防止 rolling / blue-green overlap 期间误杀仍在心跳的旧进程 |
| FOREIGN | **不收敛**，仅计数/日志 | **不收敛**，仅计数/日志 | — | F8 §10.2 明文「不得误杀其它实例」 |

- owner 默认来源：`_default_owner_instance()` = `f"{socket.gethostname()}-{os.getpid()}"`。
- `GOVERNANCE_OWNER_INSTANCE` 显式设置：跨重启 owner 字符串不变 → 归 CURRENT，走 `deadline_evidence` 路径（更慢但安全）；若运维改变该值或使用不可解析格式 → 归 FOREIGN（**只观测**），需在 Operator Note 中说明。
- **Rev 2.1 安全不变量**：① 活跃的旧进程在 rolling / blue-green overlap 期间**不得**被新进程 startup sweep 误杀（持续心跳 ⇒ `liveness_evidence` 不成立 ⇒ 不收敛）；② 真正停止心跳的 `SAME_HOST_PREV` 任务仍可在 **~15s 级**进入 reclaim（远快于等 `deadline_evidence` ≈ 630s）；③ 不新增 TaskStatus、不改 F8 lifecycle authority；④ heartbeat 仍只是 Runtime Health Telemetry，**非 lease/fencing**。

---

## 6. Reclaim Model

### 6.1 Reclaim 条件（v2 修订）

| 触发 | 条件（全部满足） | 终态（推荐，见 D5） |
|---|---|---|
| **Startup sweep** | row=running；`age > RUNTIME_RECLAIM_MIN_AGE`；且按 owner 分类满足：`SAME_HOST_PREV` → **`liveness_evidence`**（**Rev 2.1**）；`CURRENT` → **`deadline_evidence`**；`FOREIGN` → 不参与 | `aborted`（F8 §10.2 e） |
| **Periodic — heartbeat stale** | P1–P11 全部成立 + 双阶段确认 + `deadline_evidence` | `orphan_reclaimed`（F8 §10.2 c/d） |
| **Periodic — registry 脱节** | row=running；owner 分类 = CURRENT；**无 handle**；`age > MIN_AGE`；**且（【v2 · R2'/D18】证据条件）**：`心跳行存在` **或** `started_at 非空`；**且** `now - max(last_beat, started_at) > max(3 × RUNTIME_HEARTBEAT_INTERVAL, 15s)` | `orphan_reclaimed`（F8 §10.2 c） |
| policy 超期（F8 §10.2 d，可选） | 等价于 heartbeat stale（不另设开关） | `orphan_reclaimed` |

**为何需要 P10 证据条件**：`submit_task` 先建 running 行，**之后**才 `ensure_future` + 注册 handle；若循环被阻塞 > MIN_AGE，仅凭「无 handle + age」会把**刚提交尚未开始**的任务误判为 registry 脱节。证据条件确保「曾真正进入执行」（心跳行或 `started_at`）且「已超心跳超时倍数」。注意 `start_task()` 失败非致命 → 不能单独依赖 `started_at`，两者取「或」。

**Rev 2.1（Gate D15）修订说明**：startup sweep 的 `SAME_HOST_PREV` 分支**不得**仅凭「owner generation 不同」即收敛；必须叠加 `liveness_evidence`（停止心跳）。反之，`CURRENT` 分支仍用 `deadline_evidence`（更保守），因为 env 常量 owner 场景下无法区分活进程。`FOREIGN` 永不收敛。该修订不改变终态映射（仍为 `aborted`，见 D5），不新增 TaskStatus，不改变 F8 lifecycle authority。

### 6.2 状态转换（只经冻结 funnel）

```text
running ──(startup: SAME_HOST_PREV+liveness / CURRENT+deadline)──▶ aborted   [+ task_aborted]
running ──(periodic: heartbeat stale, 双阶段确认)────────▶ orphan_reclaimed [+ task_orphan_reclaimed]
running ──(periodic: registry 脱节, 满足 P10 证据)───────▶ orphan_reclaimed [+ task_orphan_reclaimed]
（不自动 resume；checkpoint/research 只读不写 —— F8 §10.2 e）
```

- 路径：`await ctl.finalize_with_event(task_id, TerminalReason.ABORTED | ORPHAN_RECLAIMED, error=<诊断字符串>)`；`error_kind=None`（既非 agent 失败亦非 governance control failure；`_validate_terminal_fields` 允许）。
- 结果：`status` / `terminal_reason` / `finished_at` / `version+1`；重启场景 `counters_snapshot=None`（内存 counter 已失，**不伪造**）；`underlying_linger_observed` 保持 NULL。

### 6.3 幂等性与 exactly-once（【v2 修订 · R3/D19】限定 envelope）

- **Supported envelope = 单 runtime instance（F8 §5.3）**。
- 单实例内：内存裁决短路（`controller.py:271-274`）+ DB 已终态采纳（L286-288）⇒ **每 task ≤1 条 terminal lifecycle event**。
- **已知限制（D19）**：**跨进程**并发收敛时，CAS 败者的 `terminalize` 会在 retry 中采纳 DB 事实但仍返回 `winner=True, already_terminal=False`（L316→L319-326），`finalize_with_event` 因此**可能重复发布** terminal 事件。该场景**不在支持范围**（多实例未支持）；硬化方案（采纳 DB 事实时标记 `adopted_from_db` 并在发布前短路）属对冻结 controller 的改动，**本批不做**，仅作为已知限制记录。

### 6.4 并发控制 / Race 矩阵

| 竞争 | 处理 |
|---|---|
| scanner vs watchdog(timed_out) | 内存裁决先到者胜；后到者 no-op；DB 侧 CAS 双保险 |
| scanner vs cancel | 同上（`cancelled` 与 reclaim 互斥） |
| scanner vs 新 submit（supersede） | submit 先 cancel 旧任务；scanner 若已裁决 → no-op |
| scanner vs 正常完成 | 完成先写 terminal → scanner 采纳/跳过 |
| scanner vs pending_terminal | P2 防护：内存裁决存在 → 跳过 |
| 两个 scanner 实例 | CAS 唯一 winner；**仍以单实例为支持边界**；FOREIGN 分类进一步降低跨实例影响 |
| scanner vs shutdown | periodic task 在 lifespan finally cancel + await；已在 funnel 内的收敛不中断 |
| scanner vs 刚提交任务 | P3 最小年龄 + P10 证据条件 |

### 6.5 Restart Recovery

1. 启动 → governance store init（ensure_schema）→ **startup sweep**（有界、批量、时间预算）；
2. 逐行按 §5.5 分类 + §6.1 条件收敛 → `aborted`；
3. 失败（DB 不可写）→ **不构造内存 pending 语义**，仅 log + 计数，交由下周期/下次启动（保持 funnel 单一语义）；
4. 输出统计（scanned/classified/reclaimed/skipped/errors/mode）；
5. `detect` 模式：只输出将回收清单（日志 + health 视图），不改 DB；
6. `flush_pending()` **本批不接线**（D6）：其目标场景（DB 写失败遗留 running）由 sweep 覆盖，但可能收敛为 `aborted`/`orphan_reclaimed` 而非原裁决 → 语义空白已在此记录。
7. **Rev 2.2 批准（`D-Phase2-P2-2-012`）**：本批确认**不接线** `flush_pending`（startup flush 在新进程恒为空操作；周期 reconciler 属 F8 文档化 future，越出本批范围）；`shutdown flush` 记为后续候选（需独立批准，且须硬超时 + fail-open）；「pending → sweep 收敛」导致的**终态语义漂移**列为已知限制。

---

## 7. Startup Scanner

### 7.0 【v2 新增 · R4】Scanner 与 controller 的关系（MUST）

| 编号 | 约束 |
|---|---|
| SC-1 | scanner **MUST** 由 **`get_controller()` 进程级单例**（`controller.py:1004-1028`）构造；`get_controller()` 是**唯一入口** |
| SC-2 | **MUST NOT** 自行 `new GovernanceController(...)`：新实例的 `_handles` 恒空，会把同进程所有活跃任务判为「无 handle」→ 全量误回收（🔴 高危） |
| SC-3 | `get_controller()` 返回 `None`（store 不可用 / 初始化失败）→ **scanner 停用**（startup: log + skip；periodic: 不启动） |
| SC-4 | scanner 不得修改 controller 的任何冻结语义；仅调用 `finalize_with_event` / 只读查询（`get_task` / `has_active_handle` / `terminal_decision`） |

### 7.1 【v2 新增 · R6】RuntimeHealthScanner 架构

```
app/runtime/governance/heartbeat.py   # HeartbeatWriter：节流 + 抖动 + UPSERT（clock 注入）
app/runtime/governance/reclaim.py     # RuntimeHealthScanner：startup sweep / periodic / 分类 / 收敛 /
                                      # health 视图 / 健康行清理（clock 注入）
```

```python
class HeartbeatWriter:                     # 由 controller 持有并调用（不新增 asyncio task）
    def beat(self, *, task_id, thread_id, run_id, owner) -> bool   # 节流窗口内直接返回 False
    def forget(self, task_id) -> None                              # 仅 scanner 清理时使用（见 LA-4）

class RuntimeHealthScanner:
    def __init__(self, controller, *, config, clock=None)          # clock 可注入（R10）
    async def startup_sweep(self) -> ScanStats                     # 有界 + 预算 + fail-open
    async def run_periodic(self) -> None                           # 可 cancel；单飞；抖动
    def classify_owner(self, owner_instance) -> OwnerClass         # CURRENT / SAME_HOST_PREV / FOREIGN
    def health_view(self, task_id) -> dict | None                  # 只读派生视图（无端点）
    def health_snapshot(self, limit: int) -> list[dict]            # 只读列表（无端点）
    async def cleanup_health_rows(self) -> int                     # 终态/孤儿健康行清理（R8）
```

- **server.py 只做生命周期接线**（≈10 行）：`ctl = get_controller()` → 构造 scanner → `await scanner.startup_sweep()` → `periodic = asyncio.create_task(scanner.run_periodic())` → shutdown 时 `cancel() + await`。
- **MUST NOT** 把扫描/分类/收敛逻辑写进 `server.py`（Review Focus C / R6）。
- 依赖方向：`reclaim.py → controller / store / events`（单向）；controller **不**依赖 scanner（仅持有 writer）。

### 7.2 Startup sweep 时序（【v2 决议倾向 · R7/D7】方案 A）

```text
方案 A（推荐）：store init（ensure_schema）→ startup sweep（有界 + 预算）→ yield → 服务流量
方案 B        ：store init → yield → background startup sweep
```

| 维度 | 方案 A（推荐） | 方案 B |
|---|---|---|
| readiness 影响 | 有界：batch 上限（默认 200）+ 时间预算（建议 ≤2s）+ fail-open；现有 lifespan 已 await checkpoint init + agent prewarm，量级远大于一次有界 sweep | 无影响 |
| 数据一致性 | 首请求即见干净账本；避免「遗留 running 与新 submit/归档守卫」的不确定收敛顺序（否则旧行可能被 submit 的 `cancel_governed` 收敛为 `cancelled` 而非 F8 语义的 `aborted`） | 窗口内可能出现遗留 running + 非确定性终态 |
| failure behavior | store 不可用/超时 → log + 跳过（**不 fail-fast**，保持现有启动语义，D7） | 同 |

→ **方案 A（Rev 2.2 已批准，`D-Phase2-P2-2-013`）**：`yield` 前执行有界 sweep（batch ≤200、预算 ≤2s、fail-open）；store 不可用/超预算 → 跳过 + 日志，**不 fail-fast**（对 F8 §7.3 c3 的显式取舍，已记录）。

### 7.3 Startup sweep 参数

| 项 | 值 |
|---|---|
| batch | `RUNTIME_SCAN_BATCH_LIMIT`（默认 200） |
| 预算 | `RUNTIME_STARTUP_SWEEP_BUDGET`（默认 2s；超时即停止，剩余交 periodic） |
| 范围 | 仅 §5.5 分类 `SAME_HOST_PREV`（**需 `liveness_evidence`**，Rev 2.1）与 `CURRENT`（需 `deadline_evidence`）；`FOREIGN` 只统计不收敛 |
| liveness 阈值 | `max(3 × RUNTIME_HEARTBEAT_INTERVAL, 15s)`；无健康行 → legacy 回退 `now - max(started_at, created_at)`（复用 D18 参数，不新增配置） |
| 误杀防护 | 活跃旧进程（rolling / blue-green overlap）持续心跳 ⇒ `liveness_evidence` 不成立 ⇒ startup sweep 不收敛 |
| 幂等 | 每次启动执行；重复/多进程由 CAS 保证恰一次（§6.3 envelope） |
| 失败 | fail-open，不阻塞启动（D7 批准前提） |

---

## 8. Periodic Scanner

| 项 | 设计 |
|---|---|
| 归属 | `RuntimeHealthScanner.run_periodic()`（§7.1），server lifespan 仅 create/cancel |
| 周期 | `RUNTIME_SCAN_INTERVAL` 默认 **15s** + **±20% 抖动**（Rev 2.2 批准，`D-Phase2-P2-2-014`；30s 亦为可接受配置；单飞 + 空闲跳过） |
| 工作 | ① 取 running 候选（batch 上限）；② 计算公式 stale（§5.1）+ 双阶段确认；③ §5.5 分类 + §5.4 防护；④ 满足条件经 funnel 收敛；⑤ 清理健康行（§4.6/§6.1 → R8）；⑥ 输出统计 |
| 单飞 | 上周期未完成 → 跳过（P9） |
| 模式 | `off` 不启动；`detect` 只判/清理不改终态；`enforce` 全功能（§13 矩阵） |
| 与 startup | 共用 `classify_owner` / `reclaim` 实现，仅条件与终态不同（§6.1） |

---

## 9. Task Lifecycle / Status Compatibility

| 项 | 结论 |
|---|---|
| TaskStatus | **零新增**（F8 冻结）；`aborted` / `orphan_reclaimed` 均为既有终态 |
| TERMINAL_STATUSES | 不变 |
| Health vs Lifecycle | `status` 只表业务生命周期；`healthy/stale/expired` 为**派生只读视图**（LA-3） |
| 终态语义 | `aborted`=restart/实例收敛；`orphan_reclaimed`=台账与执行者脱节（stale/registry），与 F8 §6.1 一致 |
| Session | 不新增 session 状态；reclaim 后 archive 守卫自然解除 |
| Frontend | **本批零改动（Rev 2.2 批准，`D-Phase2-P2-2-017`）**；reclaim 后经既有 durable `GET /api/tasks/{id}` 校正即可脱离「研搜中」；**表述更正（证据）**：`aborted`/`orphan_reclaimed` 标签与提示**已存在**（`frontend/src/lib/taskStatus.ts:13-14`、`frontend/src/components/ConversationThread.tsx:323-326`），真实缺口仅为「不展示 `error` 原因」（仅 `failed` 分支展示，`ConversationThread.tsx:311-314`）→ 记为后续项 |
| Event replay | 既有 replay/WS since_seq 自动携带新 terminal 事件（无协议变更） |

---

## 10. Event / Observability Semantics

- **durable**：复用 `gov_events.lifecycle_event()` → `task_aborted` / `task_orphan_reclaimed`（映射已存在），单写者/`(task_id,seq)`/PK 幂等/fail-open 不变。
- **诊断载体（【v2 · R/D16】）**：P2-2 **只用 `error` 字符串**（≤800 字符，`_clip_error` 既有约束），内容形如
  `reclaim: trigger=heartbeat_stale owner_class=CURRENT last_heartbeat=… threshold=630s age=…`。
  **MUST NOT** 修改 `events.lifecycle_event` 的 payload 构造（避免触碰冻结 observation 模块）；payload 扩展留 P2-4（D16 待批准）。
- **live**：经既有 `governance_terminal` bridge 帧推送；不改 WS 协议。
- **日志**：每周期一行统计（scanned/stale/confirmed/reclaimed/skipped/errors/mode/owner_class 分布）；每任务回收一行结构化字段；无凭据、无 payload 正文。
- **只读函数（P2-4 后续复用，无端点）**：`health_view` / `health_snapshot`。
- **不属于本批**：agent-step/tool 事件持久化、时间线、transcript（P2-3）；admin 端点（P2-4）。

---

## 11. SQLite / PostgreSQL Compatibility

| 关注点 | 约定 |
|---|---|
| DDL 双方言 | 0003 迁移提供 `*.sqlite.sql` + `*.postgres.sql`；时间列 sqlite TEXT / PG TIMESTAMPTZ（沿用 0001） |
| IF NOT EXISTS | 新表用 `CREATE TABLE IF NOT EXISTS`；版本表保证幂等（方案 B 的 ADD COLUMN 无 IF NOT EXISTS → 依赖版本表） |
| UPSERT | `ON CONFLICT(task_id) DO UPDATE`（双方言同形） |
| 时间比较 | **一律 Python 侧**；读回经归一（PG datetime / sqlite str） |
| 事务 | 心跳单语句 + `store.transaction()`；reclaim 复用 funnel CAS（无长事务） |
| PG 成本 | **Rev 2.2 批准（`D-Phase2-P2-2-015`）**：逐拍节流 + 抖动相位；**不引入连接池、不新增依赖**；心跳写必须**单语句短事务**（禁止在同一调用内追加查询/锁/重试风暴）；批量 writer 列为应急候选（需独立批准）；须遵守 F8 §7.2（control-path 不因 observation 开销退化） |
| 隔离级别 | 依赖既有「单语句 CAS + rowcount」证据（与 terminal CAS 同语义） |
| 迁移断言 | 见 §12.3 影响清单 |

---

## 12. Schema / Migration Proposal（**仅设计；本轮不创建**）

### 12.1 方案 A（推荐）——`db/governance_migrations/0003_runtime_health.{sqlite,postgres}.sql`

```sql
-- sqlite 方言（postgres 同形，时间列 TIMESTAMPTZ）
CREATE TABLE IF NOT EXISTS governance_runtime_health (
    task_id            TEXT PRIMARY KEY,
    thread_id          TEXT NOT NULL,
    run_id             TEXT,
    owner_instance     TEXT NOT NULL,
    last_heartbeat_at  TEXT NOT NULL,
    beat_count         INTEGER NOT NULL DEFAULT 1,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runtime_health_heartbeat
    ON governance_runtime_health(last_heartbeat_at);
```

- 与 `governance_tasks` **不建 FK**；**不动 0001 表族**（`governance_tasks` DDL 零改动）。
- **【v2 · R8】清理责任**：由 scanner `cleanup_health_rows()` 承担（终态任务行 + 孤儿健康行），**不在 controller/funnel**（LA-4）；清理滞后 ≤ 1 个扫描周期。
- 备选方案 B：`ALTER TABLE governance_tasks ADD COLUMN last_heartbeat_at` + 索引 `(status, last_heartbeat_at)`（不推荐，见 §4.1）。

### 12.2 【v2 新增 · R8】健康行生命周期

| 阶段 | 责任方 | 行为 |
|---|---|---|
| 首拍 | controller（`_execute_governed` 起点，经 HeartbeatWriter） | UPSERT 一行 |
| 周期拍 | controller watchdog tick（经 HeartbeatWriter，节流 + 抖动） | UPSERT 更新 |
| 任务终态 | **scanner**（下个周期） | 删除该 task 的健康行 |
| 孤儿行（task 不存在） | **scanner** | 删除 |
| `off` 模式 | — | 不写心跳 ⇒ 无增长（§13 矩阵） |

### 12.3 【v2 新增 · R11】Migration / Test Freeze Impact（实现阶段需更新的冻结断言）

| 文件 | 断言位置 | 影响 |
|---|---|---|
| `tests/test_runtime_governance.py` | `applied_migration_versions`（L94-127，3 处） | 期望值 → `["0001","0002","0003"]` |
| `tests/test_sessions_store.py` | L87-125（3 处） | 同上 +0003 |
| `tests/test_sessions_postgres.py` | L60-67 | 同上 +0003 |
| `tests/test_runtime_governance_postgres.py` | L90-92 | 同上 +0003 |
| `tests/test_runtime_governance.py` `TASK_COLUMNS`（L44-63） | **方案 A 下无需修改**；方案 B 下必须加列 | 冻结字段权威 |
| `tests/test_runtime_governance_postgres.py` 列类型断言（L96-123） | 同上（仅方案 B 受影响） | — |

- `governance_tasks` DDL 与 `governance_events` DDL **零改动**（方案 A）。
- 新增测试（P2-2 自己的 suite）在实现阶段编写，见 §16。

---

## 13. Configuration Proposal

| env | 默认 | 作用 | 归属 |
|---|---|---|---|
| `RUNTIME_HEARTBEAT_INTERVAL` | `5`（秒，下限 1） | 心跳写节流 | P2-2 |
| `RUNTIME_HEARTBEAT_JITTER` | `20`（%） | 首拍相位抖动 | P2-2 |
| `RUNTIME_STALE_GRACE_PERIOD` | `30`（秒） | grace（D-Phase2-P2-2-002） | P2-2 |
| `RUNTIME_STALE_CONFIRMATIONS` | `2`（下限 1；取 1 需显式 opt-in 并标注 `aggressive`） | 双阶段确认次数（`D-Phase2-P2-2-011`） | P2-2 |
| `RUNTIME_SCAN_INTERVAL` | **`15`**（秒，±20% 抖动；30s 亦可接受） | 周期扫描（`D-Phase2-P2-2-014`） | P2-2 |
| `RUNTIME_SCAN_BATCH_LIMIT` | `200` | 单遍批量上限 | P2-2 |
| `RUNTIME_STARTUP_SWEEP_BUDGET` | `2`（秒） | 启动 sweep 时间预算 | P2-2 |
| `RUNTIME_RECLAIM_MAX_PER_CYCLE` | `20` | 每周期收敛上限 | P2-2 |
| `RUNTIME_RECLAIM_MIN_AGE` | `10`（秒） | 最小年龄守卫（P3） | P2-2 |
| `RUNTIME_RECLAIM_MODE` | `enforce` | 模式开关（矩阵见下） | P2-2 |
| `RUNTIME_WALL_CLOCK_TIMEOUT` | `600`（M-Spec §7.4，默认不变） | legacy 行 stale 回退基准（主基准仍为逐任务 `effective_limits`） | D-Phase2-P2-2-002 |

### 13.1 【v2 新增 · R9】`RUNTIME_RECLAIM_MODE` 行为矩阵（写死）

| 行为 \ 模式 | `off` | `detect` | `enforce`（默认） |
|---|---|---|---|
| 心跳写入 | ❌ 不写 | ✅ 写 | ✅ 写 |
| stale 判定 | ❌ 不判 | ✅ 判（记录） | ✅ 判 |
| reclaim 收敛（改 DB 终态） | ❌ | ❌（仅日志/视图） | ✅ |
| 健康行清理 | ❌（无写入 ⇒ 无增长） | ✅ | ✅ |
| terminal 事件发布 | ❌ | ❌（未收敛） | ✅（经 funnel） |
| 等价行为 | ≈ P2-1 基线 | 灰度观察 | 完整 P2-2 |

- 模式在 **启动时读取一次**（变更需重启）；非法取值 → 回退默认 `enforce` + diagnostic 日志。
- 解析纪律：所有数值型 env 非法 → 回退默认 + diagnostic（**不 fail-fast**：健康平面不应阻断服务）。

---

## 14. Failure Modes

| # | 故障 | 行为 |
|---|---|---|
| F1 | 心跳写失败 | fail-open + log；执行不受影响；连续失败可能被判 stale（grace/双阶段/最小年龄/handle 检查兜底） |
| F2 | 扫描周期 DB 不可用 | 跳过本周期（不猜测）；log；下周期重试 |
| F3 | reclaim funnel DB 写失败 | 既有 retry → pending（内存裁决权威）；DB 侧由 CAS/下次启动兜底 |
| F4 | terminal 事件写失败 | TaskRecord 仍为真；事件缺失记 durability_gap |
| F5 | 时钟回拨 | `age<0 → 0` → 不 stale |
| F6 | 时钟前跳 / 宿主休眠唤醒 | 可能瞬时 stale；grace + 双阶段 + handle 检查 + 最小年龄吸收；必要时先用 `detect` 观察 |
| F7 | scanner 自身异常 | 捕获 + log；下周期继续（不向 lifespan 外传播） |
| F8 | 大量任务同时 stale | 每周期上限 + 批量上限，分批收敛，抑制事件风暴 |
| F9 | submit→handle 注册窗口误判 | P3 最小年龄 + P10 证据条件 |
| F10 | PID 复用致 owner 字符串相同 | 必要条件仍为「无 handle + 证据 + 超时」；`beat_count` 交叉校验（可选） |
| F11 | rolling / blue-green overlap、同机双进程或 PID 复用（旧进程仍活或 owner 相同） | **Rev 2.1 缓解**：`SAME_HOST_PREV` 必须满足 `liveness_evidence` —— 活跃旧进程持续心跳 ⇒ 不收敛（不误杀）；真正停止心跳的遗留任务 ~15s 级收敛；`CURRENT` 必须满足 `deadline_evidence`；`FOREIGN` 永不收敛；CAS 保证唯一 winner。仍不支持多实例（F8 §5.3） |
| F12 | 健康行缺失（legacy） | 回退 `started_at/created_at` 基准，不跳过收敛 |
| F13 | 优雅关闭期误判 | shutdown **不**触发 startup sweep；periodic 先 cancel；在途任务仍由既有 cancel/close 语义处理 |
| **F14（v2 新增）** | **scanner 误用新 controller** | SC-2 红线 + 代码评审 + 测试（断言 scanner 使用 `get_controller()` 实例） |
| **F15（v2 新增）** | **PG 短连接写尖峰** | 节流 + 抖动；监控写频；批量写列为后续评估（D9） |

---

## 15. Security / Safety Considerations

- 无新攻击面：**不新增 HTTP/WS 端点**（P2-4 才做），不接受用户输入驱动的 reclaim；P005 无鉴权边界不变。
- Fail-closed：任一防护不成立 → 保持 running + 记录（宁可不收敛，不误杀）。
- 资源边界：固定周期 + 批量/每周期上限 + 日志限频；预算限制启动期开销。
- 数据安全：健康表仅存 id/时间/owner（无 prompt/参数/凭据）；日志无凭据。
- 破坏性语义披露：reclaim 不可逆（无 reopen）；`detect` + `off` 提供运行期安全网。
- 多实例安全：FOREIGN 永不收敛（F8 §10.2）；CURRENT 叠加心跳陈旧（D15）。
- 审计：每次 reclaim = 1 条 durable 事件 + 1 行日志，可事后证明依据。

---

## 16. Test Strategy（实现阶段执行；本轮不写测试）

| 类别 | 用例要点 |
|---|---|
| Heartbeat 单元 | UPSERT 只写健康表（断言 `governance_tasks.status/version` 不变）；首拍在 `_execute_governed` 起点（submit 不写首拍）；节流 + 抖动相位；失败 fail-open；clock 注入驱动 |
| Stale 公式 | 边界（=threshold / +1）；NULL 心跳回退；逐任务 `effective_limits`（含 1800s 不误判）；缺快照回退 600；`age<0`；不可解析时间跳过 |
| 双阶段 | 连续 2 次才收敛；中途恢复心跳 → 计数清零 |
| 防护 | P1 handle 存活；P2 内存裁决；P3 最小年龄；P5 非 running；**P10 registry 证据条件（含 started_at 缺失但心跳存在的组合）**；P6 三分法（CURRENT / SAME_HOST_PREV / FOREIGN 各自行为） |
| Reclaim 集成 | startup：SAME_HOST_PREV → `aborted`（version+1/finished_at/1 事件/counters=None）；periodic：stale → `orphan_reclaimed`；registry → `orphan_reclaimed` |
| 幂等/并发 | 重复调用/双 scanner 竞争 → 单终态、单实例内事件恰 1；与 timed_out/cancel 竞争唯一 winner；pending 跳过 |
| 重启恢复 | 构造「旧代 owner + 陈旧心跳」→ 新 controller + startup sweep 收敛；`detect` 只读不改 DB |
| 配置 | env 覆盖/非法回退；`off`/`detect`/`enforce` 矩阵四行为逐格断言 |
| 双后端 | 核心用例 SQLite + PG（PG 门控 `AGENT_CHECKPOINT_DSN_TEST`）；迁移版本断言 +0003；时间列归一 |
| 冻结断言更新 | §12.3 清单（additive 引起的预期变更，计划中显式列出） |
| 回归 | P2-1 套件（23 用例）、governance 全套、sessions、research；全量 sqlite（排除 MySQL 污染文件） |

---

## 17. Rollback Strategy

1. **运行期**：`RUNTIME_RECLAIM_MODE=off`（重启生效）⇒ 心跳/判定/收敛/清理全停（§13.1），行为回 P2-1 基线；`detect` 可先灰度观察。
2. **代码回滚**：revert P2-2 commit 集（heartbeat/reclaim/scanner/server 接线）→ 回到 P2-1 行为。
3. **Schema 回滚**：迁移 additive（新表）⇒ **不回滚 DDL**（仓库无 rollback migration 纪律）；空表不影响 P2-1。
4. **数据后果**：已 reclaim 任务保持终态（`aborted`/`orphan_reclaimed`），**无自动 reopen**（人工恢复需手工 DB 操作，不在本批）。
5. 判据：观察期出现误收敛证据 → 立即 `off`/回滚，并按 AGENTS §11 评估 Problem 登记（D11）。

---

## 18. Explicit Non-goals（复述）

- 不实现 P2-3 / P2-4 / P2-5；不做 resume/重试/断点续跑；不做多实例 lease/fencing；
- 不新增 TaskStatus、不改 terminal funnel/CAS/replay 契约、不改 policy 默认语义；
- 不改 frontend / `app/agent/**` / `app/research/**`；不新增 HTTP API；
- 本轮（Spec v2 阶段）不创建 migration、不改测试、不建 branch、不 commit。

---

## 19. Dependency on P2-1

| 依赖 | 说明 |
|---|---|
| 默认 governed（D-Phase2-P2-1-001） | 生产任务恒有 wall_clock deadline + watchdog + terminal event ⇒ reclaim 才有兜底意义 |
| `effective_limits` 落库 | 逐任务 `wall_clock_timeout` 是 stale 阈值权威（§5.1） |
| 冻结 funnel + `finalize_with_event` | reclaim 唯一收敛入口（LA-2），零改动 |
| terminal event 常开 | reclaim 事件与既有事件流同构（replay/WS 自动可用） |
| 约束 | 若未来出现绕过 `gov_service.submit_task` 的提交路径，stale 判定需重新评估 |

---

## 20. Open Questions / Decision Gates

> 状态说明（**Rev 2.2 / 全部 Gate 已闭合**）：D1–D19 全部闭合——D5/D13/D14/D15/D16/D17/D18/D19
> （`D-Phase2-P2-2-003…010`）、D3/D6/D7/D8/D9/D11/D12（`D-Phase2-P2-2-011…017`）、
> D1/D2/D4/D10 作为已批准决策的必然推论关闭（由 001/002/007 覆盖）。无剩余待裁 Gate。

| Gate | 议题 | 结论 | 状态 |
|---|---|---|---|
| D1 | 心跳存储平面 | 方案 A 独立健康表（`D-Phase2-P2-2-001` 的必然推论） | ✅ ratified |
| D2 | stale 阈值基准 | 逐任务 `effective_limits` + grace（由 `-002`） | ✅ ratified |
| D3 | 双阶段确认 | 默认 2 次（下限 1 需 opt-in 并标注 `aggressive`） | ✅ ratified（`D-Phase2-P2-2-011`） |
| D4 | foreign owner | 只统计不收敛（由 `-001` 与 D15 `FOREIGN`） | ✅ ratified |
| **D5** | **终态映射（原冲突项）** | F8 拆分：restart→`aborted`、stale/registry→`orphan_reclaimed` | ✅ ratified（`D-Phase2-P2-2-003`） |
| D6 | `flush_pending` 交互 | 本批不接线；`shutdown flush` 记后续候选 | ✅ ratified（`-012`） |
| D7 | startup sweep 时序 | 方案 A（yield 前，有界 + 预算 + fail-open；c3 取舍已记录） | ✅ ratified（`-013`） |
| D8 | 扫描间隔 | **15s** + ±20% 抖动（30s 亦可接受） | ✅ ratified（`-014`） |
| D9 | PG 心跳成本 | 节流 + 抖动；不引连接池/依赖；单语句短事务；批量写为应急候选 | ✅ ratified（`-015`） |
| D10 | 健康行清理 | scanner 负责（`D-Phase2-P2-2-007`） | ✅ ratified |
| D11 | Problem Registry | 本轮不登记；Implementation 起点登记正式 Problem（题名/Type 已固定） | ✅ ratified（`-016`） |
| D12 | frontend 边界 | 本批零改动；标签已存在（reason 展示为后续项） | ✅ ratified（`-017`） |
| **D13** | 心跳首拍时机 | `_execute_governed` 执行起点（handle 注册后）；submit 不写 | ✅ ratified（`-004`） |
| **D14** | Scanner 归属与抽象 | `RuntimeHealthScanner`；server 仅接线 | ✅ ratified（`-005`） |
| **D15** | owner 三分法收敛条件 | **Rev 2.1**：`SAME_HOST_PREV` 需 `liveness_evidence`；`CURRENT` 需 `deadline_evidence`；`FOREIGN` 只观测 | ✅ resolved（`D-Phase2-P2-2-010`） |
| **D16** | reclaim 诊断载体 | 仅 `error` 字符串；不改 events payload | ✅ ratified（`-008`） |
| **D17** | 健康行清理责任方 | scanner（controller 不触碰）；`off` 停写心跳 | ✅ ratified（`-007`） |
| **D18** | registry 脱节收敛条件 | MIN_AGE + 证据（心跳行或 started_at）+ liveness 阈值 | ✅ ratified（`-006`） |
| **D19** | 跨进程重复事件硬化 | 本批不做，记录为已知限制（§6.3） | ✅ ratified（`-009`） |

### Pending Decision Closure

**已闭合（ratified / resolved；Decision 记录为 append-only 追加，历史条目未改写）**

| Gate | 决议 | Decision 记录 |
|---|---|---|
| D5 | F8 拆分终态映射（restart→`aborted`；stale/registry→`orphan_reclaimed`） | `D-Phase2-P2-2-003` |
| D13 | 首拍 = `_execute_governed` 执行起点；submit 不写 | `D-Phase2-P2-2-004` |
| D14 | `RuntimeHealthScanner` 组件边界 + `SC-1`–`SC-4` | `D-Phase2-P2-2-005` |
| D15 | **Rev 2.1**：`SAME_HOST_PREV` 需 liveness 证据（防 rolling / blue-green overlap 误杀） | `D-Phase2-P2-2-010` |
| D16 | 诊断载体 = `error` 字符串（不改 events payload） | `D-Phase2-P2-2-008` |
| D17 | 健康行清理责任 = scanner；`off` 停写心跳 | `D-Phase2-P2-2-007` |
| D18 | registry 脱节证据条件（MIN_AGE ∧ 证据 ∧ liveness 阈值） | `D-Phase2-P2-2-006` |
| D19 | 跨进程重复 terminal event = 已知限制，本批不硬化 | `D-Phase2-P2-2-009` |
| D1 / D2 / D4 / D10 | 推论关闭（存储平面 / 阈值基准 / FOREIGN 只观测 / 清理责任） | `-001` / `-002` / `-001`·D15 / `-007` |

**已闭合（Rev 2.2；7 项全部批准并登记，Pending Decision Closure 清空）**

| Gate | 决议 | Decision 记录 |
|---|---|---|
| D3 | 确认次数默认 2（下限 1 需显式 opt-in 并标注 `aggressive`） | `D-Phase2-P2-2-011` |
| D6 | `flush_pending` 本批不接线；`shutdown flush` 记为后续候选 | `-012` |
| D7 | startup sweep = `yield` 前有界 sweep + 预算 + fail-open（c3 取舍已记录） | `-013` |
| D8 | scanner interval 默认 15s + ±20% 抖动（30s 亦可接受） | `-014` |
| D9 | PG 心跳：节流 + 抖动；不引连接池/依赖；单语句短事务；批量写应急候选 | `-015` |
| D11 | Problem 登记时机 = Implementation 起点（题名/Type/Severity 已固定） | `-016` |
| D12 | 本批零 frontend 改动；标签已存在（reason 展示为后续项） | `-017` |

> **Pending Decision Closure = 无剩余 Gate**（D1–D19 全部闭合）。后续待办为非 Decision 类动作：
> ① Problem 正式登记（需单独批准）；② Implementation Planning 需**单独批准**（P2-2 属 L3）。

---

## 21. 分类汇总：已冻结 / P2-2 新增 / P2-3+ 后续

| 能力 | 归属 | 状态 |
|---|---|---|
| 默认 governed（policy 注入 + fail-closed 400） | P2-1 | ✅ 已冻结（`dd7af9a` / merged `342483c`） |
| watchdog / budget / terminal funnel / event replay / CAS | F8（P2-1 复用） | ✅ 已冻结 |
| heartbeat 持久化（**Runtime Health Telemetry**，R1） | **P2-2** | 🆕 v2 设计（含 LA-1–LA-4 约束） |
| stale 派生判定（healthy/stale/expired） | **P2-2** | 🆕 v2 设计 |
| reclaim（startup/periodic）+ 幂等/并发/owner 三分法 | **P2-2** | 🆕 v2.1 设计（D5/D18 ratified；D15 已按 liveness 修订 → `D-Phase2-P2-2-010`） |
| `RuntimeHealthScanner` 组件 | **P2-2** | 🆕 v2.1 设计（D14 ratified） |
| migration 0003（健康表） | **P2-2** | 🆕 仅设计（**未创建**） |
| 配置面 + 模式矩阵 | **P2-2** | 🆕 v2.2 设计（19 项 Gate 全部闭合；未实现） |
| durable agent-step / tool 时间线 | P2-3 | ⏳ pending（**未触碰**） |
| conversation transcript + 刷新恢复 UI | P2-3 | ⏳ pending（**未触碰**） |
| admin runtime 端点 | P2-4 | ⏳ pending（**未触碰**；v2 只留只读函数） |
| session 标题生成 | P2-5 | ⏳ pending（**未触碰**） |
| 前端 reclaim reason 展示（标签已存在） | 后续小批 | ⏳ 可选后续项（`D-Phase2-P2-2-017`） |
| Runtime Policy Tuning（默认值调参） | 后续独立批次 | ⏳ |

---

## 22. Status

- **Spec 版本**：`P2-2_SPEC_v2.md` = **v2 · Rev 2.2（当前唯一权威 Spec）**；v1 文件仅作已 Review 基线留痕（非并行权威）。
- **Decision 状态**：**D1–D19 全部闭合** —— `D-Phase2-P2-2-001…010`（health plane / 阈值 / 终态映射 / 首拍 / scanner 边界 / registry 证据 / 清理责任 / 诊断载体 / 跨进程限制 / D15 liveness）+ `-011…-017`（确认次数 / `flush_pending` / startup 时序 / interval / PG 约束 / Problem 时机 / frontend 策略）；**Pending Decision Closure = 无剩余 Gate**。
- **Readiness（Rev 2.2 后）**：**Ready for Implementation Planning**（全部 Decision Gate 已闭合、Spec 文本一致）；但 **Implementation Planning 需单独批准**，且 **Ready for Implementation = NO**（L3 流程：Planning → Implementation → Verification → Freeze，尚未开始）。
- 本轮未进入 Implementation：零代码改动、零 migration、零测试改动、零 frontend 改动、零 branch、零 commit/push/merge；P2-3/P2-4/P2-5 未触碰。
