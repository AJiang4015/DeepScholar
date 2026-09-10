# P2-2 Runtime Heartbeat / Stale Detection / Reclaim — Spec

Phase-2 Runtime Reliability — Batch P2-2（**Spec / Design 阶段产物；本轮零代码改动**）

- 阶段：P2-2 Spec（等待 Spec Review；**未经批准不得进入 Implementation Planning**）
- 基线：`main` = `origin/main`，P2-1 merge commit `342483c` / implementation commit `dd7af9a`（已 push）；此后 main 另有 Harness 文档 commit `98e4e5a`（AGENTS §11 Problem Registry 演进，与本批无关）
- 上游：`RUNTIME_OBSERVABILITY_AUDIT_REPORT.md`（L0 Audit）、`P2-1_*`（Spec/Plan/Report/Merge Report）、`DECISION.md`（D-Phase2-P2-1-001）
- 本 Spec 只做：repository inspection / architecture analysis / existing test inspection / spec design。
- 产出约束：零代码改动、零 migration、零 frontend、零测试改动、零 implementation branch、零 commit/push/merge。

---

## 0. INSPECTION 证据清单（读了什么、为什么读）

| 对象 | 关键结论（对本 Spec 的作用） |
|---|---|
| `app/runtime/governance/controller.py` | `terminalize()` 唯一终态 funnel（内存裁决 + `_cancel_underlying` + 乐观 CAS + retry → pending）；`_watchdog_loop`（1s tick，仅 governed 且 `timeout>0`）；`create_task/start_task/terminal_update` 为 lifecycle 写路径；`flush_pending/pending_snapshot/lifecycle()` 已存在但**无生产调用方**；`owner_instance` 默认 `hostname-pid` |
| `app/runtime/governance/models.py` | `TaskStatus`：running + 8 终态（含 `aborted` / `orphan_reclaimed`）；`TERMINAL_STATUSES` 冻结；`TaskRecord` 字段为**唯一权威**（无 heartbeat 字段） |
| `app/runtime/governance/store.py` | 函数式 store：`insert_task/get_task/list_tasks/start_task/update_run_id/terminal_update`；`terminal_update` = `WHERE task_id=? AND status='running' AND version=?`（rowcount==1 恰一次证据）；sqlite=单连接+RLock+WAL、PG=短连接 |
| `db/governance_migrations/0001_governance.{sqlite,postgres}.sql` | `governance_tasks`（sqlite: TEXT 时间；PG: TIMESTAMPTZ + JSONB）、`governance_events`（`(task_id,seq)` UNIQUE，PK event_id）；**无 heartbeat 列/表** |
| `db/governance_migrations/0002_sessions.{sqlite,postgres}.sql` | additive migration 先例：新版本文件 + governance migration runner（`governance_schema_migrations` 版本表，幂等 apply，无 rollback） |
| `app/runtime/governance/events.py` | 单写者 + `(task_id,seq)` + PK 幂等 + fail-open；`STATUS_TO_EVENT_TYPE` **已含** `orphan_reclaimed → task_orphan_reclaimed`（`aborted → task_aborted` 同理） |
| `app/runtime/governance/service.py`（P2-1 后） | `submit_task`：normalize policy → `create_task(running)` → `lifecycle_event(task_started)` → `asyncio.create_task(controller.execute(...))` |
| `app/api/server.py`（P2-1 后） | `lifespan` 仅做 checkpoint 初始化 + `get_main_agent()` prewarm（**无法启动期 governance hook**）；`_require_governance()` 惰性建 controller，store 不可用→503（c1） |
| `docs/spec/2026-09-14-f8-runtime-execution-governance.md` | §5.3 单实例假设（多实例/lease/heartbeat 明确不做）；§6.1 终态语义（`aborted` = 进程 shutdown/restart 收敛；`orphan_reclaimed` = 台账与实际执行者脱节）；§10.1 orphan 定义（session 遗留 / in-process registry 脱节 / policy 超期）；§10.2 sweeper 周期（如 15s）与 startup sweep（**本实例在途 running → aborted**，不自动 resume）；§10.2 明示 startup sweep 只在单实例下安全、不得误杀其它实例 |
| `tests/test_runtime_governance.py` | `TASK_COLUMNS` 冻结字段清单（注释：governance_tasks 不允许 Plan 之外的 lifecycle 字段）＋ `applied_migration_versions == ["0001","0002"]` 断言 |
| `tests/test_runtime_governance_postgres.py` / `test_sessions_store.py` / `test_sessions_postgres.py` | 迁移版本清单断言（0001+0002）与 PG 列类型断言 → P2-2 若加 migration 需同步更新 |
| `tests/test_runtime_governance_controller.py` / `_postgres_gate.py` / `_postgres_final.py` | `flush_pending()` 幂等/恰一次已有测试；`orphan_reclaimed` 仅被枚举与映射测试覆盖，**无产线路径** |
| `PROJECT_CONTEXT.md` §8 | 已知限制：sweeper / startup 自动恢复（orphan reclaim 产品化）未做；multi-instance governance/lease/heartbeat 未支持 |
| `AGENTS.md`（§10 Git、§11 Problem Registry）、`PROCESS.md`（§11 L0–L3）、`TESTING.md`（§1 命令、§5 security、§6/§7 Gate） | 流程与证据约束（本 Spec = L1/L3 前置 Readiness 产物） |
| `frontend/src/lib/taskStatus.ts` | 终态文案映射仅覆盖部分终态（`aborted`/`orphan_reclaimed` 会落到「已结束（status）」）；**本批禁止改 frontend**，记为后续项 |

---

## 1. Problem Statement

P2-1 已消除「生产任务无治理裸跑」的根因，但**仍未解决进程级/执行器级失联**：

1. **进程异常退出后遗留 RUNNING**：`governance_tasks` 行 permanent `running`——重启后 controller 的内存裁决/handle registry 归零，且无任何启动期收敛（F8 文档化的 future，未实现）。
2. **执行器长时间无心跳**：执行期不存在任何「存活信号」。事件循环被阻塞、executor 线程卡死、容器被冻结、进程被 OOM-kill 时，外部无法判断任务是在运行还是已失联；数据库中的 `running` 无时间语义。
3. **不可诊断**：无 `last_heartbeat`、无 stale 判定、无 reclaim 记录 → 运维只能看到「永久研搜中」（审计现场 30+ 分钟即此类）。
4. **无确定性的收敛路径**：`orphan_reclaimed` / `aborted` 两个终态**从未被任何生产路径产出**；`flush_pending()` 无生产调用方；无 sweeper。
5. **收敛安全性未被定义**：如何在不误杀正常运行任务、在 SQLite/PG 双后端一致、在并发/重启/时钟抖动下**恰一次**收敛——当前无契约。

