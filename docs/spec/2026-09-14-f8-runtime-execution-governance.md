# F8 Spec rev2 — Runtime Execution Governance（Task Lifecycle · Hard Budget · Telemetry · Durable History/Replay）

> 状态：**F8 Spec rev2 — Conditional Pass 修订版（rev1 → rev2），待 F8 Final Gate Review；禁止 implementation**
> rev1 Gate 结论：**Conditional Pass / Requires Revision**——方向不变，rev2 锁死 Runtime 语义（P0-1..P0-5 + P1-6..P1-11，见 §R）。
> 红线：本轮仅修改本 Spec 文件；未修改任何 app/migration/tests/dependencies/Agent/Tool/Checkpoint/monitor/frontend/infrastructure。
> 前置：Phase0/F1–F7 均 Final Gate PASS/FROZEN。F7（Research State Bridge）保持 terminal-only 生命周期，本轮不改。
> 阶段链：… → **F8 Runtime Execution Governance** → F9+（incremental Research State / Control，另行 Gate）

---

## 1. Capability Positioning

> **F8 Runtime Execution Governance：把 Agent Runtime 从"进程内 asyncio task + Prompt 软约束 + 临时 WebSocket 观察"升级为"持久化 Task Lifecycle + Hard Runtime Budget + Cancellation/Timeout Governance + Runtime Telemetry + Durable Task/Event History（可查询、可 replay）"。**

- F8 是 **Runtime Governance**，不是 Research Intelligence：不做 replan / verify-decision / semantic stop-continue / 自评 / 新 planner agent；也不是 Job Scheduler（不做 durable scheduling/queueing/priority/worker allocation，见 §4/§20）。
- F8 是 **deterministic-first**：budget、counters、timeout、lifecycle、terminal 转移、replay 排序、幂等全部 deterministic，**不由 LLM 决定自己是否超预算**。
- 触发背景（真实 E2E，非推测为"最终失败"）：
  > 任务刷新发生时仍在运行（>20 min、观察到 ~400k tokens）；刷新后后端 task 理论上继续；WS 事件丢失、无 replay；新 thread 可遗留旧 task；无持久 Task state；无可靠历史 token 构成。历史数据不足以证明 40 万 token 的具体构成或最终结果——F8 建立**未来可证明性**，不回填/猜测本次数据。

## 2. Current Runtime Gap（审计证据）

| 层 | 现状（代码事实） |
|---|---|
| Task 台账 | 仅 `app/api/server.py` 进程内存 `active_tasks: dict[thread_id, asyncio.Task]`；无持久 TaskRecord、无状态查询 API |
| 取消 | `POST /api/task/{thread}/cancel`：`task.cancel()` + `await wait_for(task, 1.0)`；超时返回 `cancelling`；**asyncio Task.cancel() 不保证同步阻塞工具立即停止** |
| 替换 | 仅"同 thread 新任务"cancel 旧任务；新 thread 不影响旧任务 → 遗留风险 |
| 观察 | `monitor.py` per-thread asyncio.Queue + 单消费者；disconnect/`connect` 时丢弃队列 → **事件无持久化、无 replay** |
| 执行态 | LangGraph checkpoint 按 thread_id 持久化**图执行态** |
| 研究面 | research SQLite/PG（F1–F7）持久化 Research Artifacts |
| 预算 | 无 wall-clock / steps / llm / tool / search 硬约束；deepagents `create_deep_agent` 固定 `recursion_limit=9999`；tavily "≤5 次检索"仅为 prompt 软约束 |
| 计量 | 全仓无 usage/call 计数器；无 token usage 落库（qwen provider 是否暴露 usage_metadata 未在本仓验证） |
| 重启 | backend 重启：active_tasks 消失、checkpoint 保留图态但无 resume 入口、research_runs 可能停在 running → 三层脱节 |

四态边界（LangGraph checkpoint ≠ TaskRecord ≠ Event History ≠ Research Data Plane）见 §15。

## 3. Goals

1. 建立**持久化 TaskRecord** 与完整 Task Lifecycle 状态机（§6），取代 `active_tasks: dict` 作为唯一 Task Model；
2. 提供 **Hard Runtime Budget**：`wall_clock_timeout / max_agent_steps / max_llm_calls / max_tool_calls / max_search_calls`（可配置、deterministic 强制；计数语义 §7/§8 锁死）；
3. 定义 **Cancellation / Timeout / budget-exceeded / replacement / orphan / restart** 语义与 governance terminal；
4. 建立 **Runtime Telemetry**（duration、llm/tool/search call counts、per-call latency、budget consumption）；token usage 仅 provider 可靠暴露时记录，不伪造 token 硬限制；
5. 建立 **append-only Event History** + Task 状态持久化 + **Replay / Reconnect with cursor**；
6. 明确 **control-path vs observation-path**（P0-1）：hard budget 不以 persistence failure 被静默关闭；observation fail-open 有明确 degraded/durability_gap 表达；
7. F7 integration boundary 保持（§19）：F7 terminal-only 不改；F8 保证的是"governance runtime 可控前提下 TaskRecord 收敛"，process crash 场景由 startup reconciliation 收敛（P1-10 措辞）。

