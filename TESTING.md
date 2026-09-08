# TESTING.md — Verification Contract（验证契约）

> 本文件定义：Agent 如何证明自己做对了，以及什么证据才能宣称完成。
> 当前仓库现实：**无自动化测试框架（P006）**——不存在 tests/ 目录、无 pytest、前端无 test 脚本。
> 因此本契约区分「现在可验证的」与「必须新增的」，禁止虚构不存在的验证手段。

## 1. 当前可执行的验证手段（事实）

| 手段 | 命令 | 验证什么 |
|------|------|----------|
| 依赖一致性 | `uv sync --frozen` | 依赖锁与 pyproject.toml 一致 |
| Lint / Format | `pre-commit run --all-files`（声明于 .pre-commit-config.yaml） | 静态检查（trailing-whitespace / end-of-file-fixer / check-yaml / check-toml / ruff） |
| 语法可导入 | `uv run python -m compileall -q app` | 无语法错误 |
| 前端类型 + 构建 | `cd frontend && pnpm build`（= `tsc -b && vite build`） | TS 类型检查与打包 |
| 手动 E2E | 见 §3 | 端到端链路（需 MySQL 与 LLM/Tavily/RAGFlow 凭据） |
| 安全回归（pytest） | `python -m pytest tests/ -q`（系统 Python；含 uv 环境时 `uv run pytest tests/ -q`） | 安全敏感改动的自动化回归（P001 修复引入：tests/test_db_tools.py 纯函数安全测试，无外部服务依赖、随时可跑；集成测试文件在环境缺失时自动 skip） |
| Checkpoint 回归（pytest，含 PG 门控） | 同上 `tests/ -q` | checkpoint backend 抽象 / AsyncSaver 恢复 / replay 验证。sqlite 用例任何环境可跑；PG 用例（test_checkpoint_postgres.py、test_checkpoint_replay_verify.py 的 PG 类）仅当 `psycopg`/`psycopg-pool`/`langgraph-checkpoint-postgres` 可导入 **且** `AGENT_CHECKPOINT_DSN_TEST` 指向独立测试库时才执行，否则整类 skip（TESTING.md §1 skipif 纪律；PG 服务可经 docker/docker-compose.postgres.yaml 启动） |
| Research 回归（pytest，含 PG 门控） | 同上 `tests/ -q` | research data plane（app/research）：normalize/registry/provenance/fail-open/store+migration；F2 claims/validator（R1–R10）/render_citations；F3 semantic verification；F4 conflict detection；F5 corroboration（global clustering / independent_count / cross-side independence）；F6 reconciliation（outcome 判定 / Unknown≠NotIndependent / claim register / run_unresolved）；F7 research bridge（Claim materialization / anchored-unanchored 语义 / F2–F6 orchestration enabled-skipped_off / finalization identity / fail-open；sqlite 默认；PG 门控 test_*_postgres.py 需 psycopg + `RESEARCH_DSN_TEST`） |

**PG 测试环境纪律（2026-09 起）**：`AGENT_CHECKPOINT_DSN_TEST` / `RESEARCH_DSN_TEST`
**只允许指向独立测试库**，禁止指向开发/生产库；不采用共享库临时 schema 作为默认方案。
checkpoint 表族由官方 saver setup() 自管，research_* 表族由 app/research/migrations 自管，
测试不得修改其 DDL。

**不存在**：jest、前端测试目录。pytest 自 P001 修复（DECISION.md D007）起存在——
tests/ 已建立、以系统 Python 运行，但尚未在 pyproject.toml / uv.lock 声明为 dev
dependency（P006 残余）；纯函数层测试（tests/test_db_tools.py）无重依赖可独立运行，
导入 app.tools 的测试需 mysql-connector / langchain / fastapi 等已安装。
禁止在完成报告中伪造这些命令的执行结果。
注：ruff 未在 pyproject.toml / uv.lock / requirements.txt 中声明（见 P006）——pre-commit
经 `uv run --frozen ruff ...` 调用，干净环境下可能不可用；本机系统 Python 装有
ruff 0.15.22，可 `ruff check/format` 直接调用。若不可用，如实报告为验证缺口，不得伪造输出。

## 2. 验证类型与使用时机

验证类型（分类，供 §4 报告引用）：

- **Static / Type / Lint Checks**：§1 中的 lint / format / compileall / `pnpm build`。
- **Targeted Tests**：针对本次改动的自动化测试。当前无框架；安全敏感改动 MUST 新增（§5）。
- **Regression Tests**：一旦存在自动化测试，改动相关模块后 MUST 运行该模块全部测试。
- **Integration Tests**：涉及 API / WS 链路时，用 TestClient 或手动 E2E 验证。
- **Security Tests**：涉及安全敏感代码时 MUST 按实际攻击面选择负路径（§5）。