> 结论：P2-2 要为 Runtime 引入**健康平面（runtime health plane）**：heartbeat（存活信号）→ stale detection（确定性判定）→ reclaim（经冻结 funnel 的确定性终态收敛），并覆盖启动期与周期两条路径。

---

## 2. Goals / Non-goals

### 2.1 Goals（P2-2 交付）

- G1 **Heartbeat**：governed 执行期持久化存活信号（字段/更新点/频率/事务纪律），写入**不触碰** lifecycle 权威字段。
- G2 **Stale Detection**：确定性判定「running 但已失联」，含 grace period、时钟语义、false-positive 防护、单/双阶段确认。
- G3 **Reclaim**：以冻结 funnel 为唯一入口，把 stale/遗留任务收敛为既有终态（`orphan_reclaimed` / `aborted`），**恰一次、幂等、可审计**。
- G4 **Startup Scanner**：进程启动时收敛上一世代遗留 running（F8 §10.2 e 语义）。
- G5 **Periodic Scanner**：运行期周期性检测 stale 并回收（F8 §10.2 c/d 语义）。
- G6 **诊断只读视图（函数级）**：`health` 派生视图（healthy / stale / expired），供 P2-4 暴露；**本批不新增 HTTP 端点**。
- G7 **双后端一致**：SQLite 与 PostgreSQL 行为一致（同一 SQL 形态 + Python 侧时间比较）。
- G8 **配置化**：阈值/间隔/开关可经环境变量覆盖，**默认值保持 M-Spec §7.4 与 D-Phase2-P2-2-002 冻结定义**。

### 2.2 Non-goals（明确不做；P2-3+ 或永久排除）

- ❌ 不实现 P2-3 durable timeline / agent-step 事件落库 / conversation transcript；
- ❌ 不实现 P2-4 admin runtime 端点（含 stale 列表、health 查询 API）；
- ❌ 不实现 P2-5 session 标题生成；
- ❌ 不做 checkpoint resume / 断点续跑 / 自动重试（reclaim = 收敛为终态，**不是恢复执行**）；
- ❌ 不引入多实例 lease/fencing/共享 PG 互斥（F8 §5.3 明确排除；本 Spec 的 heartbeat 是**单实例健康信号**，不是分布式租约）；
- ❌ 不新增 TaskStatus 枚举值（保持 F8 冻结；stale 是派生健康态）；
- ❌ 不修改默认 policy 语义/默认阈值数值（调参属后续 Runtime Policy Tuning）；
- ❌ 不修改 frontend（`aborted`/`orphan_reclaimed` 文案映射记为后续项）；
- ❌ 不修改 `app/agent/**`、`app/research/**`（heartbeat 只由 runtime 侧写入）。

---

## 3. Current Runtime State

```text
submit（P2-1 后）
  └─ gov_service.submit_task(policy=normalize) → create_task(running) [+ task_started event]
       └─ controller.execute(policy≠None) → _execute_governed
            ├─ BudgetCounter + GovernanceExecution(ctx) + recursion_limit
            ├─ watchdog task（tick 1s，deadline=start+wall_clock）→ 到点 funnel(timed_out)
            ├─ handle 注册（task_id → asyncio.Future）→ 仅内存
            └─ 终态：completed / failed / cancelled / timed_out / budget_exceeded（funnel + event）

现存缺口（P2-2 要补）：
  ① 执行期无任何持久化存活信号（DB 行自 running 起不再变化，除 started_at）
  ② 进程退出 → 内存 handle/decision 消失；DB 行永久 running；无启动收敛
  ③ 事件循环被阻塞/线程卡死 → watchdog 自身亦不 tick；无外部可判定信号
  ④ orphan_reclaimed / aborted 无生产产出路径；flush_pending 无调用方
  ⑤ 无 stale 判定公式、无 reclaim 并发/幂等契约、无双后端一致性约定
```

**与 P2-1 governed execution / watchdog 的关系（关键）**：
- watchdog 是**进程内** deadline 执行者（能 tick 才有效）；P2-2 的 scanner 是**账本侧**兜底（进程死了/循环阻塞时生效）。
- 两者都只能经冻结 funnel 收敛；竞争由「内存裁决 + CAS rowcount」判定唯一 winner（见 §6.4）。
- P2-1 的 `effective_limits`（含每任务 `wall_clock_timeout`）是 P2-2 stale 阈值的**权威依据**（见 §5.1），避免全局阈值与单任务实际 deadline 漂移。

---

## 4. Heartbeat Model

### 4.1 存储方案（含 Decision Gate）

| 方案 | 设计 | 优点 | 缺点 |
|---|---|---|---|
| **A（推荐）独立健康表** `governance_runtime_health` | `task_id PK / thread_id / run_id / owner_instance / last_heartbeat_at / beat_count / created_at / updated_at`（表族独立，不触碰 `governance_tasks` 字段权威） | ① 保持 F8「governance_tasks = Task Lifecycle 字段权威」纯净（现测试注释明确禁止 Plan 外 lifecycle 字段）；② 与 D-Phase2-P2-2-001「stale 属 Runtime Health，不是业务生命周期状态」一致；③ 心跳写放大隔离（不重写宽行）；④ 未来 P2-4 可直接读健康平面 | 新增表 + 需 join/两查；终态后需清理策略 |
| B 在 `governance_tasks` 加列 `last_heartbeat_at` | additive `ALTER TABLE ADD COLUMN` | 实现最少、扫描单表 | 触碰冻结 lifecycle 表的字段权威（`TASK_COLUMNS` 冻结断言需改）；5s 级心跳重写宽行（SQLite WAL 写放大）；语义上把健康态混进生命周期表 |
| C 无持久化心跳（仅 owner/handle 判定） | 启动期按 owner 收敛；运行期仅依赖 watchdog | 零 schema | **不满足用户 P2-2 目标**（无法判定「执行器长时间无心跳」、无法支撑 P2-4 诊断） |