## 4. Non-goals（写死）

- ❌ F1–F7 research schema/semantic 变更（不改 research 表族/公共 API）；
- ❌ incremental Research State（运行中增量 F2–F6）；
- ❌ replan / verify decision / stop-continue semantic reasoning / Agent self-evaluation / 新 planner/control agent；
- ❌ Redis / Kafka / Vector DB / Event sourcing 基础设施 / 新依赖；
- ❌ prompt workaround（把硬约束写进提示词当 enforcement）；
- ❌ tool-result 人为截断作为治理手段（不改工具返回语义）；
- ❌ token fake estimation 作为硬 enforcement（无可靠 usage 时不硬限 token）；
- ❌ 修改 checkpoint 官方 saver/表族、修改 monitor 事件 payload schema（R3 既有字段语义保持 additive）；
- ❌ **durable scheduling / queueing / priority scheduling / worker allocation**（F8 只做 admission control；不演化为 Job Scheduler，见 §20）；
- ❌ **多实例共享 governance/checkpoint PG**（F8 只支持 single runtime instance，见 §10/§14/OQ）。

## 5. Runtime Object Model

### 5.1 概念边界：Session / Thread ≠ Task ≠ Run

- **session/thread**：前端长期身份（localStorage thread_id；= WS 路由 key + checkpoint thread_id）。
- **Task**：一次用户提交产生的治理单元（F8 持久化主体）。当前实现 1 submit = 1 asyncio task = 1 run_deep_agent。
- **Run**：一次 `run_deep_agent` 执行（run_id，research run_id 同源）。Task 与 Run 当前 1:1。
- **"新建会话"≠ cancel 所有旧任务**：F8 以显式 session/task 生命周期关系表达（§10/§18），禁止把"换 thread_id"隐式等价为全量 cancel。

### 5.2 TaskRecord（持久化字段）

```text
task_id            PK（uuid4 hex；submit 时生成）
thread_id          非空（净化后）
run_id             可空（run_deep_agent 启动后回填；与 research run_id 同源）
status             task_status（§6；无 queued）
created_at / started_at / finished_at
terminal_reason    completed | cancelled | timed_out | budget_exceeded | failed |
                   superseded | aborted | orphan_reclaimed | (null)
error              可空（分类字段见 §16：agent_failure / governance_control_failure /
                    framework_recursion_safety，随事件带）
policy_snapshot    生效 budget 策略（§7）
effective_limits   实际生效 limits（不含 framework recursion 派生——recursion_limit 独立于
                    agent_step 语义，见 §8 P0-5）
counters_snapshot  terminal 时点累计（agent_steps/llm_calls/tool_calls/search_calls/duration）
superseded_by      same-thread 新任务 task_id（可空）
```

### 5.3 部署假设（P0-4，写死）

```text
F8 v1 仅支持：single runtime instance / single active backend execution owner
（单进程：1 个 uvicorn worker + 1 个 governance controller）。

不在 F8 v1 支持范围：
  - 多实例共享 PostgreSQL governance/checkpoint 表；
  - execution owner / lease / heartbeat 机制（如需多实例，另行 Spec，本轮不做）。

推论：
  - startup reconciliation 把"本实例在途 running → aborted"只在单实例假设下安全；
  - 若未来引入多实例，必须先引入 owner/lease/heartbeat，且 startup sweep 不得误杀
    其它实例的 Task——该场景本轮明确不支持（OQ4 记录）。
```

## 6. Task Lifecycle State Machine

### 6.1 状态集（rev2：移除 queued，P1-8）

```text
非终态：
  running           submit 成功即进入（无 queued——F8 不是 scheduler，不做 durable queue；
                    started_at 在任务协程实际启动时写，created→started 窗口不做独立状态）

终态（terminal）：
  completed         正常完成（task_result emit 路径）
  failed            agent execution failure / framework_recursion_safety / governance
                    control failure（分类见 §16；不把所有 governance 异常都归 failed 以外的
                    单一语义——见 P1-11）
  cancelled         显式 cancel 收敛
  timed_out         wall-clock governance deadline 到期（§9）
  budget_exceeded   agent_steps/llm/tool/search counter 超限（§7/§8）
  superseded        same-thread 新任务替换旧任务
  aborted           进程 shutdown/restart 收敛（startup reconciliation，§10）
  orphan_reclaimed  orphan 判定并回收（§10；判定基于台账与实际执行者脱节，非 WS 断线）
```

- 移除 `queued` 的理由（P1-8）：F8 v1 无 scheduler/queue 语义；submit 成功 = 已受理并进入 running（admission 拒绝则根本不建 TaskRecord，§20）；保留无语义状态只会制造伪状态。

### 6.2 转移表（deterministic；terminal 至多一次，幂等）

```text
running → completed / failed / cancelled / timed_out / budget_exceeded /
          superseded / aborted / orphan_reclaimed
任何非终态 → terminal（一次；已 terminal 不重复转移）
```

