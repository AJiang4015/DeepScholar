# P2-1 Implementation Report — Runtime Policy Enforcement

- 分支：`feature/runtime-p2-1-policy-enforcement`（自 `main @ af7eb02` 创建）
- 状态：**IMPLEMENTATION COMPLETE → 等待 Review + Freeze**（未 commit / push / merge）
- 决策：`D-Phase2-P2-1-001`（已登记入 `DECISION.md`）
- 依据：`P2-1_RUNTIME_POLICY_ENFORCEMENT_SPEC.md` → `P2-1_IMPLEMENTATION_PLAN.md`（Plan Review 批准后按 Step 1-3 执行）
- Scope 遵守：仅 P2-1；未触碰 P2-2 heartbeat/stale/reclaim、P2-3 durable timeline、P2-4 admin runtime、P2-5 title generation；未改 `app/agent/**`、`app/research/**`、`frontend/**`、TaskStatus enum、schema/migration。

---

## 1. 实际修改文件

### 1.1 修改（tracked diff）

| 文件 | 内容 | 行数 |
|---|---|---|
| `app/runtime/governance/service.py` | `submit_task` 增加 `normalize_policy(policy)` 注入（位于 runner 解析**之前**，非法 policy 不触发 agent/LLM 导入）；`policy_snapshot`/`effective_limits` 落归一后完整 policy；docstring 声明生产必 governed | +21 |
| `app/api/server.py` | import `PolicyValidationError`；`run_task` 与 `create_session_task_api` 捕获 → HTTP 400（fail-closed，任务不启动） | +14 |
| `DECISION.md` | 登记 `D-Phase2-P2-1-001`（默认 Runtime Policy 强制化：policy=None ⇒ governed） | +37 |

### 1.2 新增（untracked，属本实现）

| 文件 | 内容 |
|---|---|
| `app/runtime/governance/policy.py` | `DEFAULT_GOVERNED_POLICY`（单源：`wall_clock_timeout` 引用 `controller.DEFAULT_WALL_CLOCK_SECONDS`，`max_*` 值经 `controller.MAX_LIMIT_TO_COUNTER` 引用 `counters.DEFAULT_LIMITS`）+ `PolicyValidationError` + `normalize_policy`（None/{}→默认副本；显式键覆盖合并；`max_*` ≥0 int、`wall_clock_timeout` >0 校验；未知键透传） |
| `tests/test_runtime_policy_enforcement.py` | 23 用例：normalize 纯函数 11 + submit 路径 governed 7 + bare 保留 1 + 其它辅助 4（见 §3） |

### 1.3 明确未改（红线核对）

- `app/agent/**` / `app/research/**` / `frontend/**` / `app/runtime/governance/models.py`（TaskStatus）/ `store.py` / `migrations.py` / `db/**` / `checkpoint.py` / `monitor.py` / `app/session/**` —— 零改动。
- 工作树既有与 P2-1 无关的改动（`简历项目经历-深度研搜.md` 修改、`简历-*.html` 未跟踪）保持原样、不纳入本实现 scope。

---

## 2. Diff Scope Audit

- `git diff --stat`（tracked）：`DECISION.md` +37、`app/api/server.py` +14、`app/runtime/governance/service.py` +21；无其他 tracked 代码改动。
- `git diff -- app/api/server.py app/runtime/governance/service.py` 逐 hunk 审阅：仅计划内两处端点映射 + service normalize 注入；无投机重构/无关 hunk。
- `git diff --check`：通过（无 whitespace errors）。
- 新增文件仅 `policy.py` 与 `test_runtime_policy_enforcement.py`（另含本流程的三份文档：Audit Report / Spec / Plan）。
- 与 Plan 的差异（1 处，已在实现中同步口径）：**normalize 置于 runner 解析之前**（Plan 未指定顺序）。理由：非法 policy 应在任何 agent/LLM 导入与组装前即被拒绝（fail-closed 最早点）；合法请求行为不变。已由 23 用例 + HTTP 400 smoke 覆盖。

---

## 3. Test Execution Results

### 3.1 新增测试 `tests/test_runtime_policy_enforcement.py`

```
23 passed, 1 warning in 0.61s
```

覆盖（与 Plan §4.1 一一对应）：
- normalize：None/{} → 默认副本（非同一对象）；显式覆盖；`wall_clock_timeout=120`；`max_*` 负值/字符串/`True` 拒绝；`wall_clock_timeout` 0/-5/None/True 拒绝；非 dict 拒绝；未知键透传；默认表 = M-Spec 字面量；默认表与 `controller/counters` 常量同源。
- submit 路径必 governed：`policy=None` → `policy_snapshot/effective_limits == 默认表` + `completed` + durable `task_started/task_completed`；异常 → `failed(agent_failure)` + `task_failed`（不再吞成 completed）；缩时 watchdog 经 submit 生效 → `timed_out` + `counters_snapshot` + `task_timed_out`；预算替身 → `budget_exceeded` + `task_budget_exceeded`；显式 policy 与默认合并；非法 policy（`wall_clock_timeout=0`）→ `PolicyValidationError` 且无 Task 行/事件。
- bare 保留回归：`controller.execute(policy=None)` 仍 Step2 语义（completed、无 durable event）。

### 3.2 定向回归（Plan §4.3）