**推荐 A**；C 作为降级选项（若 Review 拒绝新增表，可先落 C 再补 A）。→ **Decision Gate D1**。

### 4.2 字段语义（方案 A）

| 列 | 语义 |
|---|---|
| `task_id` | PK；与 `governance_tasks.task_id` 逻辑关联（**不建 FK**，沿用 0002 sessions 的「派生关联、不越界」纪律） |
| `thread_id` / `run_id` | 冗余便于按 thread 查询/诊断 |
| `owner_instance` | 写入者身份（沿用 controller `owner_instance`，默认 `hostname-pid`），用于区分世代/主机 |
| `last_heartbeat_at` | 最近一次心跳（sqlite: TEXT ISO-8601 UTC；PG: TIMESTAMPTZ；读取经 `_as_str` 归一） |
| `beat_count` | 心跳计数（诊断用；**不做** lease fencing） |
| `created_at` / `updated_at` | 首写/末写时间（审计） |

### 4.3 更新点（唯一写入者 = Runtime，不触碰 Agent 侧）

1. **watchdog tick（主）**：`controller._watchdog_loop` 每次 tick 检查是否到达节流窗口（默认 5s）→ 写一次心跳（仅 governed 执行；bare/dev 不写）。
2. **提交即首拍**：`submit_task` 成功后立即写首拍（避免 created→第一次 tick 的判定空窗；或在 `_execute_governed` 起点写，二者等价，实现时择一并测试锁定）。
3. **终态清理（可选）**：任务终态时删除该行（或保留末拍由清理策略回收）→ 见 §12/§13 决策。

**明确不做**：不在工具/LLM 回调里写心跳（那属 P2-3 事件时间线范畴，且会触碰 agent 侧接线）。

### 4.4 更新频率

- 写节流 `RUNTIME_HEARTBEAT_INTERVAL`（默认 **5s**，下限 1s；tick 粒度沿用现有 1s）。
- 每任务每秒最多一次、默认 5s 一次；10 个并发任务 ≈ 2 次写/秒（SQLite 可承受；PG 短连接需评估，见 §11/§20）。

### 4.5 Transaction / Persistence 纪律

- **单一语句 UPSERT**（双方言同形）：`INSERT ... ON CONFLICT(task_id) DO UPDATE SET last_heartbeat_at=..., beat_count=beat_count+1, updated_at=...`（`events.py` 已有 `ON CONFLICT` 先例），包在 `store.transaction()` 内。
- 心跳写**只允许**触碰健康表；**禁止**更新 `governance_tasks` 的 `status/version/terminal_*`（lifecycle 权威仍唯 controller funnel）。
- 心跳写失败 = **fail-open**（observation/health plane，§F8 §7.2/§16(b)）：仅 log/diagnostic，**绝不影响**执行、budget、cancel、terminal 收敛。
- 心跳表行数上限 ≈ 运行中任务数（终态清理后）→ 不构成无界增长。

---

## 5. Stale Detection Model

### 5.1 判定公式（推荐：逐任务基准）

```text
reference_ts = max( last_heartbeat_at (若存在),
                    started_at (若存在),
                    created_at )
per_task_wall_clock = effective_limits.wall_clock_timeout（P2-1 已落库）
                      └─ 缺失/非法（legacy 行）→ 回退 RUNTIME_WALL_CLOCK_TIMEOUT（默认 600，M-Spec §7.4）
threshold      = per_task_wall_clock + RUNTIME_STALE_GRACE_PERIOD（默认 30s，D-Phase2-P2-2-002）
age            = now_utc - reference_ts        # 负值/不可解析 → 视为 0（不 stale）
stale_candidate := status = 'running' AND age > threshold
```

- **为何用逐任务 wall_clock（而非全局常量）**：P2-1 允许调用方显式传 `wall_clock_timeout`（如 1800s）；若 stale 用全局 600s 判定 → **必然误回收**长任务。逐任务快照（`effective_limits`）使 stale 与真实 deadline 同源。→ **Decision Gate D2**（替代方案：全局 env 阈值，简单但风险高）。
- **为何用 `max(...)`**：legacy 行无心跳时用 `started_at/created_at` 兜底；有心跳时心跳更晚，基准自然前移，避免「心跳正常但仍被判 stale」。

### 5.2 Grace Period

- 默认 `RUNTIME_STALE_GRACE_PERIOD = 30s`（D-Phase2-P2-2-002），env 可覆盖。
- 作用：吸收 WAL/短连接抖动、GC/调度延迟、心跳写节流窗口、轻微时钟漂移、以及 watchdog 到点后 funnel 重试（最多 ~2.5s）+ DB 写延迟。
- **双阶段确认（推荐默认开启）**：连续 **2** 次扫描（间隔 `RUNTIME_SCAN_INTERVAL`）均判定 stale 才进入 reclaim（`RUNTIME_STALE_CONFIRMATIONS=2`）；单次判定只记 stale 视图 + 日志。→ **Decision Gate D3**（默认 2 次；可配为 1 次激进模式）。

### 5.3 Clock Semantics

| 用途 | 时钟 | 说明 |
|---|---|---|
| 进程内 deadline（watchdog，P2-1） | `time.monotonic()` | 不受墙钟调整影响；**不可持久化** |
| stale 判定（P2-2） | UTC 墙钟（`datetime.now(timezone.utc)`） | 跨进程/跨重启唯一可用基准；DB 存 ISO-8601 UTC（sqlite TEXT / PG TIMESTAMPTZ） |
| 比较位置 | **Python 侧**（读少量候选行后比较） | 避免 `now() - interval` 方言差异；避免在 SQL 内做不确定时区运算 |

- 规则：`age < 0` → 视为 0（绝不因时钟回拨而 stale）；不可解析时间 → 跳过该行并记 diagnostic。
- 单主机假设（F8 §5.3）：跨主机时钟漂移不在支持范围（多实例本身不支持）；NTP 正向跳变由 grace + 双阶段确认吸收。
- 所有阈值计算使用**秒级**精度（现有 `_now_iso()` 为 `timespec="seconds"`；心跳同格式）。

### 5.4 False-positive 防护（必须全部成立才可 reclaim）

