# F9-P0 Final Architecture / Completion Report（Evidence-Driven Adaptive Research Loop）

> 状态：**F9-P0 Core Implementation = COMPLETE（用户 2026-10-01 正式决定并收敛）**。
> 本报告 = F9-P0 阶段收尾的最终架构与完成度文档（documentation-only，零产品代码改动）。
> 依据：用户 2026-10-01 指令（Batch9 停止推进；persistence 归 Future/P1；
> F9-P0 Core 宣告 COMPLETE；进入 Documentation / Presentation 阶段）；
> F9-P0 Spec Rev2（`docs/spec/2026-09-19-f9-p0-evidence-driven-research-loop.md`）；
> Implementation Plan Rev2（`docs/plan/2026-09-20-f9-p0-implementation-plan.md`）；
> Batch1–8 各 Implementation Report（见 §12 References）；Batch9 Readiness Review
> `docs/plan/2026-10-01-f9-p0-batch9-readiness-review.md`。
> Git 基线：main = `be6d625`（Batch8 merged；Batch1–8 全部 PASS/FROZEN；本地=远程一致）。
> 本报告只描述**已实现/已冻结事实**；不扩大能力；persistence 等明确标注为 future scope。
> 日期：2026-10-01。

```text
F9-P0 Core Implementation

STATUS:
COMPLETE

Included:
- Batch1 Projection
- Batch2 Gap Detection
- Batch3 Judge
- Batch4 Planning / Dedup
- Batch5 Targeted Research + Verification
- Batch6 Adaptive Orchestrator
- Batch7 Deterministic Eval Harness
- Batch8 Stopping Threshold Calibration

Deferred:
- Research persistence
- Runtime recovery
- Real provider E2E
- Production hardening
```

---

## 1. 总体架构（F9-P0）

F9-P0 = **Evidence-Grounded Adaptive Research Loop**：把普通 retrieval loop 变为
「Research State → deterministic Projection → Deterministic Gap Signals → Semantic Gap
Judgment(Judge) → Validated Follow-up Plan → Targeted Research → Incremental
Re-verification → Semantic + Deterministic Stopping → Final Synthesis → F7 finalization」，
全程在**单个**受 F8 治理的 governed execution 内完成（Spec Rev2 §1/§2）。

### 1.1 三层职责归属（不混淆）

```text
F8 Controller（runtime control plane）—— 唯一 lifecycle/budget/deadline/cancel/terminal 权威
  └─ F9 Orchestrator（F9 orchestration plane；唯一研究执行体；单 execute）
       ├─ Round 0 research（main agent/subagents/tools；research plane 写入）
       ├─ Research State Projection（research plane facts + current_round → deterministic JSON）
       ├─ Deterministic Gap Detection（code）
       ├─ Semantic Judge（LLM；显式 governance callback）
       ├─ Validated Follow-up Plan（LLM 提议 → deterministic validation + dedup）
       ├─ Targeted Research（复用 agent/tools/subagents）
       ├─ Incremental Verification（复用 F3–F6 APIs）
       ├─ Stopping（semantic 建议 + deterministic policy）
       ├─ F9 unavailable → baseline（run_deep_agent 作为 fallback 实现，同一 coroutine）
       ├─ Final Synthesis（同一执行体内最后一次 agent 阶段；计费进 F8）
       └─ F7 Finalization（仅 normal 完成一次；既有 finalize_run）
```

- 一次完整 F9-P0 Loop = 一个 F8 Controller-governed execution（单 TaskRecord / task_id /
  run_id=F1 同源 / 单 BudgetCounter / 单 watchdog / deadline）。`research_round` = F9
  编排计数（≠ F8 agent_step），**不是**新 Runtime Task；不注入冻结 BudgetCounter。
- **单 execute 约束**（Spec §2.2 / Plan D1）：只允许一次 `Controller.execute(policy)`；
  禁止 `round → controller.execute(...)`；禁止嵌套 Controller.execute。
