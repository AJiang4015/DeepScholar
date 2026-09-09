# P2-1 Runtime Policy Enforcement Spec

Phase-2 Runtime Reliability — Batch P2-1：默认 Runtime Policy 强制化（根因修复）

- 文档类型：Spec（L1 Spec Review 阶段产物；**当前阶段 = 只读/设计，禁止修改代码**）
- 上游：`RUNTIME_OBSERVABILITY_AUDIT_REPORT.md`（L0 Audit，已批准）
- 关联决策（用户已裁决，正式登记在 Decision Gate）：
  - `D-Phase2-P2-1-001`：policy=None ⇒ 自动注入 Default Runtime Policy；production 任务必须 governed；bare execution 仅限 unit test / local debugging / internal development；默认参数沿用 M-Spec §7.4 冻结默认值，P2-1 不调整。
  - `D-Phase2-P2-2-001 / -002`（记录，P2-2 实施）：派生 stale 视图 + 复用 `orphan_reclaimed`，不改 TaskStatus enum；阈值 `wall_clock=600s`、`stale=wall_clock+30s`、env 可配。
  - `D-Phase2-P2-5-001`（记录，P2-5 实施）：标题确定性规则优先，LLM 增强留扩展。
- 状态：**PENDING REVIEW** —— 完成本 Spec 后停止，等待 Review 批准；批准前不进入 Implementation。
- 验收锚点：Review 通过 → 进入 Implementation（须先建 feature branch，见 §8）。

---

## 1. Change Classification（变更分级）

| 项 | 值 |
|---|---|
| 变更等级 | **Architecture / Runtime Control Plane Change**（按仓库 PROCESS.md §11.3 等价于 L3 级：涉及 Runtime lifecycle、budget/timeout 语义） |
| 变更类型 | 默认执行路径纠正（policy=None → governed）＋ 回归覆盖；**非新增能力** |
| 风险等级 | 中-高（行为契约变更；但默认值全部沿用冻结 M-Spec §7.4，无新机制） |
| 影响范围 | ① 所有生产任务提交路径的执行语义（由无治理 bare → 受治理 governed）；② 任务详情/事件的审计字段（policy_snapshot/counters_snapshot/terminal event 从缺省变为完整）；③ 相关冻结测试中「policy=None = bare」的断言（行为变更，按 D-Phase2-P2-1-001 更新）；④ **无 schema 变更、无迁移、无前端改动、无 Agent 业务逻辑改动** |

### 1.1 行为变化清单（必须对用户明示）

| 之前（bare） | 之后（默认 governed） | 用户可见影响 |
|---|---|---|
| 无 wall_clock deadline | 600s 默认 deadline → watchdog → `timed_out` | 静默挂起最长 10 分钟后终态化（现场「永久研搜中」被消灭）；**超 600s 的合法长任务将被终态化**（见 §4.3 风险与裁决项） |
| 无预算硬上限 | max_llm_calls/tool/search/agent_steps 生效 → `budget_exceeded` | 失控循环任务会被预算中止 |
| agent 异常被吞 → 误标 `completed` | governance re-raise → `failed(agent_failure)` | 真实失败不再显示为成功 |
| 终态不发 durable event | `finalize_with_event` 常发 terminal event | 事件时间线完整（task_started + terminal） |
| counters_snapshot 恒 NULL | 终态快照落库 | 可审计调用量 |

---

## 2. Current Runtime Flow（现状）

### 2.1 实际执行路径

```text
前端 POST /api/sessions/{sid}/tasks 或 POST /api/task
  （TaskRequest.policy 缺省 = None；前端从不发送 policy）
  → server._start_governed_task（server.py:194）
  → gov_service.submit_task(ctl, thread_id, query, policy=None)（service.py:42-72）
      → controller.create_task(... policy_snapshot={}, effective_limits={})
      → lifecycle_event(task_started)
  → asyncio.create_task(controller.execute(task_id, run_deep_agent(...), policy=None))
      → execute(policy is None) → **_execute_bare**（controller.py:484-531）
         ├─ 无 BudgetCounter / GovernanceCallbackHandler
         ├─ 无 watchdog / 无 wall-clock deadline（controller.py:488-531）
         └─ 无 terminal lifecycle event（bare 直接 terminalize，不发事件）
```

