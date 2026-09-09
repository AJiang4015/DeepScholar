# Semantic Module Layout — Readiness Review（F9 目录语义化重构 · Phase 2 Pre-Implementation）

> 状态：**READY / IMPLEMENTATION READY（待用户 Review 本 Readiness + Implementation Plan 后批准实施）**
> 分支：`refactor/semantic-module-layout`（自干净 `main` = `1148994` 创建）
> 类型：L2/L3 边界语义化重构（Module Relocation Only；零行为变更）
> 关联：Phase 1 Mapping（2026-10-02 用户 Review 通过 + 裁决 OD1/OD2/Flag A–D）

---

## 1. 任务一句话

把 `app/f9/`（Feature 编号命名）按稳定领域职责**只做迁移**并入 `app/research/`
（Research Data/Evidence Plane + Research Intelligence / Execution + eval），
全量更新 import / 测试名 / living docs / ARCHITECTURE 依赖契约；**不改变任何算法、
行为、schema、API、Runtime 语义与生产接线**。

## 2. 用户裁决记录（本 Readiness 的契约输入）

| 裁决 | 内容 | 状态 |
|---|---|---|
| Open Decision 1 | `orchestrator.py` → `app/research/orchestrator.py`（业务研究编排，**不进** runtime/ agent/） | 已批准 |
| Open Decision 2 | Eval → `app/research/eval/`（暂不建顶层 `app/eval/`） | 已批准 |
| Flag A | 同步更新 ARCHITECTURE.md 依赖边界描述：区分 Research Data/Evidence Plane 与 Research Intelligence/Execution；保留 Runtime Governance 权威边界；明确 Research Orchestrator 是业务研究编排，不拥有 lifecycle/budget/cancellation/timeout/terminal authority | 批准并要求处理 |
| Flag B | 保持生产未接线：不改 server.py / governance service baseline path；不新增生产接线 | 批准 |
| Flag C | 测试文件语义化改名 `test_f9_*` → `test_research_*`；`_f9_helpers.py` → `_research_helpers.py`（依赖分析确认其为 Research 测试共享 helper，见 §4.3）；同步测试内部 import / patch target | 批准 |
| Flag D | 历史 docs/plan、历史 F9 Spec、Batch completion/freeze 报告**保持原状**不重写 | 批准 |

## 3. 锁定 Mapping（迁移清单，唯一权威）

```text
Before                                      After
app/f9/__init__.py                       → 删除（docstring 语义并入 app/research/__init__.py）
app/f9/projection.py                     → app/research/projection.py
app/f9/gaps.py                           → app/research/gaps.py
app/f9/judge.py                          → app/research/judge.py
app/f9/plan.py                           → app/research/plan.py
app/f9/targeted.py                       → app/research/targeted.py
app/f9/orchestrator.py                   → app/research/orchestrator.py
app/f9/eval/__init__.py                  → app/research/eval/__init__.py
app/f9/eval/world.py                     → app/research/eval/world.py
app/f9/eval/agents.py                    → app/research/eval/agents.py
app/f9/eval/harness.py                   → app/research/eval/harness.py
app/f9/eval/rubric.py                    → app/research/eval/rubric.py
app/f9/eval/scenarios.py                 → app/research/eval/scenarios.py
app/f9/eval/calibration.py               → app/research/eval/calibration.py
```

迁移后 `app/f9/` 目录不存在；顶层不新增任何目录；`app/research/` 成为
"Research 领域"（数据/证据面 + 语义算法 + 智能执行层 + 质量验证）。

## 4. 契约闭合核查（Contract Closure）

### 4.1 代码引用面（全量盘点，实施后须归零）

Python 内引用 `app.f9` 的位置（除被迁移文件自身外）：