验证命令按**改动范围**选择（REQUIRED；不是每次全量执行）：

| 改动范围 | REQUIRED 验证 |
| --- | --- |
| Python 后端代码（app/、examples/） | compileall（`uv run python -m compileall -q app`，或对改动文件单独执行）+ 适用的 lint/format |
| 前端代码（frontend/src/） | `cd frontend && pnpm build` |
| 依赖文件（pyproject.toml / requirements.txt / uv.lock） | `uv sync --frozen` |
| YAML / TOML 等配置 | 对应格式检查（pre-commit 的 check-yaml / check-toml） |
| 纯文档 / Harness 文档 | 只执行适用的文档一致性验证（§9 HARNESS REVIEW 按需） |
| 安全敏感代码（§5 清单） | 上述适用项 + §5 自动化测试要求 |
| API / WS 链路改动 | 上述适用项 + Integration Tests（TestClient 或 §3 手动 E2E） |

全仓库级检查（如 `pre-commit run --all-files`）不是每次强制：是否执行由改动范围决定，决定与结果都须在完成报告中说明。

## 3. 手动 E2E 流程（后端链路）

1. `docker compose -f docker/docker-compose.yaml up -d`（本地 MySQL 教学库）
2. `uv run uvicorn app.api.server:app --port 8000`
3. `POST /api/task {"query": "<任务>"}`，记录返回的 thread_id
4. 连接 `ws://127.0.0.1:8000/ws/{thread_id}`，记录 monitor_event 序列
5. 任务结束后 `GET /api/files` 与 `GET /api/download` 验证产物

证据 = 每条命令的输出（关键片段）+ 断言结论。

## 4. Evidence Requirement（完成报告六问）

完成报告 MUST 回答：

- What changed?（改动文件清单 + diff 摘要）
- What was tested?（§1/§2 中实际执行的项）
- Which commands were executed?（完整命令 + 关键输出）
- What was the result?（每项通过 / 失败）
- Which acceptance criteria were verified?（AC → Evidence 映射，§6）
- What remains unverified?（显式列出）

禁止用以下表述代替证据：**"Tests should pass." / "Looks good." / "Seems correct." / "Verified manually."**（无具体输出时）。

补充判定：

- 「手动验证」只有在附有具体命令、输出与断言结论时才可作证据（当前无自动化测试且改动不要求新增测试时，§3 手动 E2E 是合法验证手段）。
- 「失败已记录」在任何情况下都不构成验证通过证据（见 PROCESS.md §5：记录失败 ≠ 验证通过）。
- 「代码看起来正确」「根据经验判断」在任何情况下都不构成证据。

## 5. Security 验证规则（安全敏感代码）

安全敏感代码：SQL 执行（`app/tools/db_tools.py`）；文件读写与路径解析（`app/utils/path_utils.py`、`app/tools/upload_file_read_tool.py`、`markdown_tools.py`、`pdf_tools.py`、`app/api/server.py` 上传/下载/列表）；外部凭据（`app/ragflow/`、`app/agent/llm.py`）；任何用户可控输入（thread_id、filename、query）。

对这些代码的改动 MUST：

1. 阅读 PROBLEM.md 中相关登记问题（P001–P005；P005 为 Won't Fix 边界记录，同样必读）与对应 `docs/problem/` 记录。
2. 新增自动化回归测试（pytest）。
3. 负路径测试**按实际攻击面选择**（见下方 Security Test Matrix），覆盖四类：正常路径、典型非法输入、绕过路径、边界输入。不得要求与本次攻击面无关的负路径（例如与 SQL 无关的任务不要求测试 SQL 攻击）。
4. 验证 **fail-closed**：非法输入必须被拒绝，而不是被放行或部分执行。
5. pytest 引入流程（与 DECISION.md D006 的 Gate 关系）：MUST 在 PLAN 阶段向用户提出引入 pytest 的决策请求（Architecture Change Gate）；若用户拒绝引入 pytest，相应验收标准视为未满足——MUST 报告测试缺口并保持 NOT COMPLETE，不得静默降级为"手动验证通过"。

### Security Test Matrix（负路径按攻击面选择）

