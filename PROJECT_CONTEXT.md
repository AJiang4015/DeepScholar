# PROJECT_CONTEXT.md — Current Project State（当前项目状态索引）

> 定位：**Current State Index + Navigation**，不是规范文件，不是架构权威，不是第二个
> ARCHITECTURE.md。
> `PROJECT_CONTEXT.md` is a current-state index, not a normative authority. It MUST NOT
> override AGENTS.md / PROCESS.md / ARCHITECTURE.md / TESTING.md / DECISION.md / active
> Spec contracts.
> 冲突处理按 AGENTS.md §1 Authority 规则：不静默选择；报告冲突；以权威文档为准。
> 维护规则见 §12。Last updated：2026-09-22。

---

## 1. Current Status

- 当前阶段：**F9-P0（Evidence-Driven Adaptive Research Loop）实施期，Batch 门控推进**
- 当前 Feature：F9-P0（Spec Rev2 定稿 → Implementation Readiness Review **PASS** → Plan Rev2 **READY**）
- 当前 Batch：**F9-P0 Batch 6 — Round Orchestrator：PASS / FROZEN（2026-09-30 用户正式
  Freeze）**（报告 `docs/plan/2026-09-29-f9-p0-batch6-implementation-report.md`）
- 当前状态：Batch 6 **PASS / FROZEN**（用户 2026-09-30 正式 Freeze；D1–D6 + F7-once matrix
  全 PASS；sqlite 711 passed / 88 skipped / 0 failed、PG 81 passed / 0 failed、Batch6 tests
  17 passed；`_govadapt.py` real-provider adapter E2E = DEFERRED / environment-limited，
  不修改 Batch6 frozen contract、不虚报 PASS；ARCHITECTURE §3 例外回填一并 Freeze。
  前批：Batch 1 **PASS/FROZEN** 2026-09-22、Batch 2 2026-09-24、Batch 3 2026-09-26、
  Batch 4 2026-09-28、Batch 5 2026-09-29）
- L0–L3 Task Classification Governance = **PASS / FROZEN**（2026-09-28 用户正式 Freeze；规则
  SoT = PROCESS.md §11；实施报告 `docs/plan/2026-09-28-l0-l3-governance-implementation-report.md`）
- 是否允许继续实现：**否**。Batch6 Freeze 后禁止扩展（_govadapt 实现 / run_deep_agent glue
  抽取 / polling·status-sync / durable round state / Claim–Evidence Graph / Redis·Kafka·
  Neo4j·VectorDB / F8·F1–F7·Batch1–5 修改 / 无关 refactor）；glue duplication = Known Risk，
  非当前 defect
- 当前唯一 Next Action：F9-P0 **Batch 7 Readiness Review**（read-only；不进入 Implementation，
  等待用户 Review）
- Current Workflow Stage：**L2 · F9 Batch 7 Readiness Review 提交**——本行为瞬时状态，
  Batch/任务结束即更新或清除；不新增规则

> 备注：本仓库曾在用户要求下完成一轮 Harness 文档治理（新增本文件 + AGENTS/PROCESS
> 最小更新，2026-09-22，见 `docs/plan/2026-09-22-harness-document-governance-report.md`）。

## 2. System in One Page

「深度研搜」对话式多智能体研究系统（教学→工程化演进中）：

- **Agent**：DeepAgents 一主三从——main agent（`app/agent/main_agent.py`）＋网络搜索 /
  MySQL 查询 / RAGFlow 知识库子智能体；`run_deep_agent(query, session_id)` 为执行入口。
- **Runtime**：FastAPI + WebSocket（`app/api/server.py`）；agent 执行状态经官方
  checkpoint saver（sqlite=AsyncSqliteSaver / postgres=AsyncPostgresSaver，`app/runtime/checkpoint.py`）。
  F8 起新增 governance runtime（`app/runtime/governance/`：task/controller/counters/
  callbacks/events），F8 Controller 是 **唯一 lifecycle/budget/deadline/cancel/terminal 权威**。
- **Research Plane**：`app/research/`（F1–F7，独立双后端迁移 `db/migrations/0001–0006`），
  存 ResearchRun/SubQuestion/Query/Source/Evidence + Claim/…/Reconciliation artifacts；
  fail-open 写入、不与 Agent 主链路耦合。
