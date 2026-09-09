# F9-P0 Batch 6 — Round Orchestrator：Implementation Readiness Review

> 状态：**READINESS REVIEW（read-only）→ 结论 CONDITIONAL（见 Final Decision）**。
> 流程：PROCESS §11 L2 工作流第一阶段（Readiness Review only）；发现冻结 seam/架构边界问题 →
> 记录 Required Decisions，不自动进入 Plan/Implementation；若裁决路径需改 F8/F1–F7 冻结代码或
> 架构红线 → 依 PROCESS §11.3 Reclassify L3（见 §10 明确条件）。
> 范围：只读评估 Batch6（Round Orchestrator）可实施性；不改任何代码/tests/Spec/Plan/Harness/
> Batch1–5/F8/F1–F7。

---

## 1. Scope

Batch 6 = 单次 F8 governed execution 内把 Batch1–5 串成 adaptive research round：
round0 baseline → Projection → Gap → Judge → Plan → Targeted Research(+Verify) → 评估/
stopping → 下一轮或终止；normal completion → F7 finalization exactly once；普通 failure →
同 execute baseline fallback。不实现新 Runtime/Controller/Agent/task/ResearchRun/Budget 控制。

## 2. Frozen Baseline & 已核实的复用 seam（实读）

- F8：`Controller.execute(task_id, coroutine, policy)` 接受任意 coroutine；governed 路径绑定
  单 GovernanceExecution/BudgetCounter/watchdog/deadline/cancel（controller.py 已核实）。
- Batch1 `project` / Batch2 `detect_gaps` / Batch3 `judge_gaps` / Batch4
  `propose_followup_plans` / Batch5 `execute_targeted_plan` 均可在 governance-active ctx 内调用，
  显式 callback 计费（已实测各批）。
- F7：`app/research/bridge.py::finalize_run(run_id, final_content, …)`（public、幂等、fail-open）；
  **唯一既有接线点**在 `main_agent.run_deep_agent` astream 正常结束后（实读 main_agent.py）。
- research ctx：`app/research/context.py` set/get/reset；(run_id, sub_question_id) ContextVar。
- ARCHITECTURE.md §3 红线：MUST NOT 绕过 `run_deep_agent` 直接调 `main_agent.astream`
  （会话目录/ContextVar/monitor 初始化）。

## 3. 核心 Seam 审查：编排执行路径 vs F7-once（Batch6 最大 Readiness 风险）

Spec Rev2 §2.1/§2.2/§12：一次 governed execution 内允许**多次 graph invocation**，round 内
**禁止 F7**；F7 只在 orchestrator normal completion 后**恰一次**。Plan Rev2 R1/R2：
`run_deep_agent` 是 baseline execution primitive/fallback，由 orchestrator 内部调用；Final
Synthesis 是同一 execute 内最后一个图阶段。

**矛盾点（实读证实）**：`run_deep_agent` 每次调用都 ① `create_run_and_root`（新建 ResearchRun）、
② astream 正常结束后**立即**调 `finalize_run`（F7）并 `set_run_status(finished)`。若 orchestrator
在 adaptive 前用 run_deep_agent 跑 round0/fallback/synthesis：
- 会创建第二个 ResearchRun（违反单 run）；
- 会在 adaptive 各轮/最终前提前触发 F7 与 run finished 状态（违反 F7-once 与 R12）；
- 不可作为"逐轮研究/最终 synthesis"的 primitive 直接复用。

因此 Batch6 需要一条**受 F9 编排层掌控的图调用路径**（同一 run 内多次 astream 且不触发 F7/
不建 run），这与 ARCHITECTURE §3 红线（禁绕 run_deep_agent 直调 main_agent.astream）冲突 →
**Required Decision D1**（§9）。任何"给 run_deep_agent 加 finalize 开关/main_agent 改造"的解法
= 修改 F1–F8 区域接线契约 → **L3**；"F9 编排层豁免 + 复用 public finalize_run" 的解法 = 触碰
ARCHITECTURE 文档红线语义 → 需要架构 Decision 记录与用户裁决（仍属架构层变更，正式 Reclassify
与否按 §10 判定）。

## 4. Round State / Stopping（可纯编排实现）

