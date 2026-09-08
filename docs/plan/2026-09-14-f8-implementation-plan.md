# F8 Implementation Plan Rev2 — Runtime Execution Governance（待 Plan Final Gate；禁止 implementation）

> 状态：**F8 Implementation Plan Rev2 — Conditional Pass 修订版（Rev1 → Rev2），待 Plan Final Gate；禁止 implementation**
> Rev1 Plan Review 结论：**Conditional Pass / Requires Revision**——不推翻架构；Rev2 仅修复 P0-1..P0-4 + P1-1..P1-5（见 §0 修订记录）。
> 契约：`docs/spec/2026-09-14-f8-runtime-execution-governance.md` rev2（Final Gate **PASS/FROZEN**）——本 Plan 不修改 Spec 语义。
> 文件位置约定：此后 plan 文件一律置于 `docs/plan/`（Rev1 从 `docs/spec/` 迁入本目录并升级为 Rev2）。

---

## 0. Rev1 → Rev2 修订记录

| # | 修订 | 落点 |
|---|---|---|
| P0-1 | Terminal funnel persistence failure 语义：TaskRecord durable write = control-path；retry + pending 收敛 + startup 兜底；明确 API 返回语义 | §6 |
| P0-2 | event_id / monitor_seq / governance_seq 三者边界锁死（含示例）；单 governance ordering source | §12 |
| P0-3 | agent_step：先 runtime probe 再锁 hook；禁止退化 llm_calls==agent_step；ownership 矩阵 | §8 |
| P0-4 | orphan 判定收缩为确定性输入 A/B/C；去掉"新建 thread 猜测 orphan"；policy expiry 字段/时机/幂等显式 | §11 |
| P1-1 | startup reconciliation：single-instance 全量收敛；owner_instance 仅 audit；删除半多实例 env 分支 | §11/§19 |
| P1-2 | migration rollback 表述修正（versioned/idempotent/fresh+upgrade verify；测试 DROP 是 fixture/reset） | §4 |
| P1-3 | TaskRecord/SQL/telemetry 字段一致性；usage_available 归属 telemetry（无独立列） | §2/§16 |
| P1-4 | 删除 f7_hook.py（无实际职责）；F7 边界用集成测试证明 | §1/§17 |
| P1-5 | 补完整 terminal race matrix | §5/§21 |

---

## 1. 文件级修改范围（Rev2）

| 文件 | 动作 | 内容 |
|---|---|---|
| `app/runtime/governance/__init__.py` | 新增 | 公共导出 + 版本常量 |
| `app/runtime/governance/store.py` | 新增 | governance DB 访问 + 自管迁移 runner（双后端） |
| `app/runtime/governance/models.py` | 新增 | 枚举 + TaskRecord dataclass + row/JSON 映射（**单一字段权威，见 §2**） |
| `app/runtime/governance/controller.py` | 新增 | GovernanceController：execute wrapper、唯一 terminal funnel（§5）、pending 收敛（§6）、watchdog |
| `app/runtime/governance/counters.py` | 新增 | BudgetCounter（零 DB 依赖，§9） |
| `app/runtime/governance/callbacks.py` | 新增 | GovernanceCallback（llm/tool 事件） |
| `app/runtime/governance/steps.py` | 新增 | agent_step 探测 + 回合边界 hook（**先 probe 后锁定，§8**） |
| `app/runtime/governance/sequencer.py` | 新增 | canonical Event Sequencer（单写者，(task_id,seq) 分配，§12） |
| `app/runtime/governance/events.py` | 新增 | Event 信封/幂等 append/查询 |
| `app/runtime/governance/replay.py` | 新增 | durable replay + catch-up |
| `app/runtime/governance/sweeper.py` | 新增 | orphan（§11）/session_close/startup reconciliation |
| ~~app/runtime/governance/f7_hook.py~~ | **删除（Rev2）** | P1-4：不引入空职责模块 |
| `app/runtime/governance/adapter.py` | 新增 | monitor ↔ governance adapter（canonical event 生成；不复制 monitor._emit） |
| `app/runtime/checkpoint.py` | 只读 | 不改；governance 经现有入口取 saver（诊断） |
| `app/api/server.py` | 修改（最小） | submit/cancel/tasks/events/close 接线；active_tasks 降级为 in-process handle registry（controller 托管） |
| `app/api/monitor.py` | 修改（adapter 边界，additive） | report_* 经 adapter 提交；**R3 payload 语义不变（§13）** |
| `app/agent/main_agent.py` | 不改 | F7 接线点位置不变（§17） |
| governance migrations | 新增 | `0001_governance.{sqlite,postgres}.sql`（§4） |
| `tests/test_runtime_governance*.py` | 新增 | 测试矩阵（§21） |
| `docs/…` | 同步 | D018/ROADMAP/ARCHITECTURE/TESTING（Gate 后） |