- **F9 orchestration plane**：`app/f9/`（新增，Batch 1 = `projection.py` 只读投影）。
- **前端**：React + Vite（`frontend/`），仅经 HTTP/WS 与后端通信。
- **外部依赖**：OpenAI 兼容 LLM / Tavily / MySQL（docker 教学库）/ RAGFlow；凭据仅 `.env`。

## 3. Identity Model

| 键 | 平面 | 是什么 |
|---|---|---|
| `thread_id` | UI/会话 | 前端会话键（安全字符集 `[A-Za-z0-9_-]{1,128}`） |
| `task_id` | F8 governance | TaskRecord 主键＝**lifecycle/durable 权威**（governance_tasks） |
| `run_id` | F1 执行相关键 | 执行相关键：TaskRecord.run_id（F1 同源、governed 前绑定/回填）→ GovernanceExecution → run_deep_agent ctx → ResearchRun/monitor/durable event 同源 |
| checkpoint | 执行状态 | 官方 saver 管理的 graph 执行状态（checkpoint_* 表族，禁止仓库迁移触碰） |
| ResearchRun | research plane | research_runs 行（artifact 作用域；≠ checkpoint，不共表/迁移/事务） |

纪律：三平面（checkpoint / TaskRecord+GovernanceEvent / ResearchRun）永不混淆；
TaskRecord = terminal truth；governance event = durable observation；monitor live = 临时（fail-open）。
详细契约：`docs/plan/2026-09-18-f8-runtime-final-architecture-freeze-review.md`。

## 4. Frozen Architecture（索引+摘要，权威在所指文档）

- **F1–F8 全部语义冻结**（用户纪律：不再重审；改动任何冻结契约须先报告）。
- PostgreSQL 是**唯一生产基线与最终 Gate Backend**；SQLite 仅快速单测/fallback。
  SQLite PASS ≠ PostgreSQL PASS（CAS/race/持久化/replay 语义以真实 PG 证据为准）。
- F8 runtime：TaskStatus（running + 8 terminal）、单 Controller.execute(policy)、
  BudgetCounter 四 hard counter（compare→increment→execute，无 overshoot；search ⊆ tool）、
  terminal funnel（memory 权威 + optimistic CAS `WHERE status='running' AND version=?`、
  单 winner、retry ladder）、watchdog/cancel/mappings、durable GovernanceEvent
  `(task_id,seq)` + replay cursor（task-scoped）+ WS since_seq catch-up、run_id 前绑定
  （F1 回填）、live `governance_terminal` bridge（observation only，fail-open）。
  权威：`docs/spec/2026-09-14-f8-runtime-execution-governance.md`、
  `docs/spec/2026-09-15-f8-step3-runtime-budget-control.md`（FROZEN）、
  `docs/spec/2026-09-17-f8-step4-batch2-events-replay.md`、
  `docs/spec/2026-09-17-f8-step4-batch3-observation-closure.md`、
  `docs/plan/2026-09-18-f8-runtime-final-architecture-freeze-review.md`（FROZEN）。
- Research foundation F2–F7：Claim/Binding/Citation 幂等与 validator（F2）→ Semantic
  Verification（F3，succeeded/failed，verdict 5 态）→ Conflict Detection（F4，confirmed/
  genuine 矩阵）→ Corroboration（F5，只计独立性不评信任）→ Reconciliation（F6，
  SAME_ORIGIN/DETAIL/GENUINE_CONTESTED；Unknown ≠ Not Independent）→ F7 Research State
  Bridge（finalize_run 唯一接线点，fail-open，normal 完成恰一次）。
  权威：`docs/spec/2026-09-08…2026-09-13-*`（F2–F7，F5/F6 带 Final Gate 状态）。
- F9 不新增第二 Runtime 控制面；`research_round` 是编排计数，不注入冻结 BudgetCounter；
  禁止 Redis/Kafka/Neo4j/VectorDB/scheduler/multi-instance/lease-heartbeat/Event
  Sourcing/generic·Planner·Citation·Conflict·Verification·Research-Manager Agent。