### 6.3 terminal 语义与底层执行的关系（诚实边界，P0-2 统一）

- **TaskRecord terminal = governance 收敛声明**，不等价于 OS/process-level termination：
  - completed/failed：底层 asyncio task 已结束；
  - cancelled/timed_out/budget_exceeded/superseded：governance 先收敛 TaskRecord，再向底层注入 cancel；
    同步阻塞工具（无 await 点）可能在收敛后 linger → telemetry 标注 `underlying_linger_observed=true`；
    **不承诺"N 秒内进程一定停止"**（§9）；
  - aborted/orphan_reclaimed：进程可能已无执行者；TaskRecord 是对事实的收敛声明，不隐含内存动作。

## 7. Budget Policy（P0-1/P1-6 锁死）

### 7.1 Prompt soft ≠ Runtime hard

- soft：prompts.yml 只作行为引导，不作为 enforcement 证据；
- hard：governance 代码强制，deterministic，不由 LLM 判断。

### 7.2 control-path vs observation-path（P0-1，写死）

```text
Control-path（必须 deterministic、不可被静默关闭）：
  - hard budget enforcement（counter 比较与超限中断）
  - cancellation / timeout 收敛
  - lifecycle transition（TaskRecord terminal）
  权威来源：governance controller 内存中的计数与状态（进程内唯一写者）。
  持久化只是 control 的"记录侧"，不是 control 的前提：
    - counters 在内存递增并比较（锁内）→ 不依赖 DB 可用性；
    - DB 写失败不影响 enforcement 决策（budget 照常触发）。

Observation-path（允许 fail-open / degraded）：
  - telemetry 事件、event history 落库、replay 数据、durability
  失败语义：写失败 → 记录 durability_gap / degraded telemetry 标记，不阻断 Agent；
  见 §11/§12/§16。
```

**明确禁止**：不得因 governance DB 故障而把 hard budget 悄悄置为 off（即：不允许
"DB down → counters 不比较 → 任务无限运行"）。

### 7.3 governance failure policy（P0-1/P1-11，不是把所有故障归 failed）

| 故障类别 | 定义 | TaskRecord/服务行为 |
|---|---|---|
| (a) Agent execution failure | run_deep_agent 内部异常 / terminal_guard error 路径 | `failed`（error.kind=agent_failure） |
| (b) Observation / persistence failure | telemetry/event/durability 写失败 | **fail-open**：Agent 继续跑；标记 durability_gap / degraded telemetry；**不改 Task 状态** |
| (c) Control-path failure | governance controller 自身异常（计数/收敛逻辑抛错、submit 时无法建 TaskRecord、startup 无法初始化 governance store） | 定义如下： |
|  (c1) submit 时 governance store 不可用 | 无法建 TaskRecord / 无法落 policy | **拒绝受理（fail-closed）**：不启动无治理的 Agent 执行；返回明确错误 |
|  (c2) 运行中 controller 异常（本进程） | 计数/收敛逻辑崩 | 包装层捕获 → 取消底层执行 → TaskRecord 收敛 `failed`（error.kind=governance_control_failure）——**绝不"无状态继续无限运行"** |
|  (c3) startup governance store 不可用 | 服务无法自举 | fail-fast（同 checkpoint 纪律），拒绝启动无治理的服务 |
|  (d) framework recursion safety | GraphRecursionError 到达框架层安全上限 | `failed`（error.kind=framework_recursion_safety）——**不是** budget_exceeded（语义分离，P0-5） |

- 明确：governance 故障 ≠ 一律 failed；(b) 类绝不翻转 Task 状态；(c) 类按 (c1/c2/c3) 各自收敛。
- AC11 覆盖 (b)/(c2) fail-open 与 fail-closed 的不同路径。

### 7.4 Hard limits 与计数语义（P1-6 锁死）

| limit | 默认 | 计数单位 | 计数时机 | 比较语义 | 超限时当前 call |
|---|---|---|---|---|---|
| `wall_clock_timeout` | 600s | 秒（deadline = created_at+policy） | watchdog 周期检查（如每 1s） | deadline 到达即触发 timeout convergence（§9） | N/A（非 per-call；底层可能 linger） |
| `max_agent_steps` | 200 | agent step（定义见 7.5） | step 起点（见 7.5） | **先比较后执行**：count < limit 才允许下一个 step | **不允许发起**（在 step 开始前中断） |
| `max_llm_calls` | 120 | 一次 LLM HTTP call（含 main/subagent 全部） | LLM call 发起前（callback start） | count < limit 才允许发起 | **不允许发起**（第 121 次不会发生） |
| `max_tool_calls` | 300 | 一次 tool invocation（全部工具） | tool 调用发起前 | count < limit 才允许调用 | **不允许发起** |
| `max_search_calls` | 40 | 一次 `internet_search`（tavily） | internet_search 发起前 | count < limit 才允许调用 | **不允许发起** |

