# P001 — SQL 工具缺少只读强制约束（可执行任意写 SQL）

## Status

Mitigated（工具层校验已落地，见 Resolution 与 DECISION.md D007；部署层只读账号未做）

## Severity

High

## Created / Last Updated

2026-09-02 / 2026-09-02

## Path

`app/tools/db_tools.py`（`execute_sql_query`、`get_table_data`）

## Problem

`execute_sql_query` 把模型生成的 SQL 原样交给 `cursor.execute(query)` 执行（第 194 行），没有任何只读校验；
连接使用 `autocommit=True`（第 36 行），因此 `DELETE` / `UPDATE` / `INSERT` / `DROP` / `ALTER` 等写语句会被**立即提交**。
对模型的实际约束为 **零**：全仓库 grep「只读」仅 1 处命中（db_tools.py:193 代码注释）；prompts.yml 的 `sub_agents.db.system_prompt` 与三个工具 docstring 均无任何只读/仅查询指令，工具层也无校验。代码注释声称"依赖提示词约束模型生成只读查询"，但该约束文本从未写入提示词——注释意图与实际实现不一致，造成虚假安全感。

`get_table_data` 把 `table_name` 直接拼接进 SQL（第 130 行 `f"SELECT * FROM {table_name} LIMIT 100"`），表名未做白名单/转义。

## Impact

- 恶意或失控的用户提示可以诱导模型执行任意写 SQL，污染/清空数据库。
- 若该工具模式被复用到生产数据库，将直接导致数据破坏。
- 表名拼接为 SQL 注入入口（表名通常来自模型输出，非直接用户输入，但模型由用户提示驱动）。
- 注释（193 行）声称存在提示词约束，构成虚假安全感：审计者可能误以为已有防护。

## Root Cause

教学优先的设计**意图**（代码注释第 193 行）是把只读约束放在提示词层而非工具层，但提示词中从未写入该约束，实际为零约束。见 DECISION.md D001。

## Evidence

- `app/tools/db_tools.py:194` `cursor.execute(query)` 直接执行任意 SQL。
- `app/tools/db_tools.py:36` `"autocommit": True`。
- `app/tools/db_tools.py:130` 表名直接拼接进 SQL。
- `app/tools/db_tools.py:193` 注释声称依赖提示词约束，但提示词中无该约束（全仓库「只读」仅此 1 处）。

## Scope

### In Scope

- 工具层只读校验（如剥离注释后校验首个有效关键字为 SELECT / SHOW / WITH / EXPLAIN，拒绝多语句）。
- `get_table_data` 表名白名单（只接受 `list_sql_tables` 返回的表名）。

### Out of Scope

- 数据库账号权限分离（部署层变更，需 Decision）。

## Constraints

- 现有提示词（prompts.yml）依赖 execute_sql_query 支持多表 JOIN / 聚合 / 子查询，只读校验不得破坏这些合法查询能力。
- 教学演示场景（docker MySQL）需要保留"可执行自定义查询"的教学价值。

## Known Failure Modes

- 只读白名单过严导致合法聚合/子查询被拒（破坏教学链路）。
- 只校验首关键字可被 `/*!50000 SELECT */`、`WITH ... SELECT`、括号注释、大小写变体等绕过。
- 分号分隔的多语句注入。

## Candidate Solutions

1. 工具层只读校验：剥离注释后检查首个有效关键字；拒绝分号多语句；大小写不敏感。
2. 表名白名单：get_table_data 只接受 list_sql_tables 返回的表名。
3. 数据库只读账号（部署层）。
4. 维持现状（零约束）——**被拒绝**：不满足 fail-closed。

## Resolution

已修复（Mitigated，见 DECISION.md D007）：

- **工具层强制只读**：`app/utils/sql_security.py` `validate_read_only_sql` 只放行
  SELECT / SHOW / DESC / DESCRIBE / EXPLAIN 单条语句；拒绝写语句、多语句（分号）、
  大小写与前导注释绕过、`/*! ... */` 可执行版本化注释、`SELECT ... INTO OUTFILE/DUMPFILE`。
- **表名白名单**：`extract_table_names` + `validate_allowed_tables` 覆盖
  FROM / JOIN / 逗号表列表（含 AS 别名）/ WHERE 子查询；派生表、WITH CTE、表函数
  fail-closed 拒绝；`get_table_data` 增加表名规范化 + 白名单。
- **参数绑定**：`execute_sql_query(query, params=None)` 动态值经数据库驱动 %s 绑定，
  不做字符串拼接；`prepare_read_only_query` 证明 SQL 与参数原样透传。
- **审计日志**：每次 `execute_sql_query` 记录语句类型/语句/结果/耗时/thread_id，
  不记录参数值（logger `deepsearch.audit.sql`）。
- **提示词**：prompts.yml db system_prompt 补充只读约束（最小修改）。
- **测试**：tests/test_db_tools.py（86 项纯函数安全测试，无 DB 依赖）
  + tests/test_db_tools_mysql_integration.py（真实 MySQL 集成，环境缺失自动 skip）。

剩余风险：

- 数据库只读账号（部署层）未做——应用层校验是唯一防线，账号层面仍可写；
- 词法级校验无法覆盖全部 SQL 方言（CTE / 派生表 / 表函数被拒是主动 fail-closed）；
- 教程 git 分支（02–14）存在早期版本工具代码，主分支修复不等于分支修复。

## Related Decisions

- D001（SQL 只读约束：设计意图为提示词层，但约束文本从未落地）
- D007（P001 修复：工具层强制只读 + 表名白名单 + 参数绑定 + 审计日志）

## Related Architecture

- ARCHITECTURE.md §5（安全边界）、§8（Architecture Change Gate）

## Related Tests

- 无（当前无自动化测试，见 P006）。修复后 MUST 新增：
  - 拒绝写语句（DELETE / UPDATE / INSERT / DROP / ALTER / CREATE）
  - 拒绝多语句与注释/大小写绕过变体
  - 接受合法 SELECT / JOIN / 聚合 / 子查询
  - get_table_data 拒绝非白名单表名

## Remaining Risks

- 教程 git 分支（02–14）存在早期版本工具代码；主分支修复不等于分支修复。
- 模型可能在合法查询中混入危险子句，校验器需对抗性测试。