## 5. Current F9 Contract

F9-P0 = evidence-grounded adaptive research loop，全程在**单个**受 F8 治理的 governed
execution 内：Projection → Deterministic Gap Signals → Semantic Gap Judgment(Judge) →
Validated Follow-up Plan → Targeted Research → Incremental Verification → Semantic+
Deterministic Stopping → F7 finalization（normal 完成恰一次；F8 terminal/cancel/budget
路径**不执行 F7**）。

- F9-P0 Spec Rev2 **定稿**（"SPEC REVISION 2 COMPLETE"→ Readiness Review **PASS /
  IMPLEMENTATION READY**）：`docs/spec/2026-09-19-f9-p0-evidence-driven-research-loop.md`。
- Implementation Plan Rev2 **READY**（D1–D6 冻结决策 + 修订 R1–R5 锁死工程边界）：
  `docs/plan/2026-09-20-f9-p0-implementation-plan.md`。
- 已冻结设计要点（索引）：单 `Controller.execute(task_id, f9_orchestrator(...), policy)`；
  Judge 走 `ctx.make_handler()`+显式 callback 计费；Projection 不推断 round、budget 只读
  snapshot；query_identity ≠ dedup_identity；fallback baseline 在同一 coroutine 内；
  Batch7 = Eval / Behavioral Quality（用户 2026-09-30 定义；原 Plan Rev2 Batch7 F7-seam 目标
  视为已由 Batch6 D6/F7-once matrix 覆盖，待 Decision Closure 记录）。
- 当前允许实施的 Batch：**仅用户明确指令指定的一个**；Batch 7 Implementation 未开始
  （Batch 1–6 已 FROZEN；Batch7 当前仅 Readiness Review）。

## 6. Latest Frozen Batch — F9-P0 Batch 6：Round Orchestrator

- 状态：**BATCH 6 PASS / FROZEN**（用户 2026-09-30 正式 Freeze；实现与验证证据保留，无额外
  重构）。前批：Batch 1–5 **PASS/FROZEN**（09-22/09-24/09-26/09-28/09-29）。
- 目标：单 F8 execute 内 round0 baseline → Projection → Gap → Judge → Plan → Targeted
  Research(+Verify) → 评估/stopping → 下一轮或终止 → Final Synthesis → F7 finalize_run
  恰一次（`app/f9/orchestrator.py`）。
- 契约要点：D1 Option A 图调用（ARCHITECTURE §3 例外回填，范围收紧）；D2 round0 exactly-once；
  D3 round in-memory；D4 changed-claims 增量 F4→F5→F6；D5 Research Policy 临时默认；D6 Final
  Synthesis + F7 once；GLE/Cancel 不 catch；单 execute/单 run/单 BudgetCounter。
- Out of Scope：Batch7 Eval / F9-P1·P2；修改 F1–F8 / Batch1–5 / run_deep_agent / main_agent /
  Spec·Plan；`_govadapt.py` real-provider adapter（DEFERRED / environment-limited，不补）。
- 文档：Spec §10/§12；Plan §I Batch6；Readiness
  `docs/plan/2026-09-29-f9-p0-batch6-readiness-review.md`；Implementation Plan
  `docs/plan/2026-09-29-f9-p0-batch6-implementation-plan.md`（L3）；
  报告 `docs/plan/2026-09-29-f9-p0-batch6-implementation-report.md`。
- 测试：`tests/test_f9_orchestrator.py`（17：flow 6 + D4 changed-only 1 + controller seam 10，
  含 F7-once adversarial matrix 八场景）。
- Known limitations（保留）：真实 provider E2E 凭据受限；`_govadapt.py` DEFERRED；round
  in-memory（D3，恢复能力留未来）；glue duplication = Known Risk（Batch6 Freeze 后不修复，
  用户指令确认）。
- Gate：Batch 6 Implementation Gate → **BATCH 6 PASS / FROZEN**（2026-09-30 用户 Freeze）；
  未宣布 F9-P0 IMPLEMENTATION COMPLETE。

## 7. F9 Roadmap（一句话/批；细节在各自 Batch 指令与 Plan §I）

