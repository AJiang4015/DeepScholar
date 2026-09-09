# Semantic Module Layout — Implementation Plan（F9 目录语义化重构 · Phase 2 实施计划）

> 状态：**PLAN READY（待用户 Review）**
> 分支：`refactor/semantic-module-layout`
> 定位：Module Relocation Only —— 本计划只含移动 / import 改写 / 文档同步 / 验证，**不含任何行为行改动**。
> 前置：Readiness Review `docs/plan/2026-10-02-semantic-module-layout-readiness-review.md`（用户已批准 Mapping 与裁决）。

---

## 0. 实施前提（已满足 / 需保持）

- [x] branch = `refactor/semantic-module-layout`（自干净 main 创建）；
- [x] 工作树除本 Readiness/Plan 文档外无改动；
- [ ] 实施全程不 Commit（等用户批准 Commit Gate）；
- [ ] 实施前再次 `git status` 确认无外来改动（Mixed/Unowned Working Tree → STOP）。

## 1. 步骤 A — 物理迁移（git mv，保留历史；此步不改内容）

```bash
git mv app/f9/projection.py   app/research/projection.py
git mv app/f9/gaps.py         app/research/gaps.py
git mv app/f9/judge.py        app/research/judge.py
git mv app/f9/plan.py         app/research/plan.py
git mv app/f9/targeted.py     app/research/targeted.py
git mv app/f9/orchestrator.py app/research/orchestrator.py
git mv app/f9/eval            app/research/eval        # 整个子包（含 eval/__init__.py）
git rm  app/f9/__init__.py                              # 之后 app/f9/ 为空 → 目录消失
```

验收：`app/f9/` 不存在；`git status --short` 显示 rename/move。

## 2. 步骤 B — 被迁移代码内 import 与 docstring 路径改写（仅字符串行）

按 Readiness §4.1，逐文件替换（行为行一律不碰）：

| 文件（迁移后） | 替换 |
|---|---|
| `app/research/orchestrator.py` | L33-37 `from app.f9 import gaps/judge/plan/projection/targeted` → `from app.research import …`；L227 `from app.f9.judge import gap_id_of` → `from app.research.judge …` |
| `app/research/plan.py` | L257/264/377/519 `from app.f9.judge import …` → `from app.research.judge import …` |
| `app/research/gaps.py` | docstring L5 `app/f9/projection.py` → `app/research/projection.py` |
| `app/research/eval/agents.py` | L19/247 `from app.f9.eval.world …` → `from app.research.eval.world …` |
| `app/research/eval/harness.py` | L143 lazy `from app.f9.orchestrator import f9_orchestrator` → `from app.research.orchestrator import f9_orchestrator`（**符号名不变**） |
| `app/research/eval/rubric.py` | L21 / L121 `from app.f9.eval.harness …` → `from app.research.eval.harness …` |
| `app/research/eval/scenarios.py` | L15/16/244/311/335 `app.f9.eval.*` → `app.research.eval.*` |
| `app/research/eval/calibration.py` | L26 `import app.f9.gaps as _f9g` → `import app.research.gaps as _f9g`；L27-30 `from app.f9.eval import agents/harness/rubric/scenarios` → `from app.research.eval import …` |
| `app/research/eval/__init__.py` | docstring 目录/归属文字更新（world/agents/harness/rubric/scenarios/calibration 仍列；F9-P0 Batch7 溯源保留；明确现位于 `app/research/eval`） |
| `app/research/__init__.py` | docstring 扩展：Research 领域 = ① Data/Evidence Plane（F1–F7，保持"数据面核心不依赖 app/agent、与 checkpoint 解耦"等既有描述）② Research Intelligence/Execution（projection/gaps/judge/plan/targeted/orchestrator；单 governed execution 内；不拥有 runtime 权威）③ eval（确定性质量验证/校准） |

> 其余被迁移文件（projection.py / gaps.py / judge.py / targeted.py / eval/world.py）不含
> `app.f9` 引用 → 除上述 docstring 外不改内容。