| 类别 | 文件 | 说明 |
|---|---|---|
| 包内互引 | orchestrator.py（6 处）、plan.py（4 处 lazy）、eval/*（agents/harness/rubric/scenarios/calibration 多处） | 随迁移改为 `app.research.*` / `app.research.eval.*` |
| 测试 | test_f9_projection(+_postgres)、gaps(+_postgres)、judge、plan、targeted(+_postgres)、orchestrator、eval_harness、eval_rubric、eval_scenarios、eval_scenarios_claims、calibration | 14 个文件；随改名 + import 更新归零 |
| 生产代码 | **无**（app/ 除 f9 自身外零引用；server/agent/runtime/tools 不 import app.f9） | 已核 |

非 Python living docs 引用（须更新，Flag D 之外）：`ARCHITECTURE.md`（§3 例外条款路径）、
`README.md`（架构图 RI 标签 + 仓库树 + 测试族注释）、`PROJECT_CONTEXT.md`（§2/§6 路径）、
`docs/interview/f9-p0-interview-material.md`（分层图路径标签，1 处）。

历史 docs（Flag D，**不改**）：`docs/plan/2026-09-21…2026-10-01-*`（Batch/readiness/plan/
report/Final Completion）、`docs/spec/2026-09-19-f9-p0-*.md` 等。

### 4.2 `_f9_helpers.py` 归属确认（Flag C 条件成立）

`tests/_f9_helpers.py` = research artifact（research_runs/sub_questions/search_queries/sources/
evidences/claims/claim_evidences/verifications/conflicts/reconciliations/corroborations）的
跨后端 seeding helpers，被 projection/gaps/targeted 及其 PG gate 测试共享 —— 领域归属 =
**Research 测试共享 helper** → 更名为 `tests/_research_helpers.py`（docstring 首行去掉
"F9-P0 Batch 1" 结构前缀、保留溯源注释；内容零改动）。

### 4.3 依赖方向核查（迁移后，须写入 ARCHITECTURE）

| 迁移模块 | app 内依赖（现状，随搬迁不变） | 对 app/research 数据面不变量影响 |
|---|---|---|
| projection / gaps | 仅 `app.research.store` / 无 | **无新跨包边**（包内） |
| judge / plan | `app.runtime.governance.{callbacks,context,counters}`（顶层） | research 智能层新增→runtime/governance 依赖（受治理 LLM 契约，F8 control 原样传播） |
| targeted | 同上 + `app.tools.tavily_tool`（lazy 默认 seam） | 同上 + lazy tools seam |
| orchestrator | `app.runtime.governance.context` + lazy `app.agent.main_agent` / `app.api.monitor` / research 内部 | lazy agent/api 默认 seam |
| eval/* | runtime governance controller/store/models + lazy agent/main_agent（monkeypatch 目标） | infra 层，仅测试/校准引用 |

结论：数据面核心模块的不变量（无 app 内依赖、不依赖 app/agent）**不受影响**；
新增的是 Research Intelligence/Execution + eval 对 `app.runtime.governance` 的**受控依赖**
与对 `app.agent.main_agent` / `app.tools.tavily_tool` / `app.api.monitor` 的 **lazy seam 引用**
（与既有 F9-scoped orchestrator 例外同语义，见 ARCHITECTURE.md §3 例外条款）。
包内依赖无环（orchestrator→{projection,gaps,judge,plan,targeted}→…→research 数据面；eval→orchestrator/gaps）。
无 import-time 环：orchestrator/eval 对 agent/main_agent 的引用全部在函数体内 lazy。

### 4.4 无 schema / 无迁移 / 无配置 / 无 API 影响

- `db/` 全量 grep `f9` = 0 → 无 DB schema / migration 影响；
- pyproject/.env/.pre-commit/frontend/scripts 无 `app.f9` 引用；
- HTTP 端点 / WS schema / LangChain 工具签名 / prompts.yml 零触碰。

### 4.5 保留不改的 "f9" 文字（非结构命名，属溯源/内部值）

- 符号名：`f9_orchestrator`（用户明确：本阶段不做 symbol rename）；
- 模块 docstring / 测试 docstring 中的 "F9-P0 Batch n / F9 语义" 溯源引用
  （对齐 `app/research` 既有 F2–F7 溯源先例）；
- 常量值：`CONFIG_VERSION="f9-plan-config-v1"`（参与 source_universe identity，
  改动即行为变化 → 禁止改）；
- eval 内部 owner 字符串 / thread 前缀（`f9-b7-eval` / `b7-*`，进程内标识，非结构）。

## 5. 风险登记（Relocation 风险与缓解）

| 风险 | 等级 | 缓解 |
|---|---|---|
| 漏改 import → import 期失败 | 高 | 迁移后 grep `app\.f9|app/f9|app\\f9`（py 全部）+ `compileall app` + 定向 pytest 全绿 |
| 测试改名后 docstring 交叉引用旧测试文件名 | 低 | 改名时同步替换文件内对旧测试名的引用（如 _postgres docstring 指向 test_f9_projection.py） |
| PG gate 本环境不可执行（无 docker / 无 RESEARCH_DSN_TEST / 无 AGENT_CHECKPOINT_DSN_TEST） | 中 | PG-gated 文件自动 skip 属既有纪律；**真实 PG 全量验证列为用户环境 Gate**（§7.2），不伪造证据 |
| ARCHITECTURE 契约改写失真 | 中 | 改写仅做"描述现实分层"，不新增/删除权威语义；改动后过 HARNESS REVIEW 清单（TESTING.md §9） |
| 误改行为（顺手重构/rename 符号） | 高 | Scope 禁令清单（§6）；实施只用 `git mv` + import 字符串替换 + doc 文字，不触碰逻辑行 |
| monitor description 字符串 / eval owner 等内部值被误改 | 低 | 列明保留清单（§4.5），实施时 grep 复查 |

## 6. 禁止事项（Scope Audit 边界）

- 禁止修改 F9 算法逻辑（projection/gaps/judge/plan/targeted/orchestrator/eval 任何行为行）；
- 禁止修改 stopping / planning / judging / targeted research 行为与常量（含
  MIN_EVIDENCE / BUDGET_NEAR_RATIO / RESEARCH_POLICY_DEFAULTS / CONFIG_VERSION / MAX_*）；
- 禁止修改 Runtime Controller / Governance / checkpoint / F8 任何代码；
- 禁止修改生产执行路径（server.py / governance service / run_deep_agent 主线）与新增接线；
- 禁止修改 DB schema / migration / 配置体系 / API contract / WS schema；
- 禁止顺带重构其他 research 模块、禁止顺手优化代码、禁止 symbol rename
  （`f9_orchestrator` 等现有符号名保持）；
- 禁止修改历史 docs（Flag D）；
- 实施期间禁止 Commit（等待用户后续批准）。

## 7. 完成标准 / 验证契约（对应 Phase 2 完成标准）

### 7.1 结构验收（静态）

- [ ] `app/f9/` 不存在；`git status` 显示 move 而非 copy；
- [ ] 六模块 + `app/research/eval/` 全部就位；
- [ ] 全仓 .py grep `app\.f9|app/f9|app\\f9` = **0**；
- [ ] 全仓 living docs（README / ARCHITECTURE / PROJECT_CONTEXT / docs/interview）无旧路径残留；
- [ ] 历史 docs 无任何改动（Flag D，`git status` 不含 docs/spec 与 2026-10-01 前 docs/plan）。

### 7.2 质量门（命令与基线）

| 门 | 命令（本环境） | 预期 |
|---|---|---|
| 语法 | `python -m compileall -q app`（+ `tests`） | PASS |
| Lint | `ruff check app tests`（系统 ruff 0.15.22；如遇与本次无关的既有告警则按文件范围收口并报告） | PASS（改动面） |
| F9-P0 定向（sqlite） | `python -m pytest tests/test_research_{projection,gaps,judge,plan,targeted,orchestrator}.py tests/test_research_eval_*.py tests/test_research_calibration.py -q` | PASS（原 test_f9_* 全部用例） |
| Batch7 Eval | 同上 eval 组 | 16/16 + scenarios 8/8 |
| Batch8 Calibration | test_research_calibration.py | 4/4 |
| F1–F8 回归（sqlite 全量） | `python -m pytest tests/ -q`（排除既有 mysql 污染文件；PG 文件无 DSN 自动 skip） | ≥731 passed / 88 skipped / 0 failed |
| PG 全量 | 需 PG16 + `RESEARCH_DSN_TEST`/`AGENT_CHECKPOINT_DSN_TEST`（本环境无 docker/DSN） | **用户环境 Gate**：14 个 *_postgres 文件 81 passed / 0 failed |
| git | `git diff --check`；`git diff main...HEAD`（或 --stat）| 无 whitespace error；diff 只含迁移/import/doc |

### 7.3 Scope Audit 验收

- [ ] diff 仅含：13 个文件 move + import 行 + 包 docstring + 测试改名/import + 指定 living docs；
- [ ] 无 F1–F8 / F9 行为 / schema / API / runtime governance / agent topology 改动；
- [ ] 无新增生产接线；
- [ ] 最终交付 `F9 Semantic Module Refactoring Report`（迁移文件 / import 变化 / 文档变化 /
      验证结果 / 未改变事项 / residual references 清单）。

## 8. 结论

Contract 已闭合：Mapping 锁定、裁决全部落账、引用面与验证门确定、风险与禁令明确。
**Readiness = READY**，等用户 Review 后进入 Implementation（仍在本分支，不 Commit）。
