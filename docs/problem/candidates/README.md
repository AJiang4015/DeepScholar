# docs/problem/candidates/ — Problem Candidate（候选问题缓冲带）

## 目的

Problem Registry（PROBLEM.md）的登记门槛不降低；本目录收纳"有长期价值但尚未达到登记标准"的
观察（AGENTS.md §11.3 三问任一 Yes，但 PROBLEM.md 登记标准未满），作为
`Problem Discovery → Candidate → Review → Registry` 生命周期的中间态：避免知识流失，
又不污染正式索引。

## 准入条件

满足以下全部才建立 Candidate：

- 命中 AGENTS.md §11.3 三问之一（新的长期约束 / 未来 Agent 需要知道的信息 / 重复发生风险）；
- 尚未满足 PROBLEM.md 登记标准（证据不足、影响未明、或仅一次性出现）；
- 判断为后续任务或未来 Agent 可能需要。

排除：普通 Bug、临时调试问题、已在 Registry 中的问题（→ 直接更新对应记录）、无长期价值的
记录。

## 必填字段

- Title
- Discovered（日期 + 来源任务）
- Trigger（命中 AGENTS.md §11.1 触发清单的哪一条）
- Evidence（现象 / 复现步骤 / 文档位置 / 相关文件）
- Why Not Registered（缺少哪条登记标准）
- Promotion Hint（晋升为正式 Problem 需补什么证据）

## 晋升规则

任一情形触发晋升评估，满足 PROBLEM.md 登记标准后按该文件使用规则创建
`docs/problem/P0NN-*.md` + 索引行（含 Type），并删除 / 标注本候选：

- 后续任务 near-miss 命中，证据补足；
- REVIEW / Harness Review 裁决认定已达登记标准；
- 问题复现或影响升级。

## 淘汰规则

Candidate 被修复 / 被正式登记吸收 / 长期（跨多个任务）无证据支撑 → 删除，并在对应报告 /
PR 中记录原因；不堆死档案。

## 模板

```markdown
# <slug> — <Title>

- Discovered: <日期> @ <来源任务>
- Trigger: <AGENTS.md §11.1 触发清单条目>
- Evidence: <现象 / 复现步骤 / 文档位置>
- Why Not Registered: <缺少的登记标准项>
- Promotion Hint: <晋升需补的证据>
```
