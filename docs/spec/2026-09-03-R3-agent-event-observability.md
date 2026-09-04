# Spec: 2026-09-03-R3-agent-event-observability（ROADMAP R3：Agent Event / Observability）

> **编号规则**：Pxxx = PROBLEM.md Problem Registry；Rxx = 工程化 Roadmap ID（本任务 = **R3**）。
> **状态**：**已批准（APPROVED）→ 已实施并验证（IMPLEMENTED）**。批准裁决与实施记录见 §16。
> **范围冲突声明**：docs/ROADMAP.md §2/§4 曾把 **R3 = Agent Task Runtime**、
> R4 = Event Persistence、R6 = Observability(OTel)。本次用户指令将 R3 定义为
> **Agent Event / Observability（事件模型 + 关联 + 排序 + 异步安全投递，不做持久化/OTel）**，
> 与 ROADMAP 旧 R3 槽位内容相撞。Roadmap 裁决见 §16.1（用户已批准；ROADMAP.md 文档同步
> 后续单独处理）。本 Spec 所有"真实探测"结论均有环境实测依据（langgraph 1.1.10 + langchain-core 1.3.3 锁定环境，
> 与本机 1.2.7/1.4.8 环境双验证），非假设。

---

## 1. Problem

当前 Agent 执行过程的可观测性只是**为前端展示服务的裸推送**，还不是可验证的
Agent Runtime Event Stream：

1. **事件无身份**：WS payload 只有 `type/event/message/data/timestamp`，**没有 event_id、
   没有 thread_id 字段**（thread_id 只作为 manager 的路由 key，事件体本身无法被独立关联）；
2. **无 run 边界**：同一 thread_id 会多次执行任务（/api/task 同 thread 新任务替换旧任务，
   R2 之后还有 checkpoint 续跑），事件无法区分"哪一次运行"；
3. **生命周期不完整**：有 `session_created / tool_start / assistant_call` 但**没有 task 开始事件**；
   终态不可靠——`task_result` 由"model 节点出现无 tool_calls 的文本"启发式触发，可能多次触发、
   也可能模型收尾无文本时**永不触发**（前端 isRunning 卡死）；
4. **工具/助手只报开始不报完成/失败**：`tool_start` 在工具体内自报；**没有 tool.completed /
   tool.failed / assistant.completed**；
5. **错误事件走私有通道**：main_agent.py 直调 `monitor._emit("error", ...)`（ARCHITECTURE §4
   明示的既有债务），且与 task 终态没有统一的一次性语义；
6. **顺序无契约**：事件经 `create_task`/`run_coroutine_threadsafe` 尽力投递，**没有 seq、
   没有排序保证的明确定义**，无法测试"tool_start 先于其后续事件"；
7. **没有测试**：tests/ 中**零** monitor / event / WS 覆盖（grep 证实）。

一句话问题陈述：Agent 执行过程目前只有"面向前端图标的推送"，缺少
**带身份、可关联、有序、跨 async/sync 边界安全**的运行时事件模型，
也无法用测试证明事件流正确性。

## 2. Goal

把当前 monitor 能力收敛为明确的 **Agent Runtime Event Model**，聚焦四件事：

1. **Event Schema**：统一信封（envelope），稳定字段 + 新增关联字段；
2. **Correlation**：thread_id + run_id + event_id 显式落进每个事件；
3. **Ordering**：定义可测试的排序语义（seq + 投递保证）；
4. **Async-safe Delivery**：证明 sync 工具（executor 线程）产生的事件能安全回到
   对应 thread_id 的事件流（含真实环境证据与回归测试）。

措辞纪律：R3 只声称"**Agent Runtime 事件模型 + 关联 + 排序 + 异步安全投递**"；
不声称"事件持久化/历史查询"（ROADMAP R4，Non-Goal）、不声称
"分布式 tracing / OpenTelemetry"（ROADMAP R6，Non-Goal）、不声称"日志系统"。

## 3. Current Architecture（源码核对 + 实测）

### 3.1 事件产生与投递链（现状）