不改：research 表族/模块、checkpoint 官方 saver 表族、tools、prompts.yml、F1–F7、frontend 既有契约。

## 2. TaskRecord / SQL / telemetry 字段一致性（P1-3：单一字段权威）

字段以本 Plan §2 为准（= Spec §5.2 对齐 + 治理派生字段）；SQL DDL 与 models.py 从本表机械导出，禁止各自再定义一套。

| 字段 | 类型/存储 | 说明 |
|---|---|---|
| task_id | PK | uuid4 hex |
| thread_id | NOT NULL | 净化后 |
| run_id | NULL | run_deep_agent 回填 |
| status | NOT NULL | running \| 终态（§3） |
| terminal_reason | NULL | §3 |
| error_kind / error | NULL | agent_failure / governance_control_failure / framework_recursion_safety |
| policy_snapshot | JSON | 生效 policy 快照 |
| effective_limits | JSON | 实际生效 limits（**不含** framework recursion——独立字段，见 §9） |
| counters_snapshot | JSON | terminal 时点 {agent_steps, llm_calls, tool_calls, search_calls, duration_ms, **tokens**{...}?}——usage_available **只存在于此 telemetry JSON 内**，**无独立 TaskRecord 列**（P1-3：usage 归属 telemetry） |
| superseded_by | NULL | superseded 时填新 task_id |
| parent_session | NULL | 预留（session 语义，无独立 Registry） |
| underlying_linger_observed | BOOL NULL | 观察字段 |
| owner_instance | NOT NULL | **audit metadata only（P1-1）** |
| version | INT | 乐观并发守卫 |
| created_at / started_at / finished_at | — | — |

- usage_available 归属（P1-3 裁决）：**telemetry 层**。落在 (a) Event payload `llm_usage?`（每次 LLM 事件，可空）与 (b) TaskRecord.counters_snapshot JSON 的 `tokens:{input,output,total,available}`。**不新增 TaskRecord 顶层 usage_available 列**；不出现 schema/models/telemetry 三套定义。
- `parent_session`：F8 v1 仅作预留字段；session 生命周期用显式 `session_close` API 表达（无 Session Registry，P0-4）。

## 3. 状态枚举

- TaskStatus：`running`（无 queued）。
- TerminalReason：`completed / failed / cancelled / timed_out / budget_exceeded / superseded / aborted / orphan_reclaimed`。
- error.kind：`agent_failure / governance_control_failure / framework_recursion_safety`。

## 4. Migration / 双后端（P1-2 修正）

- `governance_tasks` / `governance_events`：schema 以 §2 与 §12 为准；SQLite TEXT-JSON / PG JSONB 同语义；`UNIQUE(task_id, seq)`、索引 thread_id/status、run_id；
- 迁移策略（**versioned migration**）：`governance_schema_migrations` 版本表；**幂等 apply**（同 research 模式）；验证 = **fresh DB verification + upgrade verification（0000→0001）**；
- **删除 Rev1 "rollback=移除文件+DROP" 表述（P1-2）**：migration 无"回滚文件"语义；测试环境清库属 **fixture/reset**，与 migration rollback 区分开；
- PG 后端：复用 checkpoint DSN 的**独立表**（不碰 checkpoint 表族）；单实例假设见 §19。

## 5. Terminal 唯一写入口 + race（P0-1/P1-5）

