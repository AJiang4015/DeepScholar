# PROCESS.md — Agent Execution State Machine（执行状态机）

> 本文件定义 Coding Agent 在本仓库执行任务的强制状态机。它不是 Checklist：每个状态
> 都有 Entry Condition / Required Actions / Required Evidence / Exit Condition /
> Forbidden Transition。状态机本身不得被跳过。

## 0. 状态总览

```text
INTAKE → INSPECTION → PLAN → IMPLEMENTATION → VERIFICATION → REVIEW → PROBLEM CAPTURE REVIEW → COMPLETE
```

允许的返回边：

- VERIFICATION FAILED → IMPLEMENTATION（REJECT COMPLETION）
- REVIEW 发现架构违规 / 验收未覆盖 → IMPLEMENTATION（或 INSPECTION，视缺陷性质）
- 任何状态发现问题理解错误 → 回到 INSPECTION

> PROBLEM CAPTURE REVIEW（§13）发现需立即修复的问题：不并入本 Task 范围（最小修改 /
> Freeze 纪律，AGENTS.md §5 / §10.3）；按 PROBLEM.md 登记或提交 Candidate 后，作为新 Task
> 或经用户指示另行处理。

## 1. INTAKE

- **Entry Condition**：收到任务（用户请求，或注册表中的 Active Problem）。
- **Required Actions**：
  - 复述任务目标，明确验收标准（Acceptance Criteria）。
  - 读 `PROJECT_CONTEXT.md`（如存在）作为**当前状态索引**（当前 Feature/Batch/状态/Next Action；**不是规范来源**，冲突按 AGENTS.md §1 处理）。
  - 按 §11 对任务做 **L0–L3 Task Classification 与 Workflow Selection**（判定依据见 §11.2）；分类决定后续阶段的必做/可省集合与产出形态，**不改变** AGENTS.md §1 Authority 顺序。
  - 查询 PROBLEM.md；命中已登记问题则 MUST 读对应 `docs/problem/` 记录。
- **Required Evidence**：一句话问题陈述 + 验收标准列表 + 命中/未命中问题记录。
- **Exit Condition**：能写出一句话问题陈述，并列出可验证的验收标准。
- **Forbidden Transition**：INTAKE → IMPLEMENTATION / PLAN（未完成问题理解）。

## 2. INSPECTION

- **Entry Condition**：INTAKE 完成。
- **Required Actions**（按改动范围取子集）：
  - 依据 `PROJECT_CONTEXT.md`（如存在）判定当前 Feature / Batch / 状态，定位当前任务相关文档（当前 Feature 的 Spec、相关 Problem / Architecture / Decision / Testing 章节），**只加载相关文档**——从"每次遍历仓库全部历史"改为"先读当前状态索引，再按需加载权威文档"。
  - 读 ARCHITECTURE.md 中相关模块边界与红线。
  - 读相关实现：app/ 对应模块源码。
  - 读调用方：谁调用该模块（依赖方向见 ARCHITECTURE.md §3）。
  - 查现有测试：当前仓库无自动化测试目录（P006）；确认改动是否新增了测试。
  - 读 DECISION.md 中相关决策。
  - 涉及安全敏感代码：读对应 `docs/problem/` 记录与 TESTING.md §5。
- **Required Evidence**：已读文件清单 + 每个文件的关键结论。
- **Exit Condition**：能回答「问题在哪里、改动会影响谁、有什么风险」。
- **Forbidden Transition**：INSPECTION → COMPLETE；未通过 PREFLIGHT GATE（§8）不得进入 IMPLEMENTATION。

## 3. PLAN

- **Entry Condition**：INSPECTION 完成。
- **Required Actions**：执行 PREFLIGHT GATE（§8）；产出最小修改计划；标注 out-of-scope。
- **Required Evidence**：PREFLIGHT GATE 清单 + 修改计划。
- **Exit Condition**：计划包含改动文件、改动理由、验证方式、验收标准 → 证据映射。
- **Forbidden Transition**：PLAN → VERIFICATION / COMPLETE；计划缺少验证方式。

