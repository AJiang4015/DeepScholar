# P2-2 Implementation Plan

Phase-2 Runtime Reliability — Batch P2-2（Runtime Heartbeat / Stale Detection / Reclaim）

- 阶段：**Implementation Planning**（本文件为计划产物；**未进入 Coding / Implementation**）
- 唯一权威实现输入：`P2-2_SPEC_v2.md` **Rev 2.2**
- 冻结决策输入：`DECISION.md` **`D-Phase2-P2-2-001 … 017`**（D1–D19 全部 Closed）
- 上游过程文档：`RUNTIME_OBSERVABILITY_AUDIT_REPORT.md`、`P2-1_*`、`P2-2_SPEC_REVIEW_REPORT.md`、`P2-2_DECISION_CLOSURE_REPORT.md`、`P2-2_D15_REVISION_REPORT.md`、`P2-2_REMAINING_DECISION_REVIEW.md`、`P2-2_PENDING_DECISION_PROPOSAL.md`、`P2-2_DECISION_CLOSURE_AND_READINESS_REVIEW.md`
- 本轮授权范围：**仅 Planning**（未授权 Coding/Implementation；未建 branch；未改代码/migration/tests/frontend；未 commit/push/merge）
- 本轮唯一写入：`P2-2_IMPLEMENTATION_PLAN.md`（本文件）

---

## 1. Scope

### 1.1 交付物（Implementation 阶段将产出，非本阶段）

1. 运行期 **Runtime Health Plane**：健康表 + heartbeat 写入路径 + 首拍接线；
2. **Stale 判定**：`liveness_evidence` / `deadline_evidence` 谓词 + owner 三分法 + 双阶段确认；
3. **Reclaim**：startup sweep + periodic scanner，经冻结 funnel 收敛为 `aborted` / `orphan_reclaimed`；
4. **健康行清理**（scanner 负责）与 **`RUNTIME_RECLAIM_MODE`** 三模式行为；
5. **可注入 clock / 可注入触发**（可测性）；
6. migration `0003`（双方言）+ 受影响冻结断言更新；
7. P2-2 测试套件（新增）+ 全量回归证据。

### 1.2 范围外（本批不做；见 §15 Explicit Non-Goals）

P2-3 durable timeline / P2-4 admin runtime（含任何新 HTTP/WS 端点）/ P2-5 title；resume/重试；多实例 lease/fencing；新增 TaskStatus；连接池/新依赖；frontend；`app/agent/**`；`app/research/**`；LangSmith 或任何 observability UI；Runtime Policy Tuning（默认值调参）；`shutdown flush`（D6 后续候选）；前端 reclaim reason 展示（D12 后续候选）。

### 1.3 基线冻结声明

- 实现必须以 `P2-2_SPEC_v2.md` **Rev 2.2** 为唯一规范来源；任何与 Rev 2.2 冲突的想法 → 回到 Spec 修订流程；
- 决策不得在实现中重新讨论；若实现中发现 Spec/Decision 不可行 → **STOP + 报告 Deviations**（不得自行扩大范围）。

---

## 2. File Impact Matrix

### 2.1 MUST CHANGE

| 文件 | 改动性质 | 具体改动（计划） |
|---|---|---|
| `db/governance_migrations/0003_runtime_health.sqlite.sql` | **新增** | 建 `governance_runtime_health` 表 + 心跳索引（sqlite 方言：时间列 TEXT） |
| `db/governance_migrations/0003_runtime_health.postgres.sql` | **新增** | 同形表（时间列 TIMESTAMPTZ） |
| `app/runtime/governance/health_store.py` | **新增** | 健康平面 store 函数：`upsert_heartbeat` / `get_health` / `list_health_for` / `list_running_tasks`（只读）/ `delete_health_rows` / `list_cleanup_candidates` / `parse_ts` |
| `app/runtime/governance/heartbeat.py` | **新增** | `HeartbeatWriter`（节流 + 抖动相位 + fail-open + `forget`）、`HeartbeatConfig` |
| `app/runtime/governance/reclaim.py` | **新增** | `OwnerClass` + `classify_owner`；`liveness_evidence` / `deadline_evidence`；`ReclaimConfig.from_env()`；`RuntimeHealthScanner`（startup sweep / periodic / cleanup / health 视图）；`ScanStats`；clock 注入 |
| `app/runtime/governance/controller.py` | **修改（additive-only）** | ① `__init__` 新增 `self.heartbeat_writer = None`（与既有 `live_sink` 同款注入模式）；② `_execute_governed` 在 **handle 注册成功后** 写首拍；③ `_watchdog_loop` 每 tick 尝试节流 heartbeat；④ `_execute_governed.finally` 调 `forget(task_id)` 释放节流状态；⑤ 新增私有 `_beat(...)`（writer 为 None → no-op；异常一律吞掉 = fail-open） |
| `app/api/server.py` | **修改（additive-only）** | lifespan：构造 config/writer/scanner；`get_controller()` 可用时 attach writer；`yield` **前** `await scanner.startup_sweep()`；创建 periodic task；shutdown 时 `cancel + await`；`off` 模式跳过 writer/scanner。**不得**新增 HTTP 端点 |
| `tests/test_runtime_heartbeat.py` | **新增** | heartbeat / store / 首拍接线 / 模式矩阵 / fail-open 测试 |
| `tests/test_runtime_reclaim.py` | **新增** | owner 分类 / 证据谓词 / startup sweep / periodic + 确认 / reclaim 映射 / 幂等与竞争 / 清理 / clock 注入 |
| `tests/test_runtime_health_postgres.py` | **新增（PG 门控）** | PG 方言：迁移 0003、UPSERT、时间读回归一、清理、scanner on PG |
| `tests/test_runtime_governance.py` | **修改（仅断言）** | 3 处 `applied_migration_versions` 期望值 +0003（见 §3.3） |
| `tests/test_sessions_store.py` | **修改（仅断言）** | 3 处 +0003（可顺带同步该用例 docstring 中「自动补 0002」的表述） |
| `tests/test_sessions_postgres.py` | **修改（仅断言）** | 2 处 +0003 |
| `tests/test_runtime_governance_postgres.py` | **修改（仅断言）** | 1 处 +0003 |

