# Spec: 2026-09-03-R2-persistent-checkpoint（ROADMAP R2 会话执行状态持久化）

> **编号规则**：Pxxx = PROBLEM.md Problem Registry；Rxx = 工程化 Roadmap ID（本任务 = **R2**）。
> **状态**：**等待审核（AWAITING APPROVAL）**。批准前禁止任何代码/测试/配置/依赖改动。
> **状态更新（2026-09-02）**：R2 已实施并验证（app/runtime/checkpoint.py、main_agent.py 改造、
> tests/test_checkpoint_recovery.py 均已落地并通过）；本日用户授权将
> `langgraph-checkpoint-sqlite==3.0.3` **正式补录**为 R2 运行时依赖（pyproject.toml /
> requirements.txt / uv.lock），详见 §12。
> **本 Spec 的版本兼容性结论基于真实环境探测**（uv.lock 解析 + PyPI wheel 源码核对 + 本机
> 依赖实测），非假设。

---

## 1. Problem

当前 Agent 会话执行状态**只存在于进程内存**：

- `app/agent/main_agent.py:41` 构造 `main_agent = create_deep_agent(..., checkpointer=InMemorySaver(), ...)`
- `InMemorySaver`（`langgraph.checkpoint.memory`）把 checkpoint 存在进程内 dict：
  - 服务重启（uvicorn reload / 崩溃 / 部署）→ 所有 thread 的执行状态丢失；
  - 用户无法"中断后继续同一个研究任务"——回到前端只能重发新任务，从头执行；
  - 与 R1 已实现的 session 目录隔离不同：`output/session_{id}` 目录是持久的（产物在），
    但 LangGraph 的 **execution state（消息、节点进度、pending writes）不在**。

对 Agent Developer 的价值缺口：LangGraph 的 thread_id + checkpointer 是本项目会话能力的
核心机制，但当前"记忆"只有进程级生命周期，无法支撑真实的
"long-running task 中断 → 服务重启 → 同一 thread_id 恢复执行"场景。

## 2. Goal

把 Agent 执行状态从 InMemorySaver 换成 **SQLite 持久化 Checkpointer**，实现：

1. `main_agent` 使用文件型 SqliteSaver（而非内存型）；
2. 服务重启后，同一 `thread_id` 的 Agent execution state 可被**新的 Checkpointer 实例找回**；
3. 提供真实可验证的 "restart recovery" 测试（进程级模拟重启，非 mock）；
4. 措辞纪律：只声称"**会话执行状态持久化与重启恢复**"，不声称"长期记忆/通用记忆库"
   （那属于 BaseStore/Store 层，Non-Goal）。

**完成判定（验收标准）**：
- AC-1：`main_agent` 的 checkpointer 为文件型 SqliteSaver，DB 文件路径可配置；
- AC-2：进程 A 用 `thread_id=A` 执行并产生 checkpoint 落盘；
- AC-3：新进程 B（模拟重启，全新进程/新 Checkpointer 实例）打开同一 DB，
  `thread_id=A` 能找回之前 state（get_state/list 非空）；
- AC-4：进程 B 能从 checkpoint 继续执行（续跑或读取，取决于任务是否完成）；
- AC-5：仅在 AC-2~AC-4 全部真实通过后，报告才可写
  "Agent execution state persisted and recovered across process restart."；
  若只能单元测试/静态验证/mock → 如实报告未达成，不写该结论。

## 3. Non-Goals

- **不做** LangGraph `BaseStore` / 长期记忆 / 跨会话知识库（那是 Store 层，另一机制）；
- **不引入** PostgreSQL 多实例共享 checkpoint（单机 SQLite 即可，多实例需 Postgres——单独决策）；
- **不改变** Orchestrator / 子智能体 / Tool / WS schema / HTTP 端点 / 前端；
- **不引入** 除 `langgraph-checkpoint-sqlite` 外的任何新依赖；
- **不升级/降级** 现有锁定依赖（langgraph==1.1.10 等保持 uv.lock 不变，见 §10 兼容结论）；
- **不做** 会话历史 UI / 恢复端点（本次仅 Runtime 层持久化 + 验证）；
- **不重复** P001（SQL）/ R1（文件安全）。