```text
run_deep_agent(query, session_id)                      app/agent/main_agent.py
 ├─ set_session_context / set_thread_context           app/api/context.py（ContextVar）
 ├─ monitor.report_session_dir(session_dir)            事件: session_created
 ├─ main_agent.astream({...}, config={thread_id})      deepagents 0.5.7 → langchain create_agent
 │    └─ 主图 chunk 循环：仅处理 node_name=="model" 的 chunk
 │         ├─ last_msg.tool_calls 含 name=="task" → monitor.report_assistant(助手名, {description})
 │         └─ 无 tool_calls 且有 content → monitor.report_task_result(content)   ← 启发式
 ├─ asyncio.CancelledError → monitor.report_task_cancelled()
 └─ Exception → monitor._emit("error", ...)             ← 私有通道直调（既有债务）
        ↑
子智能体（subagents/*，字典式）→ 子图内工具（tavily/db/ragflow/read/generate/convert）
   每个同步 @tool 体内首行 monitor.report_tool(tool_name, args)    ← 工具自报 tool_start
        ↑
monitor._emit(event_type, message, data)               app/api/monitor.py
 ├─ payload = {type:"monitor_event", event, message, data, timestamp(naive ISO)}
 ├─ thread_id = get_thread_context()（ContextVar，emit 时读取）
 ├─ 同 loop → manager_loop.create_task(send)；
 │  跨线程 → asyncio.run_coroutine_threadsafe(send, manager_loop)
 └─ console print 保底 + builtins.runtime.stream_writer（脚本调试）
        ↓
ConnectionManager.send_to_thread(payload, thread_id)   app/api/monitor.py
 └─ active_connections[thread_id]（last-connect-wins）→ websocket.send_json
        ↓
前端 useDeepAgentSession.ts / EventStream.tsx          仅消费: session_created / tool_start /
   assistant_call / task_result / task_cancelled / error（未知 event 忽略、照常渲染）
```

### 3.2 事实清单（含行号/实测）

| # | 事实 | 证据 |
|---|---|---|
| F1 | payload 无 event_id/thread_id/run_id/seq | monitor.py:51-57 |
| F2 | thread_id 只作路由 key | monitor.py:61-65、178-182、context.py |
| F3 | 同 thread 新任务替换旧任务（cancel 旧） | server.py:118-124 |
| F4 | 事件类型全集 = 6 个：session_created / tool_start / assistant_call / task_result / task_cancelled / error | monitor.py report_*；main_agent.py:129-149 |
| F5 | task_result 由 chunk 启发式触发，无"一次性/终态"保证 | main_agent.py:136-141 |
| F6 | 无 tool 完成/失败事件；工具异常大多吞成返回字符串 | db/ragflow/tavily 工具 try→return 中文提示 |
| F7 | error 走私有 _emit | main_agent.py:149（ARCHITECTURE §4 债务） |
| F8 | sync 工具在 async 图内于 **executor 工作线程**执行，但 langchain 在提交时**复制调用方 context** → 工具体内 get_thread_context() 可见（Python 3.12/3.13，langchain-core 1.4.8 与锁定 1.3.3 实测一致） | R3 探测：`[TOOL-BODY] thread=asyncio_0 cv='MAIN-CONTEXT'`（两环境同输出）；ToolNode._execute_tool_async → tool.ainvoke |
| F9 | 事件投递：同 loop create_task；跨线程 run_coroutine_threadsafe；Future 不 await（fire-and-forget） | monitor.py:79-100 |
| F10 | 无缓冲/重放：WS 未连/断线期间事件直接丢失；重启丢失（内存态） | monitor.py:59-65、server.py WS 循环 |
| F11 | 前端契约：MonitorMessage{type,event,message,data,timestamp}；未知 event 忽略但会进列表渲染 | frontend/src/types.ts、useDeepAgentSession.ts:112-142、EventStream.tsx |
| F12 | 现有测试零 monitor/WS 覆盖 | grep tests/ |
| F13 | 事件顺序跨来源（工具线程 vs 主循环）无形式化保证，但因果上 tool_start 在工具返回前发出 | monitor.py:92-100 分析 |

### 3.3 对本 Spec 的结论性判断（Inspection 回答）