### 2.2 MAY CHANGE（仅在实现发现必要时，且需记录理由）

| 文件 | 条件 |
|---|---|
| `app/runtime/governance/__init__.py` | 仅当需要 re-export（如 `ReclaimConfig`）供 server/测试导入；否则不动 |
| `tests/test_runtime_governance_postgres_final.py` | 仅当其中存在与治理表族/迁移清单相关的新增断言需要同步（当前仅列受保护表名，预期不动） |
| `PROBLEM.md` + `docs/problem/P0NN-*.md` | **D11 登记动作**：实施起点执行；**需单独批准**（本阶段不执行，见 §16） |

### 2.3 MUST NOT CHANGE

| 类别 | 文件 |
|---|---|
| 冻结 lifecycle 语义 | `app/runtime/governance/models.py`（TaskStatus / TERMINAL_STATUSES / TaskRecord 字段权威）、`app/runtime/governance/events.py`（单写者/白名单/`(task_id,seq)`） |
| 冻结 funnel / CAS / pending | `controller.py` 中的 `terminalize` / `_attempt_terminal_write` / `_coerce_terminal` / `_validate_terminal_fields` / `_funnel_result` / `_already_terminal_db_result` / `_unknown_result` / `flush_pending` / `pending_snapshot` / `lifecycle`（**只读引用，不得改写语义**） |
| store / migration runner | `app/runtime/governance/store.py`、`app/runtime/governance/migrations.py`、`db/governance_migrations/0001_*.sql`、`0002_*.sql` |
| P2-1 冻结面 | `app/runtime/governance/policy.py`（默认表与默认值不变）、`counters.py`、`callbacks.py`、`context.py` |
| Agent / Research | `app/agent/**`、`app/research/**`、`app/tools/**`、`app/prompts/**`、`app/prompt/**` |
| 前端 | `frontend/**`（全部） |
| 其他 | `app/runtime/checkpoint.py`、`app/api/monitor.py`、`app/api/context.py`、`app/session/**`、Harness 六文件与冻结 Spec 文本（除本 Plan/Spec 自身的后续 Rev） |

---

## 3. Data / Migration Plan

### 3.1 数据模型（Runtime Health Plane，方案 A —— `D-Phase2-P2-2-001`）

| 列 | sqlite | postgres | 语义 | 约束 |
|---|---|---|---|---|
| `task_id` | TEXT PRIMARY KEY | TEXT PRIMARY KEY | 与 `governance_tasks.task_id` 逻辑关联（**无 FK**，沿用 0002「派生关联」纪律） | 必填 |
| `thread_id` | TEXT NOT NULL | TEXT NOT NULL | 诊断维度 | 必填 |
| `run_id` | TEXT NULL | TEXT NULL | 诊断/correlation | 可空 |
| `owner_instance` | TEXT NOT NULL | TEXT NOT NULL | 写入者身份（`hostname-pid` 或 env 覆盖） | 必填 |
| `last_heartbeat_at` | TEXT NOT NULL（ISO-8601 UTC） | TIMESTAMPTZ NOT NULL | liveness 判定依据 | 必填 |
| `beat_count` | INTEGER NOT NULL DEFAULT 1 | INTEGER NOT NULL DEFAULT 1 | 诊断计数（**非 fencing**） | 每次 beat +1 |
| `created_at` | TEXT NOT NULL | TIMESTAMPTZ NOT NULL | 首拍时间 | 仅首次写入 |
| `updated_at` | TEXT NOT NULL | TIMESTAMPTZ NOT NULL | 末拍时间 | 每次 beat 覆盖 |

- 索引：`idx_runtime_health_heartbeat (last_heartbeat_at)`；清理扫描另按 `updated_at` 排序取候选。
- **不得**把该表任何字段加入 `TASK_COLUMNS`（该常量是 `governance_tasks` 的冻结字段清单）。
- 行数上限 ≈ 运行中任务数（终态/孤儿行由 scanner 清理，滞后 ≤ 1 个扫描周期）。

### 3.2 Migration

- 新增 `db/governance_migrations/0003_runtime_health.{sqlite,postgres}.sql`，各含 2 条语句：
  ```sql
  CREATE TABLE IF NOT EXISTS governance_runtime_health ( ... );
  CREATE INDEX IF NOT EXISTS idx_runtime_health_heartbeat ON governance_runtime_health(last_heartbeat_at);
  ```