- round state（Spec §7：ROUND_STARTED→…→FOLLOW_UP|STOPPED|NO_PROGRESS）可在 orchestrator
  in-memory 实现（无 F8 TaskStatus 映射：F9 STOPPED ≠ F8 terminal）；非法 transition → 停止
  adaptive（fail-closed，不伪造状态、不建 task）。
- Stopping 全为 orchestrator 判定（deterministic + semantic），**不依赖"还有预算"**：
  必答覆盖达标 ∧ 无未缓解 critical conflict ∧ claims 充分 → STOP；no-gap → 直接 STOP（A2，
  不调 Judge）；全部 gaps unimportant / 无合法 plan / no-progress → STOP 或 R2-1 baseline；
  budget/timeout/cancel → 让 F8 收敛（不 swallow）。
- F9 Research Policy 常量（max_research_rounds≤3 默认等，Spec §8）→ Batch6 模块常量（默认值，
  Batch8 校准）——见 Decision D5。

## 5. Baseline Fallback / Final Synthesis

- 普通 Judge/Plan/adaptive failure → 同 execute 内 baseline 分支（丢弃 partial、不建
  task/run/controller、不重复 F7）——可由 orchestrator 分支实现（Batch3/4 seam 已验证同款
  try/except + baseline marker 模式；Batch6 需把 baseline 换成**真实图执行**（D1 seam 落地后）。
- Final Synthesis = 同一 execute 内最后图阶段（Plan R2 冻结；计费进 F8）；其输出
  （final_content）供 orchestrator normal 完成后调 `research_bridge.finalize_run` 恰一次 +
  `set_run_status(finished)`（复用既有 public API，不重写 F7）。

## 6. Batch 5 → Batch 6 Seam

- `execute_targeted_plan(plan, run_id, search_tool?, verifier?, …)` 输入正是 Batch4 plan 输出；
  research ctx 由 Batch5 内部按 query 设置/复位；D-C′ 经 F3 `verifier=` 注入（handler 从当前
  GovernanceExecution 取）→ F3 real-LLM 调用仍计入 max_llm_calls；GLE/Cancel/Timeout 已证明
  可穿透到 F8（Batch5 seam 测试）。
- orchestrator 调用顺序 = 每轮：project → gaps → judge → plan →（对每个 accepted/retry plan
  query）execute_targeted_plan → 收集 result →（可选 F4–F6 信号，见 D4）→ 重投影/stopping。
- 单 run/单 ctx 纪律由 orchestrator 保证：只 create_run_and_root 一次（或由 D1 seam 建立），
  所有 registry/verify 写入显式 run_id（Batch5 已锁定）。

## 7. Context Engineering / Persistence / Identity

- 每轮注入下游的仅 bounded：Projection + GapSignals + JudgeResult + ValidatedPlan + 当前
  round 计数；禁止 parent history / tool payload / evidence 正文堆叠 / 无界 round history。
- Persistence：round state **in-memory**（同 P1/Batch4 语义）；`research_rounds` additive
  持久化属 Spec §17 Q5 未来 Gate——Batch6 不新增表/migration；restart/recovery 由未来
  startup/sweeper 机制处理（不承诺 run 内建新 task）。
- E2E identity 不变量（校验项）：TaskRecord.run_id == GovernanceExecution.run_id ==
  ResearchRun.run_id == SearchQuery/Source/Evidence/Claim/Verification.run_id；task_id =
  lifecycle；run_id = execution correlation；thread_id = session/UI grouping —— Batch6 不引入
  新 identity 来源（orchestrator 从 TaskRecord/ctx 透传 run_id）。

## 8. L2/L3 Classification Check（PROCESS §11）

- 纯能力装配（project/gaps/judge/plan/targeted 复用 + in-memory round/stopping + public
  finalize_run 收尾）本身**不修改** F8/F1–F7 运行时文件、不建新 Agent/Runtime/Controller/
  schema → 维持 L2 的技术面成立。
