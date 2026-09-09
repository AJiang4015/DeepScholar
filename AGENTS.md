# AGENTS.md — Agent Constitution（Agent 行为契约）

> 本文件是 deepsearch-agents 仓库的 Agent Runtime Harness 最高层行为契约。
> 仓库内任何执行代码修改的 Coding Agent（含人类协作者）都必须遵守。
> 本文件只定义行为规则；执行顺序、问题、验证、决策、架构分别归属 PROCESS.md / PROBLEM.md / TESTING.md / DECISION.md / ARCHITECTURE.md。

## 1. 文档优先级（Authority）

发生规则冲突时，按以下优先级裁决：

1. User Request（用户本次直接指令）
2. AGENTS.md（本文件）
3. PROCESS.md
4. PROBLEM.md / 当前 Active Problem
5. ARCHITECTURE.md
6. TESTING.md
7. DECISION.md
8. Existing Implementation（现有实现）
9. Agent Preference（Agent 偏好）

冲突处理（MUST）：

- Agent MUST NOT 静默自行裁决冲突。
- Agent MUST 明确指出相互冲突的具体规则（文件 + 条款）。
- Agent MUST 在可能产生无效改动之前停下。
- Agent MUST 向用户报告冲突并等待裁决。

## 2. 角色与职责

- 角色：本仓库的修改执行者。仓库是「深度研搜」对话式多智能体研究系统：FastAPI + WebSocket 后端，DeepAgents 一主三从智能体（网络搜索 / MySQL 查询 / RAGFlow 知识库），React 前端，Tavily / MySQL / RAGFlow / OpenAI 兼容模型外部集成。
- 职责：
  1. 先理解问题，再修改代码（PROCESS.md 状态机）。
  2. 只做最小正确修改（第 5 节）。
  3. 提供可验证的完成证据（TESTING.md）。
  4. 保持架构边界（ARCHITECTURE.md）。

## 3. 文档使用规则（何时读哪份文档）

- 每次任务开始：读 `PROJECT_CONTEXT.md`（如存在；**当前状态索引，非规范来源**）、`PROCESS.md` 与 `PROBLEM.md`（判断是否命中已登记问题；命中则必须读对应 `docs/problem/` 记录）。
- 任何修改之前：读 ARCHITECTURE.md 中与改动相关的边界与红线。
- 改动涉及安全敏感代码（SQL 执行、文件读写、上传/下载、认证授权、外部凭据、用户可控输入）：额外读 TESTING.md §5 Security 与相关 `docs/problem/` 记录。
- 需要做重大取舍或架构级改动：读 DECISION.md，并按要求新增 Decision。
- 任务开始先按 PROCESS.md §11 做 **L0–L3 Task Classification**：分类只决定流程与产出形态，不改变 §1 Authority；**L0 只读、禁止实现**；实施中发现实际影响高于初判 → 按 PROCESS.md §11.3 **立即 Stop → Reclassify**，不得为保持低等级绕过流程。
- INSPECTION 必须产出"读了什么、为什么读"的证据清单；REVIEW 若发现改动涉及与 Active Problem 无关的章节或文件 → REJECT（可验证：凭 diff 与证据清单判定）。

### Document Loading Policy

- **First read（每次任务开始）**：`AGENTS.md`、`PROJECT_CONTEXT.md`（如存在）、`PROCESS.md`、`PROBLEM.md`。
- **Then selective read（按当前任务，勿全量）**：Active Problem 对应 `docs/problem/` 记录；相关的 `ARCHITECTURE.md` / `DECISION.md`（按需章节或 Dxxx）；当前 Feature 的 `docs/spec/`；当前 Batch 的 `docs/plan/`；`TESTING.md` 中相关章节。
- **MUST NOT** 因"想了解项目背景"而默认读取全部历史：历史 `docs/plan/`、`docs/spec/`、`docs/problem/`、历史 report、历史 Decision、ROADMAP.md 等（除非当前任务确实涉及）。
- **冲突处理**：`PROJECT_CONTEXT.md` 与权威文档冲突时——不静默选择；按 §1 Authority 规则处理；报告冲突；`PROJECT_CONTEXT.md` 不得覆盖权威文档。

> `PROJECT_CONTEXT.md` is a current-state index, not a normative authority. It MUST NOT override AGENTS.md / PROCESS.md / ARCHITECTURE.md / TESTING.md / DECISION.md / active Spec contracts.

## 4. 禁止行为（MUST NOT）

- MUST NOT 在完成 INSPECTION 之前修改代码（禁止 INTAKE → IMPLEMENTATION 直跳）。
- MUST NOT 执行投机式重构（speculative refactoring）。判据：任何与 Active Problem 验收标准无映射的代码改动（必要格式/文档同步除外）即为投机重构。
- MUST NOT 顺带清理与任务无关的文件或格式。
- MUST NOT 升级与任务无关的依赖。
- MUST NOT 无需求地迁移架构或修改公共 API（HTTP 端点、WS 事件 schema、LangChain 工具签名）。
- MUST NOT 为了测试变绿而删除或弱化测试。
- MUST NOT 弱化安全边界来迁就测试或便利（见第 6 节）。
- MUST NOT 未经验证就宣称完成（见第 7 节与 PROCESS.md COMPLETION GATE）。

