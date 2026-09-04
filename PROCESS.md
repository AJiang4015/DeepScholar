# PROCESS.md — Agent Execution State Machine（执行状态机）

> 本文件定义 Coding Agent 在本仓库执行任务的强制状态机。它不是 Checklist：每个状态
> 都有 Entry Condition / Required Actions / Required Evidence / Exit Condition /
> Forbidden Transition。状态机本身不得被跳过。

## 0. 状态总览

```text
INTAKE → INSPECTION → PLAN → IMPLEMENTATION → VERIFICATION → REVIEW → COMPLETE
```

允许的返回边：

- VERIFICATION FAILED → IMPLEMENTATION（REJECT COMPLETION）
- REVIEW 发现架构违规 / 验收未覆盖 → IMPLEMENTATION（或 INSPECTION，视缺陷性质）
- 任何状态发现问题理解错误 → 回到 INSPECTION

## 1. INTAKE

- **Entry Condition**：收到任务（用户请求，或注册表中的 Active Problem）。
- **Required Actions**：
  - 复述任务目标，明确验收标准（Acceptance Criteria）。
  - 查询 PROBLEM.md；命中已登记问题则 MUST 读对应 `docs/problem/` 记录。
- **Required Evidence**：一句话问题陈述 + 验收标准列表 + 命中/未命中问题记录。
- **Exit Condition**：能写出一句话问题陈述，并列出可验证的验收标准。
- **Forbidden Transition**：INTAKE → IMPLEMENTATION / PLAN（未完成问题理解）。

## 2. INSPECTION

- **Entry Condition**：INTAKE 完成。
- **Required Actions**（按改动范围取子集）：
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

- **Entry Condition**：PREFLIGHT GATE 通过。
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

- **Entry Condition**：COMPLETION GATE（§9）全部通过。
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
- **Documentation**：新重大 Problem / Decision / 架构变更是否记录？
- **Risk**：剩余风险、未验证假设、已知限制是否报告？

全部通过才允许进入 COMPLETE。任何一项为 No → NOT COMPLETE（REJECT COMPLETION）。

COMPLETION GATE 只能在 **VERIFICATION PASSED 且 REVIEW 通过**之后执行；「失败已记录」「手动验证（无具体输出）」「代码看起来正确」均不得作为通过依据。

## 10. Harness 自身变更

修改六个核心文件或 `docs/problem/` 时，除正常流程外 MUST 执行 TESTING.md §9 HARNESS REVIEW。
发现 Harness 自身矛盾：先修复 Harness，不要继续扩展规则。