| # | 防护 | 说明 |
|---|---|---|
| P1 | in-process handle 存活检查 | `ctl.has_active_handle(task_id)` 且未 done → **绝不 reclaim**（同进程真活着） |
| P2 | 内存终态裁决存在 → 跳过 | `ctl.terminal_decision(task_id) is not None`（已收敛或 pending） |
| P3 | **最小年龄守卫** | `now - created_at < RUNTIME_RECLAIM_MIN_AGE`（默认 = max(2×heartbeat, 10s)）→ 跳过。**保护 P2-1 submit 的 create_task → handle 注册窗口**（该窗口内 handle 尚未注册，若无此守卫会误回收刚提交任务） |
| P4 | grace + 双阶段确认 | §5.2 |
| P5 | 非 running 一律跳过 | `status != 'running'` 直接忽略（含 terminal、含无行） |
| P6 | 单实例/主机范围守卫 | 仅收敛 `owner_instance` 主机名 == 本机 或（legacy）空 owner 的行；foreign owner → **只检测不回收**（F8 §10.2 多实例警告：不得误杀其它实例）→ **Decision Gate D4** |
| P7 | kill switch / 模式 | `RUNTIME_RECLAIM_MODE=off|detect|enforce`（默认 `enforce`；`detect` 只判不收敛） |
| P8 | 每周期上限 | `RUNTIME_RECLAIM_MAX_PER_CYCLE`（默认 20）→ 防时钟异常/批量误判雪崩 |
| P9 | 扫描单飞 | 上一周期未结束则跳过本周期（`asyncio.Lock` / task 单实例） |

---

## 6. Reclaim Model

### 6.1 Reclaim 条件（启动扫描 / 周期扫描不同）

| 触发 | 条件（全部满足） | 终态（推荐） |
|---|---|---|
| **Startup sweep**（进程启动，无任何 handle） | row=running；owner 主机名==本机（或空 owner legacy）；`age > RUNTIME_RECLAIM_MIN_AGE`（不必等 wall_clock+grace） | **`aborted`**（F8 §6.1/§10.2 e：restart 收敛） |
| **Periodic sweep — heartbeat stale** | §5.4 P1–P9 + 双阶段 stale 确认 | **`orphan_reclaimed`**（F8 §10.2 c/d） |
| **Periodic sweep — registry 脱节** | row=running 且 owner==本机当前实例 且**无 handle** 且 `age > RUNTIME_RECLAIM_MIN_AGE` | `orphan_reclaimed`（F8 §10.2 c：治理缺陷路径） |
| policy 超期（F8 §10.2 d，可选） | 等价于 heartbeat stale（P2-2 用 stale 覆盖该语义，不另设开关） | `orphan_reclaimed` |

> **Decision Gate D5（重要）**：F8 冻结语义区分「restart → `aborted`」与「orphan → `orphan_reclaimed`」（spec §6.1/§10.2 明文）。用户此前（P2-1 Spec 语境）的表述为「stale/reclaim → 复用 `orphan_reclaimed`」。本 Spec **推荐遵循 F8 映射**（restart=aborted，stale/registry=orphan_reclaimed），因为二者语义确实不同且 F8 已冻结；若用户希望统一为 `orphan_reclaimed`，需显式裁决并记录（会偏离 F8 文字）。

### 6.2 状态转换（只经冻结 funnel）

```text
running ──(startup sweep: 本机遗留/owner 失联)──▶ aborted            [+ task_aborted]
running ──(periodic: heartbeat stale, 双阶段确认)──▶ orphan_reclaimed [+ task_orphan_reclaimed]
running ──(periodic: registry 脱节)───────────────▶ orphan_reclaimed [+ task_orphan_reclaimed]
（restart 收敛不自动 resume；checkpoint/research 只读不写 —— F8 §10.2 e）
```

- 实现路径：`await ctl.finalize_with_event(task_id, TerminalReason.ABORTED | TerminalReason.ORPHAN_RECLAIMED, error="reclaim: <trigger> last_heartbeat=… threshold=…")`（`error_kind=None`，因为既非 agent 失败亦非 governance control failure；`_validate_terminal_fields` 允许 None）。
- 结果字段：`status`、`terminal_reason`、`finished_at`、`version+1`；`counters_snapshot` 在重启场景为 `None`（内存 counter 已失，**不伪造**）；`underlying_linger_observed` 不适用（无 handle）→ 保持 NULL。

### 6.3 幂等性

- 单进程：funnel 内存裁决 → 第二次调用 `already_terminal=True`，**不重复发布事件**（`finalize_with_event` 提前返回）。
- 跨进程/重启（CAS）：`terminal_update` 的 `WHERE status='running' AND version=?` → rowcount==1 恰一次；败者经 `_attempt_terminal_write` **采纳 DB 事实**（`_already_terminal_db_result`）→ 不发布事件。
- 不变量：**每个 task 至多 1 条 terminal lifecycle event**（`governance_events` 内 `task_aborted`/`task_orphan_reclaimed` 计数 ≤1）；测试需断言。

### 6.4 并发控制 / Race Condition 矩阵

| 竞争 | 处理 |
|---|---|
| scanner vs watchdog(timed_out) | 内存裁决先到者胜；后到者 no-op；DB 侧 CAS 双保险 → 单终态 |
| scanner vs 用户 cancel | 同上（`cancelled` 与 reclaim 互斥，先到者胜） |
| scanner vs 新 submit（同 thread supersede） | submit 路径先 cancel 旧任务；scanner 若已裁决 → no-op |
| scanner vs 任务正常完成 | 完成先写 terminal → scanner 读 DB 已终态 → 采纳/跳过（§6.3） |
| scanner vs pending_terminal（内存已裁决、DB 未落） | P2 防护：内存 decision 存在 → 跳过（pending 由既有 retry/flush 语义处理） |
| 两个 scanner 实例（误启/多进程） | CAS + rowcount 唯一 winner；**仍以单实例为支持边界**（F8 §5.3） |
| scanner vs 进程 shutdown | 扫描任务在 lifespan 收尾 cancel；已进入 funnel 的收敛不中断（内存裁决已生效） |
| scanner vs 刚提交任务（未注册 handle） | **P3 最小年龄守卫** 关闭该窗口 |

### 6.5 Restart Recovery（本批核心）

1. 进程启动 → governance store 初始化（迁移 ensure_schema）→ **startup sweep**（单遍、批量、有上限）：
   - 查询 `status='running'`（限制 batch）→ 逐行判定（owner 主机范围 / 最小年龄 / 无 handle 必然）→ `aborted`；
   - 收敛失败的（DB 不可写）→ 记 pending？**不**：scanner 不构造内存 pending 语义，失败仅 log + 计数，下个周期/下次启动重试（保持 funnel 单一语义）。