- 语义锁定：`max_llm_calls = 120` 严格 = "最多实际发起 120 次 LLM call"（先比较、达标即中断，
  不产生第 121 次）；其余 counter 同理；
- tool/search counter 是 tool_calls 的子集（search ⊆ tool），各自独立限额，各自触发 budget_exceeded；
- 触发即：cancel 底层执行 → TaskRecord=budget_exceeded → event task_budget_exceeded（带 counter 快照）。

### 7.5 agent_step 的正式定义（P0-5，锁死）

```text
agent_step = 一次"agent 决策回合"：从一次 LLM 调用（含 main agent 与任一 subagent）决定
            下一步动作开始，到其调度出的全部工具调用/子任务调用完成、再次回到 LLM 决定前
            为止的完整循环（含该回合中的 0..k 次 tool calls）。

计数事件：
  - step 起点 = 该回合第一次工具调用即将发起之前 或 直接输出最终答案前（无工具时）；
    实现上以"回合边界"（新一轮 LLM 决定将要发生）作为 step 递增点，保证每回合恰计 1。
  - 计数 ownership：governance controller（内存，锁内递增），与 LLM/tool callback 同一控制器。

范围：
  - main agent 与所有 subagent 的回合都计（同一 task 作用域）；
  - tool call 不单独算 step（tool_calls 单独计数，见 7.4）；
  - 子任务(subagent)整体调度算 main agent 回合内的 1 次 tool call（task 工具）与独立回合计数：
    每个 subagent 自己的 LLM/tool 回合也计 agent_step（同一计数器）。

max_agent_steps vs LangGraph recursion_limit（两者职责分离，禁止混用）：
  - max_agent_steps = F8 governance metric（上表，回合计数，先比较后执行）；
  - LangGraph recursion_limit = framework safety ceiling（不参与 agent_step 语义）。
      governance 设置 recursion_limit = 独立配置的 framework 安全上限（默认如 5000，
      远大于 max_agent_steps 且按框架单回合多节点特征设置——**不采用未经验证的
      max_agent_steps*2 作为语义**）；GraphRecursionError 若发生 → failed
      (framework_recursion_safety)，与 budget_exceeded 分离。
```

## 8. Enforcement Points（P0-1/P0-5/P1-6 落点）

```text
submit（POST /api/task）
  ├─ admission control（§20）：per-user/per-thread/global 上限 → 超限拒绝（不排队）
  ├─ governance store 不可用 → 拒绝受理（fail-closed，§7.3 c1）
  ├─ 创建 TaskRecord(running) → policy_snapshot/effective_limits 落库
  └─ 注册 governance controller（wrapper）：绑定 counters、watchdog、callback

run 开始（包装层）
  ├─ 记录 started_at
  ├─ 注入 GovernanceCallback：
  │    on_llm_start   → (llm 计数先比较→中断/放行→递增；usage 捕获（若有）)
  │    on_tool_start  → (tool/search 计数先比较→中断/放行→递增)
  │    回合边界         → agent_step 先比较→中断/放行→递增
  └─ astream config 注入 recursion_limit = framework 安全上限（§7.5）

运行中
  ├─ counters 内存递增（锁内，control-path 权威）
  ├─ watchdog（wall-clock deadline）
  ├─ persistence 写观察事件（observation-path；失败 → durability_gap，不关闭 enforcement）
  └─ 任一 hard limit 触发 → 抛 GovernanceLimitExceeded/Timeout → 包装层收敛

收敛（terminal）
  ├─ governance 唯一写路径 → TaskRecord terminal + event task_status_change
  └─ F7 finalize 只在 completed/failed 的"正常收尾"路径执行（§19；治理 terminal 不伪造 claims）
```

## 9. Cancellation / Timeout Semantics（P0-2 统一措辞）

| 触发 | 语义 | TaskRecord 结果 |
|---|---|---|
| explicit cancel（用户） | `POST /api/task/{id}/cancel`：注入 cancel；等待收敛窗口（如 3s） | cancelled（收敛确认）/ 否则 governance 仍记 cancelled + underlying_linger 标注 |
| wall-clock deadline 到达 | watchdog 判定 governance deadline 到期 | timed_out（随后执行 timeout convergence） |
| budget exceeded | 任一 counter 先比较命中 | budget_exceeded（当前 call 不被发起） |
| same-thread replacement | 新 submit 同 thread | 旧 task 收敛 → superseded |
| backend shutdown/restart | lifespan 关闭 / 进程消失 | aborted（startup reconciliation） |

**wall-clock 精确语义（P0-2，禁止"elapsed N 秒后进程一定停止"表述）**：

```text
- wall_clock_timeout 是 governance deadline（不是进程级硬杀承诺）；
- deadline 到达 = governance 决定终止：runtime 一旦重新获得控制（当前 await 点返回），
  必须执行 timeout convergence（cancel 底层 + TaskRecord → timed_out）；
- underlying synchronous/blocking execution 可能 linger（§6.3）；
- TaskRecord terminal 不等价于 OS/process-level termination（§6.3/§16）。
```

