# F8 Step 3 Spec — Runtime Budget / Step / Deadline / Cancellation Control Plane

> 状态：**Draft（Spec ONLY）** —— 待 Spec Review Gate；Gate 前禁止 Implementation。
> 上游契约（只读，不改）：
> - master：`docs/spec/2026-09-14-f8-runtime-execution-governance.md` rev2（FROZEN；简称 M-Spec）
> - Plan：`docs/plan/2026-09-14-f8-implementation-plan.md` Rev2（PASS / READY；简称 M-Plan）
> - 冻结实现：Step 0 probe 证据（`.deepagents-fc/probes/f8_step0_evidence_{a,b,c,d}.json`）、
>   Step 1 governance schema/migration、Step 2 lifecycle/terminal funnel（PASS / FROZEN，PG Gate 通过）
> - 本轮 Pre-Spec 证据（scratch，不入库）：
>   `.deepagents-fc/probes/f8_step3_evidence_probeA.json`（Summarization vs Decision，PROVEN）
>   `.deepagents-fc/probes/f8_step3_evidence_probeB.json`（Governance callback abort propagation，PROVEN）
> - 已批准边界：R1 = main_agent.py 最小 additive 治理接线（S1）；R4 = controller.py 的 Step 3
>   additive 演进（**不得重设计 Step 2 Terminal Funnel**：memory authority → optimistic CAS →
>   exactly-one durable winner → loser adopts terminal 原样保留）。

## 1. 定位（非目标澄清）

F8 Step 3 **不是**"统计 Agent 用了多少资源"，而是：

> 建立 Agent Runtime 的 **Hard Execution Budget / Step / Deadline / Cancellation Control Plane**，
> 并把所有治理终止信号**统一收敛到 Step 2 已冻结的 Terminal Funnel**（唯一生命周期裁决者）。

```text
Agent Execution
  ├── LLM / Tool / Search / Subagent / Decision Round
        ↓
Governance Control: Budget / Deadline / Cancellation / Recursion Safety
        ↓
Step 2 Terminal Funnel（唯一终态写入口，语义冻结）
```

Observation（usage/telemetry/events）与 Control Plane **解耦**（M-Spec §7.2）。

## 2. 冻结基线与本 Spec 引用

- 默认 hard limits 与比较语义沿用 M-Spec §7.4（不重复定义新表）：`wall_clock_timeout=600s`、
  `max_agent_steps=200`、`max_llm_calls=120`、`max_tool_calls=300`、`max_search_calls=40`；
  全部 **先比较后执行**：`count < limit` 才放行，放行即递增；`count >= limit` 时当前 call
  **不允许发起**。`max_llm_calls=120` 严格 = 最多实际发生 120 次 provider call，第 121 次不发生。
- 计数语义：`search_calls ⊆ tool_calls`；`agent_steps ≠ llm_calls`（M-Spec §7.5）。
- 收敛映射：budget → `budget_exceeded`；deadline → `timed_out`；显式 cancel → `cancelled`；
  `GraphRecursionError` → `failed(error_kind=framework_recursion_safety)`（≠ budget_exceeded）；
  其余 agent 异常 → `failed(agent_failure)`；controller 自身故障 → c2 `failed(governance_control_failure)`
  （M-Spec §7.3/§16）。

## 3. Probe A 锁定：Summarization 判别规则（不可猜测降级）

Probe A（PROVEN，证据文件）在 deepagents 0.5.7 / langchain 1.2.17 / langgraph 1.1.10 上证明：
**summarizer 与 decision LLM 共享同一 parent chain（wrap 形态）**，frame/顺序不足以区分。
锁定分类：

```text
llm_start / llm_end
  ├── kwargs.metadata.lc_source == "summarization"
  │       → Summarization LLM（非业务）
  │       → 计入 llm_calls
  │       → 不计入 agent_step
  └── metadata 无 lc_source，且处于 agent model-node execution
          → decision LLM（main 或 subagent 的决策回合）
          → 计入 llm_calls
          → 计入 agent_step
```