### 5.1 funnel（Gate D：唯一 terminal 写入口）

```text
terminalize(task_id, reason, *, error_kind=None, error=None, counters=None):
  1. controller in-memory guard（asyncio.Lock；dict[task_id→status]）
      已 terminal → return (no-op, already_terminal)
  2. 内存置 terminal + 快照；底层执行（若在本进程）→ cancel 信号
  3. durable terminal write（governance_tasks UPDATE … WHERE status='running' AND version=?）
      成功 → append Event task_status_change（同批/紧随；event 写失败=observation，不影响 task 状态）
      失败 → 进入 pending 集合（§6），API 可返回内存态（degraded 标记）
```

- completed 与 watchdog/budget/cancel/supersede 同刻竞争：均由 funnel 串行化，赢者即最终 reason；其余 no-op。
- `counters_snapshot` 与 `superseded_by` 在 funnel 内一次性写入 → deterministic。

### 5.2 terminal race matrix（P1-5，全部进入测试矩阵）

| race | 期望 |
|---|---|
| completed vs cancel | exactly one terminal；completed 赢 → 无 task_cancelled 语义；cancel 赢 → cancelled + linger 观察 |
| completed vs timeout / budget / supersede | 同上（funnel 仲裁，once） |
| failed vs cancel / timeout | 单一终态；先到者胜 |
| budget vs timeout | 单一终态 + 对应 counter 快照 / deadline 时间戳确定性 |
| cancel vs timeout | 单一终态（cancelled 或 timed_out，funnel 顺序决定） |
| supersede vs cancel | 单一终态 + superseded_by deterministic |

每条断言：exactly one terminal state；exactly one successful terminal transition（DB rowcount==1 恰一次）；terminal Event 不重复；counters_snapshot deterministic；superseded_by deterministic。

## 6. Terminal funnel 的 persistence failure 语义（P0-1 锁死）

- **分类**：TaskRecord durable write（task 生命周期状态）属于 **control-path**，不套 observation fail-open。
  - 硬预算/cancel/lifecycle 收敛不得因 governance DB 故障静默关闭（Spec §7.2）；
  - memory terminal 是 control 的**权威决策**：内存置 terminal 后即停止调度/放行（底层 cancel），
    **不存在"无状态无限运行"**——enforcement 在内存，DB 只是记录侧。
- **retry 策略**：terminal durable write：立即 + 0.5s + 2s（共 3 次）；仍失败 → 记入 in-process `pending_terminal`
  集合（含 reason/快照），由 controller 周期 reconciler（每 15s）与 shutdown flush 持续重试。
- **terminal API 返回**：允许在 durable write 最终失败时返回（返回内存 terminal + `degraded_durability=true`
  标记，绝不谎报 durable 已落库）；`GET /api/tasks/{id}` 展示 pending 状态。
- **shutdown/restart 处理"memory 已 terminal、DB 仍 running"**：
  - 正常 shutdown：flush pending_terminal（尽力写）；写不进 → 日志告警；
  - 进程死亡后 DB 残留 running：**startup reconciliation 全量收敛 running → aborted（P1-1，single-instance）**；
    若原 terminal 是 completed 但 durable 丢失，治理面记录 aborted + 审计注记（research 面仍可读到 finished——
    两平面可交叉核对，属可接受的有界不一致并文档化）。
- 保证"不会无状态无限运行"：control 决策在内存完成即停止执行；DB 状态的不一致只会造成
  "已停执行但 DB 显示 running（短暂）或已收敛 aborted（重启后）"，而不会让任务继续跑。

## 7. GovernanceController / TaskRecord

- `controller.execute(task_id, coro, policy)`：绑定 counters/callback/watchdog；coro = run_deep_agent（F7 位置不变）。
- lifecycle 转移全部经 §5 funnel；controller 是唯一 in-process handle registry（取代 active_tasks 作为 Task Model）。

## 8. agent_step（P0-3：先 probe 后锁定；禁止退化）

### 8.1 Implementation Step 0 = Runtime Event Probe（必须先于 hook 落地）