- 迁移机制：`migrations.ensure_schema()` 按文件版本号自动发现并 apply（**runner 无需改动**）；`_split_statements` 以 `;` 切分 → 本文件不含字符串内分号，安全。
- 幂等：`governance_schema_migrations` 版本表保证只 apply 一次；`0001/0002` 零改动。
- **不回滚 DDL**（仓库无 rollback migration 纪律）：回滚 = 代码 revert，遗留空表无害。

### 3.3 mutation 影响的冻结断言（**精确清单：4 文件 / 9 处**）

| 文件 | 用例 | 断言位置 | 改动 |
|---|---|---|---|
| `tests/test_runtime_governance.py` | `test_fresh_migration_0000_to_0001` | L96-99 | `["0001","0002"]` → `["0001","0002","0003"]` |
| 同上 | `test_upgrade_migration_0000_to_0001` | L115-118 | 同上 |
| 同上 | `test_migration_idempotent` | L125-128 | 同上（幂等语义不变） |
| `tests/test_sessions_store.py` | `test_fresh_applies_0001_and_0002` | L87-90 | 同上（用例名/docstring 可同步为 0003） |
| 同上 | `test_ensure_schema_idempotent` | L98-101 | 同上 |
| 同上 | `test_upgrade_0001_to_0002` | L123-126 | 同上（种子仅 0001，ensure_schema 补 0002+0003） |
| `tests/test_sessions_postgres.py` | 2 处 | L60-62、L65-67 | 同上 |
| `tests/test_runtime_governance_postgres.py` | 1 处 | L90-93 | 同上 |

**不受影响（须在报告中明示）**：

- **research 迁移族**全部断言（`["0001"…"0006"]`，如 `test_corroboration*.py`、`test_reconciliation*.py`、`test_research_*.py`、`test_semantic_verify*.py`、`test_f2_postgres.py`、`test_conflict_detection*.py`）→ 使用 `app.research.migrations` 与 `db/migrations/`，与本批无关；
- `tests/test_runtime_governance.py::test_tasks_table_columns_and_indexes`（L131-143）断言 `governance_tasks` 列集合 → **方案 A 下不变**（未改该表）；
- `tests/test_runtime_governance_postgres.py` 的治理表列类型断言 → 同上不变（**实现时需实跑确认**）。

### 3.4 Migration 执行时机

- 由 `ensure_schema` 在 governance store 首次打开时执行；P2-2 起启动期 `startup_sweep` 会触发 store 初始化（新增可观测行为：启动即建表/迁移），失败不 fail-fast（D7）。

---

## 4. Runtime Flow

### 4.1 Heartbeat 写入路径（首拍 + 周期拍）

```text
server.lifespan（startup）
  └─ cfg = ReclaimConfig.from_env()
       ├─ mode == off → 不 attach writer、不启动 scanner（health plane 整体停用）
       └─ 否则 → ctl = get_controller()；ctl.heartbeat_writer = HeartbeatWriter(ctl._store, cfg)

submit_task → controller.execute(policy≠None) → _execute_governed
  ├─ start_task(started_at)
  ├─ enter_governance_execution(ctx) → ensure_future → handle 注册
  ├─ **首拍**：self._beat(task_id, thread_id=record.thread_id, run_id=record.run_id)   ← D13（handle 注册后）
  ├─ watchdog(task) 每 tick：self._beat(...)（writer 内部按 interval 节流 + 抖动相位）
  ├─ await fut …
  └─ finally：watchdog.cancel() → self._forget_beat(task_id)（仅释放节流状态；不删 DB 行）
```

- **submit 路径不写首拍**（D13 / invariant I5）；`_execute_bare` 不写心跳（dev 路径）；
- 写入实现：单语句 UPSERT（§7），失败 fail-open（`_beat` 内 try/except + log），**绝不影响**执行/budget/cancel/terminal；
- 抖动：首拍相位 = `hash(task_id) % max(1, int(interval * jitter%))`（确定性、可测）。

### 4.2 Stale 判定与 reclaim 流程（periodic）

```text
every RUNTIME_SCAN_INTERVAL(±20% jitter)（单飞；空闲跳过）
  ├─ rows = list_running_tasks(store, limit=batch)                  # 只读 governance_tasks
  ├─ health = list_health_for(store, [r.task_id …])
  ├─ for each row:
  │    ├─ P1 handle 存活？ → skip
  │    ├─ P2 内存终态裁决存在？ → skip
  │    ├─ P3 age(created_at) < MIN_AGE？ → skip
  │    ├─ owner 分类（CURRENT / SAME_HOST_PREV / FOREIGN）
  │    │    └─ FOREIGN → 只记计数
  │    ├─ evidence：CURRENT → deadline_evidence；SAME_HOST_PREV → liveness_evidence
  │    ├─ stale 计数（双阶段确认）→ 未达 N（默认 2）→ 记 stale 视图，不收敛
  │    └─ 达到条件 + 未超每周期上限 → reclaim()
  ├─ registry 脱节（CURRENT + 无 handle + age>MIN_AGE + 证据 + liveness 超时倍数）→ reclaim(orphan)
  ├─ cleanup：删除终态/孤儿健康行（bounded）
  └─ 输出 ScanStats 日志
```

### 4.3 Reclaim funnel（唯一收敛入口）