- **禁止**以 "no-tool response" 作为 decision round 的唯一判据（summarizer 的 llm_end 也可能
  no-tool + content，会被误判）。
- `lc_source=="summarization"` 是当前锁定的 **primary discriminator**。
- prompt-head 前缀（summarizer 模板头 vs `System: …` 对话头）仅作 **fallback diagnostic**：
  fallback 不得静默改变语义 —— 当某 llm_start 缺失可判定依据（无法确认是否 summarizer）时，
  产生**可观测 warning**，且**不得**把该调用计为 agent_step（fail-safe 到"仅 llm_calls"）；
  连续出现 → 提升为 control diagnostic（任务级 degraded 标记，不伪造终态）。
- 当前框架栈中 LLM 仅两类（decision / summarization）。若未来框架引入新的非业务 LLM 类别，
  必须先重跑分类 probe（镜像 Step 0 规则），**禁止凭猜测**扩展分类。
- `llm_calls` 与 `agent_steps` 两个计数器永远独立（不同源、不同事件、不同递增点）。

## 4. Probe B 锁定：Governance abort propagation

Probe B（PROVEN）在真实 deepagents 图（main + task tool + subagent）验证：

```text
GovernanceLimitExceeded(GraphBubbleUp)
  ← callback raise（handler raise_error=True）
    on_llm_start / on_tool_start
  → 穿透 LangGraph model node / ToolNode(except GraphBubbleUp: raise)
     / SubAgentMiddleware → astream **异常终止（normal_end=false）**
```

覆盖三类执行点，全部在 provider / tool body 实际执行**之前**中止（证据：provider_calls 停在
限额值、tool body 不执行 `tool_ran==0`、异常类型原样上抛、未被 ToolNode 转成 error ToolMessage、
未被 SubAgentMiddleware 吞掉）：
- main LLM budget；
- main tool budget（`max_tool_calls` 命中 → tool body NEVER RUN）；
- subagent LLM budget（穿透子图 + task tool + ToolNode）。

## 5. 计数器语义（单一 canonical producer）

- 计数器唯一来源：`BudgetCounter`（controller 每 execution 一个实例；进程内；asyncio 锁内
  compare + increment；**零 DB 依赖**；不依赖 event persistence）。
- **禁止双计**：LLM/Tool/Search/Step 各自只有一个递增点，全部经唯一 `GovernanceCallbackHandler`
  （见 §6）；不叠加 ToolNode wrapper / middleware 计数。
- 并发安全：事件循环单线程 + 回调可能来自并发分支（同一决策回合多个并行 tool `Send`）；
  每次比较与递增在同一同步临界区（无 await 间隙），辅以 asyncio.Lock 防御 → **不允许 overshoot**。
- counter 快照（terminal 时写入 counters_snapshot，keys 与 M-Plan §2 一致）：
  `{agent_steps, llm_calls, tool_calls, search_calls, duration_ms}` + telemetry-only `tokens{…}`（见 §11）。

## 6. GovernanceCallbackHandler 行为契约

- 单一 handler（`raise_error=True`），经 run config `callbacks` 注入（§8 S1），对 main / task tool /
  subagent / summarizer 全部事件可见（Step 0 + Probe A/B 实证）。
- `on_llm_start`：
  1. llm_calls 先比较后执行（`count < limit` → 放行并递增；否则超限）；
  2. 按 §3 分类：非 summarizer（decision）→ agent_steps 先比较后执行；
  3. 超限任一 → 置 execution 终止意图并 `raise GovernanceLimitExceeded`（在 provider 发起前）。
- `on_tool_start`：tool_calls 先比较后执行；`name == "internet_search"` → search_calls 独立限额
  同样先比较后执行；超限 → 同上 raise（tool body 不执行）。