## 10. Orphan / Restart Governance（P0-3/P0-4 收紧）

### 10.1 orphan 定义（rev2 锁死）

```text
前提假设：single runtime instance（§5.3）。

execution owner / active execution 定义（rev2）：
  - 每个 running TaskRecord 在本进程内对应唯一的 governance 执行句柄
    （in-process handle：包装的 asyncio task + controller）；
  - "active execution" = governance controller 的 in-process registry 中仍持有该 handle
    且底层 asyncio task 尚未 done；
  - WS observer 不是 execution owner：观察者断开/重连不影响 execution ownership；
    WS disconnect 本身绝不构成 orphan（P0-3）。

orphan = TaskRecord.status = running，且出现"治理台账与真实执行者脱节"：
  1. session 遗留：产品层新建 Session/thread，旧 thread 任务仍 running，且该任务不再被
     任何后续 submit/cancel/session_close 指向（supersede 未覆盖）；
  2. in-process 脱节：TaskRecord running 但 governance in-process registry 已无对应
     active handle（异常路径丢失引用——正常不应发生；若发生即治理缺陷，回收并记录）；
  3. policy 超期（可选开启）：超过允许"无任何交互仍存活"上限（budget 兜底之外的显式规则）。

WS disconnect 单独出现 ≠ orphan（观察与执行解耦）；其治理由 budget 兜底。
```

### 10.2 检测与回收（deterministic sweeper；幂等）

```text
Sweeper（周期，如 15s；或事件驱动）：
  a) 同 thread 被更新 submit 覆盖 → 旧 running 收敛 superseded（submit 路径已处理则跳过）；
  b) thread/session 显式关闭（session_close API）→ 该 thread 全部 running task →
     cancel + orphan_reclaimed（或 superseded 视触发点）；
  c) in-process registry 脱节检测 → orphan_reclaimed + 治理缺陷事件；
  d) policy 超期（可选）→ cancel + orphan_reclaimed；
  e) 进程启动 reconciliation（startup sweep，仅 single-instance 假设下安全）：
     - 本实例在途 running → aborted（checkpoint/research 只读不写，F8 不自动 resume）。
```

- 回收后状态：orphan_reclaimed / superseded / aborted（按触发点）；
- restart 与 orphan 分离：restart = aborted（startup reconciliation），不是 orphan_reclaimed；
- **多实例警告（P0-4）**：以上 startup sweep 只在 single runtime instance 下正确；
  多实例共享 PG 不在 F8 支持范围；若未来支持多实例必须引入 owner/lease/heartbeat，
  且 startup sweep 不得误杀其它实例 Task（OQ4）。

## 11. Runtime Telemetry

- **Task 级**：duration、agent_steps、llm_calls、tool_calls、search_calls、budget consumption、underlying_linger；
- **Event 级**：每次 llm/tool 事件带 `latency_ms`、`llm_usage?`（provider 暴露时）、tool 名；
- **Provider usage**：qwen/openai-compat 暴露 usage_metadata → 记录；否则 `usage_available=false`，不估算；
- **观察路径故障**：telemetry/event 写失败 → fail-open + durability_gap / degraded telemetry 标记（§16 (b)）；
  该故障**不影响** control-path 的 budget/counter 决策（§7.2）；
- 目的：让未来任何超长/超预算事故可被持久化数据证明构成（本仓当前无法证明 40 万 token 构成，不推断）。

## 12. Event History Model

### 12.1 事件信封（R3 monitor envelope additive；不改既有 payload schema）

```text
event_id      uuid4 hex（幂等主键）
task_id       FK TaskRecord
thread_id     净化 thread_id
run_id        （可空）
seq           任务内单调递增（governance sequencer 在持久化写入路径分配；task-scoped 全序）
event_type    现有 monitor 类型 + governance 类型（task_created/task_status_change/
              task_budget_exceeded/task_timeout/governance_control_failure/…）
payload       JSON（原 monitor data 字段 + telemetry 附加；不含 secret）
created_at    ISO
durable       bool（true=已持久化并分配 seq；false=仅 live 预览，未持久化）
```

### 12.2 产生 / 持久化架构（P1-7 锁死：谁产生、谁分配 seq、并发、live 关系）

```text
单一 canonical 生产者：governance sequencer/publisher（唯一写路径，进程内锁串行化）。
  来源事件 = monitor 既有 report_* 的调用点 或 governance callback——统一收敛为
  governance 内部 canonical event（不复制 monitor 私有 _emit 逻辑），经 adapter 生成。

seq 分配：governance sequencer 在"持久化成功"后分配 (task_id, seq)（锁内递增，单实例）。
  并发 callback：所有事件先进入 sequencer 的串行化入口（asyncio 单写者 + 锁），
  保证 (task_id, seq) 全序；不依赖 callback 并发到达顺序。

monitor live queue 与 durable event history 的关系：
  - durable 成功 → 同一 event 进入 live queue（WS 推送），且事件带 durable=true + seq；
  - durable 失败 → 事件仍可进入 live queue 但标 durable=false（预览），并记录 durability_gap；
  - 因此 live 是"尽力投递的观测副本"，**source of truth = durable event history**。

persistence failure 时 live 仍发送：是（observation fail-open），但 live 事件无 seq 承诺；
  replay 不承诺包含 durable=false 的事件（此类事件断线即失，属可接受观测缺口并标记）。

replay 无 durable/live gap 的保证：
  - 客户端以持久层为准：重连 with since_seq → 服务端先回放 durable events（seq>since_seq），
    再切换 live；
  - 回放期间新 durable events 可能已在 live 出现 → 客户端按 seq 幂等去重，发现 seq 空洞时
    发起 catch-up 查询（§13）；服务端保证 durable 写入先于对应 live 入队（同写者内顺序），
    使 replay+live 边界可对齐。
```