1. 事件产生 = monitor.report_*（工具自报 tool_start + main_agent chunk 启发式 + 私有 error）；
2. 数据结构 = 上面 envelope（6 个事件共用，data 按事件承载字段）；
3. tool call/result 无独立事件：tool_start 有；tool result/失败不可观察；assistant 只报 call；
   error 只有 run 级一个；task 生命周期缺 started、终态不可靠；
4. thread_id 关联 = ContextVar 路由 key（不进 payload）；同 thread 多 run 无法区分；
5. WS 推送 = 原始 monitor_event envelope（无包装、无字段裁剪）——恰好说明可以直接增量扩字段；
6. 丢失/顺序/一致性缺口 = 见 F5/F6/F9/F10/F13（无缓冲→断线丢；无 seq→不可验证顺序；
   启发式终态→task_result 可能多次/缺失；error 走私有通道）；
7. async/sync 安全 = **实测可用**（F8）：sync 工具在工作线程但 context 快照可达 → 事件路由正确；
   投递经 run_coroutine_threadsafe 回 loop（F9）——这是 R3 要固化成测试与契约的核心结论；
8. 现 observability 仅"前端展示友好"，不是可验证 event stream（无身份/无顺序/无 run 边界）；
9. 重启后事件不存在（内存态）——**属 ROADMAP R4（Event Persistence）**，不在 R3；
10. 现有测试不能证明 event sequence/correlation/ordering（F12）——R3 补齐。

## 4. Event Schema（Proposed）

### 4.1 信封（envelope）——在 monitor._emit 单一出口增量扩展

```python
{
    "type": "monitor_event",      # 稳定（前端判定），不变
    "event": "<event_type>",      # 稳定，不变（取值见 §5 表）
    "event_id": "<uuid4-hex>",    # 新增：事件唯一身份（防重放/去重/测试引用）
    "run_id":    "<uuid4-hex>",   # 新增：一次 run_deep_agent 执行的身份（同 thread 多次任务/续跑可区分）
    "thread_id": "<safe-id|''>",  # 新增：显式关联键（原为隐式路由 key；无上下文时为 ''）
    "seq":       <int>,           # 新增：进程内全局单调递增（emit 时在锁内分配），排序依据
    "timestamp": "<naive ISO>",   # 稳定，不变（前端 new Date 解析；排序不用它）
    "message":   "<str>",         # 稳定，不变
    "data":      {...,},          # 稳定，不变（各事件私有字段，见 §5）
}
```

**必须稳定存在的字段**：`type / event / message / data / timestamp`（既有前端契约，R3 不变）
+ 新增 `event_id / run_id / thread_id / seq`（R3 起每个 WS 事件都携带）。

**明确不新增的字段（决策，非遗漏）**：
- `source/node`：现有 data 已按事件承载生产者信息（tool_name/assistant_name/path/result），
  信封再加 source 对前端/测试无增量价值；如需跨事件统一 source 语义，留 R3.1 评审；
- 时间戳改 UTC/单调：naive ISO 改动会改变前端 `new Date` 展示时区语义（兼容风险），
  排序职责交给 `seq`，时间戳保持现状。

**兼容性**：全部为**增量字段**，前端 MonitorMessage 结构仍成立（未知字段被忽略）；
EventStream 的 key `${timestamp}-${index}` 仍唯一；WS schema 语义无破坏。

### 4.2 事件类型与生产者（taxonomy 决策）

**原则**：不机械照搬点分式列表；以现有 6 个事件为稳定契约，**只新增 1 个事件
（task_started）**，其余缺口如实声明为 R3 外（理由见 §5.2/§6.4）。

| event（稳定） | 生产者 | data 字段 | 语义/生命周期角色 |
|---|---|---|---|
| `session_created` | run_deep_agent 入口（现状保留） | `path` | 工作目录就绪 |
| `task_started` | **新增**：run_deep_agent 入口（astream 前） | `query`(截断) | task 生命周期开始 |
| `assistant_call` | main_agent chunk（task 工具调用） | `assistant_name`、`args{description}` | 子智能体派发中（无完成事件，见缺口 A） |
| `tool_start` | 各 @tool 体内自报（现状保留） | `tool_name`、`args` | 工具开始（无完成事件，见缺口 A） |
| `task_result` | main_agent（**改为 run 级一次性**，见 §6.1） | `result` | 正常终态（内容） |
| `task_cancelled` | main_agent CancelledError（现状） | — | 取消终态 |
| `error` | main_agent Exception（**改走公共 report_error**） | `error`(新增) | 失败终态 |

