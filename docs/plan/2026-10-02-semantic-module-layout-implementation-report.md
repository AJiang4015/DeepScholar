# F9 Semantic Module Refactoring Report（Semantic Module Layout · Implementation）

> 状态：**IMPLEMENTATION COMPLETE · Freeze Approved（用户 Review 通过 2026-10-02）**
> 分支：`refactor/semantic-module-layout`（基线 = `main` @ `1148994`）
> 性质：Module Relocation Only（git mv + import 更新 + 包 docstring + 测试改名 + living docs + ARCHITECTURE 契约 + D018 登记 + 验证）
> 关联：Readiness `docs/plan/2026-10-02-semantic-module-layout-readiness-review.md`、
> Implementation Plan `docs/plan/2026-10-02-semantic-module-layout-implementation-plan.md`（用户 Review 通过）

---

## 1. Before / After Mapping + Rename 证据

### 1.1 迁移清单（全部经 `git mv`，git 检测为 rename）

| Before | After | rename 相似度（git） |
|---|---|---|
| app/f9/__init__.py | **删除**（docstring 语义并入 app/research/__init__.py） | D |
| app/f9/projection.py | app/research/projection.py | R100（纯移动，0 内容改动） |
| app/f9/gaps.py | app/research/gaps.py | R099（仅 docstring 路径行） |
| app/f9/judge.py | app/research/judge.py | R100（纯移动） |
| app/f9/plan.py | app/research/plan.py | R099（仅 4 处 lazy import） |
| app/f9/targeted.py | app/research/targeted.py | R100（纯移动） |
| app/f9/orchestrator.py | app/research/orchestrator.py | R098（仅 6 处 import） |
| app/f9/eval/__init__.py | app/research/eval/__init__.py | R076（docstring 归属更新） |
| app/f9/eval/world.py | app/research/eval/world.py | R100（纯移动） |
| app/f9/eval/agents.py | app/research/eval/agents.py | R098（仅 2 处 import） |
| app/f9/eval/harness.py | app/research/eval/harness.py | R099（仅 1 处 lazy import） |
| app/f9/eval/rubric.py | app/research/eval/rubric.py | R098（仅 2 处 import） |
| app/f9/eval/scenarios.py | app/research/eval/scenarios.py | R097（仅 import 行） |
| app/f9/eval/calibration.py | app/research/eval/calibration.py | R097（仅 import 行） |

`app/f9/` 目录已不存在（残留 __pycache__ 已清理，gitignore 内容无 git 影响）。

### 1.2 "只移动，没有改内容"证明（用户验收 #7）

- `git diff main -M -- app` 全量审查：**被迁移源码的 diff 只含** (a) `app.f9`/`app/f9` → `app.research`/`app/research` import 路径行；
  (b) 两处已批准 docstring 路径/归属行（`gaps.py` 上游路径行；`eval/__init__.py` + `app/research/__init__.py` 包 docstring 扩展）。
- **4 个文件 100% 纯移动零 diff**：projection.py / judge.py / targeted.py / eval/world.py。
- 无任何行为行、常量、阈值、策略、符号名（`f9_orchestrator`、`CONFIG_VERSION="f9-plan-config-v1"`、`f9-b7-eval`、`b7-*` 等）被改动。
- 测试 diff 仅含 import 路径、`_f9_helpers`→`_research_helpers`、docstring 中旧测试文件名引用；断言/逻辑/局部 alias 零改动。
- 无意外新增/删除/重写文件（git name-status：仅 1 D + 13 R(app) + 15 R(tests) + 6 M(docs/code)）。

## 2. Import 变化清单

| 文件 | 变化 |
|---|---|
| app/research/orchestrator.py | L33-37 五个 `from app.f9 import …` → `app.research`；L227 lazy `gap_id_of` → `app.research.judge` |
| app/research/plan.py | L257/264/377/519 lazy judge import → `app.research.judge` |
| app/research/gaps.py | docstring L5 旧路径 → `app/research/projection.py` |
| app/research/eval/agents.py | L19 顶层 + L247 lazy `app.f9.eval.world` → `app.research.eval.world` |
| app/research/eval/harness.py | L143 lazy `app.f9.orchestrator import f9_orchestrator` → `app.research.orchestrator`（符号名不变） |
| app/research/eval/rubric.py | L21 + L121 lazy `app.f9.eval.harness` → `app.research.eval.harness` |
| app/research/eval/scenarios.py | L15/16 + lazy 244/311/335 → `app.research.eval.*` |
| app/research/eval/calibration.py | L26-30 → `app.research.gaps` / `app.research.eval.*` |
| 生产代码（api/agent/runtime/tools/server） | **0 处**（本就不 import app.f9） |

