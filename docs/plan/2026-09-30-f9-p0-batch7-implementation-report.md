# F9-P0 Batch 7 — Eval / Behavioral Quality Validation Implementation Report

> 状态：**BATCH 7 PASS / FROZEN（用户 2026-09-30 正式 Freeze；branch
> `feature/f9-batch7-eval`；Git Commit / Push / Merge 待用户另行批准）**。
> 依据：用户 2026-09-30 批准（D1–D6 Decision Closure CLOSED；L2；Readiness CLOSED）；
> Batch7 Implementation Plan `docs/plan/2026-09-30-f9-p0-batch7-implementation-plan.md`；
> Spec Rev2 §14 Eval Acceptance。Git Workflow：本批全程在
> **`feature/f9-batch7-eval`** branch（AGENTS.md §10 / PROCESS.md §12）。
> 本批不 Freeze / Commit / Push / Merge（按用户指令，完成后 STOP 等待 Review）。

## 1. Scope / Deliverables

新增（feature branch 内）：

- `app/f9/eval/__init__.py`、`world.py`、`agents.py`、`harness.py`、`rubric.py`、
  `scenarios.py`（eval harness + deterministic world + scripted agents + 双执行编排 +
  metrics + rubric + 8 scenarios 定义）；
- `tests/test_f9_eval_harness.py`（5）、`tests/test_f9_eval_rubric.py`（3）、
  `tests/test_f9_eval_scenarios.py`（5，档 A 跨 run 对照）、
  `tests/test_f9_eval_scenarios_claims.py`（3，档 B claim 级）＝ **16 tests**；
- 本报告。

零修改：F8 / F1–F7 / `main_agent.py` / `orchestrator.py` / Batch1–6 / schema·migration /
runtime infra / UI / `_govadapt` / Redis·Kafka·Neo4j·Vector / durable round state。
baseline 经真实 `run_deep_agent` 入口（进程内替换 `get_main_agent`，不改文件）。

## 2. 实验契约落实（D2/D3）

- Baseline = `run_deep_agent`；Adaptive = `f9_orchestrator`；各自独立
  `Controller.execute`（独立 governance store / research DB / task / run）。
- 相同 benchmark task spec / 相同 F8 policy / 相同 deterministic world（world 路由 +
  scripted judge/plan/search/verifier）。
- 未加 orchestrator baseline-only mode；未改 orchestrator 行为。
- Gate 主路径 = deterministic scripted；real provider 未执行（无凭据；报告 Limitations）。
- 公平起点：adaptive round0 graph runner 与 baseline 使用同一 world 初始查询
  （results_for：显式路由；无结果 = 初始证据不足 → 触发 gap）；targeted 补搜经
  search_tool（results_any：路由优先、extra 兜底）。

## 3. Scenarios（D6：8 个；档 A 5 + 档 B 3）

| id | 场景 | kind | 断言（Gate） | 结果 |
|---|---|---|---|---|
| s1 | already-sufficient → no unnecessary follow-up | control | adaptive `executed_plans==0`、`stopped_reason=no_gap`、finalize==1 | PASS |
| s2 | missing required → targeted follow-up | positive | adaptive `executed_plans>=1`、rubric required 0→1、verdict gate_pass | PASS |
| s3 | unverified claim → re-verification | positive (seam/preseed) | preseed claim run 复用；changed_claims 含 claim、verification_succeeded>=1 | PASS |
| s4 | conflict → F4 seam 兼容 | positive (seam/preseed) | preseed run 上 `detect_claim_conflicts` 可执行、不破坏 state | PASS |
| s5 | insufficient corroboration → F5 seam 兼容 | positive (seam/preseed) | preseed run 上 `compute_claim_corroboration` 可执行、确定性 | PASS |
| s6 | diminishing / correct stopping | control | completed、finalize==1、stopping ∈ {no_gap,max_rounds,no_progress,all_unimportant} | PASS |
| s7 | near-budget stopping | control | completed、`cost_compliance==1`、counters ≤ limits | PASS |
| s8 | Judge failure → same-execution fallback | negative | `stopped_reason=adaptive_fallback`、finalize==1（F7 once）、error 记录 | PASS |

说明：s3/s4/s5 的 claim 语义（F3/F4/F5/F6 判定细节）由既有 F3–F6 模块测试 + Batch6
F456 changed-only matrix 覆盖；Batch7 在 claim 级验证"preseed claim run 可被 orchestrator
复用且不丢状态、changed claims 驱动 verification、F4/F5 与 claim state 兼容"（诚实 seam
边界，不重复模块测试、不 pad）。

## 4. Evaluator（D5：deterministic / rubric-based）

`rubric.py` 8 维度（0–1）：required_coverage / citation_coverage / evidence_sufficiency /
unsupported_score / conflict_coverage / redundant_score / cost_compliance + total；verdict
= adaptive 在 ≥1 grounding 维度 > baseline 且 total ≥ baseline（no-pad：相等 → gate False）。
全部确定性规则，无 real LLM。real-LLM quality judge 未实现（仅 supplementary 概念，D5）。

