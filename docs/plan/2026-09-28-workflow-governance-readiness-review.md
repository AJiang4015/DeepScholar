# Workflow Governance / Task Classification Readiness Review（L0–L3 落位可行性审计）

> 状态：**READ-ONLY AUDIT → Harness Compatibility: CONDITIONAL**。
> 目标：判断 L0–L3 Task Classification + Workflow Selection 模型能否安全落入现有 Harness；
> **不修改任何文件**（仅新增本报告）。
> 范围：AGENTS.md / PROCESS.md / PROJECT_CONTEXT.md 的职责、规则、状态管理与文档职责划分审计。

---

## 1. Scope

- 允许：只读审计、流程/职责分析、F9 Batch 5 分级判断、本报告。
- 禁止：修改 AGENTS/PROCESS/PROJECT_CONTEXT、任何代码/tests/Spec/Plan/F8/F1–F7、Harness
  重构、补历史文档、顺手修复 stale 文档。发现项只记录（§7），不实施。

## 2. A — AGENTS.md 分析（L0–L3 承载）

现状要点：Authority 顺序（§1）、行为 MUST/MUST NOT（§4）、最小修改（§5）、完成条件（§7）、
Document Loading Policy（§3，先读 4 核心 → 按需权威）。

| 检查 | 结论 |
|---|---|
| Authority/Priority 能否容纳 L0–L3 | ✅ 可容纳——分类属于"如何执行任务"，落 PROCESS；AGENTS 只需指向 PROCESS 即可，不改变 §1 顺序 |
| 是否已有与分类冲突的规则 | 无直接冲突；AGENTS §4 已禁 INTAKE→IMPLEMENTATION 直跳（与 L1/L2/L3 必须 INSPECTION 一致）；§7 完成条件对任何修改统一要求 PROCESS/VERIFICATION/REVIEW——若 L1 想"免 PLAN 文档"，现行 AGENTS 不强制正式文档，兼容 |
| Workflow Selection 放 AGENTS 还是 PROCESS | **PROCESS**（它定义 How Agent works）；AGENTS §3 引用 PROCESS 即可（分类不属行为规范，防规则重复） |
| 是否存在导致 Agent 默认"直接实现"的规则 | 无；现有纪律相反（必须先 INSPECTION）。风险点在"用户一句话小改动"若未分类可能被当 L1 直做——缺口：无显式分类义务（§7 建议） |
| 与 Freeze/Stop/Escalation 冲突 | 无冲突，但无通用 escalation 规范（见 §4）——现有冻结纪律靠"用户逐批 Freeze"与 AGENTS §1 冲突上报，非通用规则 |

## 3. B — PROCESS.md 分析（状态机 / Gate 映射）

| 检查 | 结论 |
|---|---|
| 是否已隐含 L0–L3 | 部分隐含：单一执行状态机专为"修改类"任务；只读任务（readiness/audit/report）历史上按 INTAKE→INSPECTION→报告→STOP 走，**无独立 L0 lane**（不产生 Implementation Plan/Code）——已实践、未成文 |
| INTAKE/INSPECTION/PLAN/IMPLEMENTATION/VERIFICATION → L0–L3 映射 | INTAKE/INSPECTION = 全等级 mandatory（L0 之后直接报告）；PLAN（PREFLIGHT）= L1–L3 需要但 L1 可轻量（现有 PREFLIGHT 已是轻量清单，不要求正式文档）；VERIFICATION/REVIEW/COMPLETE = L1–L3（L0 只产出报告与自审） |
| mandatory vs conditional | 现有：INSPECTION/PREFLIGHT/VERIFICATION/REVIEW 全 mandatory（对实现任务）；L0/L1 目前可省略的只有"正式 Spec / 正式 Implementation Plan 文档"，不可省略的是流程内检查步骤 |
| 小任务能否省略正式 Spec/Plan | ✅ 现有 PROCESS 从未要求正式文档（PLAN 产出"最小修改计划"即可）；L1 建议保持 |
| 高风险任务是否强制升级 | ⚠️ 无通用机制：现有保障 = ① INSPECTION 必读 ARCHITECTURE/Frozen 契约相关文件；② 触 Frozen/契约时依 AGENTS §1 停下报告；③ 用户逐阶段 Gate。缺：显式"发现影响 > 初判 → Stop → Reclassify → 重走更高流程"的通用规则（§7 建议） |
| "Plan" 与 "Readiness Review" 职责重叠 | 观察：L2/L3 前已有 readiness review（判 contract 可实施）与 implementation plan review（判怎么实现+证据方式）——两者职责应显式分离：Readiness=**Contract 闭合**（可做？缺什么裁决），Plan Review=**实现方案+验证方式可执行**。现 F9 批头指令已隐含此二段，建议成文 |
| Implementation Plan 与 Implementation Report 概念混淆 | 观察：`docs/plan/` 中两类文件名清晰（*-plan.md vs *-report.md / *-readiness-review.md），但 batch 级流程常用"单 Plan Rev2 覆盖多批 + 每批 report"；另有 freeze 状态写在 report 内与 PROJECT_CONTEXT 重复 → drift 点（§6 D2） |