## 4. Current Architecture

### Checkpointer 真实使用位置与调用链（源码核对）

```text
app/api/server.py  run_task()
  └─ asyncio.create_task(run_deep_agent(query, session_id))     # thread_id = session_id
app/agent/main_agent.py  run_deep_agent(task_query, session_id)
  ├─ main_agent = create_deep_agent(model, ..., checkpointer=InMemorySaver(),
  │                                 subagents=[...])            # 模块级，进程启动时构造一次
  ├─ session_dir = app/output/session_{session_id}（目录持久，产物在）
  ├─ config = {"configurable": {"thread_id": session_id}}       # thread_id 进入 LangGraph 的唯一路径
  └─ async for chunk in main_agent.astream({"messages":[...]}, config=config)
deepagents 0.5.7  create_deep_agent(...)                        # 源码核对（wheel 反编译）
  └─ return create_agent(..., checkpointer=checkpointer)        # deepagents/graph.py:708,715
     （langchain.agents.create_agent，非直接 langgraph；checkpointer 透传）
```

### thread_id 如何进入 LangGraph execution

- `run_deep_agent` 构造 `config = {"configurable": {"thread_id": session_id}}`（main_agent.py:96 附近），
  传入 `astream(..., config=config)`；
- LangGraph 运行时用 `config["configurable"]["thread_id"]` 作为 Checkpointer 的
  checkpoint 主键之一（`checkpoints` 表 PK = `(thread_id, checkpoint_ns, checkpoint_id)`，
  已从 SqliteSaver 源码确认）；
- thread_id 已由 R1（session_id.py）净化为 `[A-Za-z0-9_-]{1,128}`，可直接作为 DB key 使用。

### Agent state 会被 checkpoint 什么 / 不会被 checkpoint 什么

基于 LangGraph Checkpoint 机制（`langgraph.checkpoint`）：
- **会被 checkpoint**（序列化存 `checkpoints.checkpoint` BLOB）：
  - Agent 图状态（deepagents 的 AgentState：messages 列表、当前节点、pending tasks/writes）；
  - 每步执行后的完整图状态快照（get/put 每次节点后）；
- **不会被 checkpoint**：
  - 外部副作用结果（Tavily 搜索结果、MySQL 查询、RAGFlow 回答——除非写回 messages）；
  - 上传/生成的文件内容（在 output/session_{id} 文件系统，独立持久）；
  - LLM 连接、ContextVar（session_dir/thread_id）——进程内状态，重启后由 run_deep_agent 重建。

### SqliteSaver API（langgraph-checkpoint-sqlite 3.0.3 wheel 源码核对）

```python
from langgraph.checkpoint.sqlite import SqliteSaver
conn = sqlite3.connect(db_path, check_same_thread=False)   # 需显式建连接
saver = SqliteSaver(conn)                                  # 构造需外部 conn
# 表结构：checkpoints(thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata)
#         writes(thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value)
# setup() 自动建表；PRAGMA journal_mode=WAL；内部 threading.Lock 保证线程安全
```

## 5. Proposed Design

### 5.1 新增 `app/runtime/checkpoint.py`（Checkpointer 工厂，单一职责）

```python
"""Agent 执行状态持久化 Checkpointer（R2）。

职责：按配置创建文件型 SqliteSaver，管理 DB 文件路径与连接生命周期。
"""
CHECKPOINT_DB_ENV = "AGENT_CHECKPOINT_DB"          # 缺省: app/runtime/checkpoints.sqlite
def get_sqlite_checkpointer() -> SqliteSaver:       # 模块级单例（进程内复用同一连接）
    # 1) 读 env 或默认路径（默认 app/runtime/checkpoints.sqlite，目录自动创建）
    # 2) sqlite3.connect(path, check_same_thread=False)
    # 3) 返回 SqliteSaver(conn)（setup 由 LangGraph 首次使用时自动触发）
def get_checkpoint_db_path() -> str                 # 供测试/报告读取实际路径
```