### 2.2 bare execution 触发原因（代码证据）

1. `app/api/server.py:140-147`：`TaskRequest.policy: dict = None`；注释声称「缺省由 governance 服务默认（M-Spec §7.4）」，但代码未实现默认注入；
2. 前端从不传 policy：`frontend/src/hooks/useDeepAgentSession.ts` `submitTask` → `api.ts createSessionTask(sessionId, query)`（无 policy 参数）；
3. `app/runtime/governance/service.py:42-72` `submit_task` 直接把 `policy=None` 透传给 `controller.execute`，**无 normalize**；
4. `app/runtime/governance/controller.py:484-486`：`if policy is None: return await self._execute_bare(...)`。

### 2.3 governed execution 缺失点（相对目标态的差距）

- 生产路径不存在任何「policy 解析/默认注入」层；
- `_execute_governed`（controller.py:546-681）能力已完备（BudgetCounter + watchdog + 异常映射 + event + run_id 绑定），但**仅当调用方显式传 policy 才会被触发**；
- 后果即审计结论：静默挂起 → RUNNING 永久、无 heartbeat/事件、无法诊断（`RUNTIME_OBSERVABILITY_AUDIT_REPORT.md` §1/§2/§6）。

---

## 3. Target Runtime Flow（目标态）

### 3.1 Policy 默认注入位置（唯一收敛点）

```text
Task Request（HTTP/WS 提交，policy 可能为 None / {} / 显式 dict）
  → gov_service.submit_task
      → [NEW] policy = normalize_policy(policy)      ← 注入点（§3.2）
      → controller.create_task(policy_snapshot=policy, effective_limits=policy)
      → controller.execute(task_id, coroutine, policy=policy)   ← 必走 _execute_governed
  → governed execution（watchdog / budget / 异常映射 / terminal event）
```

- **注入点选在 `gov_service.submit_task`**（而非 `controller.execute` 内部）：所有生产提交（legacy `/api/task` 与 session-scoped 端点）都经 service 收敛；`controller.execute(policy=None)` 的 Step2 bare 语义**保持不变**，供 unit test / local debugging / internal development 直接调用（D-Phase2-P2-1-001：bare 保留但生产禁止）。
- `controller.execute` 不做防御性默认（避免把裸调用悄悄改成 governed 造成测试/内部工具行为漂移）；**生产禁止 bare 由提交层保证**。

### 3.2 normalize_policy 规则（新增 helper）

| 输入 | 输出 | 规则 |
|---|---|---|
| `None` / `{}` / 缺省 | `DEFAULT_GOVERNED_POLICY`（完整副本） | 注入全部默认值 |
| 显式 dict | 默认值 + 显式键覆盖 | 顶层合并：显式键优先；未知键透传（惰性，不参与 enforcement）；max_* 必须为 ≥0 int，否则拒绝（fail-closed） |
| 显式 `wall_clock_timeout` | 下界钳制 | `≤0` → 拒绝（**禁止显式关闭 watchdog**，堵住「传 0 逃逸治理」的洞） |

`DEFAULT_GOVERNED_POLICY`（单一来源常量，注释引用 M-Spec §7.4 冻结表，controller 现有默认值同源）：

```python
DEFAULT_GOVERNED_POLICY = {
    "wall_clock_timeout": 600,     # M-Spec §7.4
    "max_agent_steps": 200,
    "max_llm_calls": 120,
    "max_tool_calls": 300,
    "max_search_calls": 40,
}
```

> P2-1 不新增 env 覆盖（`RUNTIME_WALL_CLOCK_TIMEOUT` 等 knob 归 P2-2，D-Phase2-P2-2-002）；常量与 controller 现有 `DEFAULT_WALL_CLOCK_SECONDS` / `DEFAULT_LIMITS` 保持一致并加同源断言测试。