1. Batch 1 — Research State Projection ✅ **PASS / FROZEN**（2026-09-22 用户 Freeze）
2. Batch 2 — Deterministic Gap Detection ✅ **PASS / FROZEN**（2026-09-24 用户 Freeze）
3. Batch 3 — Semantic Gap Judge（显式 callback；failure→同 execute baseline）✅ **PASS / FROZEN**（2026-09-26 用户 Freeze）
4. Batch 4 — Follow-up Plan + validation + dedup（identity/validation/dedup）✅ **PASS / FROZEN**（2026-09-28 用户 Freeze）
5. Batch 5 — Targeted Research + Incremental Verification（direct tool + 增量 F3 + PG gate）✅ **PASS / FROZEN**（2026-09-29 用户 Freeze）
6. Batch 6 — Round Orchestrator ✅ **PASS / FROZEN**（2026-09-30 用户 Freeze；`_govadapt.py`
   real-provider adapter E2E 保持 DEFERRED / environment-limited）
7. Batch 7 — Eval / Behavioral Quality 验证（baseline-vs-adaptive；**Readiness Review 先行**，
   仅 Readiness，等待用户 Review；不直接进入 Implementation）
8. Batch 8 —（原 Plan Rev2 记为 Eval/Final Gate；与 Batch7 的 Eval 范围关系待 Batch7
   Decision Closure 明确，本行不自动实施）

## 8. Important Known Limitations（仍影响后续开发的）

- multi-instance governance / lease / heartbeat：未支持（F8 明确不做，见 Freeze Review）。
- sweeper / startup 自动恢复（orphan reclaim 产品化）：未做（F8 文档化的 future）。
- polling / status-sync / durability_gap 实时预览：未实现。
- server 级 superseded 接线（AC2 future）：未做。
- real provider E2E / real-LLM verifier·extractor·judge：本会话环境缺有效凭据，
  受控/门控验证（VERIFY_REAL_LLM=1 等）需凭据环境，不得伪造证据。
- live observation（monitor）与 durable truth（governance event/TaskRecord）边界：
  live fail-open、非 replay 权威。
- 测试运行：`test_db_tools_mysql_integration.py` 在 MySQL 可达时会在进程内污染
  `OPENAI_API_KEY`（load_dotenv 链），完整目录级 pytest 需排除该文件或以子集/单独文件运行
  （Batch 1 报告 §10；问题归属 P006 方向，未修复）。

## 9. Verification Baseline（PASS / FROZEN / READY；详细证据在对应 report）

| 阶段 | 状态 | 关键证据 |
|---|---|---|
| Phase 0 / R1 / R2（checkpoint 双后端） | PASS / FROZEN | specs 2026-09-03-*；D008–D010 |
| F1 / F2 / F3 / F4 | PASS / FROZEN | specs 2026-09-07…10；D011–D014 |
| F5 / F6 / F7 | PASS（F5 Final Gate FROZEN）/ F6–F7 Final Gate 已过 | specs 2026-09-11…13；D015–D017 |
| F8 Step 1–4（含 PG 双门控） | PASS / FROZEN | `docs/plan/2026-09-18-f8-runtime-final-architecture-freeze-review.md` 等 |
| F9 Spec Rev2 / Readiness / Plan Rev2 | READY | `docs/spec/2026-09-19-…`、`docs/plan/2026-09-20-…` |
| F9 Batch 1 | **PASS / FROZEN**（2026-09-22 用户 Freeze） | `docs/plan/2026-09-21-f9-p0-batch1-implementation-report.md`；sqlite 36 + PG 8；回归 sqlite 592 passed / 0 failed（排除 mysql 污染文件） |
| F9 Batch 2 | **PASS / FROZEN**（2026-09-24 用户 Freeze） | `docs/plan/2026-09-23-f9-p0-batch2-implementation-report.md`；sqlite 29 + PG 5；回归 sqlite 621 passed / 0 failed；PG 双 DSN 74 passed |
| F9 Batch 3 | **PASS / FROZEN**（2026-09-26 用户 Freeze） | `docs/plan/2026-09-25-f9-p0-batch3-implementation-report.md`；judge 24（含 F8 seam）；回归 sqlite 645 passed / 0 failed；PG 双 DSN 74 passed（无 PG 集成，见报告 §4） |
| F9 Batch 4 | **PASS / FROZEN**（2026-09-28 用户 Freeze） | `docs/plan/2026-09-27-f9-p0-batch4-implementation-report.md`；plan 34；回归 sqlite 679 passed / 0 failed；PG 双 DSN 74 passed（无 PG 集成，P1 in-memory） |
| F9 Batch 5 | **PASS / FROZEN**（2026-09-29 用户 Freeze） | `docs/plan/2026-09-28-f9-p0-batch5-implementation-report.md`；targeted 15 + 真实 PG gate 3；回归 sqlite 694 passed / 0 failed；PG 双 DSN 77 passed（real-provider E2E 凭据受限，报告 §10） |
| F9 Batch 6 | **PASS / FROZEN**（2026-09-30 用户 Freeze） | `docs/plan/2026-09-29-f9-p0-batch6-implementation-report.md`；orchestrator tests 17；回归 sqlite 711 passed / 88 skipped / 0 failed；PG 全量 81 passed（Batch5 口径 77；governance 先 reset）；`_govadapt.py` DEFERRED / environment-limited |