## 5. 修改策略（最小修改）

默认策略：**Smallest correct change**。

按顺序优先复用：

1. 现有抽象（app/tools、app/utils、app/api/context、app/api/monitor）
2. 现有模块
3. 现有依赖
4. 现有验证手段
5. 最小可行 diff

## 6. 安全约束

- 安全边界优先于测试便利。
- 涉及 SQL 执行、命令执行、认证授权、文件系统访问、外部 API 凭据、用户可控输入时：默认 **Fail Closed**（拒绝而非放行）。
- 若测试与既定安全边界冲突：识别意图 → 判断测试是否代表过时行为 → 仅当行为变更被明确要求时才更新测试 → 保留更强的安全不变量 → 报告行为变更。
- 本仓库已登记的安全问题见 PROBLEM.md P001–P005；修改相关代码前 MUST 阅读对应 `docs/problem/` 记录。
- 凭据只从环境变量读取（.env，不入库）；MUST NOT 硬编码密钥。

## 7. 完成条件

Agent 只有在同时满足以下条件时才可宣称 COMPLETE：

- PROCESS.md 状态机到达 COMPLETE（VERIFICATION 与 REVIEW 均已通过）；
- TESTING.md COMPLETION GATE 全部通过；
- 每个重要验收标准都有对应证据（AC → Implementation → Test → Evidence）；「重要」= 与安全性/正确性直接相关，或用户显式指定的验收标准；
- 未验证项、剩余风险、假设全部显式报告。

证据不足 = NOT COMPLETE。禁止用 "Tests should pass." / "Looks good." / "Seems correct." / "Verified manually." 代替证据。

## 8. 职责边界

行为规则在本文件；执行顺序在 PROCESS.md；问题索引在 PROBLEM.md；验证契约在 TESTING.md；决策记录在 DECISION.md；架构边界在 ARCHITECTURE.md。

如果一条规则无法明确归属：报告歧义，不要复制到多个文件。

## 9. Harness 演进

新增失败模式时，先判断归属（行为 → AGENTS.md；流程 → PROCESS.md；反复工程问题 → PROBLEM.md；验证 → TESTING.md；设计决策 → DECISION.md；架构约束 → ARCHITECTURE.md），再决定是否新增。发现 Harness 自身矛盾：先修复 Harness，不得继续堆规则（见 TESTING.md §9 HARNESS REVIEW）。

## 10. Git Workflow Governance（normative MUST）

> 行为归属：本文件定义 Agent **必须遵守**的 Git 规则（What）；执行步骤在 PROCESS.md §12
> （How）；当前 branch / baseline / 状态只记在 PROJECT_CONTEXT.md（Where，非规范）。
> One Feature/Batch = One Feature Branch = One Review/Freeze = One Merge。历史补登记
> （baseline）除外——用户显式批准的 baseline registration 可直接在 main 提交。

### 10.1 Branch Boundary（MUST）

- `main` = **frozen integration branch**；Feature / Batch Implementation 不得直接在 `main` 上开发。
- L2/L3 Feature/Batch Implementation 开始前，必须处于 **dedicated feature branch**
  （推荐命名 `feature/<feature-or-batch>`，如 `feature/f9-batch7-eval`）；当前 branch 为
  `main` 或与任务不符 → **STOP，不得修改代码**。
- **禁止以"用户已批准本 Feature"绕过 branch boundary**：批准只授权该 Feature 的范围与
  流程，不改变"必须在 feature branch 实施"的硬规则。
- 若 Implementation 过程中发现需修改另一已冻结 Feature 的代码 → **STOP → 报告
  Cross-Feature Compatibility Gap → 等待用户决定**，不得跨 branch 混改。

### 10.2 Readiness / Planning 与 Implementation 的 Branch 区别

- Readiness / Plan / Review-only 产物（read-only 分析、audit、readiness review、plan 文档）
  可以留在 `main`（文档提交），**不代表 Feature Implementation 已开始**。
- 一旦进入 Implementation（任何写代码 / 测试 / 实现的动作），必须切换到 dedicated feature
  branch；Readiness 产物所在的 main 基线不承载实现提交。

### 10.3 Lifecycle（MUST；语义逐项独立）

```text
Feature/Batch
  → Readiness（可留 main）
  → Implementation branch（feature/<feature-or-batch>）
  → Spec / Plan / Review
  → Implementation
  → Verification
  → User Freeze
  → Git Scope Audit
  → User approval
  → Commit
  → Push
  → Merge main
```

- **Freeze ≠ Commit**：Freeze 是用户对实现/验证状态的技术裁决；Freeze 后不得再加功能，
  但不自动授予 commit/push/merge 权限。
- **Commit ≠ Push ≠ Merge**：三者是独立动作，每个都需用户明确批准（Commit Gate 见
  §10.6；Push/Merge 见 PROCESS.md §12.4）。
- Freeze 后发现问题：不修改本 branch 已冻结实现，进入新的 Feature/Batch（新 branch）。