```python
await ctl.finalize_with_event(
    task_id,
    TerminalReason.ABORTED if trigger == "startup_same_host_prev" else TerminalReason.ORPHAN_RECLAIMED,
    error=f"reclaim: trigger={trigger} owner_class={cls} last_heartbeat={ts} threshold={x}s age={y}s",
)
```

- `error_kind=None`（既非 agent 失败亦非 governance control failure）；
- 结果：`status` / `terminal_reason` / `finished_at` / `version+1`；重启场景 `counters_snapshot=None`（不伪造）；
- durable 事件：`task_aborted` / `task_orphan_reclaimed`（映射已存在于 `events.py`）+ live bridge 帧（既有 `governance_terminal`，不改协议）；
- 幂等：内存裁决短路 + DB 已终态采纳 → 单实例内 **≤1** terminal event。

---

## 5. Startup / Periodic Lifecycle

### 5.1 startup sweep（D7 / `D-Phase2-P2-2-013`）

```text
lifespan:
  manager.set_loop → init_checkpoint_lifespan → get_main_agent()（既有）
  → [新增] cfg = ReclaimConfig.from_env()
  → [新增] ctl = get_controller()；ctl.heartbeat_writer = HeartbeatWriter(...)   # mode != off
  → [新增] scanner = RuntimeHealthScanner(ctl, cfg)
  → [新增] await scanner.startup_sweep()      # batch ≤ RUNTIME_SCAN_BATCH_LIMIT、预算 ≤ 2s、fail-open
  → [新增] periodic = asyncio.create_task(scanner.run_periodic())               # mode != off
  → yield
  → [新增] periodic.cancel(); await (suppressed)  → shutdown_checkpoint_lifespan()
```

- 启动期条件：`SAME_HOST_PREV` → `liveness_evidence`；`CURRENT` → `deadline_evidence`；`FOREIGN` → 不参与（只统计）；
- store 不可用/超预算 → 跳过 + 日志，**不 fail-fast**（对 F8 §7.3 c3 的显式取舍，已记录）；
- 仅单遍（**不适用双阶段确认**）。

### 5.2 periodic scanner（D8）

- 周期 `RUNTIME_SCAN_INTERVAL` 默认 **15s** + ±20% 抖动；**单飞**（上周期未完则跳过）；
- 空闲（无 running 行）→ 仅执行清理检查后返回；
- 模式矩阵（§8）决定判定/收敛/清理行为；
- shutdown 时 cancel + await（与 controller watchdog 同纪律）。

---

## 6. Concurrency / Race Analysis

| # | 竞争/竞态 | 场景 | 处理 | 验证 |
|---|---|---|---|---|
| C1 | scanner vs watchdog `timed_out` | 同一 stale 任务被两方同时收敛 | funnel 内存裁决先到者胜；DB CAS（`status='running' AND version=?`）双保险 | 集成用例（注入 race 顺序） |
| C2 | scanner vs 用户 cancel | 同上 | 同理；`cancelled` 与 reclaim 互斥 | 用例 |
| C3 | scanner vs 正常完成 | 完成先写 terminal | scanner 读到终态 → 采纳/跳过（不重发事件） | 用例 + 事件计数断言 |
| C4 | scanner vs pending（内存已裁决） | DB 写失败的 pending | P2 防护跳过 | 用例 |
| C5 | scanner vs 新 submit（supersede） | 旧行仍在 running | startup sweep 在 `yield` 前完成 → 窗口关闭；periodic 场景 CAS 保证唯一 | 用例（startup 场景） |
| C6 | 两个 scanner（多进程，**不支持**） | CAS 唯一 winner | 记入 D19 已知限制（跨进程可重复事件） | 不做支持性用例 |
| C7 | heartbeat 写在 terminal 之后 | tick 与 funnel 交错 | 健康行成为「终态任务的行」→ 下个周期清理（≤interval，良性） | 用例（清理覆盖） |
| C8 | scanner 判定与收敛之间的 await 窗口 | 判定后任务状态变化 | funnel 内部重新读 + CAS；已终端则采纳 | 用例（判定后注入 completion） |
| C9 | P1 handle 检查与收复之间 | 任务恰在窗口内结束 | funnel 幂等 + 内存裁决 → 无副作用 | 用例 |
| C10 | 心跳写与 shutdown | 关闭期仍写心跳 | 写失败/写成功均无害（行由下次启动/扫描清理） | 用例（shutdown 不触发 sweep 断言） |
| C11 | 时钟回拨/前跳 | 证据谓词计算结果异常 | `age < 0 → 0`（不 stale）；前跳由 grace + 双阶段吸收 | 单测（注入 clock） |
| C12 | 同一进程内 throttle 状态泄漏 | 执行结束未清理 | `_execute_governed.finally` 调 `forget(task_id)` | 用例（连续两次执行同 task_id） |
| C13 | 扫描器自身异常 | 任何内部错误 | 捕获 + 日志；下周期继续（不外抛到 lifespan） | 用例（注入异常 store） |

---

## 7. SQLite / PG Strategy