**回归探针**（此后任何时刻执行，应保持 0 命中，仅剩故意保留的溯源文字如 `f9_orchestrator`/`F9-P0`）：
`rg -n "app\.f9|app/f9|app\\f9" app tests`

## 3. 步骤 C — 测试语义化改名 + import 更新（Flag C）

`tests/` 内：

```bash
git mv tests/_f9_helpers.py               tests/_research_helpers.py
git mv tests/test_f9_projection.py        tests/test_research_projection.py
git mv tests/test_f9_projection_postgres.py  tests/test_research_projection_postgres.py
git mv tests/test_f9_gaps.py              tests/test_research_gaps.py
git mv tests/test_f9_gaps_postgres.py     tests/test_research_gaps_postgres.py
git mv tests/test_f9_judge.py             tests/test_research_judge.py
git mv tests/test_f9_plan.py              tests/test_research_plan.py
git mv tests/test_f9_targeted.py          tests/test_research_targeted.py
git mv tests/test_f9_targeted_postgres.py tests/test_research_targeted_postgres.py
git mv tests/test_f9_orchestrator.py      tests/test_research_orchestrator.py
git mv tests/test_f9_eval_harness.py      tests/test_research_eval_harness.py
git mv tests/test_f9_eval_rubric.py       tests/test_research_eval_rubric.py
git mv tests/test_f9_eval_scenarios.py    tests/test_research_eval_scenarios.py
git mv tests/test_f9_eval_scenarios_claims.py tests/test_research_eval_scenarios_claims.py
git mv tests/test_f9_calibration.py       tests/test_research_calibration.py
```

随后在改名文件中替换：
- `app.f9` → `app.research`（模块路径；`from tests import _f9_helpers as h` → `_research_helpers`）；
- 文件 docstring 内对旧测试文件名的交叉引用（如 `test_f9_projection.py` / `_f9_helpers`）同步改新名；
- **不改**测试函数/类名与断言逻辑（函数内 `f9p/f9g/f9t/f9o` 等局部 alias 保留 —— 非结构命名，
  最小 diff）；docstring 中 "F9-P0 Batch n" 溯源保留。

## 4. 步骤 D — Living Documentation 同步（Flag A + 完成标准）

| 文件 | 改动点（最小） |
|---|---|
| `ARCHITECTURE.md` | ① §2 模块表 `app/research` 行：扩展为双子域描述 —— Data/Evidence Plane（F1–F7 既有文字保持）+ Research Intelligence/Execution（projection/gaps/judge/plan/targeted/orchestrator + eval；单 governed execution 内；**业务研究编排，不拥有 lifecycle/budget/cancel/timeout/terminal authority** —— F8 Controller 仍唯一权威）+ eval（确定性行为质量验证/校准 infra）。② §3 依赖清单：`app/research/* → 无 app 内依赖` 拆分为：数据面核心模块维持该不变量（含不依赖 app/agent）；Research Intelligence/Execution 与 eval 允许依赖 `app.runtime.governance`（受治理 LLM/上下文契约，F8 control 原样传播），并仅以 **lazy import** 引用默认 seam（`app.agent.main_agent` / `app.tools.tavily_tool` / `app.api.monitor`）。③ §3 F9 orchestrator 例外条款：路径 `app/f9/orchestrator.py` → `app/research/orchestrator.py`，并补一句"属业务研究编排，不拥有 Runtime governance 权威"（Flag A）。④ 改后执行 TESTING.md §9 HARNESS REVIEW 清单（ARCHITECTURE 为六核心文件）。 |
| `README.md` | ① §3 架构图 RI 子图注释 "Research Intelligence Layer（F9-P0 app/f9）" → "Research Intelligence Layer（app/research）"（节点名与连线不动）。② §11 仓库树：删除 `f9/` 分支，`research/` 条目展开为数据面（既有 F1–F7 文件）+ 智能层六模块 + `eval/`。③ §11 tests 行注释 "governance / research / f9 / PG gate" → 去掉 f9（改为 research 含 intelligence/eval/calibration 等）。④ 其余 F9-P0 叙事文字（§1/§5 等）保持。 |
| `PROJECT_CONTEXT.md` | ① §2 "F9 orchestration plane：`app/f9/`（…）" → "Research Intelligence / Research Execution：`app/research/`（projection/gaps/judge/plan/targeted/orchestrator + eval；原 F9-P0 orchestration plane 语义迁入，单 governed execution 内）"。② §6 历史记录中的 `app/f9/orchestrator.py` 路径 → `app/research/orchestrator.py`（living 索引同步，历史叙述不改）。③ 全文件其它 `app/f9` 出现处同类替换（grep 定位）。 |
| `docs/interview/f9-p0-interview-material.md` | 分层图 "Research Intelligence Layer — F9-P0（app/f9，…）" → "（app/research，…）"（1 处路径标签；presentation 材料路径一致性）。 |