- `on_llm_end`：仅 observation —— 采集 usage_metadata（§11）；校验决策计数一致性（诊断）。
- handler 内部非治理异常策略（§10 failure matrix）：除 `GovernanceLimitExceeded` 外的任何异常
  不阻断执行 → log + degraded diagnostic；不伪造终态。
- watchdog / cancel 不是 callback 触发，由 controller 直接驱动 funnel（§7/§9）。

## 7. Controller 演进（additive；R4 边界内）

Step 2 `controller.py` 的冻结面（funnel/memory authority/CAS/race/pending/store API）**零改动**。
新增（additive）：
- `GovernanceRuntime`：一次 governed execution 的进程内载体 —— BudgetCounter + 判定上下文 +
  abort 意图（在 controller.execute 创建并持有）。
- `execute(task_id, coroutine, *, policy)`（**新增可选参数**；不传 policy 时行为与 Step 2 完全一致）：
  - 解析 effective limits（默认 M-Spec §7.4）；记录 started 单调时钟；
  - 建立 governance execution ContextVar（供 S1 接线读取，§8）；
  - 驱动 watchdog（§9）与 cancel 处理；观察底层执行退出；
  - 异常/返回收敛（映射见 §2/§10）；funnel 唯一入口不变。
- 收敛路径（关键：run_deep_agent 现状会**吞掉** astream 内异常 —— 见 §8）：
  - 底层抛 `GovernanceLimitExceeded` → funnel(`budget_exceeded`；快照写入)；
  - 底层抛 `GraphRecursionError` → funnel(`failed`, framework_recursion_safety)；
  - 其它异常 → funnel(`failed`, agent_failure)；
  - controller 内部异常 → 取消底层 + funnel(`failed`, governance_control_failure)（M-Spec c2）；
  - 正常返回 → funnel(`completed`)（F7 finalize 仅此路径，见 §12）；
  - CancelledError：不伪造 completed；先查是否已有 funnel 裁决（cancel/timed_out 已由
    controller 先裁决再 cancel），无裁决则原样上抛。
- watchdog / linger 观察：底层 sync tool 无法立刻停止 → linger 允许；terminal 后若 handle 在
  cancel 时未完成且稍后完成 → `underlying_linger_observed=true`（观察字段，不二次翻转状态）。

## 8. S1 接线契约（main_agent.py 最小 additive；R1 批准）

允许在 `app/agent/main_agent.py` 内做**仅限以下**的 additive 改动：

1. **governance execution context 注入**：读取 controller.execute 建立的 ContextVar（无 →
   本次 run 与今天逐字节同语义）。
2. **astream config 注入**：governance active 时把 GovernanceCallbackHandler 并入 config
   `callbacks`，并注入 `recursion_limit`（framework 安全上限，默认 5000；运行 config 优先于
   create_agent 的 with_config 9999 —— langchain 实证）。仅当 governance 非 active 才完全跳过。
3. **异常可见性**：现有 `except Exception` 分支内，在既有 report/状态处理后，若 governance
   context active → **re-raise**（现状是吞掉）。理由：若吞掉，controller 无法区分"预算/递归中止"
   与"正常完成"，control plane 失明；**仅当 governance active 才 re-raise**，非 active 行为不变。
   `except asyncio.CancelledError` 分支语义不改。

禁止借机：改 prompt / topology / tool signature / F1–F7 / research context / 把 governance
逻辑写入 agent business logic。任何超出上述 1–3 的改动视为 Scope 违例。

## 9. Deadline / Watchdog

- **单一 authoritative deadline**：execute 开始即算 `deadline_mono = start_mono + wall_clock_timeout`
  （单调时钟）；**禁止各组件各自 `now + timeout` 重算**。`created_at/started_at` 仅审计。
- watchdog：controller 内周期 tick（1s）：`mono_now >= deadline_mono` → `cancel` 请求 →
  funnel(`timed_out`) → 收敛后底层可能 linger（sync tool executor 线程）。