| 关注点 | 策略 |
|---|---|
| SQL 形态 | 同一套 `%s` 参数化语句；sqlite 由 `store._sqlite_placeholder` 转换 |
| UPSERT | `INSERT … ON CONFLICT (task_id) DO UPDATE SET … = EXCLUDED.…, beat_count = governance_runtime_health.beat_count + 1`（双方言同形，`events.py` 已有 `ON CONFLICT` 先例） |
| 时间列 | sqlite TEXT ISO-8601（`timespec="seconds"`）；PG TIMESTAMPTZ；读回归一由 `parse_ts()` 处理（`datetime` 与 `str` 两种输入） |
| 时间比较 | **一律 Python 侧**（不在 SQL 内做 `now()/interval`），保证双后端语义一致 |
| 清理 | 两段式：取候选 task_id（bounded）→ 单条 `DELETE … WHERE task_id IN (…)`（分批，避免长事务） |
| 并发 | 写为单语句短事务；读为无事务查询；沿用 sqlite RLock + WAL；**不引入连接池**（D9） |
| 失败语义 | 心跳/清理/扫描失败一律 fail-open；`sqlite3.OperationalError: database is locked` → 记 diagnostic 并等下周期 |
| PG 门控测试 | `AGENT_CHECKPOINT_DSN_TEST` 存在才跑；无 DSN 如实 skip（不伪造） |
| 隔离级别 | 依赖既有「单语句 CAS + rowcount」证据；新增写路径不引入跨语句依赖 |

---

## 8. `RUNTIME_RECLAIM_MODE` 行为矩阵（实现契约）

| 行为 \ 模式 | `off` | `detect` | `enforce`（默认） |
|---|---|---|---|
| heartbeat 写入 | ❌（不 attach writer） | ✅ | ✅ |
| stale 判定 | ❌ | ✅（记录/视图） | ✅ |
| reclaim（改 DB 终态） | ❌ | ❌ | ✅ |
| health 行清理 | ❌（无写入 ⇒ 无增长） | ✅ | ✅ |
| terminal 事件发布 | ❌ | ❌ | ✅（经 funnel） |

- 模式在启动时读取一次；非法取值 → 回退 `enforce` + 日志；
- 所有数值型 env 非法 → 回退默认 + diagnostic（**不 fail-fast**）。

---

## 9. Test Plan

### 9.1 新增：`tests/test_runtime_heartbeat.py`

| 组 | 用例 |
|---|---|
| 配置 | env 解析/回退/边界（interval ≥1、jitter 0–50、`RUNTIME_STALE_CONFIRMATIONS=1` 显式 opt-in 并产生 `aggressive` 诊断日志、mode 非法回退 enforce） |
| Writer | 首次写插入（`beat_count=1`、`created_at` 保留）；窗口内重复调用被节流；`force`/新窗口后 `beat_count+1`；抖动相位确定性（同 task_id 同相位） |
| Store | `upsert_heartbeat` 只触碰健康表（断言 `governance_tasks.status/version` 不变）；`get_health`/`list_health_for`；`parse_ts`（str / datetime / None / 非法） |
| 首拍接线 | `submit_task(policy=None)` + fake runner → 存在健康行；**submit 阶段不写首拍**（execute 前断言无行）；`_execute_bare` 不写心跳 |
| fail-open | 注入抛错的 store → 执行仍 `completed`，仅记日志 |
| 模式 | `off`：无行写入、无 scanner；`detect`：有行、有 stale 判定、无终态变化；`enforce`：全功能 |
| 生命周期 | 执行结束后 throttle 状态被清理（同 task_id 二次执行重新计相位） |

### 9.2 新增：`tests/test_runtime_reclaim.py`

| 组 | 用例 |
|---|---|
| owner 分类 | CURRENT / SAME_HOST_PREV / FOREIGN；env 自定义 owner（等于/不等于当前）；不可解析格式 → FOREIGN；PID 复用形态 |
| 证据谓词 | `liveness_evidence`（有行 / 无行 legacy 回退 / 边界 = 阈值 / 阈值+1 / 负 age）；`deadline_evidence`（逐任务 `effective_limits`；缺快照回退 600；含 1800s 长任务不误判） |
| startup sweep | `SAME_HOST_PREV` + 陈旧心跳 → `aborted`（version+1、finished_at、1 条 `task_aborted`、counters=None）；活跃旧进程（持续心跳）→ **不收敛**；`CURRENT` 需 deadline；`FOREIGN` 不收敛；超预算/异常 store → fail-open、不 fail-fast |
| periodic | 双阶段确认：第 1 次不收敛、第 2 次收敛；中途恢复心跳 → 计数清零；每周期上限生效；单飞 |
| registry 脱节 | 无 handle + `age>MIN_AGE` + 证据（仅 started_at / 仅心跳 两种组合）+ liveness 超时倍数 → `orphan_reclaimed`；未达证据 → 只记 stale |
| 防护 | P1（handle 存活）/P2（内存裁决）/P3（最小年龄）/P5（非 running）逐条断言 |
| 幂等/竞争 | 重复 reclaim → 事件恰 1 条；与 completion/cancel/timed_out 竞争 → 唯一 winner；pending → 跳过 |
| 清理 | 终态行被删；孤儿行被删；运行中行保留；`bounded` 上限生效 |
| 模式矩阵 | off/detect/enforce 的 (写/判/收敛/清理/事件) 四格逐项断言 |
| clock 注入 | 全部时间相关用例通过注入 clock 驱动（不 sleep） |

### 9.3 新增：`tests/test_runtime_health_postgres.py`（PG 门控，`AGENT_CHECKPOINT_DSN_TEST`）