设计要点：
- **默认路径放 `app/runtime/checkpoints.sqlite`**（gitignore 增加），避免污染 app/output
  会话目录语义（那是按 session 隔离的产物区，checkpoint DB 是跨 session 的运行时状态）；
- 路径可经 env `AGENT_CHECKPOINT_DB` 覆盖 → 测试可指向临时文件、重启恢复测试可复现；
- 单例：进程内所有 run_deep_agent 共用同一 SqliteSaver（SqliteSaver 内部有 Lock，线程安全；
  WAL 模式支持并发读写）。**生命周期 = 进程生命周期**（连接随进程，重启后新进程重开 DB，
  这正是要验证的恢复语义）。

### 5.2 修改 `app/agent/main_agent.py`（最小改动）

- import `get_sqlite_checkpointer`；
- 模块级构造改为：
  ```python
  main_agent = create_deep_agent(
      model=model, system_prompt=...,
      tools=[...], checkpointer=get_sqlite_checkpointer(), subagents=[...],
  )
  ```
- 其余（session_dir 创建、ContextVar、config thread_id、astream、monitor）**零改动**。

### 5.3 配置与安全边界

- DB 文件位置：`app/runtime/checkpoints.sqlite`（env 可覆盖）——在 app 内、gitignore；
  不暴露给 `/api/files`/`/api/download`（那两接口只允许 output/ 下，天然隔离）；
- thread_id 已净化（R1），作为 DB key 安全；
- DB 权限：默认文件由运行进程创建（教学环境不额外锁权限；若需更强，env 指定路径到
  受控目录即可——报告 Limitations）。

### 5.4 兼容性结论（真实探测，见 §10）

| 环境 | langgraph | langgraph-checkpoint | langgraph-checkpoint-sqlite 应选 |
|---|---|---|---|
| 仓库锁定（uv.lock） | 1.1.10 | 4.0.3 | **3.0.3**（requires `langgraph-checkpoint>=3,<5.0.0` ✓） |
| 本机系统 Python | 1.2.7 | 4.1.1 | 3.1.1（requires `>=4.1.0,<5.0.0` ✓；仅用于本机验证，不改 uv.lock） |

- 最新 3.1.1 要求 checkpoint>=4.1.0 → **与仓库锁定 4.0.3 不兼容**，故若按 uv 环境安装应选 **3.0.3**；
- 本机验证若 pip 安装则为 3.1.1（匹配系统 4.1.1）——两套版本的 SqliteSaver API 一致
  （wheel 源码核对：均 `SqliteSaver(conn)` + 相同表结构）。

## 6. Files To Change

### New

- `app/runtime/checkpoint.py`（Checkpointer 工厂）
- `tests/test_checkpoint_recovery.py`（重启恢复真实测试，见 §9）
- `docs/spec/2026-09-03-R2-persistent-checkpoint.md`（本文件）

### Modify

- `app/agent/main_agent.py`（checkpointer 一行构造改动 + import）
- `.gitignore`（+ `app/runtime/checkpoints.sqlite` 或 `*.sqlite`）

### Delete

- 无

范围纪律：以上文件若实施中发现必须超出，立即停止并回改本 Spec 再等批准。

## 7. Agent Value

> 为什么属于 Agent Engineering 而非普通数据库改造？

Checkpoint/State Management 是 Agent 系统的**核心运行时能力**（Tier 1/2），不是"把 dict
换成数据库"：

1. **长任务可靠性**：真实 Agent 任务（深度研搜）运行数分钟，进程中断应可续跑而非重来——
   这是 Agent 应用与普通 HTTP CRUD 的本质差异（有状态、长时、可中断）；
2. **thread_id 语义升级**：从"进程内临时会话键"升级为"跨重启的执行身份"，
   checkpoint 恢复 = LangGraph 提供的原生容错机制，理解它才能设计
   resume/retry/rollback（Agent 面试高频题）；
3. **状态边界理解**：必须分清 checkpoint 覆盖什么（图状态/消息）不覆盖什么
   （外部副作用/文件产物）——这正是 Agent 状态管理面试要考察的；
4. 与 R1 的 session 目录持久化互补：产物（文件）持久 + 执行状态（checkpoint）持久 =
   完整的中断恢复语义。