## 4. IMPLEMENTATION

- **Entry Condition**：PREFLIGHT GATE 通过；且（L2/L3 Feature/Batch Implementation）已处于
  dedicated feature branch（§12.1 Branch Preflight；当前为 main/错误 branch → STOP）。
- **Required Actions**：
  - 只做计划内改动（最小修改，见 AGENTS.md §5）。
  - 架构级改动必须先有 Decision（ARCHITECTURE.md §8 Architecture Change Gate）。
  - 安全敏感改动必须同步新增针对性测试（TESTING.md §5）。
- **Required Evidence**：实际 diff（`git diff`）与计划一致。
- **Exit Condition**：代码改动完成且 diff 已自查。
- **Forbidden Transition**：IMPLEMENTATION → COMPLETE（必须经过 VERIFICATION 与 REVIEW）。

## 5. VERIFICATION

- **Entry Condition**：IMPLEMENTATION 完成。
- **Required Actions**：按 TESTING.md §2 按改动范围选择验证命令（命令见 TESTING.md §1），逐条记录输出；适用时按 §3 执行手动 E2E。
- **Required Evidence**：每条验证命令的输入、输出（关键片段）、结论；失败的验证必须有明确的失败证据与原因（作为返回 IMPLEMENTATION 的修复依据，不作为通过条件）。
- **状态判定**（二值，无中间态）：
  - 全部 REQUIRED 验证通过 → **VERIFICATION PASSED**
  - 任一 REQUIRED 验证失败 → **VERIFICATION FAILED**
- **Exit Condition**：状态为 **VERIFICATION PASSED**（即所有 REQUIRED 验证通过）。
- **失败语义**：记录失败原因只是证据，不是通过条件。**记录失败 ≠ 验证通过**。
- **Forbidden Transition**：VERIFICATION FAILED → REVIEW / COMPLETE。VERIFICATION FAILED 必须回到 IMPLEMENTATION（REJECT COMPLETION），修复后重新进入 VERIFICATION。

## 6. REVIEW

- **Entry Condition**：VERIFICATION PASSED（所有 REQUIRED 验证通过，见 §5）。
- **Required Actions**：
  - 以 Reviewer 视角攻击自己的实现（TESTING.md §8 Adversarial Self-Review 必答问题）。
  - 审查实际 diff（不是意图）：Changed/Added/Deleted files、公共 API 变化、依赖变化、安全敏感变化、测试变化、文档变化。
- **Required Evidence**：§8 自问清单的逐项答案。
- **Exit Condition**：所有反问有明确答案，未发现未处理的违规或验收未覆盖。
- **拒绝路径**：架构违规或验收标准未覆盖 → REJECT COMPLETION → 回 IMPLEMENTATION 或 INSPECTION。

## 7. COMPLETE

- **Entry Condition**：COMPLETION GATE（§9）全部通过；PROBLEM CAPTURE REVIEW（§13）已完成。
- **Required Evidence**：完成报告（TESTING.md §4 Evidence Requirement 六问）。
- **Exit Condition**：终态。

## 8. PREFLIGHT GATE（进入 IMPLEMENTATION 之前）

```text
[ ] User request understood
[ ] Active Problem identified（命中/未命中）
[ ] Problem scope identified
[ ] Out-of-scope identified
[ ] Relevant architecture inspected
[ ] Relevant implementation inspected
[ ] Relevant callers inspected
[ ] Existing tests inspected（当前为无自动化测试，见 P006）
[ ] Relevant decisions inspected
[ ] Change risks identified
[ ] Minimal change plan created
```

任何关键项无法确认：MUST NOT 盲目实现；继续检查、明确不确定性，或向用户提问。

## 9. COMPLETION GATE（宣称完成之前）

门控项（明细清单与自检项见 TESTING.md §7，此处只列门控类别）：