2. sweep 结束输出统计（scanned/reclaimed/skipped/errors）到日志（**不新增端点**）。
3. `RUNTIME_RECLAIM_MODE=detect` 时：只输出将被回收清单（log/health 视图），不改 DB。
4. 说明：`flush_pending()`（P2-1 前遗留的 pending 兜底）**本批不接线**；其目标场景（DB 写失败遗留 running）由 periodic/startup sweep 覆盖（可能收敛为 aborted/orphan_reclaimed 而非原裁决**待裁决**）→ **Decision Gate D6**（是否在 startup 顺带 `flush_pending()`、以及是否在 shutdown 收尾 flush）。

---

## 7. Startup Scanner

| 项 | 设计 |
|---|---|
| 位置 | `server.lifespan`（`app/api/server.py`）——在 checkpoint 初始化之后 |
| 时序 | **方案 A（推荐）**：`yield` **之前**执行一次「有界单遍」sweep（`LIMIT RUNTIME_SCAN_BATCH_LIMIT`，默认 200）→ 首请求前账本干净；剩余行交给后台任务/后续周期。<br>方案 B：`yield` 之后异步执行（不阻塞 readiness，但首请求窗口内可能看到遗留 running） |
| store 不可用 | **方案 A（推荐）**：初始化失败 → log error + **跳过 sweep**、服务继续（提交侧仍由 c1 返回 503）<br>方案 B：按 M-Spec (c3) fail-fast 拒绝启动（更严格但改变现有启动语义）→ **Decision Gate D7** |
| 幂等 | 每次启动都执行；重复启动/多进程由 §6.3 保证恰一次 |
| 范围 | 仅本机 owner（P6）；foreign owner 只统计（或不统计，见 D4） |
| 失败 | fail-open：不阻塞启动（方案 A 下）；错误计入日志统计 |

## 8. Periodic Scanner

| 项 | 设计 |
|---|---|
| 位置 | lifespan 内 `asyncio.create_task(...)`，`finally` 取消并 await（与 controller watchdog 同纪律） |
| 周期 | `RUNTIME_SCAN_INTERVAL` 默认 **30s**（F8 §10.2 提到「如 15s」；30s 降低开销，可配）＋ ±20% 抖动避免同步尖峰 → **Decision Gate D8** |
| 工作 | ① 查询 running 候选（batch 上限）；② 计算 stale（§5.1）+ 双阶段确认状态（进程内 `dict[task_id] → consecutive_stale_count`）；③ 满足 §5.4/§6.1 的行进入 reclaim；④ 输出统计日志 |
| 单飞 | 上一周期未完成 → 跳过本周期（P9） |
| 与 startup 的关系 | 共用同一 `reclaim_task()` 实现，仅触发/条件与终态不同（§6.1） |
| detect 模式 | 只记录/计数（供 P2-4 后续消费），不做 DB 变更 |

---

## 9. Task Lifecycle / Status Compatibility

| 项 | 结论 |
|---|---|
| TaskStatus 枚举 | **零新增**（F8 冻结）；`aborted` / `orphan_reclaimed` 均为既有终态 |
| TERMINAL_STATUSES | 不变（两者已在集合内） |
| Health 与 Lifecycle 分离 | `status` 继续只表示业务生命周期；健康态为**派生**：`healthy`（心跳新鲜）/ `stale`（超阈未收敛）/ `expired`（已终态收敛，含 reclaim）。**不落库到 status**（D-Phase2-P2-2-001） |
| 终态语义 | `aborted` = 进程/实例收敛（restart）；`orphan_reclaimed` = 台账与执行者脱节（stale/registry）。与 F8 §6.1 一致 |
| 与 session 状态 | 不新增 session 状态；`sessions.status` 仍只有 active/archived；archive 守卫（无 running task 才可归档）在 reclaim 后自然解除 |
| 前端 | P2-2 零改动；reclaim 后前端经既有 durable 状态校正（`GET /api/tasks/{id}` → terminal）即可脱离「研搜中」；文案缺口（aborted/orphan_reclaimed 标签）记为后续项（P2-3/P2-4 或独立小批） |
| 事件 replay | 既有 `GET /api/threads/{tid}/events` + WS since_seq 自动携带新的 terminal 事件（无协议变更） |

## 10. Event / Observability Semantics

- **durable**：复用 `gov_events.lifecycle_event()`（单写者 + `(task_id,seq)` + PK 幂等 + fail-open）→ `task_aborted` / `task_orphan_reclaimed`（映射已存在于 `STATUS_TO_EVENT_TYPE`，**无需新增枚举**）。
- **payload 扩展（additive，建议）**：在既有自由 JSON payload 中增补 reclaim 诊断字段：`trigger`（startup_sweep / heartbeat_stale / registry_disconnect）、`last_heartbeat_at`、`detected_at`、`owner_instance`、`threshold_seconds`、`grace_seconds`、`scan_mode`。payload 为透传 JSON，不改 schema、不破坏 replay 契约（消费者按字段存在性可选读取）。
- **live**：经既有 `governance_terminal` bridge 帧推送（thread_id 定向），前端无需改协议即可收敛；`error` 字段承载人类可读原因（如 `reclaim: heartbeat stale (last=…, threshold=630s, trigger=heartbeat_stale)`）。
- **日志**：每个周期输出一行统计（scanned/stale/reclaimed/skipped/errors + 模式）；单任务回收输出一行结构化字段（无凭据、无 payload 正文）。
- **明确不属于本批**：agent-step/tool 级事件持久化、scroll 时间线、conversation transcript（P2-3）；admin 查询端点（P2-4）。
- **诊断函数（只读，供 P2-4 复用）**：`health_view(task_id) -> {status, health: healthy|stale|expired, last_heartbeat_at, age_seconds, threshold_seconds, owner_instance, reclaim_trigger?}`；以及 `health_snapshot(limit)` 列表（**只提供函数，不暴露端点**）。

## 11. SQLite / PostgreSQL Compatibility