- **图调用约束**：允许多次 `agent.astream(...)`，全部共享同一 task_id / run_id /
  BudgetCounter / deadline / cancellation / terminal authority；不得绕过 F8 counters。
- **Judge 计费**：Judge LLM call 经 `ctx.make_handler()` + 显式 callbacks 挂入同一
  governed execution，计入 `max_llm_calls`；禁止裸 model call；不额外计 agent_step
  （Plan D2 / Spec §2.2）。
- **F7**：仅位于 F9 orchestrator normal completion → F7 finalization；round 内禁止 F7；
  F8 terminal / cancel / timeout / budget failure 路径**不执行 F7**（F7 exactly-once，
  Batch6 adversarial matrix 覆盖）。
- baseline `run_deep_agent` 是 **execution primitive / fallback 实现**（F9 unavailable 时
  由 f9_orchestrator 在其内部同一 coroutine 调用），**不是第二条 submission/runtime path**
  （Plan D1 / 修订 R1）。
- 不新增第二 Runtime 控制面 / 新 Agent 类型 / Planner 系 Agent / Redis·Kafka·Neo4j·
  VectorDB / scheduler / multi-instance（Spec §15 Out、§16）。

### 1.2 代码结构（实现事实）

```
app/f9/
├── projection.py    Batch1 — 只读确定性投影（project(run_id, round=current_round)）
├── gaps.py          Batch2 — deterministic gap signals
├── judge.py         Batch3 — semantic judge（显式 governance callback）
├── plan.py          Batch4 — follow-up plan + validation + dedup
├── targeted.py      Batch5 — targeted research + incremental verification
├── orchestrator.py  Batch6 — round orchestrator（round0→…→synthesis→F7 once）
└── eval/            Batch7/8 — deterministic eval harness + calibration
    ├── world.py  agents.py  harness.py  rubric.py  scenarios.py（B7）
    └── calibration.py（B8）
```

## 2. Agent Execution Flow（agent 如何工作）

1. **入口**：受 F8 治理的任务经 `GovernanceController.execute(task_id, coroutine, policy)`
   启动（policy keys：`wall_clock_timeout / max_llm_calls / max_tool_calls /
   max_search_calls / max_agent_steps`；TaskRecord 保存 `counters_snapshot` +
   `effective_limits`）。F9 侧统一入口：
   `f9_orchestrator(task_query, session_id, *, research_policy=None, graph_runner=None,
   judge_model=None, plan_model=None, search_tool=None, verifier=None, ...)`，
   默认 `RESEARCH_POLICY_DEFAULTS = {max_research_rounds: 3, max_no_progress_rounds: 1,
   repeated_gap_threshold: 2}`。
2. **Round 0（broad research）**：复用 main agent / subagents / tools 完成初始研究，
   结果写入 research plane（F1–F7 双后端）。
3. **Projection**：确定性、只读、无 LLM —— 从 research plane facts + orchestrator
   current_round 组装结构化 JSON（run_id/round/budget/sub_questions/required_uncovered/
   claims/best_verdict/independent_flag/fresh/conflicts/evidence_summary/gap_signals/
   open_questions/size_limits）；`best_verdict` 排序键固定取最新 succeeded verification；
   `evidence_summary` 只做 domain 计数、**不引入 authority**（Spec §3）。
4. **Deterministic Gap Detection**：代码信号（必答未覆盖 / claim 无 evidence /
   evidence < MIN_EVIDENCE / INSUFFICIENT·UNVERIFIABLE / 未缓解 critical conflict /
   独立源不足 / citation coverage / query·source 重复率高 / budget 接近上限）。
5. **Semantic Judge**：单次 LLM 判断足够性 + 候选 follow-up（显式 callback 计费）。
   Judge failure → **adaptive unavailable → 同一 governed execution 内 fallback baseline
   research/synthesis**（R2-1 冻结语义；不创建新 task/run）。