## 13. Replay / Reconnect Protocol

- `GET /api/threads/{thread_id}/events?since_seq=&limit=` → durable events（seq 升序）；
- WS 握手 `since_seq`：重连先回放持久层（分页）再转 live；
- 客户端按 (event_id/seq) 幂等消费；发现 seq 空洞 → catch-up GET；
- 无 since_seq 全新观察者 → 最近 N 条（policy）后进入 live；
- durable=false 的 live 预览事件不计入 replay 承诺（§12.2）。

## 14. Persistence Model

- **新表族（governance 自管，与 checkpoint/research 互不触碰）**：
  - `governance_tasks`（TaskRecord）
  - `governance_events`（PK event_id；UNIQUE(task_id, seq)；索引 thread_id/seq）
  - 迁移：双后端轻量 runner（governance 自管版本表）；sqlite 缺省 / postgres 候选（OQ1）；
- **唯一写路径**：governance 模块函数（事务内 task 转移 + 对应 event 同批/顺序落库）；
- **与 checkpoint**：官方 saver 自管，governance 只读（aborted 诊断）；
- **与 research**：不动 research 表族；run_id 关联只读；
- **单实例约束**：governance/checkpoint PG 不允许多实例共享（§5.3）。

## 15. Task / Thread / Run / Checkpoint Boundary（四态边界表）

| 状态 | 归属 | 持久化 | 写路径 | F8 角色 |
|---|---|---|---|---|
| Agent Execution State | checkpoint | ✅ 官方 saver | deepagents/langgraph | 只读（诊断/aborted 收敛） |
| Task Lifecycle State | TaskRecord | ✅ governance_tasks | governance 唯一写 | 新增 |
| Event History | events | ✅ governance_events | governance 唯一写 | 新增 |
| Research Artifacts | research_* | ✅ research store | app/research | 只读（run_id 关联；不改） |

- **LangGraph checkpoint 不是 Event Store**；
- monitor 事件信封（R3）保持 additive；governance 持久层复用 event_type + 新治理类型，不改 WS payload 字段语义。

## 16. Failure Semantics（P0-1/P1-11 汇总）

- **(a) Agent execution failure** → `failed`（error.kind=agent_failure）；
- **(b) Observation/persistence failure** → fail-open + durability_gap/degraded telemetry；**不改 Task 状态**；
- **(c) Control-path failure**：
  - c1 submit 时 governance store 不可用 → **拒绝受理（fail-closed）**；
  - c2 运行中 controller 异常 → cancel 底层 + TaskRecord=`failed`（governance_control_failure）——
    **绝不无状态无限运行**；
  - c3 startup governance store 不可用 → fail-fast 拒绝启动；
- **(d) framework recursion safety** → `failed`（framework_recursion_safety），与 budget_exceeded 分离；
- cancel/timeout/budget 触发后底层 linger → 已收敛 TaskRecord + linger 标注；
- restart 中途 → aborted（startup reconciliation，single-instance）。

## 17. Idempotency

- TaskRecord：task_id 幂等创建；terminal 至多一次（版本/状态守卫）；
- Event：event_id 幂等；`(task_id, seq)` 唯一；重放/重发不重复；
- cancel/replace：同 thread 连续 submit → superseded 至多一次；
- sweeper：回收幂等（已 terminal 跳过）；
- 不引入 task_call_id。

## 18. API Surface（设计）

```text
POST   /api/task                     # 现有语义 + 返回 task_id；admission 拒绝时明确报错
POST   /api/task/{task_id}/cancel    # 显式 cancel（返回治理收敛状态）
GET    /api/tasks?thread_id=         # task 历史/状态
GET    /api/tasks/{task_id}          # 单任务（counters/policy/terminal_reason）
GET    /api/threads/{thread_id}/events?since_seq=&limit=   # 事件 replay（durable）
POST   /api/threads/{thread_id}/close                        # 显式 session 关闭（回收在途任务）
```

- WS 握手：`?since_seq=` 可选。

## 19. F7 Integration Boundary（P1-10 措辞修订）