**缺口 A（明确不在 R3）**：tool.completed / tool.failed / assistant.completed 无法在
"不改工具文件、不改子智能体装配、不引入深依赖"约束下可靠产生：
- 主图 astream chunk 只暴露 model/agent 级节点（main_agent 现状只处理 "model"），
  子智能体内部工具调用对主 stream **不可见**——这正是工具自报 tool_start 存在的原因；
- 工具层补完成/失败事件需触碰 6 个工具文件（业务逻辑相邻，违反"不改工具业务逻辑"）或
  装配层包装 main_agent + 3 个 subagents 的 tools 列表（更大表面积），或 LangChain
  callback 插桩（deepagents/langchain 版本敏感、无真实 LLM run 前不可验证）。
  → 列为 **R3.1 候选**（需用户单独裁决"改装配层 or 改工具文件 or callback 方案"）。

## 5. Correlation（thread_id / run_id 关联设计）

```text
thread_id（会话/连接级身份，R1 已净化）
   └─ run_id（一次 run_deep_agent 执行身份；/api/task 每启一次任务 = 新 run_id）
        ├─ 事件流：seq 排序的 event 序列（task_started → session_created → … → 唯一终态）
        ├─ assistant_call / tool_start 归属到 (thread_id, run_id)
        └─ 工具/执行线程内事件：emit 时经 ContextVar 快照取 thread_id + run_id
```

- `run_id` 由 run_deep_agent 入口生成（uuid4）并写入 ContextVar（新增 thread 级 run context），
  `_emit` 在锁内同时读取 thread_id 与 run_id；
- 同 thread 顺序任务/续跑：靠 run_id 分组，互不混淆（F3 场景）；
- 无 ContextVar（脚本/非 run 场景）：thread_id='' 、run_id='' ，事件仅 console（现状语义）；
- **复用优先**：thread_id 继续由 R1 session_id 净化；路由仍用 thread_id（不重构 manager key 语义）。

## 6. Ordering Semantics（定义 + 机制）

### 6.1 语义定义（写入契约，可测试）

1. **seq = 进程内全局单调递增整数**，在 `_emit` 内由 `threading.Lock` 保护分配
   （分配时刻 = 事件产生顺序；跨线程来源也能得到全序）；
2. **每 thread_id 的事件流因果有序**：同一 run 内事件按产生顺序依次出现
   （task_started < session_created < … < 唯一终态）；
3. **终态一次性（exactly-one-terminal）**：task_result / task_cancelled / error
   在同一个 run 内至多触发一次、三选一——由 main_agent 内部状态（已触发终态标志）保证；
4. **WS 投递顺序**：同 thread 投递经 **每 thread 串行发送队列**（新增，见 §7）保证
   "enqueue 顺序 = 发送顺序"，从而 seq 单调 == WS 到达顺序（跨来源竞态被队列吸收）；
   队列方案不可用时（用户否决不触碰 ConnectionManager）退化为"seq 由消费端排序"的
   best-effort 语义（二选一，需用户裁决，见 §13 审核提示）。
5. 时间戳不参与排序（naive、可并列）。

### 6.2 排序可测性

seq 单调性、终态唯一性、队列 FIFO 均有纯逻辑断言测试（§10）。

## 7. Async / Thread Safety（含实测与改动）

### 7.1 现状（已实测，见 F8/F9）

- sync @tool 在 async 图内于 **executor 线程**执行（`asyncio_0` 等），但 langchain
  提交时复制调用方 context → 工具体内 `get_thread_context()` 返回正确值
  （Python 3.13 + langchain-core 1.4.8 与 **锁定 1.3.3** 双环境同输出：`cv='MAIN-CONTEXT'`）；
- 跨线程投递走 `asyncio.run_coroutine_threadsafe(send, manager_loop)`（monitor.py:100），
  同 loop 走 `create_task`（monitor.py:98）——线程安全基线成立。