6. **Validated Follow-up Plan**：LLM 提议 → deterministic validation（查重/上限/合法性）；
   非法 plan → fail-closed 拒绝（不执行）。
7. **Targeted Research**：执行合法 plan 的 queries（复用 search tool）；增量验证——仅
   binding 变化的 claim 增量 F3 verify；冲突集合变化才增量 F4–F6（fingerprint 幂等）。
8. **Evaluate / Stopping**：semantic 建议 + deterministic policy 终裁；stop_condition
   满足或 no-progress/diminishing 收敛 → STOP；否则 FOLLOW-UP 进入下一轮（受轮数 /
   novelty / repeated-gap guard）。
9. **Final Synthesis**：同一执行体内最后一次 agent 阶段（计费进 F8），随后
   **F7 finalize_run 恰一次**（normal completion 唯一接线点；fail-open）。

baseline（对照）：真实 `run_deep_agent` 入口；eval harness 进程内替换
`main_agent.get_main_agent`（module 属性 monkeypatch，不改文件、不绕过
run_deep_agent 生命周期）。

## 3. Research Loop Lifecycle

- **Round State（orchestration 态；非 F8 TaskStatus）**（Spec §7）：
  `ROUND_STARTED → PLAN_PROPOSED → PLAN_VALIDATED → RESEARCH_EXECUTED →
  EVIDENCE_UPDATED → VERIFICATION_UPDATED → ROUND_EVALUATED → FOLLOW_UP | STOPPED |
  NO_PROGRESS`；非法 transition fail-closed（不执行、记 diagnostic、不建新 Runtime Task）。
- **Failure routing**（Spec §12 Failure Matrix）：
  - Projection failure → adaptive unavailable → baseline（同一 run）；
  - Judge failure → 停止本 decision → baseline research/synthesis（同一 F8 run）；
  - Plan validation failure → 拒绝 plan（fail-closed）→ 无合法 plan 时 baseline；
  - Search failure → 单 query 失败跳过（其余合法 plan 继续）；
  - Verification failure → claim = UNVERIFIABLE（不伪造 verdict）；
  - F8 budget exceeded / timeout / cancellation → F8 立即取得 Runtime terminal authority
    （**F8 terminal ≠ F9 STOPPED**）。
- **Loop Safety**（Spec §8）：F8 硬保障（steps/llm/tool/search/deadline/cancel/terminal）+
  F9 Research Policy（轮数上限 / query 去重 / novelty=0 / repeated-gap 僵局 /
  diminishing-return / no-progress）。
- **Stopping**（Spec §9）：Hard Stop（F8）／Semantic Stop（LLM 建议）／Deterministic
  Policy（终裁：必答覆盖达标 AND 无未缓解 critical conflict AND claims 支持充分 →
  STOP；否则 FOLLOW-UP）。**budget 是 hard ceiling 不是 completion target**。
- **Idempotency**：round identity（run 内）、plan fingerprint、query identity、
  verification fingerprint、state transition identity —— 以 PG 唯一约束 + F8 事务实现；
  不引入 Redis/Kafka/Neo4j（Spec §13）。**注意**：Spec §13 的「半程状态可幂等续算」
  限于同一次 governed execution 内；跨 execution 崩溃恢复属 startup/sweeper future，
  **本阶段未实现**（见 §10 Deferred）。

## 4. Batch1–8 能力演进