```text
F8 收敛承诺（rev2 措辞，不写"保证任何 Task 最终到 terminal"）：

"对于 governance runtime 能够继续运行并重新获得控制的 Task，TaskRecord 必须收敛到
 明确 terminal state；process crash / power loss 等不可控故障通过后续 startup
 reconciliation 收敛为 aborted。"

F7 集成：
  - F7 仍 terminal-only（astream 正常结束 → finalize_run → F2–F6）；
  - completed / failed(agent 正常异常路径) 才可能走 F7 收尾语义；
  - cancelled / timed_out / budget_exceeded 等治理 terminal：final_content 不可得时
    **不伪造 claims**（不物化）；已产出的 research 数据由查询面只读（F8 不自动 finalize）；
  - F8 不引入 incremental Research State。
```

## 20. Security / Resource Abuse Considerations

- **Admission control only（P1-9）**：per-user/per-thread/global active task 上限；
  超限 → 拒绝新任务（返回明确错误），**不做 durable queue / priority / worker allocation**；
- budget 默认服务端强制，客户端覆盖受服务端 cap 钳制；
- 查询权限：thread_id 净化（P004）+ 会话归属校验；event payload 不含 secret；
- governance 表无对外写接口（唯一写路径）；
- **单实例**：governance DB/checkpoint PG 多实例共享不在支持范围（§5.3）。

## 21. Test Strategy

- `tests/test_runtime_governance.py`（sqlite 全量）：
  - 状态机全转移 + terminal 幂等（含无 queued）；
  - counter 语义：先比较后执行（max=120 绝不发生第 121 次）、search⊆tool、step 回合计数、
    main/subagent 都计、recursion_limit 与 agent_step 分离（framework_recursion → failed ≠ budget_exceeded）；
  - wall-clock deadline convergence + underlying_linger 标注（不承诺秒级停止）；
  - control/observation 分离：governance DB 写失败 → 事件 durability_gap 但 budget 仍生效；
    c1 拒绝受理 / c2 收敛 failed / c3 fail-fast 三路径；
  - cancel/replace/supersede、orphan sweeper（WS 断线不构成 orphan）、session_close、restart→aborted；
  - event sequencer 并发串行化、(task_id,seq) 全序、幂等、replay catch-up；
- `tests/test_runtime_governance_postgres.py`（PG 镜像，门控按 OQ1）；
- 集成（可运行）：真实 run_deep_agent 小任务 + GovernanceCallback 计数/telemetry；WS since_seq replay；
- F1–F7 全仓回归；ruff/compileall。

## 22. Acceptance Criteria

| AC | 内容 |
|---|---|
| AC1 | Given submit，When 任务创建，Then 返回 task_id 且 TaskRecord=running（policy_snapshot/effective_limits 落库；**无 queued**） |
| AC2 | Given 同 thread 再 submit，When 执行，Then 旧 running → superseded 且新任务独立（不误 cancel 其它 thread） |
| AC3 | Given wall_clock_timeout=10s 且 Agent 持续运行，When deadline 到达，Then governance 重新获得控制后收敛 timed_out（event 落库）；Given 阻塞工具 linger，Then underlying_linger=true（不承诺秒级终止） |
| AC4 | Given 任一 counter 超限，When 执行，Then 当前 call 不被发起（max_llm_calls=120 不发生第 121 次）且 TaskRecord=budget_exceeded + counter 快照 + event |
| AC5 | Given 显式 cancel，When 底层协程收敛窗口内退出，Then cancelled；阻塞时 Then cancelled + linger 标注 |
| AC6 | Given WS 断线但任务正常运行，When sweeper，Then **不**构成 orphan（任务继续，仅 budget 兜底）；Given session_close/换会话遗留（策略判定），Then 收敛 orphan_reclaimed/superseded 且幂等 |
| AC7 | Given backend 重启（模拟，单实例），When startup reconciliation，Then 在途 running → aborted；checkpoint/research 不被改写 |
| AC8 | Given provider 暴露 usage_metadata，When LLM 完成，Then input/output/total_tokens 落 telemetry；不暴露 → usage_available=false 且无伪造 token |
| AC9 | Given 并发 callback，When append，Then sequencer 串行化、(task_id,seq) 全序唯一、event_id 幂等 |
| AC10 | Given 断线重连 with since_seq，When replay，Then durable events 按序回放并转 live（客户端按 seq 去重；seq 空洞触发 catch-up）；durable=false 预览不承诺 |
| AC11 | Given governance DB 写失败（observation），When Agent 运行，Then 不阻断执行且 budget 仍生效（durability_gap 标记）；Given control-path c1/c2/c3，Then 分别拒绝受理/收敛 failed/启动失败（fail-closed，绝非"无限运行无状态"） |
| AC12 | Given F7 正常收尾任务，When completed，Then F7 finalize 保持触发；Given 治理 terminal（cancel/timeout/budget），Then 不伪造 claims 物化 |
| AC13 | Given 单实例部署，When 运行，Then 无跨实例误杀（多实例共享 PG 明确不支持） |
| AC14 | Given 全仓，When 回归，Then F1–F7 全绿 + F8 新增绿 + ruff/compileall 绿；scope audit 无越界 |