- **wall-clock timeout 是 governance deadline，不是 OS/process kill**；terminal TaskRecord
  不等价于底层线程已停止。

## 10. Control / Observation Failure Matrix（硬边界）

| # | 故障 | 路径 | 行为 |
|---|---|---|---|
| F1 | observation DB / event / telemetry 写失败 | Observation | fail-open + durability_gap / degraded 标记；**不改 Task 状态、不禁用 budget** |
| F2 | provider 无 usage_metadata | Observation | `usage_available=false`；**不造估计值** |
| F3 | handler 非治理异常（回调自身 bug） | Control | log + degraded diagnostic；不伪造终态；enforcement 尽力继续（异常在放行前不注入） |
| F4 | `GovernanceLimitExceeded` | Control | abort → funnel budget_exceeded（幂等） |
| F5 | watchdog 自身异常 | Control | 单 tick 失败 → log + 下 tick 重试；连续失败 → c2 收敛 failed(governance_control_failure) |
| F6 | governance store 写失败 | Control（记录侧） | Step 2 语义：内存裁决权威 + pending + degraded_durability；budget/超时照常触发 |
| F7 | controller 内部异常 | Control | c2：取消底层 + failed(governance_control_failure) |
| F8 | recursion 超限 | Control | GraphRecursionError → failed(framework_recursion_safety) |

红线：**Observation failure 不得让 Budget / Timeout / Cancellation 失效**（M-Spec §7.2 原文）。

## 11. Token Usage（Observation only）

- `AIMessage.usage_metadata`（provider 返回时由 langchain-openai 挂载）存在 → 采集
  input/output/total tokens 到 telemetry（AC8）；不存在 → `usage_available=false`。
- 禁止 tokenizer 估算 / 字符估算 / fake token / prompt-length 估算 / 把估算值当 hard budget。
- Step 3 不引入 token budget 的 hard limit（M-Spec §16(AC8) 只做 telemetry）。

## 12. F7 Boundary

- F7 finalize（`run_deep_agent` 正常结束分支）只在 **normal completion** 执行；
- governance 终止（budget_exceeded / timed_out / cancelled / failed / aborted）→ astream 非正常
  结束 → F7 finalization **NOT RUN**；**治理 terminal 不得制造 final answer / 不得伪造 claims**
  （M-Spec §19；AC12）。
- `run_deep_agent` 层接线验证属 Implementation/Integration Test，本 Spec 不假设已完成。

## 13. Race Semantics（funnel 不变）

覆盖组合（含既有 Step 2 已证组合）：`budget_exceeded×completed`、`budget_exceeded×cancelled`、
`budget_exceeded×timed_out`、`timed_out×completed`、`cancelled×completed`、
`superseded×cancelled`、`budget×timeout`（…）。仲裁仍由 Step 2 funnel：
memory authority → optimistic CAS → **exactly-one durable winner → loser adopts terminal**。
Step 3 不新增第二套终态机，不改变 winner 规则。

## 14. PostgreSQL Boundary（F8 全局规则）

- PostgreSQL 是唯一生产基线与最终 Gate Backend；SQLite 仅快速逻辑测试。
- Step 3 Gate 至少覆盖（真实 PG16）：budget/timed_out/cancelled 与 completed 的 terminal race
  （真并发双 controller CAS，复用 Step 2 gate 编排）；terminal persistence 恰一次 / version+1；
  restart→aborted 衔接；governance failure 路径的 TaskRecord 收敛；**control 不依赖 observation
  DB**（PG store 故障期间 budget 仍生效）。SQLite PASS 不替代上述证明。

## 15. Step 3 Test Matrix（规划）

- Counter（SQLite 快速逻辑）：compare-before-execute；120 不发生 121；search⊆tool；agent_step≠
  llm_calls 反偷换；concurrent callbacks 无 overshoot；double-count 防护（单一 producer）。