| Batch | 能力 | 交付模块 | 冻结日期 | 批内测试证据 |
|---|---|---|---|---|
| B1 | Research State Projection（只读确定性投影） | `app/f9/projection.py` | 2026-09-22 | sqlite 36 + PG 8（回归 sqlite 592/0） |
| B2 | Deterministic Gap Detection | `app/f9/gaps.py` | 2026-09-24 | 29（回归 621；PG 双 DSN 74） |
| B3 | Semantic Gap Judge（显式 callback；failure→同 execute baseline） | `app/f9/judge.py` | 2026-09-26 | judge 24（含 F8 seam；回归 645） |
| B4 | Follow-up Plan + validation + dedup（query_identity ≠ dedup_identity） | `app/f9/plan.py` | 2026-09-28 | plan 34（回归 679） |
| B5 | Targeted Research + Incremental Verification（direct tool + 增量 F3 + PG gate） | `app/f9/targeted.py` | 2026-09-29 | targeted 15 + 真实 PG gate 3（回归 694；PG 77） |
| B6 | Round Orchestrator（round0→adaptive→fallback→synthesis→F7 once；changed-claims 增量 F4→F5→F6） | `app/f9/orchestrator.py` | 2026-09-30 | orchestrator 17（含 F7-once adversarial 八场景；回归 711/88；PG 81） |
| B7 | Deterministic Eval Harness / Behavioral Quality Validation | `app/f9/eval/{world,agents,harness,rubric,scenarios}` | 2026-09-30 | eval 16 + 8/8 scenarios（回归 727/88；PG 81） |
| B8 | Stopping / Threshold Calibration | `app/f9/eval/calibration.py` | 2026-10-01 | calibration 4 + 61 观测点（回归 731/88；PG 81） |

> 注：B7/B8 走完整 feature-branch Git 生命周期并 MERGED → main
> （B7 `118493e`→merge `619f6c7`；B8 `4e4af78`→merge `be6d625`）。B1–B6 于 2026-09-30
> 按用户批准的 Historical Freeze Baseline 合并登记于单次 `chore(baseline)` commit
> `fdc0cdd`（不重构逐批历史）。回归均为 sqlite 全量 passed / 0 failed（排除既有 mysql
> 污染文件），PG 为 14 个 postgres 文件全量。

## 5. 当前系统能力边界（factual，不扩大）

**已具备（F9-P0 core，全部可运行）：**
- 完整 Adaptive Research Loop：单 governed execution 内 round0 → 多轮 adaptive
  （projection/gap/judge/plan/targeted/verify）→ 收敛 stopping → final synthesis →
  F7 exactly-once；
- Judge / Plan 失败语义冻结：同一 F8 run 内 fallback baseline（不丢治理、不双 run、
  F7 once）；
- 确定性、可复现的 baseline-vs-adaptive 行为评估（8 scenarios，rubric 8 维，无 real
  LLM 依赖）；stopping/阈值参数敏感性校准（61 观测点，结论=维持现状默认值）；
- 复用而不重复：F1–F7 research plane、F3–F6 claim 语义、F8 governance runtime、
  既有 `run_deep_agent` baseline primitive。

**边界（已实现范围上限）：**
- adaptive 判定基于 research plane facts 的**确定性投影**（Judge 不直读完整 DB /
  parent history）；evidence_summary 不含 authority/credibility 语义；
- budget 只读 snapshot（Projection 不推断 round、不新计费）；
- round state / follow-up plan 目前为 **orchestrator 内 in-memory**（Batch6 D3）；
  query identity 已在 Batch4 实现（dedup 判定用 dedup_identity ≠ query_identity），
  但**无跨 execution 持久化载体**；
- Eval 为 deterministic scripted world（真实 provider 未执行——本会话无有效凭据，
  Gate 全部为确定性断言，不伪造真实 provider 证据）；
- 未裁决/未实现项（不属于 F9-P0 core）：unresolved-gaps 在最终答案的可见性（Spec OQ4，
  F7 边界 feature）；`_govadapt.py` real-provider adapter（DEFERRED /
  environment-limited）；glue duplication = Known Risk（Freeze 后不修，用户指令确认）。

## 6. Eval Architecture（Batch7，deterministic）