`app/research/__init__.py` docstring 扩为 Research 领域三段式（Data/Evidence Plane · Research Intelligence/Execution · eval）；`app/research/eval/__init__.py` docstring 注明归属与只读引用边界（无失效路径字面量）。

## 3. 测试重命名（Flag C）

15 个文件 `git mv`：`tests/_f9_helpers.py → _research_helpers.py`；`test_f9_{projection(+_postgres),gaps(+_postgres),judge,plan,targeted(+_postgres),orchestrator,eval_harness,eval_rubric,eval_scenarios,eval_scenarios_claims,calibration}.py → test_research_*.py`。同步更新：内部 import（`app.f9` → `app.research`、`_f9_helpers` → `_research_helpers`）与 docstring 中旧测试文件名交叉引用。测试行为零改动。

## 4. Living Documentation 变化

| 文件 | 变化点 |
|---|---|
| ARCHITECTURE.md | §2 新增 `app/research（② Research Intelligence/Execution + eval；原 F9-P0）` 表行（orchestrator = 业务研究编排、不拥有 runtime 权威、eval 定位）；§3 依赖清单拆分数据面核心（维持无 app 内依赖）与智能层（允许依赖 runtime/governance + lazy seam）；§3 F9 orchestrator 例外路径 → `app/research/orchestrator.py` + 权威边界澄清句 |
| README.md | §3 架构图 RI 标签 → "Research Intelligence Layer（F9-P0 · app/research）"；§11 仓库树合并 f9/ 入 research/（数据面 + 智能层 + eval）；tests 注释去 f9 |
| PROJECT_CONTEXT.md | §2 Research Intelligence/Execution 位置行；§6 Batch6 记录中 orchestrator/test 路径 |
| docs/interview/f9-p0-interview-material.md | 分层图 1 处路径标签 |
| DECISION.md | 新增 **D018**（决策记录：语义重构 + 归属裁决 + 依赖契约 + 约束），满足 ARCHITECTURE §8 / TESTING §7 记录要求 |

> 说明：D018 为按 Harness 完成门要求补充的 Decision 记录（living 决策文档新增，非历史改写）；如你裁定其超出本次授权范围，可要求移除后重新走 Gate。

## 5. ARCHITECTURE 契约变化（Flag A 摘要）

- **不变量保持**：Research Data/Evidence Plane 核心模块"无 app 内依赖、不依赖 app/agent"；F8/Runtime 权威边界原样；工具/agent 依赖红线原样；无 import app.api.server 例外。
- **新增分层描述**：Research Intelligence/Execution + eval 允许依赖 `app.runtime.governance`（GovernanceExecution · make_handler · counters；F8 control 原样传播不吞）；默认 seam 仅 **lazy import**（main_agent / tavily_tool / monitor）。
- **权威边界**：Research Orchestrator 属业务研究编排，**不拥有** lifecycle / budget / cancellation / timeout / terminal authority —— F8 Controller 仍唯一权威（已写入 §2/§3）。
- 修改后已按 TESTING.md §9 HARNESS REVIEW 自审（单职责 / 无重复冲突规则 / 反映真实仓库 / 完成标准可验证 —— 全部满足；六核心文件中仅 ARCHITECTURE.md 被触碰）。

## 6. 验证结果（命令与输出）

| 门 | 命令 | 结果 |
|---|---|---|
| 语法 | `.venv\Scripts\python.exe -m compileall -q app tests` | **PASS**（exit 0） |
| Lint | `ruff check app tests` | **PASS**（All checks passed, exit 0） |
| 定向 Research/Eval/Calibration（sqlite） | `pytest tests/test_research_{projection,gaps,judge,plan,targeted,orchestrator}.py tests/test_research_eval_{harness,rubric,scenarios,scenarios_claims}.py tests/test_research_calibration.py -q` | **175 passed**（17.7s） |
| F1–F8 全量回归（sqlite） | `pytest tests/ -q --ignore=tests/test_db_tools_mysql_integration.py -p no:cacheprovider` | **731 passed / 88 skipped / 0 failed**（47.1s；与基线完全一致） |
| PG gate | `pytest tests/ -q -k postgres -p no:cacheprovider` | 无 `RESEARCH_DSN_TEST`/`AGENT_CHECKPOINT_DSN_TEST` → **88 skipped（纪律 skip），0 failed** |
| git | `git diff --check` | **PASS**（无 whitespace error） |

