# P2-1 Runtime Policy Enforcement — Implementation Plan

- 文档类型：Implementation Plan（**Planning 阶段产物；当前禁止修改代码 / 建分支 / 改测试**）
- 上游：`RUNTIME_OBSERVABILITY_AUDIT_REPORT.md`（L0 Audit，批准）→ `P2-1_RUNTIME_POLICY_ENFORCEMENT_SPEC.md`（L1 Spec，**Review 已批准**）
- 决策基线：`D-Phase2-P2-1-001`（policy=None ⇒ 默认 governed；bare 仅测试/本地/内部；默认值 = M-Spec §7.4，P2-1 不调参）
- 状态：**PENDING PLAN REVIEW** —— 本 Plan 完成后停止；Plan Review 批准后才允许：创建 `feature/runtime-p2-1-policy-enforcement` 分支 → 修改代码 → 执行测试。
- 实施基线：`main @ af7eb02`。

---

## 1. Implementation Scope

### 1.1 修改文件清单（预期全部改动）

| # | 文件 | 改动内容 | 影响 |
|---|---|---|---|
| 1 | `app/runtime/governance/service.py` | `submit_task` 内对入参 `policy` 先 `normalize_policy(policy)`（仅当 None/{} 时注入默认值；显式 dict 与默认值合并），随后 `create_task(..., policy_snapshot=policy, effective_limits=policy)` 与 `controller.execute(..., policy=policy)` 均使用归一后值；docstring 声明「生产提交必 governed」 | 行为变更（批准契约） |
| 2 | `app/api/server.py` | 两处任务提交端点（`run_task` L279-303、`create_session_task_api` L438-467）对 `PolicyValidationError` 做 400 映射（fail-closed 的客户端错误形态；不改任何其他语义） | additive，最小 |
| 3 | 既有测试 | **预期 0 修改**；仅执行「call-site 复核」清单（见 §4.2），若复核发现确有断言旧 submit 语义（policy=None ⇒ 空 snapshot / 无 terminal event）才改（当前 grep 结果：**无**） | — |

> 变更总量预估：service.py ≈ ±15 行；server.py ≈ ±8 行；policy.py ≈ 80 行；新测试 ≈ 300 行。

### 1.2 新增文件清单

| 文件 | 内容 |
|---|---|
| `app/runtime/governance/policy.py` | `DEFAULT_GOVERNED_POLICY`（单一默认值来源，引用 `controller.DEFAULT_WALL_CLOCK_SECONDS`(600) 与 `counters.DEFAULT_LIMITS` 避免双源漂移）＋ `PolicyValidationError` ＋ `normalize_policy(policy) -> dict`（None/{} → 默认副本；显式键覆盖；max_* 校验 ≥0 int；`wall_clock_timeout` 必须 >0（禁止 0 关闭 watchdog）；未知键透传不报错） |
| `tests/test_runtime_policy_enforcement.py` | P2-1 定向测试（§4.1） |

### 1.3 明确禁止修改文件（红线）

- `app/agent/**`（main_agent / llm / prompts / subagents）——Agent 业务逻辑零改动；
- `app/research/**`、`app/runtime/governance/models.py`（TaskStatus / TERMINAL_STATUSES）、`store.py`、`migrations.py`、`db/**/*.sql`、`app/runtime/checkpoint.py`、`app/api/monitor.py`、`app/runtime/governance/events.py`、`app/session/**`、`app/api/context.py`；
- `frontend/**`（本批无前端改动）；
- Harness 治理文件（AGENTS/PROCESS/PROJECT_CONTEXT/DECISION/TESTING）与冻结 Spec —— DECISION 登记须在 Decision Gate 单独批准后执行，本批不顺手改。

---

## 2. Change Mapping（Spec → 代码位置）

### 2.1 normalize_policy 注入点（唯一收敛点）

```text
Spec §3.1 / §3.2
  → app/runtime/governance/policy.py           [NEW]  DEFAULT_GOVERNED_POLICY + normalize_policy()
  → app/runtime/governance/service.py:50-71    [EDIT]  submit_task：
        policy = normalize_policy(policy)                # ← 注入点（create_task 之前）
        record = controller.create_task(..., policy_snapshot=policy, effective_limits=policy)
        task = asyncio.create_task(controller.execute(record.task_id, runner(...), policy=policy))
  → app/api/server.py:279-303 / :438-467       [EDIT]  PolicyValidationError → HTTP 400（不改提交语义）
```

### 2.2 Spec 条目 → 代码位置映射