- governance + session + research 定向套件：**270 passed, 6 skipped**（PG 门控 skip，无 DSN 如实跳过）。
- 最终代码状态关键子集复跑：**76 passed, 3 skipped**（含 step4_batch1/audit/batch3、sessions、policy enforcement）。

### 3.3 全量 sqlite 回归（排除 MySQL 污染文件，PROJECT_CONTEXT §8 口径）

```
798 passed, 91 skipped in 51.97s      （最终代码状态复跑确认；两轮一致）
```

- PG 门控套件（`test_runtime_governance_postgres*.py` 等）：环境无 `AGENT_CHECKPOINT_DSN_TEST` / `RESEARCH_DSN_TEST` → 整类 skip（91 skipped 内），**如实报告，不伪造通过**。
- 排除：`tests/test_db_tools_mysql_integration.py`（已知进程内污染 OPENAI_API_KEY，见 PROJECT_CONTEXT §8）。

### 3.4 静态 / 卫生

- `python -m compileall -q app`：通过（uv 因沙箱 cache 权限不可用，用系统/venv python 执行）。
- `ruff check`（policy.py / service.py / server.py / 新测试）：**All checks passed!**
- `git diff --check`：通过。

### 3.5 HTTP 冒烟（server 400 映射）

- 方式：TestClient（隔离临时 governance sqlite DB，不进 lifespan、不启动 agent）。
- `POST /api/task` `{"query":"smoke","policy":{"wall_clock_timeout":0}}` → **HTTP 400**，detail 含 `wall_clock_timeout … 禁止关闭 watchdog`。SMOKE_OK。
- 说明：normalize 位于 runner 解析之前 ⇒ 400 路径无需 LLM/agent 环境即可确定性触发。

---

## 4. Verification Gate Results（Plan §6 checklist 逐项）

| 门禁 | 结果 |
|---|---|
| `test_runtime_policy_enforcement.py` 全绿（23 用例） | ✅ 23 passed |
| governance 定向回归套件 | ✅ 0 failed |
| session / research 回归 | ✅ 0 failed |
| 全量 sqlite 套件（排除 MySQL 污染文件） | ✅ 798 passed / 91 skipped / 0 failed（两轮） |
| PG 门控套件 | ⏭ 无 DSN 如实 skip（非伪造） |
| compileall / ruff | ✅ 0 error |
| `git diff --check` + scope audit | ✅ 通过；diff 仅含允许文件 |
| 行为验收锚点 | ✅ 见 §3.1（默认表注入 + watchdog + terminal event；异常→failed；裸 execute 保留；非法 policy→400 且不建任务） |
| `P2-1_IMPLEMENTATION_REPORT.md` | ✅ 本文档；**等待用户 Freeze，不自行 commit/merge** |

---

## 5. Known Limitations

1. **真实 provider E2E 未跑**：真实 LLM/Tavily 链路的「静默挂起 → 默认 600s watchdog → timed_out」未在本环境验证（无有效凭据/网络，仓库既有真实 provider E2E 凭据受限限制）。watchdog 行为由缩时替身测试（0.05s）证明路径生效。
2. **HTTP 200 默认 policy 端到端未跑**：会启动真实 governed agent run（依赖 provider），故仅以 service 层测试（case 9）证明默认注入；HTTP 层仅做 400 fail-closed smoke。计划中该用例即标记 optional。
3. **PG 双后端 gate 未跑**：无 DSN 环境，如实 skip（不影响——本批无 schema/migration，逻辑与 dialect 无关）。
4. **uv 不可用**：`uv run` 因沙箱 cache 目录权限失败（`C:\Users\AJiang\AppData\Local\uv\cache` 拒绝访问），改以 `.venv` python 执行 pytest/compileall（TESTING.md 允许系统 Python 路径）。

---

## 6. Remaining Risks

| # | 风险 | 状态 / 缓解 |
|---|---|---|
| 1 | 600s 默认 wall clock 对超长合法研究任务偏紧 → 误 timed_out | Spec/Plan R1：显式 policy 可覆盖；默认值按用户裁决冻结（M-Spec §7.4）；观察后调参属后续 Runtime Policy Tuning（独立批次），**本批不改默认**；若试运行出现大面积误伤 → 停 + 上报 + 回滚（纯代码 revert） |
| 2 | 既有调用方（如直接 `controller.execute` 的脚本/工具）语义不受影响，但任何**新**生产提交若绕过 `gov_service.submit_task` 仍可能 bare | 现状唯一生产提交入口即 service（server 两端点已核实）；如需强制可在 controller 层设防（属未来加固，不属 P2-1） |
| 3 | `policy_snapshot/effective_limits` 从空变为默认表，可能影响下游对「空 snapshot」的弱假设 | 全量回归 0 failed；无断言依赖空 snapshot |
| 4 | 未知 policy 键透传（惰性、不参与 enforcement） | 符合 Spec §3.2；controller 忽略额外键 |
| 5 | 异常语义变化（agent 失败不再显示 completed） | 属批准的行为变化（spec §1.1 / D-Phase2-P2-1-001）；回归覆盖 |
| 6 | 回滚 | 无 schema/迁移 → revert 提交集即回滚；中间态 TaskRecord 均为合法终态，无需数据清洗 |

---

*Scope 复核：本实现仅含 §1 文件；未 commit / push / merge；未自行进入 P2-2。等待用户 Review + Freeze。*