| 关注点 | 约定 |
|---|---|
| DDL 双方言 | 迁移提供 `0003_*.sqlite.sql` + `0003_*.postgres.sql`（列/表同形；sqlite 时间列为 TEXT，PG 为 TIMESTAMPTZ——沿用 0001 既有约定） |
| IF NOT EXISTS | 新表用 `CREATE TABLE IF NOT EXISTS`（同 0002 先例）；方案 B 的 `ALTER TABLE ADD COLUMN` 无 IF NOT EXISTS → 由版本表幂等保证（runner 只跑未应用版本） |
| UPSERT | `INSERT ... ON CONFLICT(task_id) DO UPDATE`（双方言同形；`events.py` 已有 ON CONFLICT 先例） |
| 时间比较 | **一律 Python 侧**（避免 `now()`/interval 方言差异）；读回时间经 `_as_str`/`_as_datetime` 归一（PG 返回 datetime，sqlite 返回 str） |
| 事务 | 心跳写单语句 + `store.transaction()`；reclaim 复用 funnel 的 CAS（单语句 UPDATE，无长事务） |
| 连接成本 | sqlite=单连接+RLock（heartbeat 5s/任务可接受）；PG=每写一短连接 → 需评估（见 §13/§20 D9：节流 vs 连接池） |
| 隔离级别 | 依赖既有的「单语句 CAS + rowcount」证据（PG 默认 READ COMMITTED 下 UPDATE … WHERE version=? 具原子性；与既有 terminal CAS 同语义） |
| 迁移断言 | 现有 4 处 `applied_migration_versions == ["0001","0002"]` 断言需在实现时更新为含 `0003`（先例：D019 更新 0001+0002） |

## 12. Schema / Migration Proposal（**仅设计，本批不创建**）

**推荐方案 A（独立健康平面表）——`db/governance_migrations/0003_runtime_health.{sqlite,postgres}.sql`**