| Spec 条目 | 代码位置 | 落实 |
|---|---|---|
| §3.2 `DEFAULT_GOVERNED_POLICY` | `policy.py`（引用 `controller.py:65 DEFAULT_WALL_CLOCK_SECONDS` + `counters.py:25 DEFAULT_LIMITS`） | 单源常量；同源由测试断言锁定 |
| §3.2 normalize 规则（None/{} / 覆盖 / max_* / wall_clock 下界） | `policy.py::normalize_policy` | 纯函数，可单测 |
| §3.1 注入点 = `gov_service.submit_task` | `service.py:42-72` | 唯一生产收敛点（server 两个端点都经它，`server.py:222/294/453`） |
| §3.3 production 必 governed、bare 保留 | `controller.py:484-486` **不改**；由提交层保证 | execute(policy=None)=bare 语义冻结保留（测试/本地/内部） |
| §3.4 lifecycle guarantee | 无需改 controller（能力已完备 `controller.py:546-681`） | 由「恒有 policy」触发 |

### 2.3 execution flow 变化（改动前后对照）

```text
改动前：
  TaskRequest(policy=None) → service.submit_task → controller.execute(policy=None)
      → _execute_bare：无 watchdog/deadline/budget/terminal event
      → agent 异常在 run_deep_agent 被吞（governance 非 active）→ 误标 completed
      → 静默挂起 ⇒ TaskRecord 永久 running（现场）

改动后：
  TaskRequest(policy=None) → service.submit_task
      → policy = normalize_policy(None) = DEFAULT_GOVERNED_POLICY   [新增]
      → controller.create_task(policy_snapshot=默认, effective_limits=默认)
      → controller.execute(policy=默认) → **_execute_governed**（恒真）
      → watchdog(600s)/budget/异常映射/terminal event 全生效
      → run_deep_agent governance-active → 异常 re-raise → failed(agent_failure)
```

---

## 3. Step-by-step Implementation Sequence

> 每步产出证据；任一步失败即停，修复后重入该步；全部步骤在 `feature/runtime-p2-1-policy-enforcement` 分支上进行（Plan Review 批准后创建）。

### Step 1 — 新增 policy 模块（无行为变化）

- 创建 `app/runtime/governance/policy.py`：
  1. `DEFAULT_GOVERNED_POLICY`：`{"wall_clock_timeout": <controller.DEFAULT_WALL_CLOCK_SECONDS>, **counters.DEFAULT_LIMITS}`（运行时引用常量，模块级组字典）；
  2. `PolicyValidationError(ValueError)`；
  3. `normalize_policy(policy: Optional[dict]) -> dict`：None/{}→默认副本（`dict()` 深拷贝，杜绝共享可变对象）；dict → `{**DEFAULT_GOVERNED_POLICY, **显式键}`；校验：已知 `max_*` 键必须为 int ≥0（bool 视为非法）；`wall_clock_timeout` 必须 int/float >0（含缺省/None）；未知键透传不校验；校验失败抛 `PolicyValidationError`（含键名与原因）。
- 本步不改任何调用方；运行 `uv run python -m compileall -q app` 通过。
- 退出条件：模块可导入、纯函数可调用（冒烟 REPL 断言 normalize(None) == 默认表）。

### Step 2 — 接线 submit_task + server 错误映射（行为变更点）

- `service.py`：
  - import `normalize_policy`；
  - `submit_task` 入口处 `policy = normalize_policy(policy)`（参数名就地归一，保持后续签名不变）；
  - `create_task(policy_snapshot=policy, effective_limits=policy)`（policy 此时必为完整 dict）；
  - `execute(..., policy=policy)`；
  - 更新模块 docstring：生产提交经 service 后必 governed；bare 仅直接 `controller.execute`（测试/内部）。
- `server.py`：
  - `run_task` 与 `create_session_task_api`：捕获 `PolicyValidationError` → `HTTPException(400, detail=str(exc))`（沿用 `_map_session_error` 同款风格或最小 try/except 包裹 `_start_governed_task`/`submit_task` 段）。
- 本步后行为已切换（提交层 policy=None 不再裸跑）。运行 `compileall` 通过；暂不跑全量（Step 3 补测试后统一验证）。
- 退出条件：diff 仅含上述两文件；代码评审自查（无投机改动）。

### Step 3 — 测试补齐 + 受影响用例复核 + 验证

- 新增 `tests/test_runtime_policy_enforcement.py`（用例清单见 §4.1，sqlite 后端；不新增 PG 专测——submit 接线与 dialect 无关，PG 回归由既有 governance PG 套件覆盖）；
- 执行 §4.2 复核清单（grep 现有 `submit_task`/`execute` call-site），**预期无既有用例需修改**；若有命中（断言 submit(None) 旧语义）→ 最小修改并记录于实现报告；
- 运行 §6 Verification Gate 全部门禁，收集输出证据；
- 退出条件：Verification Gate 全绿 + diff 自查 + scope audit 通过。