迁移 0003 应用 + 表/索引存在；UPSERT 语义（并发 beat 计数）；TIMESTAMPTZ 读写归一；清理删除；scanner 在 PG 上完成一次 startup sweep（seed 旧 owner 行 → `aborted`）；无 DSN → 整类 skip。

### 9.4 修改（冻结断言，9 处）

见 §3.3；改动仅限期望值（+`"0003"`）；`test_sessions_store.py` 中该用例 docstring 的「补 0002」表述可同步为 0003。

### 9.5 回归范围（Regression Plan）

| 层级 | 命令 | 通过标准 |
|---|---|---|
| 新增套件 | `python -m pytest tests/test_runtime_heartbeat.py tests/test_runtime_reclaim.py -q` | 全绿 |
| PG 门控 | `python -m pytest tests/test_runtime_health_postgres.py -q` | 全绿或全 skip（无 DSN 如实报告） |
| governance 定向 | `tests/test_runtime_governance*.py`（含 step3/step4 全套）+ `test_sessions_*.py` | 0 failed |
| P2-1 冻结套件 | `tests/test_runtime_policy_enforcement.py`（23 用例） | 全绿 |
| research 定向 | `tests/test_research_orchestrator.py`、`test_research_judge.py`、`test_research_targeted.py`、`test_research_plan.py` | 0 failed |
| 全量 sqlite | `python -m pytest tests/ -q --ignore=tests/test_db_tools_mysql_integration.py` | 0 failed（skip 如实记录） |
| 静态 | `python -m compileall -q app`、`ruff check <changed files>`、`git diff --check` | 0 error |

> 运行环境注记：本机 `uv` cache 目录不可用（P2-1 已记录），使用 `.venv` python 或系统 python 执行 pytest。

---

## 10. Rollback / Failure Handling

| 场景 | 处理 |
|---|---|
| 运行期需紧急停用 | `RUNTIME_RECLAIM_MODE=off`（重启生效）→ 心跳/判定/收敛/清理全停（行为回 P2-1 基线）；`detect` 可先灰度 |
| 误收敛证据出现 | 立即 `off`/回滚；按 D11 评估 Problem 登记 |
| 代码回滚 | revert P2-2 commit 集（新模块 + controller/server 接线 + 测试）→ 行为回 P2-1 |
| Schema 回滚 | 不回滚 DDL（additive 空表无害）；无需数据修复 |
| 已 reclaim 的任务 | 保持终态，**无自动 reopen**（人工恢复需手工 DB 操作，不在本批） |
| 心跳写失败 | fail-open；连续失败由 grace/双阶段/最小年龄/handle 检查兜底 |
| 扫描器异常 | 捕获 + 日志；下周期继续；绝不向 lifespan 外抛 |
| 启动 sweep 超时/失败 | 跳过 + 日志（不 fail-fast）；剩余行交 periodic |
| Store 不可用 | `get_controller()` 返回 None → scanner 停用；提交侧仍由 c1 返回 503 |

---

## 11. Risk Register

| # | 风险 | 影响 | 缓解 | 检测/证据 |
|---|---|---|---|---|
| R1 | 误杀 rollover 中的活跃旧进程 | 任务被错误 `aborted` | D15 liveness 证据（持续心跳 ⇒ 不收敛） | 用例：活跃旧进程跳过；`detect` 模式灰度 |
| R2 | registry 脱节窗口误判（submit→handle） | 刚提交任务被回收 | P3 最小年龄 + 证据谓词 + 超时倍数 | 用例：未注册 handle + 无心跳/无 started_at → 跳过 |
| R3 | PG 心跳写抖动拖慢事件循环 | watchdog/agent 调度延迟 | 节流 + 抖动相位；单语句短事务；**不引池** | 实现后观测写频；必要时启用批量 writer 候选 |
| R4 | 迁移断言遗漏导致回归翻红 | 测试失败/误导 | §3.3 精确清单（4 文件 9 处） | 全量 sqlite + PG 门控 |
| R5 | `governance_tasks` 被 health 路径污染 | 违反 LA-1/LA-2 | store 函数仅含健康表语句；断言 `status/version` 不变 | 用例 + 代码评审 |
| R6 | 事件重复发布（跨进程） | 审计噪声 | 单实例 envelope 限定（D19 已知限制） | 单实例内事件计数断言 |
| R7 | 健康行无界增长 | 存储膨胀 | 清理责任在 scanner；`off` 停写 | 用例：清理覆盖终态/孤儿行 |
| R8 | 时钟异常批量误判 | 大范围错误收敛 | grace + 双阶段 + 每周期上限 + handle 检查 + `age<0` 归零 | 单测（回拨/前跳） |
| R9 | 扫描器与 shutdown 交错 | 关闭期异常 | periodic cancel + await；`suppressed` 处理 CancelledError | 用例：shutdown 不触发 sweep |
| R10 | 实现者在 controller 顺手改 frozen 逻辑 | 冻结语义破坏 | §2.3 禁止清单 + review diff gate | 冻结套件（P2-1 + governance）全绿 |
| R11 | 引入新依赖/连接池 | 违反 D9 / AGENTS §4 | 计划冻结「不引池/不加依赖」；依赖文件零改动 | `git diff` 检查 `pyproject.toml`/`requirements.txt` 零改动 |
| R12 | 400/503 行为变化影响前端 | 用户可见回归 | 不改任何 HTTP 端点；D12 零 frontend 改动 | 端点 diff 为空 |