## 8. Interview Value

- Agent 为什么需要 checkpoint？interrupt/resume 如何工作？
- thread_id 在 LangGraph 里如何索引执行状态？重启后恢复的机制？
- checkpoint 与 memory/BaseStore 的区别（执行状态 vs 长期知识）？
- SQLite 为什么够用？何时要 Postgres？（单实例 WAL vs 多实例共享）
- 如何验证"重启恢复"而不是 mock？（真实子进程 + 磁盘 DB）
- 恢复后哪些副作用不会自动重放（外部 API 调用）？如何设计幂等？

## 9. Test Plan

### 9.1 真实 restart recovery 测试（核心，非 mock）

`tests/test_checkpoint_recovery.py`，用 **真实子进程** 模拟进程重启：

```text
Phase 1（进程 A）：
  subprocess 启动 python -c "<最小 LangGraph 图脚本>" --db <tmp.sqlite> --thread A
  → 图执行一部分（写入 checkpoint）→ 进程 A 正常退出
Phase 2（进程 B = 重启）：
  新 subprocess（全新进程）打开同一 <tmp.sqlite>，thread_id=A
  → get_state(config) 返回非空 → 从 checkpoint 继续执行（剩余节点跑完）
断言：
  - Phase1 后 DB 文件存在且 checkpoints 表有 thread A 记录；
  - Phase2 能列出/读取 thread A 的 checkpoint（状态含 Phase1 已产生的消息/值）；
  - Phase2 续跑结果 = 完整执行结果（证明恢复而非重跑，或读取到已完成 state）。
```

- 图用最小 `langgraph.graph.StateGraph`（不依赖 deepagents/LLM/网络，稳定可复现）；
  若验证目标为 main_agent 级，则额外提供 `tests/test_main_agent_checkpoint_restart.py`
  标注 skipif（deepagents 缺失时跳过，完整环境可跑）——但 **R2 验收不依赖它**，
  以最小真实图 + 真实子进程为准（§2 AC-2~AC-4 的可验证载体）。
- 本机环境实测：langgraph 1.2.7 + checkpoint 4.1.1 已装；langgraph-checkpoint-sqlite
  需 pip 安装（3.1.1）——实施阶段先执行安装并记录版本，若网络/权限不可用则如实报告
  验证缺口（不伪造）。

### 9.2 单测

- checkpoint 工厂：默认路径生成、env 覆盖、DB 文件创建、单例复用、SQLite 连接可开可关；
- main_agent 改动为一行构造替换 → 用 AST/导入检查验证签名兼容（deepagents 缺失时不能
  import main_agent，如实报告）。

### 9.3 回归

- 现有 173+ 测试保持全绿（R1 文件安全 / P001 SQL / 工具层）；
- ruff / compileall。

### 9.4 对抗/边界

- 同一 thread_id 在不同进程写（并发）——WAL + Lock 语义（Limitations 说明，不做压力测试）；
- DB 路径不可写 → 工厂报清晰错误（fail-fast），不静默退化到内存。

## 10. Risks / Limitations

- **本机系统 Python 与仓库 uv.lock 版本不同**（langgraph 1.2.7 vs 1.1.10）：本机验证用
  系统环境（sqlite saver 3.1.1），仓库锁定的真实运行环境需用户在 uv 环境按
  langgraph-checkpoint-sqlite==3.0.3 安装后复验（Spec §5.4 表）。
  （已落实：2026-09-02 已正式补入 pyproject/requirements/uv.lock 并在 uv 环境复验通过，见 §12）
- SqliteSaver 官方注释：轻量同步场景，**不扩展到多线程/多实例**；本项目单进程 asyncio
  调度 + 内部 Lock 足够；多实例共享 checkpoint 需 Postgres（Non-Goal，单独决策）；
- 恢复不重放外部副作用（搜索/DB/RAG 结果不自动重取，除非在消息里）；幂等性需上层设计
  （Limitations 如实说明）；