## 4. C — PROJECT_CONTEXT.md 分析

- 能否只记 Current Batch/Stage：✅ 当前 §1/§6/§7/§9/§11 正如此使用（Current Status/Current
  Batch/Next Action/Baseline）。
- 是否适合记录当前 workflow stage：✅ 可作为 "Current Stage（如 L2 · Plan Review 等待中）" 一
  行，纯状态不产生规则。
- 与 PROCESS 规范混淆风险：低——文件头部自述"index, not normative authority" + §12 维护规则
  （禁创规则/禁覆盖权威/冲突上报）。
- 把状态写成永久流程规则的风险：有（任何状态索引的通病）——缓解 = §12 + 每次 Batch 结束更新；
  审计建议保持"只存状态+指针"，workflow stage 字段若加也必须是**瞬时状态**（Batch 完成即移）。

## 5. 文档职责划分（§3 目标模型的合理性）

| 职责 | 目标模型 | 现状 | 重叠/Drift 风险 |
|---|---|---|---|
| AGENTS | 行为（MUST obey） | ✅ 一致 | — |
| PROCESS | How + Task Classification + Workflow Selection | 现状缺分类（行为有、选择无） | 分类若放 AGENTS → 与 §3/§8 职责边界冲突（应放 PROCESS） |
| PROJECT_CONTEXT | Current state/batch/workflow stage | ✅ 一致 | 与 report 的状态行重复 → 靠维护规则防 drift（D2） |
| docs/spec | What/Why/Contract | ✅（FROZEN 语义在此） | Spec 内带实现细节的 Revision 记录轻微越界（记录于 2026-09-22 治理审计） |
| docs/plan/*-plan | How to implement | ✅（F8 plan、F9 Plan Rev2 含 Batch 段落） | Batch 级"plan"依赖父 Plan + 每批指令，非独立文件 → 检索成本（非冲突） |
| docs/plan/*-report | 实际实现 + 证据 | ✅ | report 兼任"Freeze 记录"（状态行）→ 与 PROJECT_CONTEXT/后续 report 重复状态（D2） |
| 无独立 reports/ | — | docs/plan 兼历史证据 | 已记录于 2026-09-22 治理审计（未来可选分流） |

Source-of-Truth 总评：行为=AGENTS、流程=PROCESS、状态=PROJECT_CONTEXT（权威在 spec/plan/
report）、契约=spec、决策=DECISION（F8+ 暂居 freeze review）——基本清晰；L0–L3 落地若遵循
"分类进 PROCESS、状态进 PROJECT_CONTEXT、行为引用进 AGENTS" 不会破坏该模型。

## 6. 流程边界 / Escalation / 简化（§4/§5 审查结论）

### 边界触发判据（建议，非实施）
- L0 → L1：结论是"需要修改"且改动风险最低（单点、无契约/跨模块）→ 重新立项 L1。
- L1 → L2：INSPECTION 发现触及公共契约 / 跨模块 / Research Plane 行为 / 前后端契约 / 需要
  新 Spec 语义 → 升 L2。
- L2 → L3：触及 Frozen（F1–F8 / Batch1–4 freeze）、identity、persistence/schema、runtime/
  lifecycle/budget/timeout/cancel、新 Agent/Runtime/Controller、concurrency/recovery/replay、
  安全边界 → 升 L3。
- Escalation：当前 Harness **部分支持**（能停：AGENTS §1 冲突上报 + PROCESS PREFLIGHT + 用户
  Gate；不能自动：无"Stop → Reclassify → 重走流程"通用条款）→ 缺口 = 建议最小条款（§7 M2）。

### 简化矩阵（建议最小但安全）
| 等级 | Mandatory | Optional 可省 | 必须存在 |
|---|---|---|---|
| L0 | INTAKE → INSPECTION（读核心+相关权威）→ 报告 → STOP；自审 | Implementation Plan（不产代码）；正式 Spec | — |
| L1 | INTAKE → INSPECTION → 轻量计划 → IMPLEMENTATION → 验证 → 报告 | 正式 Spec / 正式 Implementation Plan 文档 | PREFLIGHT 前检查清单（轻量） |
| L2 | Spec（或既有 Spec 段）→ Implementation Plan → Plan Review → 实现 → Verify → Report → User Review → Freeze | Spec Review 仅在 Spec 需新契约时 mandatory；每级 Readiness 按批头惯例 | User Freeze（用户裁决后才冻结） |
| L3 | Spec → Spec Review → Readiness Review（Contract 闭合）→ Decision Closure → Implementation Plan → Plan Review → Implement → Verify → Adversarial/Integration → Report → 架构/实现 Review → User Freeze | — | Readiness Review；Decision 记录；User Freeze |

- Readiness Review 省略条件：无新契约裁决且无冻结边界风险（纯实现确认）——但 F9 批纪律倾向
  每批先 readiness（保留，成本低收益高）。
- Plan Review 必须存在：L2/L3 且实现含公共/冻结边界时；L1 单点修复用 PREFLIGHT 清单替代。

## 7. F9 Batch 5 分级（现状实例）

- 内容（Plan Rev2 §I Batch5）：Targeted Research + Incremental Verification —— 复用
  agent/tools 与 F3–F6 public API 回写 research plane（既有 registry/verify 等），在单个
  F8 governed execution 内作为 future orchestrator 的能力层；**不新增表/migration、不改 F8/
  controller/budget/watchdog、不改 F1–F7 schema、不改 identity**。
- 判级：**L2**。理由：跨模块/Research Plane 行为变化（Feature 级）→ L2；未触及 L3 触发项
  （frozen、runtime lifecycle、persistence/schema、identity、concurrency、安全边界）。
  风险点记录：若实现中发现"需改现有 agent 接线/监控/record 语义"或"新增工具/子代理"→ 立即
  Escalate L3（Stop → Reclassify），不得绕过。
- Workflow 起点：按 F9 批纪律从 **Readiness Review** 开始（虽 L2 可选，本项目批惯例保留）；
  完成后若 READY → 基于既有 Plan Rev2 §I Batch5 段产出**独立 Implementation Plan**（Bounded，
  不必重写父 Plan）→ Plan Review → Implementation → Verify（含 F8/F3–F6 seam 与 Batch1–4
  回归）→ Report → **User Review → User Freeze**。
- Report 与 Plan 分离：Plan = How（模块/接口/验证方式，实现前冻结评审）；Report = 实际改动+
  AC→Test→Evidence + Freeze 记录（实现后）；PROJECT_CONTEXT 只存状态指针（阶段一行），不复制
  二者内容。

## 8. Issues（只记录，不实施）

### Blocking
- 无。（L0–L3 与现有三文档无不可兼容冲突；本审计未发现需先改 Harness 才能决策的阻断项。）

### Non-blocking（含既有治理审计已记录项，列此备查）
1. N1：PROCESS 无独立只读（L0）lane——实践存在未成文（历史 readiness/audit 均手动走
   INTAKE→INSPECTION→report→stop）。
2. N2：无通用 Escalation/Reclassify 条款（能停、不能自动重分类）。
3. N3：Readiness Review 与 Plan Review 职责重叠感（建议显式：Readiness=Contract 闭合 /
   Plan Review=实现+验证可执行）。
4. N4：Freeze 状态同时写 report 与 PROJECT_CONTEXT → 双写 drift 点（靠维护规则缓解）。
5. N5：docs/plan 兼实现报告与历史证据；无独立 reports/（2026-09-22 治理审计已记录）。
6. N6：L1 尚无已归档实例可对照（历史上最小改动多以 L2 形态带 Spec 走完）——落地时可先给 1–2
   个真实 L1 样本验证矩阵成本。

### Recommendation（未实施）
- R1：把 Task Classification（L0–L3 定义 + 判级三准则 + Highest-applicable-wins）写入
  PROCESS.md（How Agent works），不写 AGENTS。
- R2：Escalation 条款进 PROCESS：任一阶段发现实际影响 > 初判 → 立即 Stop → 记录理由 →
  Reclassify → 从对应更高流程阶段重入（不回到最低起点浪费，也不跳过已满足阶段）。
- R3：AGENTS §3 增加一行：开始前按 PROCESS L 分类；分类影响加载范围与产出形态（L0 只读，
  L1 轻量，L2/L3 全流程）。AGENTS §4 已有"禁直跳"保持。
- R4：PROJECT_CONTEXT 可加一行 "Current Stage / Workflow（如 L2 · Plan Review）"，瞬时状态，
  Batch 结束即清；不承载规则。
- R5：readiness 与 plan review 文案各加一句话定位（Contract 闭合 vs 实现方案+证据），消除
  重叠感；report 头部保持 Freeze 状态行并与 PROJECT_CONTEXT 同步（现有冻结惯例即可）。

### Suggested minimal change（若获批落地，最小文件集）
- `PROCESS.md`：新增 "## 0.x Task Classification (L0–L3)" + Escalation 条款（≤ ~40 行）。
- `AGENTS.md` §3：加一行分类引用与 "L0 只读、禁实现；越界影响 → 按 PROCESS Escalate"（≤ 3 行）。
- `PROJECT_CONTEXT.md` §1：加瞬时 "Current Workflow Stage" 一行（可选，≤ 2 行）。
- （建议性，非强制）DECISION.md 增加一条 L0–L3 采纳 Decision（若采纳分类治理）。

## 9. F9 场景确认（§6 收尾）
- Batch 5 = **L2**；从 Readiness Review 开始；Readiness 后须有独立 Implementation Plan →
  Plan Review → Implementation → Report → User Review → **User Freeze**；Batch5 报告与计划
  职责分离（§7 末）。

## 10. Final Decision

```text
Harness Compatibility: CONDITIONAL
```

- 现有 Harness（AGENTS/PROCESS/PROJECT_CONTEXT）与 L0–L3 模型**无阻断性冲突**；职责划分与
  目标模型一致（行为/流程/状态/契约/证据各自单一 SoT）。
- CONDITIONAL 依据：通用分类与 Escalation 规范尚未成文（N1/N2）；Readiness vs Plan Review
  定位需一句话澄清（N3）；Freeze 状态双写 drift 需遵守维护规则（N4）。补齐最小条款（§8
  Suggested minimal change）即可安全落地，无需重构。

### Recommended Governance Model（建议矩阵摘要）
见 §6 简化矩阵；规则归属 = PROCESS（分类+Escalation+Workflow Selection）；行为引用 = AGENTS
（指向 PROCESS、L0 禁实现、越界即停）；状态 = PROJECT_CONTEXT（瞬时 stage 行）；文档 = spec
(What/Why/Contract)、plan(How)、report(实际+证据+Freeze 记录)；freeze 一律 User 显式指令。

### Change Scope（未来落地最小文件；本次不改）
1. `PROCESS.md`（分类+Escalation；~40 行）
2. `AGENTS.md` §3（引用行；≤3 行）
3. `PROJECT_CONTEXT.md` §1（瞬时 stage 行，可选）
4. （可选）DECISION.md 记录采纳 Decision

**STOP。** 未实施任何 Governance 修改；等待用户 Review 本报告后再决定是否正式落地 L0–L3。