## 23. Open Questions（rev2 关闭/保留）

1. governance 持久化后端：复用 checkpoint DSN 独立表 vs 独立 governance DSN？→ 倾向 checkpoint 同库（单实例、单 infra）；Gate 裁决。
2. ~~queued 是否保留~~ → **关闭（P1-8）：移除**。
3. session 关闭 API 本轮是否暴露？→ 建议暴露（给"新建会话"显式回收语义）；前端接入可选。
4. 多实例支持（owner/lease/heartbeat、共享 PG 互斥）→ **明确不在 F8 v1**；保留为未来 Spec 输入（§5.3/§10.2）。
5. budget 默认值是否按会话可配 → 默认表 + per-submit 覆盖（cap 钳制）；默认值 Gate 裁决。
6. framework recursion 安全上限具体值（默认 5000）与 watchdog 周期（1s）→ 实现期参数化（deterministic，不属架构语义）。

## 24. Self Review（adversarial，rev2）

针对 Gate 要求逐项攻击内部矛盾：

1. **hard budget vs fail-open** → 已分离 control/observation（§7.2）：enforcement 内存权威、持久化失败不禁用；c1/c2/c3 定义 governance failure policy（§7.3/§16）。✅
2. **wall-clock hard limit vs blocking tool** → §9/§6.3：deadline=governance deadline；重新获得控制后收敛；linger 标注；不承诺秒级终止（全 Spec 措辞统一）。✅
3. **WS disconnect vs orphan** → §10.1：observer≠owner；WS 断线不构成 orphan；owner=in-process handle。✅
4. **single-instance restart sweep vs multi-instance PG** → §5.3/§10.2/AC13：单实例假设写死；多实例明确不支持（不设计会误杀其它实例的 sweep）。✅
5. **agent step vs recursion_limit** → §7.5：两者职责分离；不采用未经验证乘数；framework_recursion → failed ≠ budget_exceeded。✅
6. **event ordering vs concurrent callbacks** → §12.2：sequencer 单写者串行化；seq 持久化路径分配。✅
7. **terminal TaskRecord vs underlying running** → §6.3/§9：收敛声明 ≠ 进程终止；linger 标注。✅
8. **governance failure vs task failed** → §7.3/§16：(a)(b)(c)(d) 分类；(b) 不改状态；c2 收敛 failed（governance_control_failure）。✅
9. **F7 terminal-only vs governance terminal** → §19：措辞改为"可控前提下收敛 + crash 由 startup reconciliation 收敛 aborted"；治理 terminal 不伪造 claims。✅
10. queued 状态移除 → 无 scheduler 语义（§6.1/P1-8）。✅
11. token 是否伪造硬限？否（telemetry-only）。✅
12. 是否演化成 Job Scheduler / 引入新 infra / 破坏 F1–F7 / checkpoint / monitor？否（§4/§12/§15/§20）。✅
13. 40 万 token 是否被断言？否（仅未来可证明性）。✅

## 25. Scope Audit

rev1 → rev2 本轮仅修改 `docs/spec/2026-09-14-f8-runtime-execution-governance.md`（Spec 内容修订）；
未创建/修改代码、migration、tests、dependencies、Agent、Tool、Checkpoint、monitor、frontend、infrastructure、F1–F7。

## R. rev1 → rev2 修订记录

| # | 修订 | 落点 |
|---|---|---|
| P0-1 | control-path vs observation-path 分离；hard budget 不被持久化故障静默关闭；governance failure policy（c1/c2/c3） | §7.2/§7.3/§16 |
| P0-2 | wall-clock = governance deadline + convergence + linger；全 Spec 措辞统一 | §6.3/§9/§3 |
| P0-3 | orphan 定义收紧：observer≠owner；WS 断线不构成 orphan；owner=in-process handle | §10.1 |
| P0-4 | single runtime instance 假设写死；多实例共享 PG 明确不支持；startup sweep 边界 | §5.3/§10.2/§23 OQ4 |
| P0-5 | agent_step 正式定义（回合、计数事件、main/subagent、tool 不单算）；与 recursion_limit 职责分离 | §7.5/§8 |
| P1-6 | 每个 counter 的 unit/timing/comparison/超限行为锁死（先比较后执行，max=120 无第 121 次） | §7.4 |
| P1-7 | Event 产生/持久化架构：sequencer 单写者、seq 分配、并发串行化、live/durable 关系、replay gap 保证 | §12.2/§13 |
| P1-8 | 移除 queued（非 scheduler） | §6.1/§6.2/§7 |
| P1-9 | 并发上限 = admission control only；禁止调度/队列/优先级/worker | §4/§20 |
| P1-10 | F7 terminal 措辞修订（可控收敛 + crash → startup reconciliation aborted） | §19/§3 |
| P1-11 | failed 语义分类（agent/observation/control/recursion） | §7.3/§16 |

**STOP**：F8 Spec rev2 + Self Review 完成，等待 **F8 Final Gate Review**；Gate 通过前不实现、不写 implementation plan。