```sql
-- sqlite 方言（postgres 同形，时间列改 TIMESTAMPTZ、BOOLEAN 语义一致）
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

- 与 `governance_tasks` **不建 FK**（沿用 0002 sessions「派生关联」纪律，允许 legacy 行无健康行）。
- 清理策略选项：(a) 终态即删（funnel 成功后删除，行数≈运行中任务数）；(b) 保留 TTL（如 7 天，由扫描顺带清理）。推荐 (a) + 扫描兜底清理孤儿健康行。→ **Decision Gate D10**。
- **备选方案 B**（`ALTER TABLE governance_tasks ADD COLUMN last_heartbeat_at TEXT/TIMESTAMPTZ` + `CREATE INDEX ... (status, last_heartbeat_at)`）：实现更少，但需修改冻结 `TASK_COLUMNS` 断言与 lifecycle 表字段权威（§4.1）。
- 迁移影响（无论 A/B）：`applied_migration_versions` 断言（`test_runtime_governance.py`、`test_sessions_store.py`、`test_sessions_postgres.py`、`test_runtime_governance_postgres.py`）需同步；方案 B 另需更新 `TASK_COLUMNS` 与 PG 列类型断言。

## 13. Configuration Proposal

| env | 默认 | 作用 | 归属 |
|---|---|---|---|
| `RUNTIME_HEARTBEAT_INTERVAL` | `5`（秒，下限 1） | 心跳写节流 | P2-2 新增 |
| `RUNTIME_STALE_GRACE_PERIOD` | `30`（秒） | stale grace（D-Phase2-P2-2-002） | P2-2 新增（用户已裁决内容，待登记） |
| `RUNTIME_STALE_CONFIRMATIONS` | `2` | 双阶段确认次数（1=激进） | P2-2 新增 |
| `RUNTIME_SCAN_INTERVAL` | `30`（秒，抖动 ±20%） | 周期扫描 | P2-2 新增 |
| `RUNTIME_SCAN_BATCH_LIMIT` | `200` | 单遍扫描批量上限 | P2-2 新增 |
| `RUNTIME_RECLAIM_MAX_PER_CYCLE` | `20` | 每周期最多收敛数（防雪崩） | P2-2 新增 |
| `RUNTIME_RECLAIM_MIN_AGE` | `10`（秒） | 最小年龄守卫（P3） | P2-2 新增 |
| `RUNTIME_RECLAIM_MODE` | `enforce`（`off`/`detect`/`enforce`） | 总开关/灰度 | P2-2 新增 |
| `RUNTIME_RECLAIM_LOCAL_ONLY` | `true` | 仅收敛本机 owner 行（P6） | P2-2 新增 |
| `RUNTIME_WALL_CLOCK_TIMEOUT` | `600`（M-Spec §7.4，默认不变） | legacy 行 stale 回退基准（**不改变**默认 policy 语义；主基准仍是逐任务 `effective_limits`） | D-Phase2-P2-2-002（用户已裁决） |

- 解析纪律：非法值 → 回退默认 + 记 diagnostic（**不 fail-fast**；健康平面不应阻断服务）。所有默认值 = 冻结值（不调参）。

## 14. Failure Modes

| # | 故障 | 行为 |
|---|---|---|
| F1 | 心跳写失败（DB 忙/锁/短连接错误） | fail-open + log；执行不受影响；连续失败 → 可能被判 stale（grace/双阶段/最小年龄 + handle 存活检查兜底） |
| F2 | 扫描周期内 DB 不可用 | 跳过本周期的检测与收敛（不猜测）；log 错误；下周期重试 |
| F3 | reclaim funnel DB 写失败 | 走既有 retry → pending（内存裁决权威，DB 侧下轮/下次启动由 CAS 兜底）；terminal 事件 fail-open 可能缺失（durability_gap） |
| F4 | 事件写失败 | TaskRecord 仍为真；事件缺失记为 durability_gap（不改状态、不回滚） |
| F5 | 时钟回拨 | `age<0 → 0` → 不 stale（安全侧） |
| F6 | 时钟前跳/宿主休眠唤醒 | 可能瞬时 stale；由 grace + 双阶段 + handle 检查 + 最小年龄吸收；`detect` 模式可先观察 |
| F7 | 扫描任务自身异常 | 捕获 + log + 下周期继续（绝不让扫描器异常传播到 lifespan 收尾之外） |
| F8 | 大量任务同时 stale（如 OOM 重启） | 每周期上限 20 + 批量上限；分批收敛，事件风暴受抑 |
| F9 | 误判风险（新增 handle 未注册窗口） | P3 最小年龄守卫 + 双阶段 |
| F10 | PID 复用导致 owner 字符串相同 | 仍以「无 handle + 年龄超阈」为必要条件；`beat_count/上世代心跳时间` 交叉校验（可选） |
| F11 | 扫描器误启两份（同机双进程） | CAS 唯一 winner；**仍声明不支持多实例**；`owner` 主机范围守卫降低影响 |
| F12 | 健康行缺失（legacy 行/方案 B 前数据） | 回退 `started_at/created_at` 基准（§5.1），不因缺失而跳过收敛 |
| F13 | 服务优雅关闭时把在途任务判为 aborted | 关闭路径 **不触发** startup sweep（仅启动时）；periodic 在收尾时先 cancel；正常 shutdown 的在途收敛仍由既有 cancel/close 语义处理（本批不新增 shutdown 收敛，见 D6） |

## 15. Security / Safety Considerations

- **无新攻击面**：不新增 HTTP/WS 端点（P2-4 才做），不接受用户输入驱动的 reclaim；P005 无鉴权边界不变。
- **Fail-closed 收敛前置**：任一防护不成立 → **保持 running + 记录**（宁可不收敛，不误杀）。
- **资源边界**：周期固定、批量上限、每周期收敛上限、日志限频 → 不会被单点故障放大为资源耗尽。
- **数据安全**：健康表仅存 id/时间/owner（无 prompt、无工具参数、无凭据）；日志不含凭据。
- **破坏性语义披露**：reclaim 是不可逆终态（DB 无 reopen 路径）；`detect` 模式 + kill switch 提供运行期安全网。
- **多实例安全**：默认仅收敛本机 owner 行；foreign owner 不回收（F8 §10.2 明示不得误杀）；多实例仍是 out of scope。
- **审计**：每次 reclaim 产生 1 条 durable 事件 + 1 行日志统计，可事后证明收敛原因与依据。

## 16. Test Strategy（实现阶段执行；本批不写测试）

| 类别 | 用例（要点） |
|---|---|
| Heartbeat 单元 | UPSERT 只更新健康表；不触碰 `governance_tasks`（status/version 不变断言）；节流（同 tick 多拍只写 1 次）；失败 fail-open（注入抛错，执行继续）；终态清理策略按 D10 |
| Stale 公式单元 | 边界（`age == threshold` / `tha+1`）；NULL 心跳回退 `started_at`/`created_at`；逐任务 `effective_limits.wall_clock_timeout`（含 1800s 长任务不误判）；缺快照回退默认 600；`age<0` 不 stale；不可解析时间跳过 |
| 双阶段确认 | 连续 2 次才收敛；中途恢复心跳 → 计数清零，不收敛 |
| False-positive 防护 | P1 handle 存活跳过；P2 内存裁决跳过；P3 最小年龄窗口（模拟 submit→注册间隙）；P5 非 running 跳过；P6 foreign owner 不收敛 |
| Reclaim 集成 | 启动扫描：上一世代 owner 行 → `aborted`（version+1、`finished_at`、1 条 `task_aborted` 事件、counters=None）；周期扫描：stale → `orphan_reclaimed` + `task_orphan_reclaimed`；registry 脱节 → `orphan_reclaimed` |
| 幂等/并发 | 重复调用/两个 scanner 竞争 → 单终态、**事件恰 1 条**；与 watchdog(timed_out)/cancel 竞争 → 唯一 winner；pending 场景跳过 |
| 重启恢复集成 | 构造「旧 owner + 陈旧心跳」行 → 新 controller + startup sweep → 收敛；`detect` 模式只读不改 DB |
| 配置 | env 覆盖与非法值回退；`RUNTIME_RECLAIM_MODE=off` 完全停用；上限生效 |
| 双后端 | 上述核心用例 SQLite + PG 各跑（PG 门控 `AGENT_CHECKPOINT_DSN_TEST`）；迁移版本断言更新 0003；时间列读写归一 |
| 回归 | P2-1 套件（`test_runtime_policy_enforcement.py` 23 用例）、governance 全套、sessions、research；全量 sqlite（排除 MySQL 污染文件） |
| 冻结断言更新 | 4 处迁移版本断言（+方案 B 的 TASK_COLUMNS / PG 列类型断言）——属 additive 引起的**预期变更**，须在计划中显式列出 |

## 17. Rollback Strategy

1. **运行期**：`RUNTIME_RECLAIM_MODE=off`（重启生效）→ 扫描器完全停用；再 `detect` 只观察。
2. **代码回滚**：revert P2-2 commit 集（scanner/heartbeat/store 函数/server 接线）→ 行为回到 P2-1 基线。
3. **Schema 回滚**：迁移为 additive（新表或新列），**不回滚 DDL**（仓库无 rollback migration 纪律）；空表/空列不影响 P2-1 逻辑；数据无损坏。
4. **数据后果**：已被 reclaim 的任务保持终态（`aborted`/`orphan_reclaimed`）；**无自动 reopen**（如需人工恢复需手工 DB 操作 + 说明，不在本批范围）。
5. 判据：若观察期出现误收敛（false positive 证据）→ 立即切 `off`/回滚，并把证据登记为 Problem（见 §20 D11）。

## 18. Explicit Non-goals（复述，防越界）

- 不实现 P2-3 durable timeline / P2-4 admin runtime / P2-5 title；
- 不做 resume/重试/断点续跑；
- 不做多实例 lease/fencing、共享 PG 互斥；
- 不新增 TaskStatus、不改 terminal funnel/CAS/replay 契约、不改 policy 默认语义；
- 不改 frontend、不改 `app/agent/**`、`app/research/**`；
- 不在本 Spec 阶段创建 migration、不改测试、不建 branch、不 commit。

## 19. Dependency on P2-1

| 依赖 | 说明 |
|---|---|
| 默认 governed（D-Phase2-P2-1-001） | 所有生产任务都有 wall_clock deadline + watchdog + terminal event → reclaim 才有「兜底」意义；若无 P2-1，bare 任务无 deadline，stale 判定将不可靠 |
| `effective_limits` 落库 | P2-1 起 `policy_snapshot/effective_limits` 恒为完整 policy（含逐任务 `wall_clock_timeout`）→ P2-2 stale 阈值可逐任务精确计算（§5.1） |
| 冻结 funnel + `finalize_with_event` | reclaim 唯一收敛入口，复用内存裁决/CAS/事件发布/live bridge，零改动 |
| `terminal event` 常开 | P2-1 保证生产任务终态必有 durable 事件 → reclaim 事件与既有事件流同构（replay/WS 自动可用） |
| `PolicyValidationError` 400 路径 | 与本批无交互；确认 reclaim 不走 HTTP 层 |
| 无 P2-1 的假设 | 若未来出现绕过 `gov_service.submit_task` 的提交路径，P2-2 的 stale 判定需重新评估（记为约束） |

## 20. Open Questions / Decision Gates

| ID | 议题 | 选项 | 推荐 |
|---|---|---|---|
| D1 | 心跳存储平面 | A 独立健康表 / B governance_tasks 加列 / C 无持久化心跳 | **A**（保持 F8 lifecycle 字段权威 + 与「stale 属 health 不属 lifecycle」一致） |
| D2 | stale 阈值基准 | 逐任务 `effective_limits` + grace / 全局 env 常量 | **逐任务**（防长任务误回收） |
| D3 | 双阶段确认 | 2 次（默认）/ 1 次激进 | **2 次** |
| D4 | foreign owner 行 | 只统计不收敛（推荐）/ 也收敛 | **只统计**（F8 §10.2 多实例警告） |
| D5 | 终态映射 | F8 拆分：restart→`aborted`，stale/registry→`orphan_reclaimed`（推荐）/ 统一 `orphan_reclaimed` | **F8 拆分**（与冻结语义一致） |
| D6 | `flush_pending` 接线 | 不接线（本批，sweep 覆盖）/ startup 顺带 flush / shutdown 收尾 flush | **不接线**（避免引入第二种收敛语义；如需另开小批） |
| D7 | 启动期 store 不可用 | 跳过 sweep 服务继续（推荐）/ 按 c3 fail-fast | **跳过**（不改变现有启动语义；c3 fail-fast 另议） |
| D8 | 周期扫描间隔 | 30s（推荐）/ 15s（F8 示例值） | **30s**（可配） |
| D9 | PG 心跳写成本 | 逐拍短连接（简单）/ 复用连接池 / 批量合并写 | **先逐拍 + 节流**；P2-4 前评估（备选：批量） |
| D10 | 健康行清理 | 终态即删（推荐）/ TTL 保留 | **终态即删 + 扫描清理孤儿行** |
| D11 | Problem Registry 沉淀 | 是否将「Runtime 无健康平面导致遗留 RUNNING 不可诊断」登记为 Problem（AGENTS §11） | 建议在实现阶段（或本轮经批准后）登记 Candidate；**本轮不改 PROBLEM.md** |
| D12 | 前端终态文案（aborted/orphan_reclaimed） | 本批不改（推荐）/ 随批小改 | **本批不改**（frontend 禁改）；记为后续项 |

### Decision Registration Proposal（不自行修改 DECISION.md）

DECISION.md 现状：**仅存在 `D-Phase2-P2-1-001`**；其中文本引用了 `D-Phase2-P2-2-002`，但**无 P2-2 正式条目**。因此本 Spec 提出以下登记提案（待 Review 批准后由 Implementation/Decision Gate 登记）：

| 提议 ID | 内容 |
|---|---|
| `D-Phase2-P2-2-001` | Stale 属 Runtime Health 派生态，不新增 TaskStatus；复用既有终态收敛（用户此前已裁决内容，建议正式登记） |
| `D-Phase2-P2-2-002` | 阈值默认 `wall_clock=600s`、`stale grace=30s`，env 可覆盖，默认值保持 M-Spec §7.4；不在 P2-2 调参（用户此前已裁决内容，建议正式登记） |
| `D-Phase2-P2-2-003`（新） | Heartbeat 存储平面 = 独立健康表（D1-A）；心跳写不触碰 lifecycle 权威字段；fail-open |
| `D-Phase2-P2-2-004`（新） | Stale 判定 = 逐任务 `effective_limits` 基准 + grace + 双阶段确认；UTC 墙钟 + Python 侧比较 |
| `D-Phase2-P2-2-005`（新） | Reclaim 经冻结 funnel；终态映射按 F8 拆分（restart→aborted / stale·registry→orphan_reclaimed）；CAS 恰一次 + 事件 ≤1 |
| `D-Phase2-P2-2-006`（新） | Scanner 生命周期：startup（有界、yield 前）+ periodic（30s、单飞、可 kill switch）；启动期 store 不可用 = 跳过不 fail-fast |
| `D-Phase2-P2-2-007`（新） | 配置面（§13 env 集合）+ `RUNTIME_RECLAIM_MODE` 灰度开关；默认值与冻结语义一致 |

---

## 21. 分类汇总：已冻结 / P2-2 新增 / P2-3+ 后续

| 能力 | 归属 | 状态 |
|---|---|---|
| 默认 governed（policy 注入 + fail-closed 400） | P2-1 | ✅ 已冻结（`dd7af9a` / merged `342483c`） |
| watchdog wall-clock deadline / budget / terminal funnel / event replay / CAS | F8（P2-1 复用） | ✅ 已冻结 |
| heartbeat 持久化（健康平面） | **P2-2** | 🆕 本 Spec 设计 |
| stale 派生判定（healthy/stale/expired） | **P2-2** | 🆕 本 Spec 设计 |
| reclaim（startup / periodic）+ 幂等/并发契约 | **P2-2** | 🆕 本 Spec 设计 |
| migration 0003（健康表或列） | **P2-2** | 🆕 仅设计（不创建） |
| 配置面（§13 env） | **P2-2** | 🆕 仅设计 |
| durable agent-step / tool 时间线 | P2-3 | ⏳ pending spec（**未触碰**） |
| conversation transcript + 刷新恢复 UI | P2-3 | ⏳ pending spec（**未触碰**） |
| admin runtime 端点（running/stale/health 查询） | P2-4 | ⏳ pending spec（**未触碰**；P2-2 只留只读函数） |
| session 标题生成 | P2-5 | ⏳ pending spec（**未触碰**） |
| 前端终态文案（aborted/orphan_reclaimed） | 后续小批 | ⏳（本批禁改 frontend） |
| Runtime Policy Tuning（默认值/env 调参） | 后续独立批次 | ⏳ |

---

## 22. Status

- 本 Spec 完成后 **立即停止**，等待 P2-2 Spec Review。
- 未获批准：不进入 Implementation Planning，不创建 branch，不改代码/测试/schema/frontend。