### 10.4 Destructive / History-Rewriting Operations（MUST NOT）

除非用户**明确**指示，否则 MUST NOT：

- `force-push`
- `reset`（含 `reset --hard`）
- `rebase`
- `amend`
- 丢弃 / 覆盖用户或无法确认归属的 working-tree changes

### 10.5 Mixed / Unowned Working Tree（MUST）

发现以下任一情况：

- working tree 同时包含多个 Feature / Batch 的改动；
- 无法确认某文件归属（属于哪个 Feature/Batch / 是否已批准）；
- 历史遗留的 uncommitted changes 无法安全分类；

必须：

```text
STOP → report → wait
```

不得自行：猜测归属、拆分、`reset`、`stash`、`cherry-pick`、重写历史、或将改动塞进
"看起来合理"的 commit 来隐藏。

### 10.6 Commit Gate（Commit 前 Git Scope Audit，MUST）

Commit 前必须逐项确认：

- 只包含 intended files（当前 Feature/Batch 的批准 Scope）；
- 无 unrelated / accidental modifications；
- 无 secrets / credentials / `.env` / 机器配置；
- 无临时 / debug artifacts（临时测试文件、日志、探针、`_testtmp` 产物等）；
- 无未批准 / 未 Freeze 的 implementation；
- 无对 frozen modules / contracts（F8 / F1–F7 / Batch1–5 / 已 Freeze Batch）的意外修改；
- `git diff --check` 通过（无 whitespace errors）；
- tests / verification / freeze gate 已通过（证据留档，见 TESTING.md）。

Commit message：语义清晰、对应一个完整逻辑变更（如 `feat(...)` / `test(...)` /
`docs(...)` / `chore(baseline):` 等）；**禁止**无意义提交、**禁止**未验证先做
"final/freeze" commit、**禁止** amend/rebase 事后改写已验证历史。

### 10.7 Harness 自身维护例外（最小范围）

- **允许**：Harness 自身治理文件的**低风险文档维护 / 规则闭环**（如 AGENTS/PROCESS/
  PROJECT_CONTEXT 的最小文字补充、规则澄清、状态同步）可按 Harness Review（PROCESS §10）
  在 `main` 直接提交，**不 push 或 push 另需用户批准**；前提：零产品代码、零测试改动、
  零 frozen contract 修改、working tree 仅含被批准的 Harness 文件。
- **不允许**：涉及 Feature/Batch Implementation、产品代码、架构实现或高风险行为的
  Harness 变更仍必须走对应 L2/L3 feature branch workflow（§10.1–§10.6 不受本条影响）。

## 11. Problem Capture（问题沉淀；Engineering Problem Registry）

> 归属：PROBLEM.md 是**工程问题注册表（Engineering Problem Registry）**的索引——定义 What 被登记、
> Type 词表与登记门槛；本节定义 Agent **何时必须评估沉淀**（行为 What）；执行节点与证据要求见
> PROCESS.md §13（How）。注册表不限于安全漏洞与生产故障：还沉淀运行时行为约束、架构约束、
> 测试/环境限制、流程误解导致的返工、文档与实现偏差、Agent 易重复犯的错误模式等长期工程知识。

### 11.1 Capture Triggers（主动评估触发，MUST）

任务执行中出现以下任一情况，Agent MUST 评估"是否产生应沉淀的 Problem"（**评估 ≠ 必须登记**）：

- 问题具有复现可能（现象可再次触发）；
- 调试 / 排错依赖隐含知识（未在任何文档或代码注释中显式可见）；
- 暴露系统边界缺失或契约缺口；
- 已导致或将导致重复返工；
- 暴露文档与实际行为不一致；
- 未来 Agent 很可能重复踩坑。

### 11.2 Problem 范围（不限定）

Problem **不限定**为 Security vulnerability 或 Production incident，可以包括：Runtime behavior
issue、Architecture constraint、Testing limitation、Environment trap、Agent workflow mistake、
Correctness issue、Process misunderstanding 等。完整 Type 词表与登记标准在 PROBLEM.md
（此处不复制，避免双源）。

### 11.3 Problem Capture Review（Task 完成前 MUST）

每个 Task 完成前（PROCESS.md §13 PROBLEM CAPTURE REVIEW 节点），MUST 逐项自答三问：

1. 本任务是否发现新的长期约束？
2. 是否产生未来 Agent 需要知道的信息？
3. 是否存在重复发生风险？

任一回答 Yes：按 PROBLEM.md 登记标准处置——达标 → 创建 Problem（`docs/problem/P0NN-*.md`
+ PROBLEM.md 索引行）；有长期价值但未达标 → 提交 Problem Candidate
（`docs/problem/candidates/`）；均不达标 → 不登记，并在证据中记录"No"。

### 11.4 克制原则

Problem Capture Review 只增加"评估义务"，不增加登记义务，不得批量制造问题：普通 Bug、
临时调试问题、一次性实现细节仍按 PROBLEM.md 门槛排除（本规则**不降低登记门槛**）。捕获产物是
知识记录（`docs/problem/`），不是代码改动，不得借本规则扩大任务实现范围（§4、§5 不受影响）。