- **Problem**：是否解决 Active Problem？验收标准是否全部满足？是否越出范围？
- **Architecture**：边界是否保持？是否触发 Forbidden Changes？新架构决策是否记录？
- **Testing**：定向验证是否按改动范围执行（TESTING.md §2）？回归/安全测试是否按需执行？验收标准是否有证据映射？
- **Change Scope**：是否只改了必要文件？有无投机重构、多余依赖、意外 API 变化？
- **Security**：安全不变量是否保持？负路径是否按实际攻击面测试（TESTING.md §5 Security Test Matrix）？fail-closed 是否保留？
- **Documentation**：新重大 Problem / Problem Candidate（含 §13 Problem Capture Review 决策）/ Decision / 架构变更是否记录？
- **Risk**：剩余风险、未验证假设、已知限制是否报告？

全部通过才允许进入 COMPLETE。任何一项为 No → NOT COMPLETE（REJECT COMPLETION）。

COMPLETION GATE 只能在 **VERIFICATION PASSED 且 REVIEW 通过**之后执行；「失败已记录」「手动验证（无具体输出）」「代码看起来正确」均不得作为通过依据。

## 10. Harness 自身变更

修改六个核心文件或 `docs/problem/` 时，除正常流程外 MUST 执行 TESTING.md §9 HARNESS REVIEW。
发现 Harness 自身矛盾：先修复 Harness，不要继续扩展规则。

## 11. Task Classification & Workflow Selection（L0–L3）

> 规则归属：本文件（How Agent works）。行为侧只引用（AGENTS.md §3）；状态侧只记录瞬时
> Workflow Stage（PROJECT_CONTEXT.md §1，非规范）。分类目的：**最小但安全的流程矩阵**——
> 不为 L0/L1 制造官僚流程，也不让 L2/L3 跳过必要 Gate。

### 11.1 等级定义

| 等级 | 含义 / 例子 | 流程 |
|---|---|---|
| **L0** Read-only / Analysis | architecture review、readiness review、audit、investigation、analysis、文档一致性检查；不产代码 | `Read → Inspect → Analyze → Report → STOP`；**不产生 Implementation Plan，禁止实现**；报告须含 §13 三问结论，发现问题只建议 Candidate / 登记（不直接建档） |
| **L1** Local / Low-risk Change | 单点 bug fix、单函数局部修复、明确的小型测试修复、typo / 文档更正；不触架构 / 公共契约 / 跨模块边界 | `INTAKE → INSPECTION → 轻量计划 → IMPLEMENTATION → VERIFICATION → REVIEW → PROBLEM CAPTURE REVIEW → COMPLETE`；**可省略正式 Spec / Implementation Plan 文档**，但 INSPECTION 与 PREFLIGHT 轻量清单必走 |
| **L2** Feature / Cross-module Change | 新功能、API / contract 修改、跨模块修改、Agent capability、Research Plane 行为变化、前后端契约变化 | `Spec（或既有 Spec 段）→ Spec Review → Implementation Plan → Plan Review → IMPLEMENTATION → VERIFICATION → REVIEW → PROBLEM CAPTURE REVIEW → Report → User Review → Freeze` |
| **L3** Architecture / High-risk Change | 修改 F8 / F1–F7 frozen、Runtime lifecycle、task_id/run_id/thread_id、checkpoint/persistence、budget/timeout/cancellation、新 Agent/Runtime/Controller、DB schema/migration、新基础设施、concurrency/recovery/replay、安全边界、跨模块核心协议 | L2 全部 + `Readiness Review（Contract 闭合）→ Decision Closure → … → Adversarial/Integration Verification → 架构/实现 Review → User Freeze` |

### 11.2 判级原则（MUST）

- 等级由 **architectural impact + risk + boundary impact** 决定；**禁止**以 LOC、修改文件数、
  代码表面复杂度或 Agent 主观"看起来很小"降级。
- **Highest applicable level wins**：同时命中多级特征 → 取最高级流程。
- 分类只影响流程与产出形态，不影响验收证据要求（TESTING.md）与 Authority（AGENTS.md §1）。

### 11.3 Escalation / Reclassify（MUST）

