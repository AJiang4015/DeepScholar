# L0–L3 Task Classification Governance — Implementation Report

> 状态：**PASS / FROZEN**（用户 2026-09-28 正式 Freeze）。
> 依据：Workflow Governance Readiness Review（2026-09-28，CONDITIONAL）＋用户正式批准按报告
> §8 Suggested minimal change 落地（2026-09-28）。
> 改动授权：仅 `PROCESS.md`、`AGENTS.md`、`PROJECT_CONTEXT.md` ＋本报告；**未改 DECISION.md**、
> 未改代码/tests/Spec/Plan/F8/F1–F7/F9 实现。

## 1. Scope

In：把 L0–L3 Task Classification & Workflow Selection 按报告 §8 最小改动写入三文件；分类规则
归属 PROCESS（How Agent works），行为引用归 AGENTS，瞬时状态归 PROJECT_CONTEXT。

Out：未改 DECISION.md（用户明确禁止）；未触碰任何代码/测试/Spec/Plan/F8/F9；未进行 Harness
重构或顺手修复（含已知 stale 文档）。

## 2. Changes

### PROCESS.md（+46 行：INTAKE 1 条 + 新增 §11）

1. §1 INTAKE Required Actions 新增：按 §11 做 L0–L3 Task Classification 与 Workflow Selection
   （判定依据 §11.2；分类决定阶段必做/可省与产出形态，不改变 AGENTS.md §1 Authority）。
2. 新增 **§11 Task Classification & Workflow Selection（L0–L3）**：
   - §11.1 等级定义表：L0（只读/分析，`Read→Inspect→Analyze→Report→STOP`，禁实现、不产
     Implementation Plan）；L1（局部/低风险，可省略正式 Spec/Plan 文档但 INSPECTION+PREFLIGHT
     必走）；L2（Feature/跨模块，Spec→Plan→Plan Review→…→User Review→Freeze）；L3（架构/高
     风险，L2 全部 + Readiness Review + Decision Closure + Adversarial/Integration + 架构/实现
     Review + User Freeze）。
   - §11.2 判级原则：architectural impact + risk + boundary impact；禁止按 LOC/文件数/表面复杂
     度/主观"看起来小"降级；Highest applicable level wins；分类不改 Authority 与证据要求。
   - §11.3 Escalation/Reclassify：实际影响 > 初判 → **立即 Stop → 记录理由 → 按新等级重入**
     （已满足且无关阶段可复用证据）；禁止为保持低等级绕过流程；L0 转"需要修改"时重新分类立项。
   - §11.4 必做/可省/Freeze：L1 免正式文档；L2/L3 Plan Review 与 User Freeze 必须；L3 加
     Readiness + Decision Closure；Readiness 可省略条件（无新契约裁决且无冻结边界风险，省略需
     INTAKE 记录理由）；Plan（How，实现前）与 Report（实际+证据+Freeze，实现后）分离。

### AGENTS.md（+11/−1：§3 一条 bullet）

- §3 新增：任务开始先按 PROCESS.md §11 做 L0–L3 分类（只决定流程与产出形态，不改 §1
  Authority）；**L0 只读、禁止实现**；实施中发现实际影响高于初判 → 按 PROCESS.md §11.3
  **立即 Stop → Reclassify**，不得为保持低等级绕过流程。

### PROJECT_CONTEXT.md（+2 行：§1 瞬时状态）

- §1 新增 "Current Workflow Stage: L2 · 待命（无进行中的实现任务）——L0–L3 已于 2026-09-28
  采纳（PROCESS.md §11）；本行为瞬时状态，任务/Batch 结束即更新或清除"。仅状态，非规则。

## 3. AC → Test → Evidence（文档级；无代码故无 pytest，验证 = 一致性/引用/diff 审计）