```text
probe 目标：在真实 deepagents 0.5.7 + LangGraph 上跑一个小型确定性任务，记录完整事件序列：
  model_start/model_end / tool_start/tool_end / subagent(task 工具) dispatch+return /
  graph 每轮 transition / 最终无工具回答。
产出：事件序列日志 + transition 表（哪些 signal 标识"一轮 decision round 结束/下一轮开始"）。
判定：若能稳定识别 Spec §7.5 decision round → 锁定实际 hook；
     若框架 signal 不足以可靠识别 → **回到 Plan Review 裁决**（禁止退化为 llm_calls==agent_step，
     也禁止用未验证启发式上线）。
```

### 8.2 ownership 矩阵（明确 main/subagent/首轮/各形态）

| 场景 | agent_step | llm_calls | tool_calls / search_calls |
|---|---|---|---|
| 首轮初始模型调用（main） | +1（回合起点） | +1 | — |
| main 决策后调用工具 | 不单独计 | — | +1（internet_search 另计 search） |
| 工具返回后 main 再次决策 | +1 | +1 | — |
| main 无工具直接最终回答 | +1（终答回合） | +1 | — |
| subagent 调度（main 调 task 工具） | main 回合内 1 次 tool_calls；**不**另计 main step | — | +1 |
| subagent 内部自身回合（每轮模型→工具→回模型） | 每轮 +1 | 每轮 +1 | 各自 +1 |
| 非业务回合 LLM（summarization/retry 等，若未来引入） | **不**计 | +1 | — |

- 语义分离：agent_step = decision round（业务回合）；llm_calls = 全部 provider 调用（含非回合）。
- 反偷换测试：注入假中间件额外 LLM 调用 → llm_calls+1 且 agent_step 不变；再验证普通回合 llm_calls==agent_step 仅数值偶合（hook 不同源）。

## 9. BudgetCounter 与 enforcement hook（Gate A/E：零 DB 依赖）

| counter | hook | 先比较后执行 | 超限 |
|---|---|---|---|
| llm_calls | on_llm_start（发起前同路径） | count<limit 才放行+递增 | raise GovernanceLimitExceeded → funnel(budget_exceeded) |
| tool_calls | on_tool_start | 同上 | 同上 |
| search_calls | on_tool_start + name=='internet_search'（tool 子集，独立限额） | 同上 | 同上 |
| agent_step | steps.py 锁定 hook（§8） | 同上 | 同上 |
| wall_clock | watchdog（controller 内） | deadline 到达 → funnel(timed_out) | 见 §10 |

- counters 全内存（asyncio.Lock）；**完全不依赖 DB**（E：DB 故障下 budget 仍触发，测试覆盖）。
- recursion_limit：controller 注入独立 `framework_recursion_safety`（默认 5000）；**不参与** agent_step/budget；
  GraphRecursionError → funnel(failed, framework_recursion_safety)（≠ budget_exceeded，隔离测试）。

## 10. watchdog / deadline convergence

- controller watchdog：每 1s 检查 `now >= created_at+wall_clock_timeout` → funnel(timed_out) 路径：cancel 底层 + 收敛；
- 不做 OS/进程级 kill（F）；收敛后底层 linger → `underlying_linger_observed` 观察标注，不二次翻转状态。

## 11. orphan / session_close / restart（P0-4/P1-1 收缩）

orphan 判定输入（**确定性，仅三种**）：

```text
A. running TaskRecord + in-process registry 无该 task 的 active handle（异常路径丢失引用）
   → orphan_reclaimed（funnel + 治理缺陷审计事件）
B. 显式 session_close(thread_id)（API）
   → 该 thread 全部 running tasks → cancel + orphan_reclaimed
C. WS disconnect
   → 永不触发 orphan（observer ≠ owner）
```

- **删除**"用户新建 thread/session 就猜测旧任务 orphan"（P0-4）；
- policy expiry（可选，**默认 OFF**）：若开启，字段 = policy_snapshot.max_idle_minutes（nullable）；判定时机 =
  sweeper tick（15s）；幂等 = 已 terminal 跳过；回收 = cancel + orphan_reclaimed；