- 任一阶段发现**实际影响高于最初分类**（如 L1/L2 中发现改动 frozen component / public
  contract / persistence / runtime / identity / concurrency semantics）：**立即 STOP 修改 →
  记录 Reclassify 理由（文件+条款）→ 按新等级从对应阶段重入**（已满足且与升级无关的阶段可
  复用证据，不重复劳动；被升级影响的阶段不得跳过）。
- 不得为保持低等级而绕过流程（不得把 L3 改动包装成 L1 提交）。
- L0 中若结论转为"需要修改"：重新按 §11.1 分类立项（通常 ≥ L1），不得在 L0 任务内顺手改码。

### 11.4 必做 / 可省 / Freeze

- L0：不产 Implementation Plan；产出分析/审计报告。
- L1：不强制正式 Spec/Plan 文档；PREFLIGHT（§8）轻量清单必走。
- L2：Spec（新契约时）+ Implementation Plan + Plan Review + User Review/Freeze 必须。
- L3：Readiness Review（Contract 闭合）与 Decision Closure 必须；Plan Review 必须。
- **Readiness Review 可省略条件**：无新契约裁决且无冻结边界风险（纯实现确认）——F9 批纪律
  默认仍先 readiness（成本低、收益高），省略需在 INTAKE 记录理由。
- **User Freeze 必须**：L2/L3 在 User Review 通过后由用户显式 Freeze；Agent 不得自宣 Freeze。
- 报告与计划分离：Implementation Plan = How（实现前评审）；Implementation Report = 实际改动
  + AC→Test→Evidence + Freeze 状态（实现后）；PROJECT_CONTEXT 只记状态指针，不复制两者内容。

## 12. Git Workflow Execution（How；规则 What 见 AGENTS.md §10）

> 本文件定义 **如何执行** Git Workflow；不复制 AGENTS.md §10 的 MUST 条款。
> One Feature/Batch = One Feature Branch = One Review/Freeze = One Merge。
> Freeze ≠ Commit ≠ Push ≠ Merge：每步独立、逐步批准。

### 12.1 Before Implementation（Branch Preflight）

IMPLEMENTATION（§4）开始前，INSPECTION/PLAN 完成后，除既有 PREFLIGHT GATE（§8）外执行：

```text
Current Feature/Batch:
Workflow Level（§11）:
Current branch:            # git branch --show-current
Target branch:             # feature/<feature-or-batch>（L2/L3 Implementation）
Base branch:               # 通常 main
Approved Scope:
Frozen modules:
```

分支决策：

- **L0 / Readiness / Plan / Review-only**：产物为文档/分析，允许留在 `main`
  （文档提交不构成 Implementation 开始）。
- **L1**（本地 / 低风险修复）：默认也在 `main` 或当前冻结 Feature 的 branch 内完成；若该
  Feature 已 Freeze 则开新修复 branch（AGENTS.md §10.3 Freeze 后语义）。
- **L2 / L3 Feature/Batch Implementation**：必须在 **dedicated feature branch**；
  当前为 `main` 或错误 branch → **STOP，不得修改代码**，先创建/切换 branch
  （创建 branch 属 git 写操作，需用户批准：见 §12.4）。

### 12.2 Implementation 阶段

- Implementation 全部发生在 feature branch；每轮改动后 `git diff --check`。
- 开发过程允许多个语义清晰的 commit（对应完整逻辑变更，如 `feat(f9-b7): …`、
  `test(f9-b7): …`、`docs(f9-b7): …`）。
- 不制造无意义 commit；不在未完成验证前做 "final/freeze" commit。

### 12.3 Verification / Review / Freeze

- VERIFICATION PASSED（§5）→ REVIEW（§6）→ PROBLEM CAPTURE REVIEW（§13）→ Implementation Report。
- **User Freeze**（§11.4）为技术状态裁决：表示"实现与验证通过、禁止再加功能"。
- **Freeze 不等于 Git commit 权限**：Freeze 后仍须按 §12.4 逐项批准。

### 12.4 Post-Freeze Git Flow（顺序固定、逐步批准）

```text
Freeze
  → Commit preparation（git status / git diff --check / Git Scope Audit，见 AGENTS.md §10.6）
  → User approval
  → Commit（feature branch）
  → User approval
  → Push（feature branch）
  → Review / approval（如远程有保护）
  → Merge main
  → Push main
```