| AC（§8/用户批准） | Implementation | Test | Evidence |
|---|---|---|---|
| 分类规则入 PROCESS（How） | PROCESS §11（L0–L3 + 判级 + Escalation） | grep 引用一致性 | 三文件交叉引用命中（见 §4） |
| 行为引用入 AGENTS（≤3 行） | AGENTS §3 bullet（L0 禁实现 / Escalate） | grep | AGENTS 行 43 命中 |
| 瞬时状态入 PROJECT_CONTEXT | §1 Workflow Stage 行 | grep | CTX 行 24 命中 |
| 不改 DECISION/代码/tests/Spec/Plan/F8/F9 | 未触碰 | git diff/status | status 仅 AGENTS/PROCESS M + 既有未跟踪产物；无代码/tests 变更 |
| 不自动开始 F9 Batch 5 | 本任务即 STOP | — | 未创建 Batch5 任何文件/代码 |

## 4. Consistency Verification

- PROCESS §11 header（行 132）、§11.3（行 154）存在；AGENTS 引用 PROCESS §11/§11.3 与
  "L0 只读、禁止实现"（行 43）；PROJECT_CONTEXT Current Workflow Stage 行（行 24）存在。
- 职责无重复定义：分类只在 PROCESS §11 定义一次；AGENTS 只引用（无复制规则）；PROJECT_CONTEXT
  只记状态行（无规则）。
- diff 审计：`git diff --stat -- AGENTS.md PROCESS.md` = AGENTS +12/−1、PROCESS +46；
  git status 确认本次仅上述两文件被修改 + PROJECT_CONTEXT（未跟踪全文件内容更新）；其余 M/??
  均为先前任务既有产物，未触碰。

## 5. HARNESS REVIEW（PROCESS.md §10 / TESTING.md §9，修改核心文件触发）

- [x] 每个文档只有一个主要职责（分类=PROCESS；引用=AGENTS；状态=PROJECT_CONTEXT）
- [x] 无重大规则重复（§11 单一 SoT；AGENTS/PROJECT_CONTEXT 不复制规则）
- [x] 无互相冲突规则（分类与现有 INSPECTION/PREFLIGHT/冻结纪律一致；与 AGENTS §1 Authority 无冲突）
- [x] PROCESS.md 与 AGENTS.md 一致（AGENTS 引用 PROCESS §11/§11.3 与实际文本一致）
- [x] TESTING.md 与 PROCESS.md 一致（未改状态机/Gate/验证契约；L1 免文档但证据要求不变）
- [x] PROBLEM.md 是索引而非详细日志（未改）
- [x] docs/problem/ 承载详细记录（未改）
- [x] DECISION.md 记录理由而非流水账（未改；L0–L3 采纳的 Decision 记录留待用户后续决定，
  本报告已注明——见 §7）
- [x] ARCHITECTURE.md 反映真实仓库（未改；既有 F8/F9 回填缺口已记录于 2026-09-22 治理审计）
- [x] 完成标准可客观验证（§4 一致性 + git diff/status）
- [x] 自审可以拒绝完成（若一致性失败 → 不宣称完成）
- [x] 安全敏感改动有更强验证规则（本任务纯文档，不适用）

## 6. Verification（实际执行）

- grep 交叉引用：PROCESS(§11/§11.3)/AGENTS(§11/§11.3/L0 禁实现)/PROJECT_CONTEXT(Workflow
  Stage) 全部命中。
- git diff/status：仅授权文件变更；无代码/测试/Spec/Plan/F8/F9/DECISION 修改。

## 7. Known Limitations / 待用户裁决

1. DECISION.md 未记录 L0–L3 采纳 Decision（用户本次明确禁止改 DECISION.md）——如需正式决策
   记录，留待后续单独指令。
2. Readiness Review 可省略条件的默认判断与 F9 批纪律（默认每批 readiness）并存：PROCESS §11.4
   已写明"省略需在 INTAKE 记录理由"，无冲突。
3. L1 尚无真实归档样本校准流程开销（readiness review 已记录 N6）；后续首个 L1 任务可验证。
4. PROJECT_CONTEXT "Current Workflow Stage" 为瞬时字段，需随任务/Batch 更新或清除（§12 维护
   规则同样适用）。

## 8. Final Decision

```text
L0–L3 GOVERNANCE — PASS / FROZEN（2026-09-28 用户正式 Freeze）
```

实现与验证证据（§2–§7 内容）原样保留，未重写、未补充新治理规则。未开始 F9 Batch 5（Batch 5
= PENDING，等待用户显式启动指令）。
