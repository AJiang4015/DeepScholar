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

- 每次任务开始：读 PROCESS.md 与 PROBLEM.md（判断是否命中已登记问题；命中则必须读对应 `docs/problem/` 记录）。
- 任何修改之前：读 ARCHITECTURE.md 中与改动相关的边界与红线。
- 改动涉及安全敏感代码（SQL 执行、文件读写、上传/下载、认证授权、外部凭据、用户可控输入）：额外读 TESTING.md §5 Security 与相关 `docs/problem/` 记录。
- 需要做重大取舍或架构级改动：读 DECISION.md，并按要求新增 Decision。
- INSPECTION 必须产出"读了什么、为什么读"的证据清单；REVIEW 若发现改动涉及与 Active Problem 无关的章节或文件 → REJECT（可验证：凭 diff 与证据清单判定）。

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