- 每一步写操作（commit / push / merge / push main）都需用户**明确批准**；Agent 不得将
  Freeze 自动解释为 commit/push/merge 授权。
- Push/Merge 遇 branch protection / push policy / 权限限制 → **STOP 报告**，不绕过、
  不 force-push、不偷改 remote。
- Merge 后 main 为集成冻结基线；后续 Feature/Batch 从 main 开新 branch。

### 12.5 Scope Isolation / Diff Audit（Freeze→Commit 前）

Freeze 前与 commit 前执行：

```text
git status
git diff main...<feature-branch>（或 git diff --stat / --name-status）
git diff --check
```

确认：无 unrelated files / 无 accidental modifications / 无 frozen module modifications /
无临时测试文件 / 无 debug artifacts / 无未批准 dependency changes / 无 generated
secrets·config / 无 scope creep。发现越界 → **STOP**，不得通过 commit 隐藏。

### 12.6 Mixed / Unowned Working Tree

工作树含多 Feature/Batch 混合、无法确认归属、或历史遗留无法分类的改动 → 按
AGENTS.md §10.5：`STOP → report → wait`；不得自行猜测/拆分/reset/stash/cherry-pick/
重写历史。历史补登记（baseline）仅在用户显式批准下可直接在 main 提交。

## 13. PROBLEM CAPTURE REVIEW（Post-Task 问题沉淀门）

> 章节编号靠后仅为避免重排既有引用（§8–§12 编号被 AGENTS.md / 历史文档引用）；状态机位置以
> §0 总览与 §11.1 流程行为准。行为义务（何时必须评估）见 AGENTS.md §11；登记标准 / Type /
> 格式见 PROBLEM.md；本节定义本节点如何执行与证据要求。

- **Position**：REVIEW 之后、Freeze / COMPLETE 之前（§0；§11.1 L1–L3；§12.3）。
  L0 任务不单列节点：三问结论并入分析报告，发现问题只记录建议，不直接建档
  （建档属 L1 文档变更）。
- **Entry Condition**：REVIEW 通过（VERIFICATION PASSED 且无未处理违规）。
- **Required Actions**：
  1. 按 AGENTS.md §11.1 触发清单回顾本任务（调试陷阱、隐含约束、返工、文档偏差、
     边界缺失、可复现问题）；
  2. 逐项自答 AGENTS.md §11.3 三问，给出 Yes/No 与一句话依据；
  3. 按 PROBLEM.md 标准作出唯一决策：**D1 不登记**（默认）；**D2 直接登记**（达标）→ 创建
     `docs/problem/P0NN-*.md` 并在 PROBLEM.md 索引追加一行（含 Type）；**D3 提交 Candidate**
     （有长期价值未达标）→ 创建 `docs/problem/candidates/<slug>.md`；**D4 既有 candidate
     近命中** → 阅读并补充证据或评估晋升。
- **克制约束**：本节点只判断"是否存在需长期登记的问题"，不自动批量创建；普通 bug / 一次性
  问题默认 D1；登记产物不并入本 Task 实现范围，作为独立 docs 提交单元走 Commit Gate
  （AGENTS.md §10.6）。
- **Required Evidence**：三问逐项答案 + 决策类别（D1/D2/D3/D4）+（D2/D3/D4 时）创建 / 更新的
  文件路径。
- **Exit Condition**：决策已作出；D2/D3/D4 的 docs 产物与索引 / 目录一致；可进入 COMPLETE（L1）
  或 Implementation Report → User Freeze（L2/L3）。
- **Forbidden Transition**：跳过三问宣称 COMPLETE / Freeze；把未达门槛问题批量登记为 Problem；
  将捕获产物未经 Commit Gate 混入 Feature/Batch commit。
- **REVIEW 拒绝路径**：REVIEW 判定本节点产生无关 / 低质登记 → REJECT → 移除登记或降级为
  candidate（按 PROBLEM.md 使用规则）。