- **组件**：`eval/world.py`（确定性 scripted world：query→evidence 路由；
  `results_for` 显式路由 vs `results_any` 路由优先+extra 兜底）；`eval/agents.py`
  （ScriptedBaselineAgent 等）；`eval/harness.py`（双执行编排：baseline
  `run_deep_agent` vs adaptive `f9_orchestrator`，各自独立 `Controller.execute`——
  独立 governance store / research DB / task / run；preseed_run_id 支持 claim 级 seam
  场景）；`eval/rubric.py`；`eval/scenarios.py`。
- **公平起点**：adaptive round0 graph runner 与 baseline 使用同一 world 初始查询；
  未加 orchestrator baseline-only mode；未改 orchestrator 行为。
- **Evaluator（rubric 8 维 0–1）**：required_coverage / citation_coverage /
  evidence_sufficiency / unsupported_score / conflict_coverage / redundant_score /
  cost_compliance + total；verdict = adaptive 在 ≥1 grounding 维度 > baseline 且 total ≥
  baseline（**no-pad**：相等 → gate False）。全部确定性规则，无 real LLM。
- **Scenarios（8）**：s1 already-sufficient→no unnecessary follow-up（control）；
  s2 missing required→targeted follow-up（positive）；s3 unverified claim→
  re-verification（seam/preseed）；s4 conflict→F4 seam（seam/preseed）；s5 insufficient
  corroboration→F5 seam（seam/preseed）；s6 diminishing→correct stopping（control）；
  s7 near-budget（control）；s8 judge-failure→same-execution fallback（negative）。
  结果 8/8 PASS。
- **指标**：rounds（orchestrator summary）；research metrics（Registry/projection 只读
  计数）；cost（TaskRecord `counters_snapshot` + `effective_limits`）；final answer
  （baseline agent.final / adaptive finalize_sink）。零 schema 改动。
- **限制（如实）**：deterministic scripted 为主；real provider 未执行；baseline/
  adaptive glue 差异（path_instruction/session-dir vs task prompt）；s3/s4/s5 为
  preseed claim run 的 seam 兼容验证；citation 用 locator 子串匹配（简化近似）。

## 7. Calibration Methodology（Batch8）

- **目的**：为 stopping/阈值常量提供实验证据（D5：「显式配置；初始值由 Eval calibration
  决定；禁止为过测试临时调参」）。
- **设计**：复用 Batch7 eval harness 不改契约；参数扫描 5 参数 × 2–3 档 × 5 代表场景
  （s1/s2/s6/s7/s8）= **61 观测点**；每点独立 research db + governance（无跨点污染）。
  参数覆盖**不改生产默认值文件**：MIN_EVIDENCE / BUDGET_NEAR_RATIO 经 gaps 模块属性
  runtime monkeypatch，3 个 policy 值经 `research_policy` 注入；每档结束恢复默认值
  （测试断言恢复）。
- **搜索空间**：min_evidence 1/2/3（默认 2）；budget_near_ratio 0.1/0.2/0.3（默认 0.2）；
  max_research_rounds 2/3/4（默认 3）；max_no_progress_rounds 1/2（默认 1）；
  repeated_gap_threshold 2/3（默认 2）。
- **结论**：质量与成本对 5 参数不敏感（avg_total 恒定 6.6/6.5、cost 全合规）；唯一可测
  影响 = MIN_EVIDENCE 对 stopping 轮次（1→2.6 / 2→3.0 / 3→3.4 轮，ME=3 触发
  no_progress）；stopping correctness 全档 completed 收敛（no_gap / adaptive_fallback），
  无 timeout/cancel。
- **决策**：Pareto 判定默认值质量不劣于任何邻档 → **维持现状默认值**
  （MIN_EVIDENCE=2, BUDGET_NEAR_RATIO=0.2, max_research_rounds=3,
  max_no_progress_rounds=1, repeated_gap_threshold=2）；**无 production default 修改**。
- 证据：`_testtmp/b8_evidence.json`（gitignored，61 点原始汇总）。

## 8. Verification Evidence（累计）