---

## 12. Frozen Invariants I1–I18 对应关系

| Invariant | 实现落点 | 验证 |
|---|---|---|
| I1 F8 controller 唯一 lifecycle 权威 | 新模块只调用 `finalize_with_event`；不改 funnel | 冻结套件 + 代码评审 |
| I2 heartbeat 只写 health plane | `health_store.upsert_heartbeat`（单表 UPSERT） | 用例：`status/version` 不变 |
| I3 reclaim 只经 `finalize_with_event` | `reclaim.py::_reclaim()` 唯一写路径 | 用例 + grep 断言（无 `terminal_update` 直调） |
| I4 health 态不进 TaskStatus | 派生视图函数（`health_view`/`health_snapshot`） | 用例：无 status 写入 |
| I5 scanner 必须来自 `get_controller()` | `reclaim.py` 构造签名只接受 controller；server 传单例 | 用例：SC-2 反例断言（伪造 controller → 不收敛） |
| I6 防护 P1–P11 全成立才收敛 | `reclaim.py` 判定流水线 | 逐条用例 |
| I7 owner 三分法 + 证据 | `classify_owner` + 两个谓词 | 用例矩阵 |
| I8 终态由触发源决定 | `_terminal_for(trigger)` 映射（startup→aborted / stale·registry→orphan） | 用例：两类触发各自终态 |
| I9 exactly-once 仅单实例 | 内存裁决 + CAS；D19 记录 | 事件计数断言 |
| I10 可注入 clock + Python 侧比较 | `clock` 参数贯穿 writer/scanner；无散落 `datetime.now()` | 用例（无 sleep）+ grep |
| I11 清理仅 scanner；`off` 停写 | `cleanup_health_rows()`；mode gating | 用例矩阵 |
| I12 不新增 TaskStatus/端点/依赖；不动 agent·research·frontend | 文件影响矩阵 §2.3 | Scope Audit + diff gate |
| I13 启动 sweep 无确认次数 | sweep 单遍实现 + 证据谓词 | 用例 |
| I14 任何 flush 调用点 fail-open | 本批无 flush 调用点（D6） | grep 断言 |
| I15 startup sweep 有界 + fail-open | batch/预算 + try/except | 用例（超预算/异常 store） |
| I16 interval/抖动为进程随机相位；延迟公式入验收 | `ReclaimConfig` + 相位计算 | 用例 + 验收文档 |
| I17 心跳：不引池/无新依赖/单语句/ fail-open | `heartbeat.py` + `health_store.py` | 依赖文件零改动 + 用例 |
| I18 本批零 frontend 改动 | 文件矩阵 | `git diff -- frontend` 为空 |

## 13. Decision D1–D19 对应关系

| Decision | 实现落点 |
|---|---|
| D1 存储平面 A | `0003_runtime_health.*.sql` + `health_store.py` |
| D2 逐任务阈值 | `deadline_evidence`（读 `effective_limits.wall_clock_timeout`，缺省回退 env 600） |
| D3 确认次数 2 | `ReclaimConfig.confirmations` + scanner 计数（`=1` 显式 opt-in + `aggressive` 日志） |
| D4 FOREIGN 只观测 | `classify_owner` + scanner 分支（仅计数） |
| D5 终态映射 | `_terminal_for(trigger)` |
| D6 不接线 `flush_pending` | 不新增 flush 调用点；§2.3 禁止改动 `flush_pending` |
| D7 startup 时序 A | `server.lifespan`（yield 前 sweep + 预算 + fail-open） |
| D8 interval 15s ±20% | `ReclaimConfig.interval/jitter` + `run_periodic` 相位 |
| D9 PG 约束 | `heartbeat.py` 节流/抖动/单语句；无池、无依赖；批量 writer 仅登记候选 |
| D10 清理责任 | `cleanup_health_rows()`（scanner） |
| D11 Problem 时机 | §16 动作项（Planning 声明，实施起点执行，需单独批准） |
| D12 零 frontend | 文件矩阵 + diff gate |
| D13 首拍时机 | `_execute_governed` handle 注册后 `_beat()` |
| D14 Scanner 边界 | `reclaim.py::RuntimeHealthScanner`；server 仅接线；`get_controller()` 单例 |
| D15 liveness 证据 | `liveness_evidence`（SAME_HOST_PREV / legacy 回退） |
| D16 诊断载体 | `error` 字符串模板（不扩展 events payload） |
| D17 清理 + off 停写 | mode gating + `off` 不 attach writer |
| D18 registry 证据 | 判定流水线中 registry 分支 |
| D19 跨进程限制 | 不做硬化；报告与 Spec 声明已知限制 |

---

## 14. Implementation Order（每步含退出条件）

