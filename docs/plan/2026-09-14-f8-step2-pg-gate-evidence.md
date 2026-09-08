# F8 Step 2 — PostgreSQL Gate Evidence Completion（验证记录）

> 状态：**F8 Step 2 = Implementation Complete + PostgreSQL Gate Complete + FROZEN**
> （Final Gate 确认：P1–P5 全部真实 PG16 证据；全仓回归 0 failed；Scope clean；生产逻辑零改动；
> 冻结后不再修改 Step 2 生产代码：controller.py / store.py / terminal funnel / race semantics 保持现状。）
> 全局规则（F8 起锁定）：**PostgreSQL 是唯一生产基线与最终 Gate Backend**；SQLite 仅快速单测/
> fallback/纯逻辑测试，SQLite PASS ≠ PostgreSQL PASS —— 后续每个 F8 Step 均适用。
> 本轮未修改生产逻辑（controller.py / store.py 零改动），未进入 Step 3。

## 1. 范围

| 文件 | 动作 | 内容 |
|---|---|---|
| `tests/test_runtime_governance_postgres_gate.py` | 新增 | P1 真并发 CAS（3 组）、P2 CAS loser/adoption + rowcount 语义、P3 persistence failure→pending→flush、P5 Thread≠Task≠Run + duplicate UniqueViolation + start 不覆盖 terminal 行 |
| `tests/test_runtime_governance_postgres.py` | 未改 | Step 1/Step 2 既有 PG 镜像（本 Gate 未触碰） |
| `.deepagents-fc/_pg_reset_governance.py` | scratch | 测试库 governance 表族 fixture/reset（DSN 从 `AGENT_CHECKPOINT_DSN_TEST` 读取） |
| `app/runtime/governance/*` | **零改动** | 生产逻辑未动 |

## 2. PostgreSQL 环境

- 真实 PG16（远端 lab 实例，`192.168.127.101:5432`，测试库 `deepscholar_checkpoint_test`，
  governance **独立表族**，不触碰 checkpoint/research 表）。DSN 经环境变量 `AGENT_CHECKPOINT_DSN_TEST` 注入，不在本仓库落明文。
- 运行方式：`uv run --no-project --with pytest --python .venv python -m pytest …`
  （.venv = py3.12.13，含 psycopg）。

## 3. P1 — 真并发 CAS 仲裁（两个独立 Controller + 同一真实 PG task）

**结构**：两个线程各自 `GovernanceController`（独立 `asyncio.Lock` / `_decisions` / 独立
`_GovernancePostgresStore` 实例）对同一 running task 并发 `terminalize()`。为避免单线程事件
循环把两次 store 调用天然串行化（后到者会在"决策读"时看到已 terminal 而走 adoption、根本不发
第二条 UPDATE），测试在 **store 边界** 安装纯测试读点对齐闸门（`_PairWaveGate`：让双方的
决策读、CAS 前重读各成对完成后才放行）。闸门不改任何 SQL/语义；**两条真实 PG UPDATE 语句
随后并发发出，由 PG 行锁仲裁**，store recorder 直接记录每次 `terminal_update` 的 rowcount 结果。

观测（真实运行取证输出，每组独立 task 行）：

| Race | store recorder（controller, rowcount==1） | durable winner | DB final status | version | counters_snapshot | superseded_by |
|---|---|---|---|---|---|---|
| completed × cancel | B=True, A=False | B (cancelled) | `cancelled` | 1 | `{"side":"B"}` | — |
| budget × timed_out | B=True, A=False | B (timed_out) | `timed_out` | 1 | `{"side":"B"}` | — |
| supersede × cancel | A=True, B=False | A (superseded) | `superseded` | 1 | `{"side":"A"}` | `task-new` |

断言（测试内）：恰一个 `rowcount==1`、至少一个 `rowcount==0`；DB 单终态 == durable winner 的
reason；`version 0→1` 恰一次；赢家 counters/superseded_by 快照与 DB 一致；败者最终采纳 DB 事实
（状态/快照收敛到赢家）；追加 funnel 仍 no-op 且 version 不再变。

**语义注记**：两个 Controller 各自在"本进程内存"裁决，故双方 funnel 结果 `winner=True`
（=“本进程决策者”），**durable winner 的唯一性**由 recorder 的恰一个 `rowcount==1` 直接证明
（败者从未取得第二次 `rowcount==1`）。

稳定性：整文件连跑 4 轮均 9/9 通过（无 flake）。