- startup reconciliation（P1-1）：single-instance **全量** running → aborted；owner_instance 仅 audit metadata，
  **不**做 `owner != current 跳过` 的半多实例逻辑，不引入 env 分支、不设计 owner/lease/heartbeat；
- restart ≠ orphan：aborted 与 orphan_reclaimed 分离。

## 12. Event Sequencer / seq 边界（P0-2 锁死）

- **event_id**：publish 时生成，live/durable/replay 共用唯一事件身份（durable 成功/失败都保持同一 event_id）；
- **monitor_seq**：既有 R3 live 展示/顺序语义（进程级）；**不得**作为 governance replay cursor；
- **governance_seq**：durable history 的 replay cursor（task-scoped，(task_id,seq) 唯一，sequencer 分配）；
- **单 governance ordering source**：仅 durable sequencer（C：monitor 不再持有第二套持久 seq）；
- frontend：以 event_id 去重；空洞判断用 replay 返回的 governance_seq；**不得混用 monitor_seq/governance_seq**；
- 示例：

```text
event_id=X
live:     { …, "event_id":"X", "seq":101 }           # monitor_seq（R3 原样，governance_seq 不可见）
replay:   { "event_id":"X", "governance_seq":17, … } # durable API 响应
```

- durable success → event 落库并分配 governance_seq，再进 live（durable=true）；durable failure → 仍可进 live
  （durable=false 预览，无 governance_seq），记 durability_gap；两者 event_id 一致。

## 13. monitor adapter 边界

- adapter.publish(event_type, message, data)：monitor report_* 内部改调（fail-open）；
- 不修改 R3 payload schema/字段语义：live 事件仍是现有 monitor envelope（event_id/monitor seq/type/…）；
  新增治理字段只出现在 durable API 响应层（additive 且不在 WS payload 制造双 seq）；
- monitor 仅保留 WS 推送职责；canonical 事件构造与持久化归 governance（不复制 monitor._emit）。

## 14. durable / live / replay 顺序关系

```text
触发 → adapter.publish(event_id)
   ├─ sequencer：durable 写 + governance_seq 分配（成功）→ enqueue live（durable=true）
   └─ 失败：enqueue live（durable=false，无 governance_seq）→ durability_gap
回放：GET events?since_seq=（governance_seq）→ durable 升序；客户端 event_id 去重；seq 空洞 → catch-up
```

## 15. replay API / WS since_seq

- `GET /api/threads/{thread_id}/events?since_seq=&limit=`（durable，governance_seq 升序，含 next_seq 分页）；
- WS 握手 `?since_seq=` → 先回放再转 live；全新观察者 → 最近 N 条（默认 200）。

## 16. telemetry / provider usage（P1-3 归属落实）

- Event payload：`llm_usage:{input,output,total}` + `latency_ms`（provider 暴露时）；无 usage → payload 无该字段；
- TaskRecord.counters_snapshot JSON：`tokens:{input?,output?,total?,available:bool}`——**usage_available 仅存于此**
  （无独立列）；terminal 快照含 agent_steps/llm/tool/search/duration；
- 不估算 token；40 万 token 案例只建立未来可证明性。

## 17. F7 integration（P1-4）

- 无 f7_hook.py；保持 `run_deep_agent → F7 finalize（may_emit task_result 内）→ return → controller.completed`；
- 集成测试：normal completion → F7 finalize 触发；governance terminal（cancel/timeout/budget/supersede/orphan/aborted）
  → 不伪造 claims（F7 不触发）；
- main_agent/run_deep_agent 结构不改。

## 18. failure semantics（落实 Spec §7.3/§16）

(a) agent_failure → failed；(b) observation/persistence（event/telemetry 写）→ fail-open + durability_gap，不改状态；
(c1) submit store 不可用 → 拒绝受理（fail-closed）；(c2) controller 异常 → cancel + failed(governance_control_failure)；
(c3) startup store 不可用 → fail-fast；(d) recursion → failed(framework_recursion_safety)。
TaskRecord durable terminal write = control-path（§6），与 (b) 明确区分。

## 19. single-instance assumption（P1-1）