### 7.2 R3 加固（monitor.py 内，不改 ContextVar 语义）

1. `_emit` 内 seq/计数器访问加 `threading.Lock`（当前无锁，多线程 emit 有竞态）；
2. thread_id/run_id 在 emit 时**一次性读取并落进 payload**（快照语义，跨线程不追改）；
3. 每 thread 串行发送队列（§6.1-4）：`ConnectionManager` 新增 per-thread `asyncio.Queue`
   与消费者 task（connect 时启动、disconnect 时取消）；`send_to_thread` 变为 enqueue；
   单消费者串行 `send_json` → 同 thread 顺序确定、无并发 send_json 交错；
4. run_coroutine_threadsafe / create_task 的 Future 统一 fire-and-forget 并捕获异常
   （现状 print 保底保留），若 manager loop 已关闭 → 记日志不阻断主链路。

### 7.3 版本敏感性（如实声明）

ContextVar 复制是 langchain executor 路径行为（1.3.3 与 1.4.8 均验证）；若未来版本不再
复制，in-tool 自报事件 thread_id 将变 ''（事件丢失到 WS）。**R3 不把正确性唯一押在
该机制上**：投递侧始终以 payload 内显式 thread_id 为准；该机制只决定"来源能否取到
thread_id"。届时补救（run 包装显式传 thread_id）列为 R3.1。

## 8. Error Semantics

| 场景 | 现状 | R3 后 |
|---|---|---|
| run_deep_agent 异常 | main_agent 直调私有 `_emit("error")` | 新增公共 `monitor.report_error(message, data={error: str})`；终态一次性 |
| 任务取消 | `report_task_cancelled`（CancelledError） | 保持，纳入终态一次性语义 |
| 工具内部异常（被工具吞成字符串） | 不可观察 | **不伪造**：如实不产生 tool.failed（缺口 A，R3.1） |
| WS 发送失败 / loop 关闭 | `print` 保底 | 保留 + 日志，不阻断 Agent 主链路（fail-open 于可观测性，业务链路不受影响） |
| run 无上下文（脚本模式） | console | 保持；thread_id='' 但 seq 仍分配（全序不破） |

## 9. Proposed Changes（含"为什么必须改/为什么不能 Runtime-only 解决"）

### 9.1 改动（3 文件 + 1 新测试 + 本 Spec）

| 文件 | 改动 | 为什么必须改 | 能否 Runtime-only 解决 |
|---|---|---|---|
| `app/api/monitor.py` | envelope 增量字段（event_id/run_id/thread_id/seq + 锁）；新增 `report_task_started` / `report_error`；per-thread 发送队列（或 seq-only，二选一）；`_emit` 统一出口 | 事件身份/关联/顺序/线程安全全部落在事件出口；不改即无法获得可测试事件流 | 是——本文件即 Runtime 层（无需动 WS schema/前端） |
| `app/agent/main_agent.py` | run 入口生成 run_id、写入 run context；emit `task_started`；终态一次性逻辑（task_result 只在 run 收尾/内容候选）；`_emit("error")` → `report_error` | run 边界与生命周期事件只能由 run 拥有者产生；chunk 启发式需在此收紧 | 否——run_deep_agent 是唯一 run 边界（不得绕过，ARCHITECTURE §3） |
| `tests/test_agent_events.py`（新增） | 见 §10 | R3 核心是"可验证的事件流"，无测试等于未做 | — |
| `docs/spec/2026-09-03-R3-agent-event-observability.md` | 本文件 | 契约 | — |

### 9.2 明确不改（并说明为什么不需要改）

- **前端 / WS schema**：新增字段全增量、未知 event 前端忽略（F11）；event 名只新增 1 个
  （task_started），EventStream 以默认图标渲染、stats 只统计既有三个名字 → 前端行为不受影响；
  前端 types.ts 的 TS 描述可后续同步，**R3 不需要**（tsc 只检查前端自身，不受后端影响）；