### 3.3 Production Execution 约束

1. 生产 HTTP/WS 提交（`/api/task`、`/api/sessions/{sid}/tasks`）**一律 governed**；
2. 判定依据（可测试）：submit 后 TaskRecord `policy_snapshot` 必为完整默认/归一 policy（不再为空），`controller._handles` 对应 execution 必带 watchdog；
3. bare 路径保留仅限：直接调用 `controller.execute(task_id, coroutine)` 的单元测试 / 本地脚本 / internal dev 工具；任何经 `gov_service.submit_task` 的调用都不可能落入 bare。

### 3.4 Lifecycle Guarantee（P2-1 交付的保证）

- 每条生产任务从提交即进入 `_execute_governed`：单调 deadline（默认 600s）+ 1s watchdog（controller.py:683-716）；
- **正常返回** → funnel `completed` + durable `task_completed`；**异常** → `failed(agent_failure)`（run_deep_agent governance-active re-raise，main_agent.py:259-268）；**GraphRecursionError** → `failed(framework_recursion_safety)`；**预算超限** → `budget_exceeded`；**watchdog 到点** → `timed_out`；**cancel** → `cancelled`；
- 由此保证：**不存在「无任何外部干预且超过 wall_clock 仍 RUNNING」的生产任务**（P2-1 边界内；sync tool linger 的进程内线程残留属文档化现象，见 controller.py §linger，不在本批收敛）。
- 不包含（P2-2+）：heartbeat / stale / reclaim / startup sweep / durable step event / transcript / title（按批准范围隔离）。

---

## 4. Compatibility Analysis（兼容性）

### 4.1 对 F8 / F9 冻结契约

| 冻结面 | 影响 | 结论 |
|---|---|---|
| `TaskStatus` 枚举 / TERMINAL_STATUSES（models.py） | 零改动 | ✅ 不动 |
| terminal funnel / 内存裁决 / CAS / pending 语义（controller.py） | 零改动 | ✅ 不动 |
| `execute(policy=None)` Step2 语义 | **保持**（bare 仍可达，仅测试/开发使用） | ✅ 行为契约按 D-Phase2-P2-1-001 变更的是「提交层默认」，非 execute 本身 |
| BudgetCounter / GovernanceCallbackHandler（counters.py/callbacks.py） | 复用，零改动 | ✅ |
| `governance_events` 单写者 / `(task_id,seq)` / replay cursor | 零改动；生产任务从此会多写 terminal event（observation，additive） | ✅ 不破坏 replay |
| schema / migration / store | **零改动（本批无迁移）** | ✅ |
| F9 / research plane / orchestrator / eval harness | 零改动（eval 本就显式传 policy，见 `research/eval/harness.py:96/164`） | ✅ |
| 冻结 spec 文档 | Step2「不传 policy = bare」的文字描述仍准确（execute 层未变）；**需补一句**「生产提交层自 P2-1 起注入默认 policy」 | 文档同步（Implementation 阶段随 diff 更新冻结 spec 备注或新增 D 记录） |

### 4.2 对 Harness 组件（AGENTS/PROCESS/PROJECT_CONTEXT 等）

- 不修改任何 Harness 文件；
- 按用户裁决流程：本 Spec = L1 Spec Review 产物（可留在 main 的 Readiness/Plan 类文档，AGENTS.md §10.2）；
- Implementation 阶段**必须**先切 `feature/runtime-p2-1-policy-enforcement` 分支（main 冻结，AGENTS.md §10.1），并按 L3 流程 Gate 推进；Decision（D-Phase2-P2-1-001 等）在 Decision Gate 登记入 `DECISION.md`（届时需用户批准写文档变更）。

### 4.3 对已有 Agent 能力 / 用户可见行为