- **触发 L3 的条件（若任一发生 → Stop → Reclassify L3，记录原因）**：
  1) D1 裁决选择"改 run_deep_agent/main_agent（finalize 开关）" → 修改 F1–F8 区域接线契约；
  2) D1 裁决需新增"第二执行体/独立 Controller/新 Runtime/新 task"；
  3) 实现需要修改 checkpoint/lifecycle/identity/budget/watchdog/cancel 或新增 migration/
     schema/基础设施；
  4) 需要修改 F8 或 F1–F7 任何 public contract/schema。
  当前 Readiness：未到达上述任一触发（尚未进入实现），但 D1 的解析路径将决定 Batch6 正式
  定级（L2 with doc-decision vs L3 with frozen change）。

## 9. Required Decisions（进入 Implementation Plan 前需用户裁决；均非纯实现小项）

- **D1（编排执行 seam / F7-once）**：如何在同一 run 内做 round0 baseline、fallback、final
  synthesis 的**图调用且不提前 F7/不建第二 run**。
  - 选项 A（推荐审查）：F9 编排层专用图调用路径（复用 `get_main_agent` 返回的 agent 或
    既有 subagent 装配，在同一 governed coroutine 内 astream；research ctx 与 monitor 由
    编排层维护；run 由 orchestrator create_run_and_root 一次；收尾调 public
    `bridge.finalize_run` 恰一次）——**不改 F8/main_agent/F7 代码**；需用户确认 F9-scoped
    例外/后续 ARCHITECTURE §3 红线回填（架构 Decision）。
  - 选项 B：给 `run_deep_agent` 增加 finalize/status 开关（保持默认语义）——**修改 F1–F8
    区域文件** → L3，需另行裁决。
  - 选项 C：拒绝多图调用，只允许单 baseline run_deep_agent 全链 + F9 纯后处理（无 adaptive
    round 图执行）——不符合 Spec §2.2/adaptive 目标（不推荐）。
- **D2（round0 语义）**：首轮是否即为"baseline research"（图执行）并计入
  max_*；与 Projection 的 round0 映射。
- **D3（round state 持久化确认）**：确认 in-memory（不建 research_rounds；恢复走未来机制）。
- **D4（F4–F6 在 round 的触发点）**：Batch5 D-D 已把 F4–F6 编排归 Batch6 —— 确认每轮在
  增量验证后对 changed claims 调 F4（detect_claim_conflicts）/F5/F6（claim 级、real gated）
  还是仅在收尾评估一次；以及其计数/失败语义。
- **D5（Research Policy 常量默认）**：max_research_rounds（≤3）、no-progress 连续轮数、
  repeated-gap 阈值、diminishing-return 判定 —— Batch6 模块常量默认值（Batch8 校准前非冻结）。
- **D6（final synthesis 内容来源与 F7 收尾参数）**：final_content 由哪一图阶段产出、F7 的
  extractor/f3..f6 stage 配置是否沿用 run_deep_agent 既有默认（bridge 参数化）。

## 10. Blocking / Non-blocking

- Blocking：无纯代码级 blocking（能力层全部可复用）；D1 为**契约级前置裁决**（影响 L2/L3
  定级与 ARCHITECTURE 红线处理），未裁决前不进入 Implementation Plan。
- Non-blocking：N1 ARCHITECTURE §3 红线与 F9 多图调用冲突（2026-09-22/28 审计已记录）；
  N2 round0/多轮图执行的 monitor/session 胶水细节（batch6 impl 细化）；N3 real-provider 图
  执行 E2E 凭据受限（受控 fake/scripted 验证纪律同前）。

## 11. Final Decision

```text
BATCH 6 READINESS: CONDITIONAL
L3 ESCALATION REQUIRED（仅当 D1 选择改 F8/main_agent/F1–F7 接线或新增第二执行体时——见 §8）
```

- 结构性条件满足：Batch1–5 + F8 public seam 可在单 execute 内装配 adaptive round；
  round/stopping/baseline fallback/in-memory persistence/identity 不变量均可由 orchestrator
  层实现；无新 schema/migration。
- 进入 Implementation Plan 前需用户裁决 **D1–D6（§9）**，其中 **D1 决定 Batch6 的 L2/L3
  定级与 F7-once/ARCHITECTURE 红线处理方式**。

**STOP。** 未创建 Implementation Plan、未实现 Batch 6；等待用户 Review 与 D1–D6 裁决。
