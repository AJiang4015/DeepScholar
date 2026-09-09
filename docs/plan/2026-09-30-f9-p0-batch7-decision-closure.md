# F9-P0 Batch 7 — Eval / Behavioral Quality Validation：Decision Closure

> 状态：**DECISION CLOSED — D1–D6 全部裁决（2026-09-30 用户批准）；Batch7 = Eval /
> Behavioral Quality Validation（L2）。Readiness = CLOSED；Implementation = NOT STARTED。**
> 依据：用户 2026-09-30 指令（Batch6 Freeze + Batch7 只做 Readiness + 本次 Decision Closure）、
> Batch7 Readiness Review `docs/plan/2026-09-30-f9-p0-batch7-readiness-review.md`
> （CONDITIONAL，§5 Required Decisions D1–D6）、F9-P0 Spec Rev2 §14 Eval Acceptance。
> 本文件 = Decision Closure 记录；不实现 Eval、不修改 F8/F1–F7/Batch1–6、不建 implementation
> branch（Git Workflow：AGENTS.md §10 / PROCESS.md §12 生效中）。

## 1. D1 — Batch 编号（CLOSED）

- 当前 **Batch7 = Eval / Behavioral Quality Validation**（用户 2026-09-30 定义）。
- 原 Plan Rev2 Batch7（F7 Seam Verification）的验收目标，视为**已由 Batch6 D6 的 F7-once
  adversarial matrix 覆盖并冻结**（Batch6 report §6 Matrix #1–#8 全 controller-level）。
- **不再单独创建**原编号 Batch7 的 F7 seam implementation batch。

## 2. D2 — Baseline 对照入口（CLOSED：Option (a) 跨 execute 对照）

- Baseline = `run_deep_agent`（既有产品入口）；Adaptive = `f9_orchestrator`（Batch6 frozen）。
- 两者**分别由各自独立的 `Controller.execute(...)` 执行**；使用相同 benchmark task
  specification、相同 F8 policy、相同 deterministic world。
- **不在同一个 task_id / run_id 中顺序执行 baseline + adaptive**（不违反"单 execute/单 run"
  冻结语义；比较为跨 run / 跨 execute A/B）。
- **禁止**为 Batch7 给 orchestrator 增加 baseline-only mode；**不修改** Batch6 frozen
  orchestrator 行为。

## 3. D3 — Glue Fairness（CLOSED）

- **不修改** Batch6 frozen `main_agent.py` / `orchestrator.py` 以人为消除 glue 差异。
- **deterministic scripted benchmark = Batch7 Gate 主路径**（可复现、可断言）。
- real-provider 结果仅作为 **non-gate supplementary evidence**。
- 报告必须明确记录 baseline/adaptive graph-entry glue 差异（run_deep_agent 带
  path_instruction/session-dir；orchestrator graph runner 仅 task prompt）及其对真实模型
  归因的限制。
- 该差异**不自动升级 L3**；仅当后续实现确实证明无法完成 Eval → STOP 并重新 Decision
  Closure。

## 4. D4 — Persistence（CLOSED）

- **Batch7 不新增 schema/migration**。
- rounds/eval metrics **不进入数据库**：
  - rounds 使用 orchestrator summary（返回值收集）；
  - research metrics 从现有 Research Registry / projection **只读**获取；
  - cost 从 TaskRecord `counters_snapshot` 获取。

## 5. D5 — Quality Evaluator（CLOSED）

- Batch7 Gate 的 **primary quality evaluator 必须是 deterministic / rubric-based**。
- 重点评估（确定性指标）：required coverage、citation coverage、evidence sufficiency、
  unsupported claim penalty、conflict coverage 等。
- `answer quality >= baseline` **不得**仅由一个 real LLM judge 决定。
- real-LLM quality judge 如实现，仅作为 **non-gate supplementary signal**，单独报告。

## 6. D6 — Scenario Set（CLOSED）

- 第一版使用 **6–8 个 deterministic scripted scenarios**；**不新增数据库 dataset schema**。
- 第一版优先使用代码内 / 测试 fixture；如确有必要再提出固定 dataset 文件，但**不在未批准
  情况下扩大 scope**。
- 场景必须同时覆盖 **positive 与 negative/control cases**；至少覆盖：
  1. already-sufficient evidence / no unnecessary follow-up
  2. missing required coverage → targeted follow-up
  3. insufficient/unverified claim → re-verification
  4. conflict → additional research / reconciliation
  5. insufficient independent corroboration
  6. diminishing return / correct stopping
  7. near-budget stopping
  8. Judge/Plan failure → same-execution baseline fallback

## 7. L-level（CLOSED）

**L2 — Eval / Behavioral Quality Validation**。前提：

- 不修改 F8 / F1–F7 / Batch1–6 frozen semantics；
- 不修改 `main_agent.py` / `orchestrator.py`；
- 不新增 migration/schema；
- 不新增 runtime infrastructure。

若 implementation 过程中发现上述前提无法满足 → **STOP → 记录 Compatibility Gap → 重新
L3 Decision Closure**。

## 8. Scope（Batch7 Implementation 候选，未开始）

- In：eval harness（构造任务 → 注入同一 F8 policy + scripted 依赖 → baseline 与 F9 各自
  独立 execute → 指标收集）；deterministic scripted benchmark 6–8 scenarios；rubric-based
  quality evaluator；指标报告 / 断言；Implementation + Verification Report。
- Out：修改 run_deep_agent / main_agent / orchestrator / F8 / F1–F7 / Batch1–6；新增
  schema/migration/infrastructure；_govadapt real-provider 实现；durable round state；
  Claim–Evidence Graph；Redis/Kafka/Neo4j/Vector；polling/status-sync；UI；F9-P1/P2。
- Git Workflow：Implementation 必须发生在 **feature/f9-batch7-eval**（Readiness/Plan/本
  Decision Closure 文档可留 main 基线，不代表 Implementation 开始）。

## 9. Read-only Scope / Contract Check（本文件阶段已执行）

- 本阶段零代码改动（只新增本文件 + PROJECT_CONTEXT 状态同步，见下）；未触碰 F8/F1–F7/
  Batch1–6 实现、测试或冻结文档。
- 已核：D2 跨 execute 对照不触碰"单 execute/单 run"语义（readiness §2/§3）；D4 全部指标
  可从事务库 + orchestrator summary + TaskRecord counters_snapshot 只读获取（readiness §3
  表）；D3/D5 以 deterministic/rubric 为主、real 仅 supplementary（与 Spec §14 / Batch1–6
  凭据纪律一致）。
- 未发现需修改 frozen contract 的硬缺口；无 COMPATIBILITY GAP。

## 10. Final Decision

```text
F9-P0 Batch 7 — Eval / Behavioral Quality Validation
Readiness = CLOSED（D1–D6 Decision Closure 完成，2026-09-30）
L-level = L2
Implementation = NOT STARTED
```

等待用户批准进入 Batch7 Implementation（届时按 Git Workflow 创建 `feature/f9-batch7-eval`，
并在该 branch 完成 Implementation → Verification → Review → User Freeze → Scope Audit →
User approval → Commit → Push → Merge main）。
