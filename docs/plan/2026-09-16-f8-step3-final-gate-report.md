# F8 Step 3 Final Gate Report（Phase F — PostgreSQL）

> 状态：F8 Step 3 全链 Final Gate 记录（controller-governed `run_deep_agent`）。
> 语义依据：`docs/spec/2026-09-15-f8-step3-runtime-budget-control.md`（FROZEN）。
> PostgreSQL = 唯一 Production Baseline / Final Gate Backend；SQLite 仅快速回归。

## 1. Environment

- PostgreSQL：**16.15**（Debian，远端 lab `192.168.127.101:5432`，测试库
  `deepscholar_checkpoint_test`；governance **独立表族**，不触碰 checkpoint/research 官方表）。
- DSN 来源：`AGENT_CHECKPOINT_DSN_TEST`（环境注入；本报告不落明文凭据）。
- Python / 包：python 3.12.13；deepagents 0.5.7；langchain 1.2.17；langchain-core 1.3.3；
  langgraph 1.1.10；langgraph-checkpoint 4.0.3；psycopg（venv）。运行：`uv run --with pytest`。

## 2. Test Matrix（Layer 3 — PostgreSQL）

| 项 | 测试 | count | PASS |
|---|---|---|---|
| F1 CAS race（补 completed×budget、completed×timeout；其余组合由 Step 2 PG gate 覆盖） | `test_runtime_governance_postgres_final.py::TestF1CasRacesPG`（2×并发真 CAS） | 2 | ✅ |
| F2 durable write failure→pending→flush 恢复；c2 control failure | `TestF2PersistenceFailurePG` | 2 | ✅ |
| F3 startup 遗留 running→aborted 收敛（冻结 funnel 组合；terminal 不被改） | `TestF3StartupReconciliationPG` | 1 | ✅ |
| F6/F7 isolation/unique/start 不覆盖/migration 幂等/表族隔离 | `TestF6F7IsolationPG` | 2 | ✅ |
| F4/F5 run_deep_agent 全链矩阵（normal/llm/tool/search/subagent/timeout/cancel/recursion/failure）+ F7 边界 | `TestF4F5RunDeepAgentPG::test_matrix`（9 场景） | 9 | ✅ |
| 复用 Step 2 PG gate 9 + Step1/2 PG 镜像 8 | `postgres_gate.py` + `postgres.py` | 17 | ✅ |
| **PG 合计** | | **33** | ✅ 0 failed |

（F1 的 completed×cancel、budget×timeout、supersede×cancel 与真并发编排已有 Step 2 Gate 的真实 PG16 证据
——`f8_step3_evidence_phaseB/P1` 与 `test_runtime_governance_postgres_gate.py`，本 Gate 不重复制造。）

## 3. PostgreSQL Evidence 摘要

- **CAS rowcount**：每组真并发 race 恰一个 `rowcount==1`、至少一个 `rowcount==0`（store recorder）；
  `version 0→1` 恰一次；loser 采纳 DB 事实；无第二次成功 UPDATE。
- **version transition / final status**：全部场景 `version==1`、单终态（completed/budget_exceeded/
  timed_out/cancelled/failed[framework_recursion_safety|agent_failure|governance_control_failure]/
  aborted）。
- **race winner**：final DB status == durable winner reason（recorder label 定位）。
- **persistence recovery**：PG durable 写故障 3 次 retry → pending/degraded（内存裁决权威、真实 PG 行
  保持 running/v0）→ 恢复后 flush 恰一次 transition、重复 flush 幂等。
- **startup aborted reconciliation**：遗留 running（owner=inst-crashed）→ 新实例 funnel(ABORTED)
  version=1；已 terminal 任务不被重复修改；无 fake research/F7。
- **F7 invocation count**：normal=exactly once；其余 8 类治理终止=0（矩阵逐行断言：finalize_calls==0、
  无 task_result、非 completed）。

## 4. Regression

- SQLite（Layer 1+2 快速回归）：Step1 9 + Step2 controller 35 + PhaseA 13 + PhaseB 20 + PhaseC 7 +
  PhaseD 12 + PhaseE 12 = **108 passed / 0 failed**（venv）。
- PostgreSQL：**33 passed / 0 failed**（真实 PG16，见 §2）。
- ruff check / format：全绿；compileall 通过。

## 5. Scope Audit

- 变更文件：`tests/test_runtime_governance_postgres_final.py`（新增，Layer3 PG Gate）、
  `tests/_step3_repo_harness.py`（store_kind='postgres' 测试基建）、
  `docs/plan/2026-09-16-f8-step3-final-gate-report.md`（本报告）、scratch 取证。
- **生产代码零改动**；无 migration 变更；无依赖变更；F1–F7 / Step 1–2 funnel / Phase A–E 语义冻结确认。
- 未实现 sequencer/replay/sweeper(产品)/adapter/API/multi-instance/Phase G。

## 6. Limitations

- 真实 LLM credential 不可用 → 无 real-provider E2E；Layer 3 集成用 ScriptedChatModel（继承
  BaseChatModel，真实 callback/metadata 链路），**不伪造 provider 证据**；Q1 usage_metadata 保持
  observation-only。
- OS 级 process crash 无法在此环境真实模拟 → "startup reconciliation" 以"旧实例 running 行 + 新
  controller 实例经冻结 funnel 收敛 aborted"模拟（语义证据充分）；生产 startup hook/sweeper 模块属后续
  Step，非本 Gate 范围。
- 真并发 race 用两线程/独立 store 实例在单机进程内验证 PG 行锁仲裁；多进程/多实例 ownership 明确
  不属于 F8（AC13/OQ4）。

## 7. Final Decision

**PASS** —— 真实 PG16 下，controller-governed 的 `run_deep_agent` execution 在正常完成、预算耗尽、
超时、取消、递归安全、普通异常、并发 terminal race、durable write failure、restart 收敛等情形下，均
可靠收敛到唯一、可审计、可恢复的 TaskRecord terminal state（version 恰 +1、单 winner、pending→flush
可恢复、aborted 兜底），且只有正常完成进入 F7。