- **工具 6 文件 / subagents**：缺口 A 决策（§5.2）；
- **context.py / server.py / thread_id 语义 / R1 / R2 / P001**：不动；
- **依赖 / pyproject / uv.lock**：零新增（asyncio + stdlib uuid/threading 即可）；
- **ROADMAP.md**：阶段重排需用户裁决后另行同步，**本 Spec 阶段不改**。

## 10. Tests（新文件 tests/test_agent_events.py）

环境约束（ROADMAP §5）：app/api/monitor、app/api/context 可 import 实测；
app.api.server 不可 import（→ deepagents 缺失）；故测试以 **monitor + 伪 manager 直测**
为主，WS/TestClient 集成留 uv 完整环境 skipif。

| 测试 | 验证 | AC |
|---|---|---|
| envelope 完整性：每个 report_* 产出含 type/event/event_id/run_id/thread_id/seq/timestamp | schema 稳定 | AC-1 |
| event_id 唯一、seq 严格递增（连续 emit） | 身份与全序 | AC-1/AC-5 |
| thread_id/run_id 落进 payload 且等于当前 context | 显式关联 | AC-2 |
| **sync @tool 经真实 `tool.ainvoke`（async 图内）emit 事件 → 捕获事件 thread_id 正确**（复用 R3 探测模式，真实 langchain 执行路径，非 mock） | async/sync 安全 | AC-6 |
| 两个并发 context（不同 thread_id）各自 emit → 各自事件集互不串扰 | 隔离 | AC-7 |
| 跨线程并发 emit（ThreadPoolExecutor）→ seq 全序唯一（锁正确性） | 排序 | AC-5 |
| 每 thread 发送队列：乱序 enqueue（多线程）→ 消费者按 enqueue 序发送（FIFO 断言） | WS 投递顺序 | AC-5 |
| 生命周期序列：task_started → session_created → tool_start/assistant_call → 唯一终态（task_result/cancelled/error）；终态一次性（重复触发被抑制） | 生命周期可验证 | AC-3/AC-9 |
| 工具"只报开始不报完成"为文档化行为（不在 AC 断言完成事件） | 缺口如实 | — |
| 回归：既有 182+1 全绿；ruff / compileall | 无回归 | AC-8 |
| （uv 完整环境，skipif）TestClient WS：连 /ws/{tid} → 触发 run → 收到带 thread_id/seq 的 monitor_event | 端到端投递 | AC-2/AC-5 |

## 11. Acceptance Criteria（AC，最终以本表为准）

| AC | 内容 | 验证 |
|---|---|---|
| AC-1 | 每个 WS 事件携带稳定 envelope（含新增 event_id/run_id/thread_id/seq），既有字段（type/event/message/data/timestamp）语义不变，additive 兼容 | envelope 测试 |
| AC-2 | 每个事件显式携带 thread_id（含从 sync 工具 executor 线程发出的事件） | AC-6 同款 + envelope 测试 |
| AC-3 | 生命周期可验证：task_started 存在；终态三选一且 exactly-once | 生命周期测试 |
| AC-4 | tool_start / assistant_call 可归属 (thread_id, run_id) 且与同 run 事件因果有序；**tool completed/failed 明确为 R3 外缺口（不伪称实现）** | envelope + 排序测试 |
| AC-5 | seq 进程内全序唯一；每 thread WS 投递顺序 = enqueue 顺序（或消费端可依 seq 复原全序） | 排序/队列测试 |
| AC-6 | async 图内 sync 工具产生的事件安全回到正确 thread（真实 langchain ainvoke 路径） | async/sync 测试 |
| AC-7 | 不同 thread_id 事件互不串扰 | 隔离测试 |
| AC-8 | 既有测试全绿 + ruff + compileall | 回归 |
| AC-9 | error/cancel 终态事件可观察且一次性（report_error 公共化、取消保持） | 生命周期测试 |

## 12. Non-Goals（R3 不做）

- **不做事件持久化/历史查询/重启恢复事件**（ROADMAP R4：sqlite event store + GET /events
  + WS 重放——本 Spec 明确 r3 结束时事件仍只存在于进程内）；