- DB 文件安全：默认在 app/runtime/ 下且 gitignore；无独立访问控制（教学边界，同 P005 精神）；
- deepagents 0.5.7 对 checkpointer 的透传（create_agent）已源码确认接受 langgraph
  Checkpointer，SqliteSaver 是其子类——兼容风险低，但 main_agent 级 E2E 仍需完整环境。

## 11. Resume Claims

### 可以写（严格对应最终真实实现）

- "将 Agent 执行状态从内存 Checkpointer 升级为 SQLite 持久化（langgraph-checkpoint-sqlite），
  同一 thread_id 的执行状态可跨进程重启恢复";
- "实现并验证 restart recovery：子进程写 checkpoint → 新进程打开同一 DB 找回状态并续跑
  （真实子进程测试，非 mock）";
- "理解 checkpoint 边界：图状态/消息持久化 vs 外部副作用不自动重放，与产物文件持久化互补"。

### 不能写

- "长期记忆 / 跨会话知识库"（未实现 BaseStore）；
- "多实例高可用 checkpoint"（单机 SQLite 限定）；
- 若 restart recovery 未能真实通过（只能单测/mock）→ 该条不写，如实报告。

---

## 审核提示（给用户）

1. **兼容版本选择**：仓库 uv 环境按 `langgraph-checkpoint-sqlite==3.0.3`（与 langgraph
   checkpoint 4.0.3 匹配）；本机验证环境用系统 Python 的 3.1.1。批准后实施阶段是否允许
   在本机 pip 安装 3.1.1 用于跑 restart recovery 测试？（uv.lock 不为此改动）
2. **DB 默认路径** `app/runtime/checkpoints.sqlite` + env `AGENT_CHECKPOINT_DB` 覆盖，
   是否认可？（决定是否需新增 env 说明到 .env.example）
3. **验收载体**：restart recovery 用"最小 LangGraph 图 + 真实子进程"作为可验证载体
   （不依赖 deepagents/LLM/网络），main_agent 级 E2E 另作 skipif 测试并在完整环境跑——是否认可？
4. **审核重点**：§4 源码核对结论（InMemorySaver 位置 / create_deep_agent 透传 /
   SqliteSaver API）、§10 版本兼容表、Files To Change 是否越界。

---

## 12. 依赖正式声明（补录记录，2026-09-02）

R2 实现中 `app/runtime/checkpoint.py` 真实 import `from langgraph.checkpoint.sqlite import
SqliteSaver`，但 `langgraph-checkpoint-sqlite` 此前**未声明**在 pyproject.toml /
requirements.txt / uv.lock —— 首次 `uv sync` 不会安装它，导致后端启动
`ModuleNotFoundError: No module named 'langgraph.checkpoint.sqlite'`；后续任何 `uv sync`
也会把临时安装清掉，持续破坏 R2。

**正式补录（用户授权）**：

```text
pyproject.toml     dependencies: "langgraph-checkpoint-sqlite==3.0.3"
requirements.txt   langgraph-checkpoint-sqlite==3.0.3
uv.lock            langgraph-checkpoint-sqlite 3.0.3（+ 传递依赖 aiosqlite / sqlite-vec）
```

- **版本固定 3.0.3**：与仓库锁定 langgraph==1.1.10 / langgraph-checkpoint==4.0.3 匹配
  （requires checkpoint>=3,<5.0.0）；最新 3.1.1 要求 checkpoint>=4.1.0，与锁定不兼容，不用；
- **范围纪律**：仅新增该包，未升级/改动任何其他 LangGraph/LangChain 锁定依赖；
- **验证记录**（uv 环境，Python 3.12.13）：
  - `uv sync --frozen` 通过（锁与 pyproject 一致）；
  - `tests/test_checkpoint_recovery.py`（R2 restart recovery：真实子进程跨进程恢复）通过；
  - 完整 `pytest tests/ -q`、`ruff check`、`python -m compileall -q app` 通过；
  - 后端 import 链恢复：`uvicorn app.api.server:app` 启动无此 ModuleNotFoundError。
- **相关变更记录**：DECISION.md D009 追加 Update（2026-09-02）说明依赖正式补录，
  推翻原"不改 uv.lock / pyproject.toml"安排。