| 阶段 | 回归 sqlite（passed / skipped） | PG（postgres 文件全量） | 备注 |
|---|---|---|---|
| B1 后 | 592 / 0 | — | B1 批内 sqlite 36 + PG 8 |
| B2 后 | 621 / 0 | 74（双 DSN） | — |
| B3 后 | 645 / 0 | 74 | — |
| B4 后 | 679 / 0 | 74 | — |
| B5 后 | 694 / 0 | 77 | 真实 PG gate 3 |
| B6 后 | 711 / 88 | 81 | orchestrator 17 |
| B7 后 | 727 / 88 | 81 | eval 16 + 8/8 scenarios |
| B8 后 | **731 / 88 / 0 failed** | **81 / 0 failed** | calibration 4；61 点 |

- 每批 ruff / compileall（`app/f9` 与测试）/ diff-check PASS；**COMPATIBILITY GAP =
  None**（每批均未触碰 F8/F1–F7/已冻结 Batch 契约，无 schema/migration/UI/infra/Agent
  改动）。
- sqlite 全量回归统一排除既有 mysql 污染文件 `test_db_tools_mysql_integration.py`
  （P006 方向问题，未修复）；PG 为唯一生产基线口径。
- real-provider E2E / real-LLM verifier·extractor·judge：本会话环境缺有效凭据 → 未执行
  （受控/门控验证需凭据环境；**不伪造证据**，Gate 以确定性证据为准）。

## 9. Frozen Boundaries（不可再动）

- F1–F8 全部语义冻结（用户纪律：不再重审）；PostgreSQL = 唯一生产基线与最终 Gate
  Backend（SQLite 仅快速测试；SQLite PASS ≠ PostgreSQL PASS）。
- F9-P0 Spec Rev2（含 R2-1…R2-6）与 Implementation Plan Rev2（D1–D6 + 修订 R1–R5）
  冻结；ARCHITECTURE.md §3 F9-governed-orchestrator 例外（scope-tightened）冻结。
- Batch1–8 各批冻结语义：不改生产默认值常量文件；不改 `main_agent.py` /
  `orchestrator.py` / `run_deep_agent` 生命周期；不引入 Redis/Kafka/Neo4j/VectorDB/
  scheduler/multi-instance/lease-heartbeat/Event Sourcing/重复 Agent/第二 Runtime。
- Git Workflow（AGENTS.md §10 / PROCESS.md §12）：main = frozen integration baseline；
  One Feature/Batch = One Feature Branch = One Review/Freeze = One Merge；Freeze ≠
  Commit ≠ Push ≠ Merge（各自独立批准）；禁止 force-push/reset/rebase/amend。

## 10. Deferred Items（明确 future scope）

| 项 | 状态说明 |
|---|---|
| Research persistence（round state / query-identity ledger 持久化） | **未来（F9-P1 / Batch9 方向）**。Batch6 D3 明确 round in-memory；2026-10-01 用户决定不作为 F9-P0 阶段实施。若实施 = research plane schema additive（L3，新表 + migration，**零 ALTER 既有 F1–F8 表**），不触碰 orchestrator 运行期 in-memory 语义 |
| Runtime recovery / resume（跨 execution 崩溃续算） | future（F8 startup/sweeper 语义边界）；Spec §13 不承诺 run 内建第二 task |
| Real provider E2E | 非 Gate；受凭据硬限制（本环境缺有效凭据）；凭据环境就绪后可作为 supplementary 证据 |
| Production hardening | future（sweeper/orphan reclaim 产品化、status-sync、server 级 superseded 接线、real-LLM verifier·extractor·judge 受控验证、`_govadapt.py` adapter 等） |
| unresolved-gaps 最终答案可见性 | 未裁决（Spec OQ4，F7 边界 feature，独立 feature 非主干） |

## 11. Future Roadmap（Batch9 / P1）