- 不做 OpenTelemetry / Prometheus / Grafana / ELK / 分布式 tracing（ROADMAP R6）；
- 不做 Kafka / Redis Streams / 数据库 event store / 新依赖；
- **不改前端、不改 WS schema、不改工具业务逻辑、不改子智能体装配、不改 thread_id 语义、
  不引入 run 队列/任务调度（旧 ROADMAP R3 Task Runtime 内容，去向待用户裁决）**；
- 不做 tool.completed / tool.failed / assistant.completed（缺口 A → R3.1 候选）；
- 不做 callback 插桩 / 装配层包装（R3.1 候选，需要时单独决策）。

## 13. Risks / Limitations

- **R3 事件仍在内存**：WS 未连/断线/重启期间事件丢失（现状不变）；历史查询是 R4；
- **终态一次性依赖 main_agent 控制流**：并发同 thread 替换（旧 cancel + 新 run）短暂交错时
  靠 run_id + seq 区分；同一 run 内多事件由终态标志保证；
- **task_result 语义收紧风险**：从"每个无工具调用的 model 文本"改为"run 收尾一次性"，
  若 deepagents 多轮中间文本场景依赖旧行为，需回归确认（无真实 LLM 环境 → 留给用户
  本机 E2E 验证清单）；**只收紧重复触发，不改变 data.result 内容与前端语义**；
- **ContextVar 复制机制版本敏感**（§7.3）；测试以真实 ainvoke 固定当前版本行为；
- **chunk 级 deepagents 行为**未经真实 run 验证（无 LLM key），改动保持保守
  （不新增 chunk 解析，只加 run 边界事件与终态收敛）；
- 每 thread 发送队列（若采纳）改变 ConnectionManager 内部结构，需回归 WS 心跳/
  disconnect/重连路径（uv 环境 TestClient 测试覆盖，本环境伪 manager 覆盖逻辑）；
- naive 时间戳维持（前端兼容），排序以 seq 为准。

## 14. Resume / Interview Claims

### 可以写（对应 R3 真实实现 + 测试）

- "Agent 执行事件流建模：统一 envelope（event_id/run_id/thread_id/seq）承载工具/助手/任务
  生命周期事件，支持跨 async/sync 边界的关联与排序";
- "证明 sync 工具在工作线程执行时，其事件仍能安全、有序地回到对应 thread_id 的事件流
  （真实 langchain executor 路径测试，非 mock）";
- "理解可观测性分层：事件模型（R3）→ 事件持久化（R4）→ 分布式 tracing（R6）不是一回事；
  事件排序用单调 seq 而非时间戳；工具副作用不自动重放/重报"。

### 不能写

- "事件持久化 / 重启后仍可查询历史事件"（R4，未实现）；
- "tool completed/failed 已可观察"（缺口 A，R3.1）；
- "分布式 tracing / trace_id 贯穿"（R6，未实现）；
- 若 AC-1~AC-9 未全部真实通过（只能静态/mock）→ 对应条目如实降级报告。

## 15. 审核提示（给用户）

1. **ROADMAP 冲突裁决**：本次 R3 = Agent Event/Observability 与 ROADMAP 旧 R3（Task Runtime）、
   R4（Event Persistence）、R6（OTel）的关系如何重排？（建议：本 R3 作为事件模型先行，
   旧 Task Runtime 顺延其后；R4 事件持久化在本 R3 之上叠加 sqlite store——是否认可？）
2. **顺序保证二选一**：(A) per-thread 串行发送队列（推荐，WS 到达顺序 == seq，可测）；
   (B) 不触动 ConnectionManager，仅 seq 全序 + 消费端排序（更小改动，WS 到达顺序 best-effort）。
3. **缺口 A（tool 完成/失败事件）** 是否同意列为 R3.1（需要触碰工具文件或装配层或 callback 插桩，
   单独决策）？R3 只保证 tool_start 的可关联/有序，不伪称完成事件。
4. **task_result 收紧**：从 chunk 启发式（可能多次/缺失）改为 run 级一次性终态语义，
   是否认可？（前端语义不变，仅去掉重复/缺失风险；真实 LLM 行为差异留你本机 E2E 复核）
5. **文件范围**：monitor.py + main_agent.py + 新测试文件；前端/工具/subagents/依赖零改动——是否认可？