## 10. Important Files（改什么先看什么）

| 想改的东西 | 先看 |
|---|---|
| 行为规范 | AGENTS.md |
| 执行流程 | PROCESS.md |
| 当前问题注册 | PROBLEM.md（命中才读 docs/problem/ 对应文件） |
| 架构边界 | ARCHITECTURE.md（注意：F8/F9 架构权威另在 Freeze Review/F9 Spec，见 §4） |
| 长期决策 | DECISION.md（按需读 Dxxx；F8+ 决策另在 docs/plan freeze review） |
| 验证契约 | TESTING.md |
| 当前状态 / 下一步 | PROJECT_CONTEXT.md（本文件） |
| 当前 Feature 设计 | docs/spec/（当前阶段 Spec，例如 F9：2026-09-19-…） |
| 当前实现计划与证据 | docs/plan/（当前 Batch Plan/Report；历史报告勿默认读） |
| 历史问题细节 | docs/problem/（仅命中时读） |

## 11. Current Next Action

> **当前唯一允许的下一步：F9-P0 Batch 7 Readiness Review（read-only）。**
> - F9-P0 Batch 1–6 均已 **PASS / FROZEN**（2026-09-22 / 09-24 / 09-26 / 09-28 / 09-29 /
>   09-30 用户正式 Freeze），实现与验证证据保留、无额外重构；
> - 下一阶段 = **F9-P0 Batch 7 — Eval / Behavioral Quality 验证（baseline-vs-adaptive）**；
>   Batch6 Freeze 后禁止扩展（_govadapt 实现 / glue 抽取 / durable round / 图结构 / 新 infra
>   / F8·F1–F7·Batch1–5 修改 / 无关 refactor）；
> - Batch 7 仅 Readiness Review（本批唯一交付物 = `docs/plan/2026-09-30-f9-p0-batch7-*`）；
>   不得修改代码 / F8 / F1–F7 / Batch6 implementation；遇 frozen contract 冲突 → STOP 并报
>   COMPATIBILITY GAP；
> - 等待用户 Review / Decision Closure 后方可进入 Batch7 Implementation（未批准不实施）。

## 12. Context Maintenance Rules

- 本文件**只保存"当前状态"**：当前 Feature/Batch/状态/允许动作/Next Action/权威文档指针。
- **不保存**：完整历史、完整 Spec、测试日志、实现细节、已失效的历史状态。
- Feature / Batch 完成后更新本文件（状态、Next Action、§9 基线）；历史信息留在对应
  Spec / Plan / Report，不复制回本文件。
- 与权威文档冲突时：以权威文档为准（AGENTS.md §1），**报告冲突**，不得让本文件覆盖权威。
- 本文件新增条目禁止"创造新规则"或"重新定义架构"——只能索引与摘要。
- 若本文件内容已无法在低上下文成本下回答 §「完成标准」的 10 问 → 精简本文件，
  而不是继续加规则。