- **Batch9 / P1 首要候选 = Round / Query-Identity Persistence**（唯一未实现的 Spec §15 In
  项；直接对应 Batch6 D3 known limitation「round in-memory，恢复能力留未来」）。
  依据 Batch9 Readiness Review：research plane additive schema（双后端 migration，零
  ALTER）；首版语义倾向 write-only audit + 只读查询（恢复/续算留 future）；
  触碰 Batch6 D3 冻结边界 → 需 L3 路径 + 用户另行批准 + 独立 feature branch
  （`feature/f9-batch9-round-persistence`）。**2026-10-01 用户决定：不进入 F9-P0 阶段
  实施，整体 defer 至 P1。**
- P1 其余方向（按需、非承诺顺序）：跨 execution eval / 审计的事实基础（依赖
  persistence）；更多 world/语料的 eval 扩展；参数在 real-provider 上的补充验证；
  unresolved-gaps 可见性裁决。
- 展示/交接（本阶段 Documentation / Presentation）：项目 README（展示入口）、
  Interview Material（1 分钟介绍 / 5 分钟架构 / 技术难点 / Trade-off 问答）——见
  PROJECT_CONTEXT §1 当前阶段。

## 12. References

- Spec：`docs/spec/2026-09-19-f9-p0-evidence-driven-research-loop.md`（Rev2，SPEC
  REVISION 2 COMPLETE）。
- Plan：`docs/plan/2026-09-20-f9-p0-implementation-plan.md`（Rev2，D1–D6 + R1–R5）。
- Batch Reports（按批）：
  - `docs/plan/2026-09-21-f9-p0-batch1-implementation-report.md`
  - `docs/plan/2026-09-23-f9-p0-batch2-implementation-report.md`
  - `docs/plan/2026-09-25-f9-p0-batch3-implementation-report.md`
  - `docs/plan/2026-09-27-f9-p0-batch4-implementation-report.md`
  - `docs/plan/2026-09-28-f9-p0-batch5-implementation-report.md`
  - `docs/plan/2026-09-29-f9-p0-batch6-implementation-report.md`
  - `docs/plan/2026-09-30-f9-p0-batch7-implementation-report.md`
  - `docs/plan/2026-10-01-f9-p0-batch8-implementation-report.md`
- Batch9：`docs/plan/2026-10-01-f9-p0-batch9-readiness-review.md`
  （CONDITIONAL；D-A 已于 2026-10-01 由用户裁决 = 停批收尾，persistence 归 P1）。
- 状态索引：`PROJECT_CONTEXT.md`（F9-P0 Core = COMPLETE；Batch1–8 FROZEN/MERGED；
  Batch9 = Deferred / Future P1；Current phase = Documentation / Presentation）。
- Git：main `be6d625`（HEAD）；B7 `118493e`→`619f6c7`；B8 `4e4af78`→`be6d625`；
  baseline `fdc0cdd`；harness closure `ce53b09`。

## 13. Final Status

```text
F9-P0 Core Implementation
STATUS: COMPLETE（用户 2026-10-01 决定；Batch1–8 全部 PASS/FROZEN，B7/B8 已 MERGED → main）

Included: Batch1 Projection · Batch2 Gap Detection · Batch3 Judge · Batch4 Planning/Dedup
          · Batch5 Targeted Research+Verification · Batch6 Adaptive Orchestrator
          · Batch7 Deterministic Eval Harness · Batch8 Stopping Threshold Calibration
Deferred: Research persistence · Runtime recovery · Real provider E2E · Production hardening
          （均归 Future / P1；不在 F9-P0 阶段继续开发）
```

- 文档性质：本报告为 documentation-only（docs/plan 收尾文档），零产品代码/测试改动；
  不创建 Batch9 feature branch；不 Commit/Push/Merge（等待用户 Review → Scope Audit →
  Commit → Push，逐步批准，AGENTS.md §10 / PROCESS.md §12）。