| 攻击面 | 负路径（按改动范围取子集） |
| --- | --- |
| P001 SQL 工具 | 写 SQL（DELETE/UPDATE/INSERT/DROP/ALTER/CREATE）、多语句注入、大小写变体、注释绕过（`/*!...*/`、WITH 前缀）、非法 SQL |
| P002 文件路径隔离 | `../` 逃逸、绝对路径、`updated/` 前缀、包含性绕过（containment bypass）、重复 session 名 |
| P003 上传接口 | filename 路径穿越（`../`、`..\`）、绝对路径 filename、嵌套 filename、异常 filename（空、超长、特殊字符） |
| P004 thread_id/session_id | 路径穿越、分隔符（`/`、`\`）、畸形 thread_id、空串、超长、碰撞 |

安全边界与测试冲突时：MUST NOT 为让测试变绿而弱化安全边界（AGENTS.md §6）。

## 6. Acceptance Criteria → Evidence 映射

非平凡任务 MUST 建立四元映射：

```text
Acceptance Criterion → Implementation（文件/函数） → Test（测试/命令） → Evidence（输出）
```

示例（P001 修复后）：

```text
AC-1 只接受只读 SQL
  → db_tools.py 只读校验函数
  → pytest test_rejects_write_operations()
  → 测试输出（拒绝 DELETE/UPDATE/INSERT/DROP）
```

REVIEW 阶段（PROCESS.md §6）MUST 能对每个重要 AC 指出对应证据；无法指出 = NOT COMPLETE。

## 7. COMPLETION GATE 明细

宣称完成前逐项自检（与 PROCESS.md §9 对应）：

### Problem
- [ ] 是否解决 Active Problem？
- [ ] 全部验收标准满足？
- [ ] 未越出问题范围？

### Architecture
- [ ] 架构边界保持（ARCHITECTURE.md）？
- [ ] 未触发 Forbidden Changes？
- [ ] 新架构决策已记录（DECISION.md）？

### Testing
- [ ] 定向验证已按改动范围执行（§2）：已有自动化测试 → 相关 targeted tests 已运行；无自动化测试且改动不要求新增 → 适用的现有验证手段已执行；安全敏感改动 → §5 自动化测试要求已满足
- [ ] 回归测试执行（如有自动化测试）
- [ ] 相关 Static / Lint / Type 检查通过（按改动范围，§2）
- [ ] AC → Evidence 映射完整

### Change Scope
- [ ] 无无关文件改动
- [ ] 无投机重构
- [ ] 无多余依赖变更
- [ ] 无意外 API 变更

### Security
- [ ] 安全不变量保持
- [ ] 负路径已测（按实际攻击面，见 §5 Security Test Matrix）
- [ ] 绕过路径已考虑（替代 API、校验顺序、失败路径）
- [ ] fail-closed 保留

### Documentation
- [ ] 新重大 Problem 已登记（符合 PROBLEM.md 标准时）
- [ ] 新重大 Decision 已记录
- [ ] 架构变更已文档化

### Risk
- [ ] 剩余风险已识别
- [ ] 未验证假设已列出
- [ ] 已知限制已报告

任一项为 No → NOT COMPLETE，回 PROCESS.md 对应状态（REJECT COMPLETION）。

## 8. Adversarial Self-Review（REVIEW 阶段必答问题）

```text
What could still be wrong?
What input could bypass this implementation?
What existing behavior could have regressed?
What assumption did I make without evidence?
What architecture boundary could I have crossed?
What test case would most likely expose my mistake?
What would a malicious caller try?
What happens on malformed / empty / duplicated / unexpected / unauthorized input?
What happens when dependencies (LLM / Tavily / RAGFlow / MySQL) fail?
```

安全敏感改动追加：

```text
How can this security control be bypassed?
Can validation be bypassed through another code path?
Does validation happen BEFORE the dangerous operation?
Does an alternate API provide the same dangerous capability?
Does failure result in rejection or accidental execution?
```

REVIEW 交叉验证：除逐项作答外，MUST 至少执行一条针对"最可能暴露错误"的测试或手工验证并记录结果（防止自审流于形式）。

## 9. HARNESS REVIEW（Harness 自身变更时）

修改六个核心文件或 `docs/problem/` 时，除正常流程外 MUST 逐项检查：

- [ ] 每个文档只有一个主要职责
- [ ] 无重大规则重复
- [ ] 无互相冲突规则
- [ ] PROCESS.md 与 AGENTS.md 一致
- [ ] TESTING.md 与 PROCESS.md 一致
- [ ] PROBLEM.md 是索引而非详细日志
- [ ] docs/problem/ 承载详细记录
- [ ] DECISION.md 记录理由而非流水账
- [ ] ARCHITECTURE.md 反映真实仓库
- [ ] 完成标准可客观验证
- [ ] 自审可以拒绝完成
- [ ] 安全敏感改动有更强的验证规则

发现 Harness 自身矛盾：先修复 Harness，不得继续扩展规则。