**PG gate 状态：User Environment Gate（未在本环境执行真实 PG）**
本机无 docker、无 `RESEARCH_DSN_TEST`/`AGENT_CHECKPOINT_DSN_TEST` → PG-gated 文件全部按既有 skipif 纪律跳过；
**不伪造 81 passed**。真实 PG16 全量验证需你方环境执行：
`RESEARCH_DSN_TEST=<独立测试库> AGENT_CHECKPOINT_DSN_TEST=<独立测试库> .venv\Scripts\python.exe -m pytest tests\test_research_*_postgres.py tests\test_*_postgres.py -q`
（历史口径：14 个 postgres 文件 81 passed / 0 failed；迁移后预期同值——本重构零行为改动。）

## 7. Scope Audit（用户验收 #7 逐项）

1. **migrated source 是否 rename/move**：✅ 全部 `git mv`；git 相似度 R076–R100（4 个 R100 纯移动）。
2. **除 import / 批准 docstring 路径外是否有行为代码 diff**：✅ 无（§1.2 全量 diff 审查）。
3. **测试除 import / 文件名引用外是否有行为 diff**：✅ 无（diff 逐行核验）。
4. **是否有意外新增/删除/重写文件**：✅ 无（name-status 全在批准 Scope；仅 2 份计划文档 + 本报告为新增 docs/plan 文档，属交付物）。
5. **`git diff main...HEAD` 是否只落在批准 Scope**：✅ 35 个 tracked 文件（1 D + 13 R app + 15 R tests + 6 M：ARCHITECTURE/DECISION/PROJECT_CONTEXT/README/research__init__/interview），全部对应计划 §2–§4；+3 untracked docs/plan（readiness/plan/report）。

**未改变事项（确认清单）**：F1–F8 语义与代码；F9-P0 算法/stopping/planning/judging/targeted/orchestrator 行为；
常量与阈值（MIN_EVIDENCE / BUDGET_NEAR_RATIO / RESEARCH_POLICY_DEFAULTS / MAX_* / CONFIG_VERSION）；
Runtime Controller / Governance / checkpoint；Agent topology；生产执行路径（server.py / governance service / run_deep_agent）；无新增生产接线；
DB schema / migration / config / API / WS；symbol 名（`f9_orchestrator` 等）；历史 docs（Flag D）。

## 8. Residual Historical References（Flag D 预期残留，非缺陷）

全仓 `.py`（app/tests/examples/…）对 `app.f9`/`app/f9` 的引用：**0**（grep 复核）。
Living docs（README/ARCHITECTURE/PROJECT_CONTEXT/docs/interview）：**0**。
历史文档保留 `app/f9` 文字（未改动，共 ~17 个 docs/plan 文件，Flag D）：
`docs/plan/2026-09-21…2026-10-01-f9-p0-batch*-{readiness-review,implementation-plan,implementation-report}.md`、
`2026-09-22-harness-document-governance-report.md`、`2026-10-01-f9-p0-final-completion-report.md`。
另：本分支新增的 readiness/plan/report 文档含 Before 状态路径引用（预期，记录重构过程）。

## 9. 异常与限制

- **ruff format --check**：本仓库存在**既有**格式漂移（`app/tools/markdown_tools.py`、`tests/test_agent_events.py` 与若干未动文件漂移；
  我们动过的 eval/测试文件亦含历史 drift——逐 hunk 核验均不在本重构改动行）。仓库先例 = D015–D017 "format 仅历史 drift 未改"；
  本次遵循该先例**不越界格式化**（`ruff check` 为 lint 门，PASS）。如需恢复全仓 format 干净属独立收尾任务（超出本 Scope）。
- pytest cache 写入告警（Windows 权限）已用 `-p no:cacheprovider` 规避；不影响结果。
- 本环境测试依赖经 `.venv`（3.12）运行；向 .venv 补装 pytest 9.1.1（仅本地环境工具，未改 pyproject/uv.lock——延续 P006 残余）。
- 真实 PG gate 未执行（User Environment Gate，见 §6）。

## 10. Git 状态

- branch：`refactor/semantic-module-layout`；35 tracked 变更 + 3 docs/plan 文档（readiness / plan / report）；
  `git diff --check` PASS。
- **Freeze Approved（用户 2026-10-02）→ Commit 已授权**（本报告随 commit 固化后于此处回填 hash）；
  Push / Merge 留待下一阶段用户 Review。

---

**STOP —— 等待用户 Review（Freeze / Commit / Push / Merge 决策）。**