---

## 4. Test Implementation Plan

### 4.1 新增测试（`tests/test_runtime_policy_enforcement.py`）

**A. normalize_policy 单元（纯函数，无 store）**
1. `None` → 返回 `DEFAULT_GOVERNED_POLICY` 完整等价副本，且 `is not` 同一对象（深拷贝）；
2. `{}` → 同上；
3. 显式覆盖 `{"max_llm_calls": 5}` → 其余默认、llm=5（校验「默认+覆盖」合并）；
4. 显式 `{"wall_clock_timeout": 120}` → 120；
5. 非法 `max_llm_calls=-1` / `"3"` / `True` → `PolicyValidationError`；
6. `wall_clock_timeout=0` / `-5` → `PolicyValidationError`（禁止关 watchdog）；
7. 未知键 `{"custom": 1}` → 透传且不抛错；
8. 同源断言：`DEFAULT_GOVERNED_POLICY` == `{"wall_clock_timeout": 600, **counters.DEFAULT_LIMITS}`（防 controller/counters 常量漂移）。

**B. 提交路径必 governed（service 集成；复用 gov_tmp 风格 fixture，替换 `gov_service.run_deep_agent` 为受控替身）**
9. `submit_task(policy=None)` + 替身正常返回 → TaskRecord `policy_snapshot == DEFAULT_GOVERNED_POLICY`；终态 `completed`；**durable terminal event 存在**（bare 时代无——证明恒 governed）；
10. `submit_task(policy=None)` + 替身抛 `RuntimeError` → `failed` / `error_kind=agent_failure`（不再吞错误标 completed）；
11. `submit_task(policy=None)` + 替身挂起（缩时：注入 policy `{"wall_clock_timeout": 0.05}` 同语义）→ watchdog 收敛 `timed_out`；
12. `submit_task(policy=None)` + 替身触发预算（`{"max_llm_calls": 0}` 同语义）→ `budget_exceeded` + counters_snapshot 落库；
13. 显式 `submit_task(policy={"max_llm_calls": 5})` → snapshot=默认+覆盖合并值；
14. HTTP 端到端（可选，若可用 TestClient）：`POST /api/task` / `POST /api/sessions/{sid}/tasks`（body 无 policy）→ 200；随后 `GET /api/tasks/{id}` `policy_snapshot == DEFAULT_GOVERNED_POLICY`；
15. 非法 policy（如 `wall_clock_timeout=0`）经 HTTP → 400，且任务未创建；
16. **bare 保留回归**：直接 `controller.execute(task_id, coroutine)`（policy=None）仍 bare（Step2 冻结语义）——验证改动未误伤 dev 路径。

### 4.2 修改既有测试（复核清单；预期 0 修改）

当前全库 grep 结论（实现时复核）：
- `gov_service.submit_task` 现有 call-site **全部显式传 `policy={}` 或显式 dict**（`test_runtime_governance_step4_batch1.py:90/137/153/233/253`、`step4_audit.py:80/88/117/132/159/198`、`test_sessions_isolation.py:80/139`）→ `{}` 归一为默认值，仅 snapshot 内容变化；**无用例断言空 snapshot**；
- `policy=None → bare` 的唯一测试是 `controller.execute` 直调（`test_runtime_governance_step4_batch3.py:213-225 test_bare_execution_no_bridge`）→ **controller 层语义保留，不受影响**；
- `_step3_repo_harness.py:402 policy=None` 为 controller 直调 helper 默认 → 保留。
- 复核动作：实现时再跑一次相同 grep + `pytest` 定向套件，把「0 修改」结论写入实现报告；若出现意外翻红（例如某用例依赖 submit 后 counters_snapshot 为 NULL），按最小修改处理并记录。

### 4.3 回归测试范围