- Runtime（确定性 probe，scratch→按需转正）：main LLM budget / main tool budget / subagent LLM
  budget / subagent tool budget；summarizer 不计 agent_step；recursion 独立（framework_recursion
  → failed ≠ budget_exceeded）；watchdog timed_out；cancel；sync tool linger（不误报 terminated）。
- Race（SQLite + PG Gate）：§13 组合；funnel 幂等。
- Failure：F1–F8 矩阵条目。
- F7：governance abort 不触发 F7；normal completion 才 F7；无 duplicate terminal。
- PostgreSQL Gate：§14 清单。
- AC 关联：AC3/AC4/AC5/AC8/AC11/AC12/AC14（master 清单）。

## 16. Non-goals（Step 3 明确不做）

sequencer / replay / adapter / API / sweeper / monitor payload 变更 / research 增量 /
F3–F6 / tool-result truncation / prompt workaround / token hard budget / scheduler /
多实例 / Step 4+ 能力。除 §7/§8 列出的 additive 外，**不改任何已冻结实现**。

## 17. 一致性声明（vs 冻结文档）

- 默认 limit 与比较语义 = M-Spec §7.4；agent_step 定义 = M-Spec §7.5；funnel/race/持久化失败 =
  Step 2 冻结面；fault policy = M-Spec §7.3/§16；F7 = M-Spec §19；backend 规则 = F8 全局锁定。
- 本 Spec 不改写上述任何条款；S1 属对 M-Plan "main_agent.py 不改" 的**最小边界澄清**（R1 批准），
  非重设计 Plan。

## 18. Self-review（adversarial；对应攻击面结论）

1. callback double counting → 单一 handler/单一 producer；无 wrapper/middleware 并行计数 ✅
2. main/subagent ownership 错误 → Step 0 + Probe A/B 事件树归属；subagent LLM 与 main LLM
   同规则、同计数器 ✅（集成 probe 再验）
3. summarizer 误计 agent_step → §3 metadata 判别 + fallback fail-safe ✅
4. framework/internal LLM 误计 → 当前栈仅 summarization 一类非业务 LLM；未来新类别先 probe ✅
5. tool budget overshoot → 先比较后放行、tool body NEVER RUN（Probe B）✅
6. concurrent callback overshoot → 同一同步临界区 + asyncio.Lock ✅
7. GraphBubbleUp 被 ToolNode 吞掉 → Probe B 实证穿透 ✅
8. budget 与 recursion 混淆 → 独立语义、独立默认值、GraphRecursionError 映射分离 ✅
9. timeout/cancel race → funnel 唯一裁决（Step 2 冻结）✅
10. terminal funnel duplicate → funnel 幂等（Step 2 冻结）✅
11. observation failure 关闭 control → F1/F6 矩阵：fail-open 不影响 enforcement ✅
12. sync tool linger 误报 terminated → linger 观察字段、不二次翻转 ✅
13. governance terminal 错误触发 F7 → F7 仅 normal completion 分支；governance abort normal_end=false
    （Probe B）✅
14. token estimation 偷渡 hard budget → §11 禁止 ✅
15. S1 接线扩大为业务修改 → §8 三条款白名单 + 非 active 不变式；integration diff review 门禁 ✅

**关键发现（已入 Spec）**：`run_deep_agent` 现状 `except Exception` 会吞掉 astream 内异常 → 若
不加 §8.3 的 governance-active re-raise，controller 无法区分预算/递归中止与正常完成（会把治理
终止误收为 completed）。该点已写入 S1 契约并设 Integration Test，属 additive 最小变更。

## 19. Open Questions（Step 3 Spec 保留，不阻塞）

1. provider（Qwen openai-compat）真实 usage_metadata 可用性 → Implementation 首个 runtime probe
   确认（仅 observation，不阻塞）。
2. watchdog tick 间隔与 linger 观察窗口的精确值 → Implementation 常量 + 测试校准（不涉语义）。