---

## 16. 批准裁决与实施记录

### 16.1 用户批准（2026-09-03，5 项裁决）

1. **Roadmap 重排（裁决）**：R1 Agent Tool/File Security → R2 Agent Execution State/Checkpoint →
   R3 Agent Event/Observability → R4 Agent Task Runtime → R5 Agent Event Persistence/Replay →
   R6 OpenTelemetry/Distributed Tracing；本次 R3 Implementation **不修改 ROADMAP.md**（文档同步后续单独处理）。
2. **Ordering 方案 A（批准）**：per-thread 串行发送队列；seq = 进程内 event allocation order 全序，
   **不是物理时间戳、不代表绝对因果时间**；时间排序以 seq 为准，timestamp 不参与。
3. **Tool completed/failed → R3.1（批准）**：R3 不改 6 个工具文件、subagents 装配、callback 插桩；
   只保证现有 tool_start 的 event_id/thread_id/run_id/seq/ordering/async-sync correlation；不伪造完成事件。
4. **task_result → run 级 exactly-once terminal（批准）**：保留 data.result 与前端语义；
   不猜测 deepagents 最终 chunk 行为；无真实 LLM E2E 时用单元/集成测试验证 terminal exactly-once 控制逻辑。
5. **Scope（批准）**：仅 app/api/monitor.py、app/agent/main_agent.py、tests/test_agent_events.py + 本 Spec 状态更新；
   不改 frontend/tools/subagents/context.py/server.py/R1/R2/P001/dependencies/pyproject.toml/uv.lock；零新依赖。

### 16.2 实施要点（对应代码）

- `app/api/monitor.py`：envelope 增量字段（event_id/run_id/thread_id/seq）；`_seq` + `threading.Lock`；
  run_id ContextVar（本模块托管，context.py 未改）+ set/get/reset；新增 `report_task_started` /
  `report_error`（error 公共化，取代 main_agent 对私有 `_emit` 的直调）；`RunTerminalGuard`（终态一次性，纯逻辑可单测）；
  `ConnectionManager` 增加 per-thread `asyncio.Queue` + 单消费者 task（connect 启动 / disconnect 取消 /
  重连 last-wins + stale guard / 跨线程 `call_soon_threadsafe` 入队）；投递失败 fail-open 不阻断 Agent 主链路。
- `app/agent/main_agent.py`：run 入口生成 `run_id` 并 set run context（finally reset）；`task_started`（query 截断 500）
  先于 `session_created`；`task_result` 由 chunk 启发式改为 **run 收尾一次性**（记录 final_content 候选，
  astream 正常结束才经 `RunTerminalGuard.may_emit("task_result")` 发出；cancelled/exception 分别经 guard
  走 `task_cancelled` / `report_error`）；`monitor._emit("error")` 私有调用移除。
- `tests/test_agent_events.py`：16 项覆盖（envelope 完整性 / event_id 唯一 / seq 单调与跨线程唯一 /
  thread_id/run_id 显式 / 隔离 / 跨线程 emit / per-thread FIFO / 生命周期顺序 / task_started /
  terminal exactly-once / cancel / error / 真实 sync @tool → tool.ainvoke → executor 线程正确 thread_id /
  断开与重连生命周期 / 双线程互不阻塞）。

### 16.3 验收证据（AC → 结果）

- AC-1/AC-2/AC-5：tests/test_agent_events.py（envelope/seq/FIFO）；本机与 uv 锁定环境全绿；
- AC-3/AC-9：RunTerminalGuard 单测 + 生命周期序列断言（main_agent 级 wiring 无法在本环境 import，
  逻辑已下沉可测；真实 Agent run 的最终 result 行为留用户 LLM E2E 复核——如实报告，未伪造）；
- AC-4：tool_start 的 event_id/thread_id/run_id/seq/排序/跨线程关联已测；completed/failed = R3.1（不伪称）；
- AC-6：真实 langchain `tool.ainvoke`（executor 线程）→ 事件回正确 thread_id（非 mock）；
- AC-7：并发 thread/run 隔离测试；
- AC-8：既有 182+1 回归全绿 + ruff + compileall（双环境）。

