# PROBLEM.md — Problem Registry（问题索引）

> 本文件只是 **Problem Index**，不承载问题细节。细节、元数据与证据见 `docs/problem/` 对应文件。
> 登记标准（满足任一）：影响系统正确性 / 安全性、导致重要故障、暴露架构缺陷、导致重复工程成本、
> 需要长期保留上下文、未来 Agent 可能重复遇到、具有重要设计或决策价值、需要跨任务持续追踪。
> 普通 Bug、临时调试问题、一次性实现细节：不得进入注册表。

## 索引

| ID | Status | Severity | Title | Summary | Detail |
|----|--------|----------|-------|---------|--------|
| P001 | Mitigated | High | SQL 工具缺少只读强制约束 | 已由 D007 修复：工具层只读校验 + 表名白名单 + 参数绑定 + 审计日志（app/utils/sql_security.py）；部署层只读账号未做 | docs/problem/P001-sql-tool-write-sql.md |
| P002 | Mitigated | High | 文件工具会话目录隔离可绕过 | R1 修复：resolve_path 全分支 resolve + is_within(session_dir) 统一校验，越界抛 PathSafetyError（fail-closed）；三个工具返回"安全拒绝" | docs/problem/P002-file-tool-path-escape.md |
| P003 | Mitigated | High | 上传接口路径穿越写入 | R1 修复：文件名净化(basename+非法字符清洗) + 类型白名单 + 大小上限 + 随机存储名 + manifest 原名映射 | docs/problem/P003-upload-path-traversal.md |
| P004 | Mitigated | Medium | session_id/thread_id 未净化拼入目录 | R1 修复：thread_id 安全字符集 [A-Za-z0-9_-]{1,128}；/api/task 非法换新、/api/upload 与 WS 非法拒绝 | docs/problem/P004-session-id-path-traversal.md |
| P005 | Won't Fix | Medium | 无认证/授权边界 | 全系统无鉴权（任务/WS/上传/下载）；README 能力边界已声明为教学边界 | docs/problem/P005-no-auth-boundary.md |
| P006 | Open | Medium | 无自动化测试基础设施 | 无 tests/、无 pytest；README 声称 tests/ 存在但与实际不符 | docs/problem/P006-no-test-infrastructure.md |

## 使用规则

- 新问题符合登记标准：在 `docs/problem/` 创建独立文件（文件名 `P0NN-<slug>.md`，稳定可引用），并在此表追加一行。
- 状态 / 严重度变更：只更新对应 `docs/problem/` 文件与本表，不在本表展开分析。
- Agent 执行任务前 MUST 检查本表是否命中；命中后 MUST 阅读对应 Detail 文件。
- 不要把问题完整分析复制进本表。

## 元数据字段

每个 `docs/problem/` 文件必须包含：ID / Title / Status（Open、Investigating、Mitigated、Resolved、Won't Fix）/
Severity（Critical、High、Medium、Low）/ Created / Last Updated / Path / Summary / Related Decision / Related Architecture / Related Tests。