历史 docs（`docs/plan/2026-09-21…2026-10-01-*`、`docs/spec/*`）：**不改**（Flag D）。

## 5. 步骤 E — 残留检查（全仓）

```bash
# 活跃代码：必须 = 0
rg -n "app\.f9|from app\.f9|import app\.f9|app/f9|app\\f9" app tests
# living docs：必须 = 0（README / ARCHITECTURE / PROJECT_CONTEXT / docs/interview）
rg -n "app\.f9|app/f9|app\\f9" README.md ARCHITECTURE.md PROJECT_CONTEXT.md docs/interview
# 历史 docs：允许保留（Flag D）——列出作为 residual references 附录，不修改
rg -l "app/f9|app\.f9" docs/plan docs/spec
# 目录不存在
Test-Path app/f9
```

## 6. 步骤 F — 验证（TESTING.md §1/§2 + Readiness §7）

按序执行并逐条记录输出（失败 → 修复 → 重跑，不得带失败宣称完成）：

1. `python -m compileall -q app tests`
2. `ruff check app tests`（如遇与本重构无关的既有告警 → 按 Readiness §7.2 收口处理并报告）
3. 定向 sqlite（迁移后的 F9-P0 / eval / calibration 全部用例）
4. Batch7 eval 场景与 Batch8 calibration 断言
5. F1–F8 全量回归：`python -m pytest tests/ -q`（排除既有 mysql 污染文件；PG 无 DSN 自动 skip）
6. PG 全量：尝试 `python -m pytest tests/test_research_*_postgres.py tests/test_*_postgres.py -q`
   —— 本环境无 docker / 无 `RESEARCH_DSN_TEST` / `AGENT_CHECKPOINT_DSN_TEST` → **如实报告 skip**，
   真实 PG 81-pass 门列为用户环境执行项（提供 DSN 或在其机器上跑），**不伪造证据**。
7. `git diff --check`；`git status`；`git diff main...refactor/semantic-module-layout --stat`
   （无 Commit；diff 仅含迁移/import/doc）。

## 7. 步骤 G — 交付（不 Commit）

产出 `F9 Semantic Module Refactoring Report`（docs/plan/2026-10-02-semantic-module-layout-implementation-report.md +
chat 摘要）：
- Before/After 文件对照（git status rename 证据）
- import 变化清单（§2/§3 表）
- 文档变化清单（§4 表）
- 验证结果（§6 每条命令输出 + 结论；PG 门如实标注）
- Scope Audit：未改变事项（F1–F8 / F9-P0 行为 / schema / API / runtime / agent topology / 生产接线）
- Residual references：历史 docs 中保留的 `app/f9` 文字清单（Flag D 预期残留，非缺陷）

然后 **STOP**：呈报 git diff/status + 报告，等用户 Review → 再决定 Freeze / Commit / Push / Merge。

## 8. Out of Scope（再次声明）

本计划不含：生产接线、symbol rename、行为/常量/阈值修改、其它 research 模块重构、
依赖升级、DB/config/API 改动、历史文档改写。任何步骤发现越界 → STOP 报告。