- v1 仅 single runtime instance；startup reconciliation 全量 running→aborted；owner_instance=audit only；
- 无 owner/lease/heartbeat；无"半多实例"分支；文档注明多实例共享 governance/checkpoint PG 不支持。

## 20. API 与 Spec 一致性

- `POST /api/task`（+task_id）、`POST /api/task/{id}/cancel`、`GET /api/tasks?thread_id=`、`GET /api/tasks/{id}`、
  `GET /api/threads/{id}/events?since_seq=`、`POST /api/threads/{id}/close` 与 Spec §18 一致；
- 移除 Rev1 中 `POST /api/task/{id}/budget`（Spec §18 无此端点——补一致）→ 预算经 submit policy/服务配置；
- models.py/DDL/telemetry 以 §2 单表导出（P1-3）。

## 21. Test Matrix（AC 映射 + race + 双后端）

| 测试文件 | 覆盖 | AC |
|---|---|---|
| probe（Step 0，见 §8.1） | deepagents 事件序列记录 + decision round 可识别性判定 | —（前置产出） |
| test_runtime_governance.py（sqlite） | 状态机/terminal 幂等；**race matrix（§5.2 全 9 组）**；counter 先比较后执行（120 无 121）；step hook 反偷换；recursion 隔离；watchdog/linger；funnel persistence failure（retry 3 次 → pending → 重启 aborted；API degraded 返回）；orphan A/B/C（WS 断线不触发）；session_close；startup 全量 aborted；event sequencer 串行化/(task_id,seq) 全序/event_id 幂等；replay catch-up；adapter fail-open；usage_available 归属；c1/c2/c3 | AC1–AC14 |
| test_runtime_governance_postgres.py | 同语义 PG 镜像（schema/UNIQUE/乐观更新/迁移 fresh+upgrade 验证） | AC1–AC14 |
| monitor adapter 测试 | R3 payload 不变；live durable=false 预览；event_id 跨 live/replay 一致 | AC9/AC10 |
| F7 集成 | normal→F7；governance terminal→不伪造 | AC12 |
| F1–F7 全仓回归 | 无越界 | AC14 |

## 22. Implementation Scope Audit 计划

- 允许：`app/runtime/governance/**`（新）、server/monitor 最小接线、governance migrations、测试、docs（含本 Plan）；
- 禁止：F1–F7 schema/semantic/API、checkpoint 官方 saver 表族、tools、prompts.yml、frontend 契约、依赖、基础设施；
  无 Redis/Kafka/VectorDB/EventSourcing；非 Scheduler（无 durable queue/priority/worker）；
- 验收前 `git status/diff --stat` 复核；越界先修正再报告。

## 23. 未决实现问题（Plan-level 收敛后残余）

| # | 未决 | 处理 |
|---|---|---|
| R1 | agent_step 帧映射需 probe 验证 | Implementation Step 0 = probe；不可识别 → 回 Plan Review（不退化） |
| R2 | completed durable 写丢失后重启 → 治理面 aborted 与 research finished 不一致 | 有界不一致 + 双平面审计注记（§6），文档化 |
| R3 | pending_terminal 集合在长期 DB 故障下的最终一致 | 重试 + shutdown flush + startup aborted 兜底（§6） |

---

# Plan Final Gate Decision

- P0-1..P0-4 / P1-1..P1-5：全部在 Plan 层闭合（修订记录 §0）；
- 额外检查 1–10：API/Spec 一致（修正 budget 端点遗漏）；persistence/models 单字段权威；SQLite/PG 同语义；
  monitor adapter 不改 R3 payload；单 governance ordering source；budget 零 DB 依赖；control/observation 分离；
  checkpoint/research read-only；F1–F7 无越界；无 scheduler/新 infra 漂移；
- 残余 R1–R3 已定义为实施期前置 probe / 有界不一致 / 兜底策略，不阻塞 Plan。

> **F8 Implementation Plan Rev2 = PASS / READY FOR IMPLEMENTATION**
> （Implementation 仅在用户下达进入 implementation 指令后进行；本文件为 docs/plan/ 下的 Plan 契约。）
