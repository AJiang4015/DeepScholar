# PROBLEM.md — Engineering Problem Registry（工程问题注册表 · 索引）

> 本文件只是 **Problem Index**，不承载问题细节。细节、元数据与证据见 `docs/problem/` 对应文件。
> 注册表同时登记故障类问题与**长期工程知识**：问题不限定为安全漏洞 / 生产故障，还包括运行时
> 行为约束、架构约束、测试 / 环境限制、流程误解导致的返工、文档与实现偏差、Agent 易重复犯的
> 错误模式等（Type 词表见下）。
> 登记标准（满足任一，门槛不降低）：影响系统正确性 / 安全性、导致重要故障、暴露架构缺陷、
> 导致重复工程成本、需要长期保留上下文、未来 Agent 可能重复遇到、具有重要设计或决策价值、
> 需要跨任务持续追踪。
> 普通 Bug、临时调试问题、一次性实现细节：不得进入注册表。

## Problem Lifecycle（登记生命周期）

```text
Problem Discovery（任务中发现；触发清单 AGENTS.md §11.1）
  → Problem Candidate（docs/problem/candidates/，未达门槛但有长期价值）
  → Review（按登记标准判断：Agent 自评 / 用户裁决）
  → Problem Registry（本表 + docs/problem/P0NN-*.md）
  → Resolution Tracking（Status 演进；Mitigated/Resolved 须指向修复证据）
```

Candidate 不进本表；晋升为正式 Problem 时创建 `docs/problem/` 文件并在此表追加一行（含 Type），
同时按 `docs/problem/candidates/README.md` 规则移除或标注对应 candidate。

## Type（问题类型，单选主类）

| Type | 含义 |
| --- | --- |
| Security | 安全边界 / 漏洞 / 凭据 / 攻击面 |
| Correctness | 逻辑正确性缺陷（结果、状态、数据错误） |
| Architecture | 架构约束、边界缺失、模块契约问题 |
| Runtime | 运行时行为、并发 / 生命周期 / 持久化 / 恢复 |
| Testing | 测试与验证基础设施限制及陷阱 |
| Process | 流程误解 / 工作流导致的返工 |
| Environment | 环境、工具、外部服务、凭据限制 |
| Agent Workflow | Agent 易重复犯的错误模式（工具使用、上下文、输出习惯） |

## 索引

| ID | Type | Status | Severity | Title | Summary | Detail |
|----|------|--------|----------|-------|---------|--------|
| P001 | Security | Mitigated | High | SQL 工具缺少只读强制约束 | 已由 D007 修复：工具层只读校验 + 表名白名单 + 参数绑定 + 审计日志（app/utils/sql_security.py）；部署层只读账号未做 | docs/problem/P001-sql-tool-write-sql.md |
| P002 | Security | Mitigated | High | 文件工具会话目录隔离可绕过 | R1 修复：resolve_path 全分支 resolve + is_within(session_dir) 统一校验，越界抛 PathSafetyError（fail-closed）；三个工具返回"安全拒绝" | docs/problem/P002-file-tool-path-escape.md |
| P003 | Security | Mitigated | High | 上传接口路径穿越写入 | R1 修复：文件名净化(basename+非法字符清洗) + 类型白名单 + 大小上限 + 随机存储名 + manifest 原名映射 | docs/problem/P003-upload-path-traversal.md |
| P004 | Security | Mitigated | Medium | session_id/thread_id 未净化拼入目录 | R1 修复：thread_id 安全字符集 [A-Za-z0-9_-]{1,128}；/api/task 非法换新、/api/upload 与 WS 非法拒绝 | docs/problem/P004-session-id-path-traversal.md |
| P005 | Security | Won't Fix | Medium | 无认证/授权边界 | 全系统无鉴权（任务/WS/上传/下载）；README 能力边界已声明为教学边界 | docs/problem/P005-no-auth-boundary.md |
| P006 | Testing | Open | Medium | 无自动化测试基础设施 | 无 tests/、无 pytest；README 声称 tests/ 存在但与实际不符 | docs/problem/P006-no-test-infrastructure.md |
| P007 | Runtime | Open | High | Runtime 缺少健康平面：进程退出/执行器失联后任务永久 RUNNING 且不可诊断 | 执行期无 heartbeat / stale 判定 / reclaim 路径；`orphan_reclaimed`·`aborted` 无生产产出、`flush_pending` 无调用方；由 P2-2（D-Phase2-P2-2-001~017）实现关闭 | docs/problem/P007-no-runtime-health-plane.md |

> 存量问题（P001–P006 建立于 Type 字段引入之前）：Type 以本表为准；对应 `docs/problem/` detail
> 文件内容未改动。新登记问题 MUST 在 detail 文件元数据中带 Type。

## 使用规则

- 新问题符合登记标准：在 `docs/problem/` 创建独立文件（文件名 `P0NN-<slug>.md`，稳定可引用），
  并在本表追加一行（含 Type）。
- 未达门槛但有长期价值：创建 `docs/problem/candidates/<slug>.md`（规则与模板见
  `docs/problem/candidates/README.md`），不进入本表；晋升评估在后续 REVIEW / Harness Review
  或问题复现（near-miss）时进行。
- 状态 / 严重度变更：只更新对应 `docs/problem/` 文件与本表，不在本表展开分析；变更时同步
  Last Updated 并记录原因。
- Resolution Tracking：Mitigated / Resolved 必须在 detail 文件指向修复证据（Decision / Spec /
  Test / commit）；Open / Investigating 为追踪中。
- Agent 执行任务前 MUST 检查本表是否命中；命中后 MUST 阅读对应 Detail 文件。任务疑似命中既有
  candidate（near-miss）→ 阅读，并在本任务 Problem Capture Review（PROCESS.md §13）时评估
  补充证据或晋升。
- 不要把问题完整分析复制进本表。

## 元数据字段

每个 `docs/problem/` 文件必须包含：ID / Title / **Type（新登记必填；存量以索引为准）** /
Status（Open、Investigating、Mitigated、Resolved、Won't Fix）/ Severity（Critical、High、
Medium、Low）/ Created / Last Updated / Path / Summary / Related Decision / Related
Architecture / Related Tests。