| Step | 内容 | 退出条件 |
|---|---|---|
| **S1** | migration `0003`（双方言）+ `health_store.py` + 其单元测试 | 迁移在 sqlite 应用成功、表/索引存在；`upsert`/读/删单测通过；**不动应用行为** |
| **S2** | `heartbeat.py`（config/throttle/jitter/fail-open）+ store 集成测试 | 节流/抖动/计数语义用例通过；`governance_tasks` 零写入断言通过 |
| **S3** | controller 接线：`heartbeat_writer` 注入点 + 首拍 + watchdog beat + `forget` + `_beat` fail-open | 首拍时序用例通过；bare 不写心跳；P2-1 与 governance 冻结套件全绿 |
| **S4** | `reclaim.py` 纯逻辑：`OwnerClass`/`classify_owner`/两个证据谓词/`ReclaimConfig`/health 视图 | 分类与谓词用例（含边界/时钟注入）全绿 |
| **S5** | `RuntimeHealthScanner`：startup sweep + reclaim 映射 + 清理 + 模式矩阵 | startup/periodic/幂等/竞争/清理/模式用例全绿 |
| **S6** | `server.lifespan` 接线（sweep 前置于 yield、periodic task、shutdown cancel + await） | lifespan 相关用例通过；`off` 模式不启动 scanner |
| **S7** | PG 门控套件 + 冻结断言更新（4 文件 9 处） | PG 套件全绿或有 DSN 时全绿；sqlite 全量 0 failed |
| **S8** | 全量回归 + 静态检查 + Scope Audit + 证据收集 → `P2-2_IMPLEMENTATION_REPORT.md` | §17 Verification Gates 全部通过 |

> Step 顺序理由：先建数据面与纯逻辑（无行为变化），再接线（行为变化集中在 S3/S6），最后回归与断言同步，便于回滚与定位。

---

## 15. Explicit Non-Goals

- ❌ 新增 TaskStatus 或修改 `TERMINAL_STATUSES`；❌ 修改 F8 lifecycle authority / funnel / CAS / replay；
- ❌ 引入 lease / fencing / 多实例互斥；❌ 引入 connection pool 或任何新依赖；
- ❌ 修改 frontend（任何文件）；❌ 修改 `app/agent/**`、`app/research/**`、`app/tools/**`；
- ❌ 新增 HTTP/WS 端点或事件 schema 变更（admin runtime 属 P2-4）；
- ❌ P2-3 durable timeline / conversation transcript；❌ P2-5 标题生成；
- ❌ resume / 断点续跑 / 自动重试；❌ checkpoint / research 面写入；
- ❌ LangSmith 或任何 observability UI / 埋点平台；
- ❌ Runtime Policy Tuning（默认值调参）；❌ `shutdown flush`（D6 候选）；❌ 前端 reclaim reason 展示（D12 候选）；
- ❌ 执行 migration、修改测试、创建 branch、commit/push/merge（本 Planning 阶段）。

---

## 16. D11 Problem Registry 动作项（声明，不在本阶段执行）

| 项 | 内容 |
|---|---|
| 动作 | 登记正式 Problem（`docs/problem/P0NN-*.md` + `PROBLEM.md` 索引行） |
| 时机 | **P2-2 Implementation 起点**（或 Readiness 通过时） |
| 题名（拟） | Runtime 缺少健康平面：进程退出/执行器失联后任务永久 RUNNING 且不可诊断 |
| Type / Severity（拟） | Runtime behavior / Architecture constraint；High；预期 Open → 由 P2-2 实现关闭 |
| 关联 | `RUNTIME_OBSERVABILITY_AUDIT_REPORT.md`、`P2-2_SPEC_v2.md` Rev 2.2、`D-Phase2-P2-2-001` |
| **前置** | **需单独批准**（文档变更）；本 Planning 阶段**不执行** |

---

## 17. Verification Gates

| Gate | 内容 | 通过标准 |
|---|---|---|
| VG-1 | 新增套件（heartbeat + reclaim） | 全绿 |
| VG-2 | PG 门控套件 | 有 DSN：全绿；无 DSN：如实 skip 并记录 |
| VG-3 | governance / sessions 定向回归 | 0 failed |
| VG-4 | P2-1 冻结套件（23 用例） | 全绿 |
| VG-5 | research 定向回归 | 0 failed |
| VG-6 | 全量 sqlite（排除 MySQL 污染文件） | 0 failed |
| VG-7 | 静态：compileall / ruff / `git diff --check` | 0 error |
| VG-8 | Scope Audit：`git diff --stat` 仅含 §2.1 文件；`pyproject.toml`/`requirements.txt`/`frontend/`/`app/agent`/`app/research` diff 为空 | 通过 |
| VG-9 | 不变式抽查：I2/I3/I5/I9/I11 对应断言用例存在且通过 | 通过 |
| VG-10 | 证据与报告：`P2-2_IMPLEMENTATION_REPORT.md`（diff 摘要、测试输出、风险与剩余项） | 产出并等待 Review/Freeze |

---

## 18. Scope Audit（本轮 Planning）

| 项 | 结果 |
|---|---|
| 本轮写入文件 | 仅 `P2-2_IMPLEMENTATION_PLAN.md`（+ 本报告末尾输出的证据命令） |
| 代码 / migration / 测试 / frontend | **零改动**（`git diff --name-only -- app db tests frontend` = 空） |
| branch | 未创建（仍 `main`） |
| commit / push / merge | 未执行（HEAD 未变） |
| `.venv` / 依赖文件 | 未触碰 |
| P2-3 / P2-4 / P2-5 | 未触碰（仅作为 Non-Goals 列出） |

> Implementation 阶段启动前置：**用户单独批准 Coding/Implementation** + 创建 `feature/runtime-p2-2-heartbeat-stale-reclaim` 分支（AGENTS §10.1）。本 Plan 不自行进入该阶段。