- Agent 能力（模型、prompt、topology、工具、subagents、checkpoint、research 接线）**全部不变**；
- 用户可见变化：失败不再伪装成功；失控/挂起任务获得终态与原因；**可能影响点**——默认 600s wall clock 对「深度多轮合法研究」偏紧，超过将被 `timed_out` 终态化。

### 4.4 风险与缓解

| # | 风险 | 缓解 |
|---|---|---|
| R1 | 600s 默认超时误伤合法长任务 | 调用方可显式传更大 `wall_clock_timeout`（normalize 支持显式覆盖）；默认值保持 M-Spec 冻结（用户裁决）；观察期后属 Runtime Policy Tuning（后续独立 batch，非 P2-1/P2-2） |
| R2 | 显式传 `wall_clock_timeout=0` 逃逸治理 | normalize 下界钳制，`≤0` 拒绝（fail-closed） |
| R3 | 现有测试断言「policy=None ⇒ bare/无 snapshot」翻红 | 按 D-Phase2-P2-1-001 语义更新受影响用例（提交层），execute 层裸调用用例保留 |
| R4 | 回归遗漏（某些内部直连 controller 的调用被误认为生产） | grep 全量调用方清单（§5.1）纳入 Review diff 范围；仅 server→service 一条生产入口 |
| R5 | 长任务 `timed_out` 后前端只显示原因，无中间记录 | 属 P2-2/P2-3 范围（heartbeat/事件落库）；P2-1 已消除「无终态」本身 |

---

## 5. File Impact（文件影响）

### 5.1 修改文件（最小集）

| 文件 | 改动 |
|---|---|
| `app/runtime/governance/service.py` | `submit_task` 增加 policy normalize 调用；`policy_snapshot/effective_limits` 与 `execute(policy=...)` 全部使用归一后 policy；补充 docstring 说明生产默认 |
| `app/runtime/governance/__init__.py`（如需要） | 仅 re-export DEFAULT_GOVERNED_POLICY / normalize_policy（供测试与 server 读取），可省略 |
| 受影响测试 | `tests/test_runtime_governance_step4_batch1.py`、`tests/test_runtime_governance_controller.py`、`tests/test_runtime_governance.py`（如有）中「submit policy=None ⇒ bare/空 snapshot」断言 → 更新为「submit ⇒ governed + 默认 snapshot」（行为契约变更依据 D-Phase2-P2-1-001）；新增测试见 §6 |

### 5.2 新增文件

| 文件 | 内容 |
|---|---|
| `app/runtime/governance/policy.py`（建议） | `DEFAULT_GOVERNED_POLICY` 常量（与 controller 同源断言）+ `normalize_policy(policy) -> dict`（§3.2 规则 + fail-closed 校验 + 类型/边界处理） |
| `tests/test_runtime_policy_enforcement.py` | P2-1 定向测试（§6） |

> 若实现评审认为 policy 常量并入既有 `counters.py`/`controller.py` 更小，可合并；原则：**单一默认值来源 + 单一 normalize 函数**，禁止两处定义默认表。

### 5.3 禁止修改文件（红线）

- `app/agent/**`（main_agent / llm / prompts / subagents）—— Agent 业务逻辑零改动；
- `app/research/**`、`app/runtime/governance/models.py`（TaskStatus）、`store.py`/migrations/schema、`checkpoint.py`、`app/api/monitor.py`、`app/session/**`、`frontend/**`；
- 冻结 spec / Harness 治理文件（除 §4.1 文档备注外，须单独批准）。

---

## 6. Test Plan

> 测试纪律：不新增 Agent 能力；只验证 runtime 路径；sqlite 定向 + 关键用例 PG 双后端（仓库 Gate 纪律）。

### 6.1 单元（policy normalize）