## 5. Metrics（D4：零 schema）

- rounds：orchestrator summary（返回值）；
- research metrics：Registry/projection 只读查询（evidence/source/claim/verification/
  conflict/reconciliation/binding 计数）；
- cost：governance TaskRecord `counters_snapshot` + `effective_limits`；
- final answer：baseline agent.final / adaptive finalize_sink 捕获。

## 6. Verification

- sqlite 全量：**727 passed / 88 skipped / 0 failed**（= 711 既有 + Batch7 16；排除既有
  mysql 污染文件）。
- PG 双 DSN（Batch5 口径 / 全 14 postgres 文件）：**81 passed / 0 failed**（不受影响）。
- ruff / compileall（app/f9/eval + 测试）：全绿。
- eval tests 16 passed：harness 5（world 路由语义 / baseline 确定性 / rubric shape）＋
  rubric 3（deterministic 分数 / verdict no-pad）＋ 场景档 A 5 ＋ 档 B 3。

## 7. Baseline vs Adaptive 指标（代表性；deterministic，可复现）

- s2（missing required）：baseline required_coverage=0、total=4；adaptive required=1、
  total=6、`improved=[required_coverage, citation_coverage]`、delta=+2 → **adaptive 补足
  required 且成本合规**（orchestrator 真实轮次 no_gap 停止，非靠轮次堆叠）。
- s1（already-sufficient）：adaptive 不额外加搜（plans==0），语义 = 不为指标加搜
  （R2-6 防"加搜充收益"）。
- s8（judge failure）：adaptive_fallback → completed，F7 once、无第二 run。

（完整逐场景断言见 §3 表与 tests；此处为 Gate 判定摘要。）

## 8. Limitations（如实记录）

1. **deterministic scripted 为主**：real provider（真实 LLM/search）未执行 —— 本会话无
   有效凭据（OPENAI_API_KEY/TAVILY_API_KEY 仅占位），Gate 全部为 scripted 断言。
2. **glue 差异**：baseline `run_deep_agent` 注入 path_instruction/session-dir；
   adaptive `f9_orchestrator` graph runner 仅 task prompt（D3 记录为限制；未改冻结文件）。
   real-model 归因受该差异限制，报告中显式声明。
3. s3/s4/s5 为 preseed claim run 的 seam 兼容验证（F3–F6 判定细节属既有模块测试与
   Batch6 F456 matrix 范围），非 orchestrator 自动 reconcile 的端到端断言。
4. world/脚本内容为确定性构造，非真实语料；citation 用 locator 子串匹配（简化近似）。
5. budget/timeout/cancel 的 adversarial 行为已由 Batch6 F7-once matrix 覆盖；本批补充
   near-budget（s7）与 fallback（s8）对照。

## 9. Scope Audit / Compatibility Gap

- 新增文件仅 `app/f9/eval/*` 与 4 个 `tests/test_f9_eval_*.py`（＋本报告）；
- 未触碰冻结边界（§1 清单）；无 schema/migration；无 runtime infra；无 baseline-only
  mode；未改 main_agent/orchestrator 文件；
- **COMPATIBILITY GAP：无**（实现过程未发现需突破 Batch6/F8/F1–F7 frozen contract 的点）。
- baseline 对 `main_agent.get_main_agent` 的替换仅在 harness 进程内（monkeypatch 语义），
  不改文件、不绕过 run_deep_agent 入口（ARCHITECTURE §3 例外不触发）。

## 10. Git / Branch 状态

- branch：`feature/f9-batch7-eval`（base：main fdc0cdd + harness closure ce53b09）。
- 工作区变更（未 commit）：`app/f9/eval/*`、4 个 eval 测试、docs/plan decision-closure 与
  implementation-plan、PROJECT_CONTEXT.md（Batch7 状态同步）。
- 未 Freeze / Commit / Push / Merge（等待 User Review）。

## 11. Final Decision

```text
F9-P0 Batch 7 — Eval / Behavioral Quality Validation
STATUS: PASS / FROZEN（2026-09-30 用户正式 Freeze）
branch: feature/f9-batch7-eval
```

- 验收项（用户 Review 确认）：Eval 16/16 PASS、8/8 deterministic scenarios PASS、
  SQLite 727 passed / 88 skipped / 0 failed、PostgreSQL 81 passed / 0 failed、
  ruff / compileall / diff-check PASS、Compatibility Gap = None。
- Limitations（已记录）：real provider 未执行（无凭据）；baseline/adaptive glue 差异；
  s3/s4/s5 preseed claim run 的 seam 边界；citation locator 子串匹配简化。
- Git 状态：工作树保持 Batch7 scope（未 Commit / Push / Merge）；Git Commit 待用户明确
  批准后执行（AGENTS.md §10 / PROCESS.md §12 流程）。