## 4. P2 — CAS loser/adoption 与 rowcount 语义

- **P2a 真实 store 层 CAS rowcount 直接证据**：错 version（期望 5，实际 0）→ `rowcount==0`、
  version 不误写；对 version → `rowcount==1`、version 0→1；已 terminal 后任意 CAS（含 version 匹配）
  → `rowcount==0`、version 不再 +1。
- **P2b 独立第二 Controller 采纳**：A `completed`（唯一 durable 写，records=1）→ B（独立内存/独立
  store）`cancelled` → B **零第二次 UPDATE**（records 仍 =1）、返回 `already_terminal=true /
  adopted_from_db=true / durable=true`，DB 保持 completed、version=1、A 的快照不被覆盖。

## 5. P3 — persistence failure → pending → flush（真实 PG）

- store 为**真实 PG**；仅在 `terminal durable 写` 边界注入故障（`terminal_update` 抛错 ×3，
  读仍走真实 PG：DB 行保持 running/v0）。
- 断言：`attempts==3`（立即+0.5s+2s 阶梯，测试缩时）；返回 `durable=False / pending=True /
  degraded_durability=True`（**绝不谎报**）；内存裁决不被撤销（`terminal_decision` 仍 cancelled）；
  pending 期间重复收敛 no-op；恢复后 `flush_pending()` → `flushed=1`，DB cancelled、version 0→1
  **恰一次**、counters 一致；重复 `flush_pending()` 幂等（`flushed=0`、version 仍 1）。

## 6. P5 — Thread ≠ Task ≠ Run / 唯一约束

- 同 thread `T` 下 task A（run-RA）、task B（run-RB）：task_id 互异、thread_id 相同、run_id 独立；
  A 收敛不影响 B，B 独立收敛；两行 version 各自 0→1（lifecycle 以 task_id 为主键）。
- duplicate task_id 二次 INSERT → 真实 PG 唯一约束 **sqlstate 23505**（非应用层检查），原行不被覆盖。
- `start_task` 不覆盖 terminal 行（UPDATE 语义：WHERE status='running'）。

## 7. 验证结果汇总

| 运行 | 结果 | 角色 |
|---|---|---|
| PG Gate 证据（9 项；连跑 4 轮） | 9 passed ×4 | **最终 Gate 证据（真实 PG16）** |
| Step1 + Step2 + Gate 全 PG（reset 后） | **17 passed** | 最终 Gate 证据 |
| 全仓 sqlite-only 回归 | 468 passed / 50 skipped / 0 failed | 快速回归/单测（不做 Gate 依据） |
| ruff / format / compileall（改动文件） | 全绿 | 卫生 |

## 8. 并发诚实性自检

1. 两个 Controller 是否真正独立？ **是**：各自线程、各自 `asyncio.Lock`、各自 `_decisions/_handles`、各自 `_GovernancePostgresStore` 实例（独立短连接）。
2. 两个 asyncio.Lock 独立？ **是**。
3. 共享同一 PG task row？ **是**（每组独立 uuid task 行）。
4. 两条 UPDATE 真实交错？ **是**：读点闸门只对齐"读已完成"，随后两条真实 UPDATE 并发发出，PG 行锁仲裁；非在锁内顺序执行。
5. 观察到 winner rowcount=1 / loser rowcount=0？ **是**：store recorder 直接记录（见 §3 表格）。
6. 是否存在 fixture/lock/event loop 造成的隐式串行化？ 决策读与 CAS 前读被测试闸门**有意**对齐（否则第二条 UPDATE 根本不会发出）；对齐之后两个 UPDATE 本身无任何共享锁，真并发由 PG 仲裁 —— 不构成隐式串行化，且这正是为了可观测地证明 DB CAS 语义。
7. 若无法证明真交错应标 NOT PROVEN —— 本组已直接观测 rc1/rc0，无需此标记。

## 9. 未证明/残余限制（真实）

- 多进程（非线程）级并发未做：两个 OS 进程各自 controller 竞争同一 PG 行属同一 CAS 语义面，
  线程版已覆盖 DB 层裁决；进程版差异仅在各自内存隔离，已在 §3 结构中等价覆盖（独立 controller/内存/store）。
- 长期（分钟级）PG 故障窗口下 reconciler 周期重试（15s reconciler）属后续 Step（sweeper/startup），
  本轮只证明 flush/shutdown 路径收敛。
- AC11(c2)/(c3) 仍属后续接线/startup Step，本轮未实现（按 Gate 裁决，不冒充）。