| 用例 | 断言 |
|---|---|
| `normalize_policy(None)` / `normalize_policy({})` | 返回 `DEFAULT_GOVERNED_POLICY` 完整副本（深拷贝，不共享可变对象） |
| 显式键覆盖 | `{max_llm_calls: 5}` → 其余默认、llm=5 |
| 非法 `max_*`（负数/非 int/未知类型） | 抛错（fail-closed） |
| `wall_clock_timeout ≤ 0` | 抛错（禁止关 watchdog） |
| 默认表与 controller `DEFAULT_LIMITS` / `DEFAULT_WALL_CLOCK_SECONDS` 同源 | 断言相等（防漂移） |
| 未知键 | 透传（惰性、不参与 enforcement）且不抛错 |

### 6.2 集成（提交路径必 governed）

| 用例 | 断言 |
|---|---|
| `gov_service.submit_task(policy=None)`（替身 runner 正常返回） | TaskRecord `policy_snapshot == defaults`；执行结束 status=`completed`；**durable `task_completed` 事件存在**（bare 之前无） |
| 同上 + runner 抛异常 | status=`failed`、`error_kind=agent_failure`（不再吞错误标 completed） |
| 同上 + 替身挂起（sleep 超长，测试缩时 wall clock） | watchdog 在 deadline 后收敛 `timed_out`（证明默认注入后 watchdog 常开） |
| 同上 + runner 触预算超限（如 max_llm_calls=1） | status=`budget_exceeded` + counters_snapshot 落库 |
| `POST /api/task` / `POST /api/sessions/{sid}/tasks`（无 policy body，HTTP 层） | 返回 200；随后 GET /api/tasks/{id}：policy_snapshot=defaults（端到端证明生产路径无 bare） |
| 直接 `controller.execute(task_id, coroutine)`（policy=None，dev 语义） | 仍走 bare（Step2 冻结语义保留）——仅测试/内部使用 |
| 显式传 `wall_clock_timeout=0` | 提交被拒（4xx/失败），任务不启动 |

### 6.3 回归

- 既有 governance 全套：`test_runtime_governance*.py`（sqlite + PG 双 DSN）；
- `tests/test_sessions_*.py`（session 端点回归，无 policy body 场景被 6.2 HTTP 用例覆盖）；
- research/eval 回归（确认 eval harness 显式传 policy 路径零变化）；
- 手动 E2E：真实提交一条研究任务 → 观察 TaskRecord/事件/前端状态；不再出现「无终态研搜中」。

---

## 7. Rollback Plan（回滚方案）

- **无 schema/迁移/数据变更** → 回滚 = 代码回退，零数据迁移成本。
- 步骤：
  1. revert `service.py`（去掉 normalize 调用）＋ 删除 `policy.py` ＋ 还原受影响测试（保留 P2-1 新增测试文件即可，标记 skip 或移除）；
  2. 重新部署；校验提交路径恢复原语义（可选快速验证：submit policy=None → snapshot 为空 + 无 terminal event）；
  3. 中间态遗留数据说明：P2-1 运行期间产生的 TaskRecord 均为 governed 行（含可能 `timed_out` 终态）——回滚后仍为合法终态，无需清洗；RUNNING 行只可能来自「回滚前已启动且仍在跑」的任务，重启后按既有（无 reclaim）行为处理（P2-2 解决）。
- 回滚判据：若上线后发现 600s 默认导致合法任务大面积 `timed_out`（R1），先回滚再走 Runtime Policy Tuning 批次（不动默认值本身）。

---

## 8. Gate 与后续动作

1. **本 Spec 交 Review**（当前阶段终点）；Review 通过前**不实现**；
2. Review 通过 → Implementation 前置：创建 `feature/runtime-p2-1-policy-enforcement` 分支（main 冻结）；在 Decision Gate 登记 D-Phase2-P2-1-001（+关联记录）入 DECISION.md（该文档变更需另行批准）；
3. Implementation（最小 diff，仅 §5 文件）→ Verification（§6 全绿 + PG 双 DSN + 回归）→ Review → Freeze；
4. 产出 `P2-1_IMPLEMENTATION_REPORT.md` 并等待用户 Freeze；
5. P2-2（heartbeat/stale/reclaim）与后续批次另起 Spec（用户裁决的 D-Phase2-P2-2-00x / P2-5-001 届时细化）。