| 层级 | 命令 | 通过标准 |
|---|---|---|
| 定向（新测试） | `uv run pytest tests/test_runtime_policy_enforcement.py -q` | 全绿 |
| governance 定向回归 | `uv run pytest tests/test_runtime_governance.py tests/test_runtime_governance_controller.py tests/test_runtime_governance_step3_controller.py tests/test_runtime_governance_step3_integration.py tests/test_runtime_governance_step3_wiring.py tests/test_runtime_governance_step4_batch1.py tests/test_runtime_governance_step4_batch2.py tests/test_runtime_governance_step4_batch3.py tests/test_runtime_governance_step4_audit.py tests/test_runtime_governance_step4_f1.py tests/test_runtime_governance_budget.py tests/test_runtime_governance_callbacks.py -q` | 全绿（0 failed） |
| session 回归 | `uv run pytest tests/test_sessions_service.py tests/test_sessions_store.py tests/test_sessions_isolation.py -q` | 全绿 |
| 研究面回归（防 eval 路径漂移） | `uv run pytest tests/test_research_orchestrator.py tests/test_research_judge.py tests/test_research_targeted.py tests/test_research_plan.py -q` | 全绿 |
| 全量 sqlite | `uv run pytest tests/ -q`（排除 `test_db_tools_mysql_integration.py`，见 PROJECT_CONTEXT §8 污染说明） | 全绿（0 failed） |
| PG 门控（如 DSN 环境可用） | 既有 PG 套件（`test_runtime_governance_postgres*.py` 等，`AGENT_CHECKPOINT_DSN_TEST`/`RESEARCH_DSN_TEST`） | 全绿；环境缺失如实报告 skip，不伪造 |
| 静态 | `uv run python -m compileall -q app`；`ruff check app/runtime/governance/policy.py app/runtime/governance/service.py app/api/server.py tests/test_runtime_policy_enforcement.py`（不可用则如实报告缺口） | 0 error |
| diff 卫生 | `git diff --check` | 无 whitespace errors |

---

## 5. Risk Checklist

| # | 风险 | 缓解 / 判定 |
|---|---|---|
| 1 | **policy=None 行为变化**：submit 任务由 bare → governed（新增 watchdog/预算/terminal event；snapshot 从空→默认表） | 批准契约（D-Phase2-P2-1-001）；spec §1.1 行为变化清单已对用户明示；用例 9-12 锁定 |
| 2 | **600s 默认超时误伤合法长任务**（用户可见最显著变化） | 显式 policy 可覆盖；默认值冻结不改；观察后属 Policy Tuning（独立批次）。判定：若回归/试运行出现大面积 timed_out → 立即停并上报，不经本批改默认 |
| 3 | **回归风险**：既有测试/调用方依赖旧 submit(None) 语义 | grep 复核清单（§4.2）结论 = 0 命中；controller 直调 bare 测试保留；定向 + 全量回归门禁兜底 |
| 4 | **多源默认值漂移**（policy.py vs counters/controller 常量） | 运行时引用单源 + 同源断言（用例 8） |
| 5 | **显式传 `wall_clock_timeout=0` 逃逸治理** | normalize 下界校验拒绝（fail-closed）；HTTP 400 + 任务不启动（用例 6/15） |
| 6 | **接线遗漏**：存在绕过 service 的生产提交路径 | 代码 grep（`server.py` 仅两提交端点均经 service）+ Review diff 范围确认 |
| 7 | 改动面扩散（顺带改 agent/research/前端） | §1.3 红线 + scope audit（Git Scope Audit，AGENTS.md §10.6） |
| 8 | **Rollback** | 无 schema/迁移 → revert 提交集即回滚（service/server/policy.py/新测试按需移除）；中间态 governed 行均为合法终态无需清洗；判据：若 600s 默认引发大面积误伤（风险 2）优先回滚 → 再走 Tuning |

---

## 6. Verification Gate（实施完成后的门禁，逐项留证据）

- [ ] `tests/test_runtime_policy_enforcement.py` 全绿（16 用例，§4.1）
- [ ] governance 定向回归套件全绿（0 failed，§4.3 清单）
- [ ] session / research 回归全绿
- [ ] 全量 sqlite 套件全绿（排除 MySQL 污染文件；如实记录 skipped/排除）
- [ ] PG 门控套件（DSN 可用时）全绿；不可用 → 如实记录为验证缺口
- [ ] `compileall` / `ruff`（或如实报告不可用）0 error
- [ ] `git diff --check` 通过；diff 仅含 §1.1/§1.2 允许文件；红线文件零改动（`git diff --stat` + 文件清单比对）
- [ ] 行为验收锚点逐条核对（Spec §6 / 本 Plan §4.1 用例语义）：
  - 无 policy 提交 → snapshot=默认表 + watchdog 生效 + terminal event 存在；
  - 异常不再误标 completed → `failed(agent_failure)`；
  - 直接 `controller.execute(policy=None)` 仍 bare（dev 语义保留）。
- [ ] 产出 `P2-1_IMPLEMENTATION_REPORT.md`（含 diff 摘要、验证输出、风险/剩余项）并**等待用户 Freeze**（不自行 commit/merge）

> 本 Plan 为 Planning 阶段产物：仅设计，无代码/分支/测试改动。Plan Review 批准前不进入 Implementation。
