# PROJECT_CONTEXT.md — Current Project State（当前项目状态索引）

> 定位：**Current State Index + Navigation**，不是规范文件，不是架构权威，不是第二个
> ARCHITECTURE.md。
> `PROJECT_CONTEXT.md` is a current-state index, not a normative authority. It MUST NOT
> override AGENTS.md / PROCESS.md / ARCHITECTURE.md / TESTING.md / DECISION.md / active
> Spec contracts.
> 冲突处理按 AGENTS.md §1 Authority 规则：不静默选择；报告冲突；以权威文档为准。
> 维护规则见 §12。Last updated：2026-10（P3 Decision Closure 落地）。

---

## 1. Current Status

- 当前阶段：**P3 Runtime Observability（Decision Closure = APPROVED + LANDED；未实现）→
  P3-1 Event Contract L3 Spec 准备阶段**
- 当前 Feature：**P3 Runtime Observability**（`D-P3-001…008` 已批准并落地；P3-1…P3-5 为唯一规范编号，
  旧 P2-3/P2-4 的 runtime observability 部分 superseded-by-P3）
- 当前状态：F9-P0 Core = COMPLETE（Batch1–8 PASS/FROZEN/MERGED）；**P2-1 / P2-2 已 FROZEN + MERGED**
  （P2-1 `dd7af9a`；P2-2 `884a681` + merge `8d9531d`）；**P3 Decision Closure 已批准并落地**
  （`D-P3-001…008`，docs-only commit）；P3 各批次实现**尚未开始**。
- **Batch9 = Deferred / Future P1（用户 2026-10-01 决定停止推进）**：round/query-identity
  persistence（Spec §15 In 唯一未实现项）、runtime recovery 等**不作为 F9-P0 阶段继续
  开发**；不创建 Batch9 feature branch、不实施 schema migration、不改 F9 runtime。
  记录：`docs/plan/2026-10-01-f9-p0-batch9-readiness-review.md`（§11 Decision
  Resolution，D-A 裁决 = 停批收尾）+ `docs/plan/2026-10-01-f9-p0-final-completion-report.md`
  （F9-P0 Final Completion Report，宣告 COMPLETE）。
- L0–L3 Task Classification Governance = **PASS / FROZEN**（2026-09-28 用户正式 Freeze；规则
  SoT = PROCESS.md §11；实施报告 `docs/plan/2026-09-28-l0-l3-governance-implementation-report.md`）
- 是否允许继续实现：**否（P3 实现未开始）**。允许：P3-1 L3 Spec 准备（文档）；禁止：修改
  `app/**`（含 `events.py` / `callbacks.py` / `controller.py`）、DB / migration / 依赖、创建
  `feature/p3-1-event-contract` 以外分支、绕过 `D-P3-001…008` 冻结约束、Commit 前扩大 scope
- Git 状态（以实际 `git status` 为准）：branch = `main`（P3 Decision Closure docs-only commit 之上）；
  工作树仍含**非本任务**未提交内容（P2-1/P2-2 后期报告、简历类文件、`docs/images/*.png`）——
  归属未确认，按 AGENTS §10.5 不触碰
- 当前唯一 Next Action：**创建 `feature/p3-1-event-contract` branch（git 写操作，需用户批准）→
  进入 P3-1 Event Contract 的 L3 Spec**（Spec → Readiness → Decision → Plan Review）
- Current Workflow Stage：**P3 Decision Closure 已落地 → P3-1 Spec 准备（L3；仅文档）——等待
  用户批准 branch 创建 → Spec 起草**（本行瞬时状态，阶段结束即更新或清除；不新增规则）

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
- **Runtime Observability（P3；决策已落地 / 未实现）**：Task lifecycle 已 durable、Health Plane
  （heartbeat / stale / reclaim）已落地；但 **agent / tool / step 执行事实尚未持久化**，live 与
  durable 事件无统一契约，尚无只读 projection / admin API / Console（P3-1…P3-5 范围）。
- **Research Plane**：`app/research/`（F1–F7，独立双后端迁移 `db/migrations/0001–0006`），
  存 ResearchRun/SubQuestion/Query/Source/Evidence + Claim/…/Reconciliation artifacts；
  fail-open 写入、不与 Agent 主链路耦合。
- **Research Intelligence / Execution（原 F9 orchestration plane）**：`app/research/`
  （projection/gaps/judge/plan/targeted/orchestrator + eval/；单 governed execution 内；
  orchestrator 只做业务研究编排，不拥有 Runtime governance 权威）。
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
- **P3 冻结指针（`D-P3-001…008`，2026-10 落地）**：Event Contract additive（不改 lifecycle_event /
  terminalize / CAS / funnel / enforcement）；事件粒度默认 `tool`（step 默认关）；Admin API 默认关闭 +
  仅回环 + 只读 + 字段最小化；LangSmith 关联沿用 `run_id`（不新增 `trace_id`）；transcript 不在 P3；
  P3 默认 zero migration；新增 env MUST 同步 `.env.example`。权威：`DECISION.md` `D-P3-001…008` +
  `P3_DECISION_CLOSURE_REPORT.md`。

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
- Batch 实施状态：**Batch1–8 全部 PASS / FROZEN / MERGED**（2026-09-22…2026-10-01）；
  F9-P0 Core = **COMPLETE**（2026-10-01）；Batch9 = Deferred / Future P1（不再作为
  F9-P0 阶段开发；见 §1/§7/§11 与 Final Completion Report）。

## 6. Frozen Batch 记录 — F9-P0 Batch 6：Round Orchestrator（历史记录；最新 frozen 见 §7/§9）

- 状态：**BATCH 6 PASS / FROZEN**（用户 2026-09-30 正式 Freeze；实现与验证证据保留，无额外
  重构）。前批：Batch 1–5 **PASS/FROZEN**（09-22/09-24/09-26/09-28/09-29）。
- 目标：单 F8 execute 内 round0 baseline → Projection → Gap → Judge → Plan → Targeted
  Research(+Verify) → 评估/stopping → 下一轮或终止 → Final Synthesis → F7 finalize_run
  恰一次（`app/research/orchestrator.py`）。
- 契约要点：D1 Option A 图调用（ARCHITECTURE §3 例外回填，范围收紧）；D2 round0 exactly-once；
  D3 round in-memory；D4 changed-claims 增量 F4→F5→F6；D5 Research Policy 临时默认；D6 Final
  Synthesis + F7 once；GLE/Cancel 不 catch；单 execute/单 run/单 BudgetCounter。
- Out of Scope：Batch7 Eval / F9-P1·P2；修改 F1–F8 / Batch1–5 / run_deep_agent / main_agent /
  Spec·Plan；`_govadapt.py` real-provider adapter（DEFERRED / environment-limited，不补）。
- 文档：Spec §10/§12；Plan §I Batch6；Readiness
  `docs/plan/2026-09-29-f9-p0-batch6-readiness-review.md`；Implementation Plan
  `docs/plan/2026-09-29-f9-p0-batch6-implementation-plan.md`（L3）；
  报告 `docs/plan/2026-09-29-f9-p0-batch6-implementation-report.md`。
- 测试：`tests/test_research_orchestrator.py`（17：flow 6 + D4 changed-only 1 + controller seam 10，
  含 F7-once adversarial matrix 八场景）。
- Known limitations（保留）：真实 provider E2E 凭据受限；`_govadapt.py` DEFERRED；round
  in-memory（D3，恢复能力留未来）；glue duplication = Known Risk（Batch6 Freeze 后不修复，
  用户指令确认）。
- Gate：Batch 6 Implementation Gate → **BATCH 6 PASS / FROZEN**（2026-09-30 用户 Freeze）；
  未宣布 F9-P0 IMPLEMENTATION COMPLETE（彼时状态；F9-P0 Core 已于 2026-10-01 宣告
  COMPLETE，见 §1 与 Final Completion Report）。

## 7. F9 Roadmap（一句话/批；细节在各自 Batch 指令与 Plan §I）

1. Batch 1 — Research State Projection ✅ **PASS / FROZEN**（2026-09-22 用户 Freeze）
2. Batch 2 — Deterministic Gap Detection ✅ **PASS / FROZEN**（2026-09-24 用户 Freeze）
3. Batch 3 — Semantic Gap Judge（显式 callback；failure→同 execute baseline）✅ **PASS / FROZEN**（2026-09-26 用户 Freeze）
4. Batch 4 — Follow-up Plan + validation + dedup（identity/validation/dedup）✅ **PASS / FROZEN**（2026-09-28 用户 Freeze）
5. Batch 5 — Targeted Research + Incremental Verification（direct tool + 增量 F3 + PG gate）✅ **PASS / FROZEN**（2026-09-29 用户 Freeze）
6. Batch 6 — Round Orchestrator ✅ **PASS / FROZEN**（2026-09-30 用户 Freeze；`_govadapt.py`
   real-provider adapter E2E 保持 DEFERRED / environment-limited）
7. Batch 7 — Eval / Behavioral Quality 验证（baseline-vs-adaptive）✅ **PASS / FROZEN**
   （2026-09-30 用户 Freeze；已 MERGED → main 619f6c7）
8. Batch 8 — Stopping / Threshold Calibration ✅ **PASS / FROZEN**（2026-10-01 用户 Freeze；
   61 点实验 → 维持现状默认值；无 production default 修改；已 MERGED → main be6d625）
9. Batch 9 — **DEFERRED / Future P1（2026-10-01 用户决定停止推进）**：round/query-identity
   persistence（Spec §15 In 唯一未实现项）与 runtime recovery **不作为 F9-P0 阶段继续
   开发**；不创建 Batch9 feature branch。记录：Batch9 Readiness（§11 Decision
   Resolution）+ Final Completion Report
10. **P3 Runtime Observability（当前 Phase；`D-P3-001…008` 已落地）**：P3-1 Event Contract →
    P3-2 State Projection → P3-3 Admin API → P3-4 Frontend；P3-5 LangSmith integration（独立，
    需依赖 / 数据外发单独裁决）。旧 P2-3/P2-4 的 runtime observability 部分 **superseded-by-P3**；
    P2-3 残余（conversation transcript）**不在 P3**；P2-5（session 标题）**不受影响**

## 8. Important Known Limitations（仍影响后续开发的）

- multi-instance governance / lease / heartbeat：未支持（F8 明确不做，见 Freeze Review）。
- sweeper / startup 自动恢复（orphan reclaim 产品化）：未做（F8 文档化的 future）。
- polling / status-sync / durability_gap 实时预览：未实现。
- **P3 相关已知限制（`D-P3-001…008`）**：① conversation transcript / 对话历史**不在 P3**（刷新后仅
  恢复执行态与时间线，不恢复完整对话内容）；② 事件粒度默认 `tool`、step 级默认关闭（细粒度诊断需
  显式开启）；③ Admin API **默认关闭**（`RUNTIME_ADMIN_API=disabled`），默认配置下管理 Console
  不可用；④ LangSmith **未接入**（P3-5 独立，依赖与数据外发需单独裁决）；⑤ `pending_terminal` 仍无
  生产 flush 调用方（`D-Phase2-P2-2-012` 待办），投影必须容忍 `degraded_durability`；⑥ 跨进程重复
  terminal event 为已知限制（`D-Phase2-P2-2-009`）。
- **存量偏差（待单独裁决）**：`ARCHITECTURE.md` §9 要求新增环境变量同步 `.env.example`；P2-2 新增的
  12 个 `RUNTIME_*` 变量**从未同步**（`.env.example` 中 `RUNTIME_` 条目为 0）。P3 已冻结「新增 env
  MUST 同步 `.env.example`」；存量修复待裁决（详见 `P3_DECISION_CLOSURE_REPORT.md` §8.2）。
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
| F9 Batch 7 | **PASS / FROZEN**（2026-09-30 用户 Freeze） | `docs/plan/2026-09-30-f9-p0-batch7-implementation-report.md`；eval 16/16 + 8/8 scenarios；回归 sqlite 727 passed / 88 skipped / 0 failed；PG 全量 81 passed；branch `feature/f9-batch7-eval`（已 MERGED → main 619f6c7）；Compatibility Gap = None |
| F9 Batch 8 | **PASS / FROZEN**（2026-10-01 用户 Freeze） | `docs/plan/2026-10-01-f9-p0-batch8-implementation-report.md`；calibration runner + 4 tests；61 点实验 → 维持现状默认值（无 production default 修改）；回归 sqlite 731 passed / 88 skipped / 0 failed；PG 81 passed；branch `feature/f9-batch8-calibration`（已 MERGED → main be6d625）；Compatibility Gap = None |
| P2-1 默认 Runtime Policy 强制化 | **FROZEN + MERGED**（`dd7af9a`） | `D-Phase2-P2-1-001`；生产提交恒 governed |
| P2-2 Runtime Health Plane（heartbeat / stale / reclaim） | **FROZEN + MERGED**（`884a681` + merge `8d9531d`） | `P2-2_SPEC_v2.md` Rev 2.2；`D-Phase2-P2-2-001…017`；`P2-2_DECISION_CLOSURE_REPORT.md`；`PROBLEM.md` P007 关闭 |
| P3 Decision Closure | **APPROVED + LANDED**（docs-only commit；未实现） | `DECISION.md` `D-P3-001…008`；`P3_DECISION_CLOSURE_REPORT.md`；`P3_DECISION_UPDATE_PROPOSAL.md`；`P3_RUNTIME_OBSERVABILITY_DISCOVERY_REPORT.md` |
| F9-P0 Core | **COMPLETE**（2026-10-01 用户收敛决定） | `docs/plan/2026-10-01-f9-p0-final-completion-report.md`（Final Completion Report）；Batch1–8 全部 PASS/FROZEN/MERGED；Batch9 = Deferred / Future P1 |

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
| P3 决策与规划（当前 Phase） | `P3_DECISION_CLOSURE_REPORT.md`（Decision Closure + 冻结 / 实施约束）；`P3_DECISION_UPDATE_PROPOSAL.md`（`DECISION.md` 追加原文 + 裁决记录）；`P3_RUNTIME_OBSERVABILITY_DISCOVERY_REPORT.md`（L0 Discovery） |
| F9-P0 收尾报告 | `docs/plan/2026-10-01-f9-p0-final-completion-report.md`（Final Completion Report）；Batch9 记录 `docs/plan/2026-10-01-f9-p0-batch9-readiness-review.md` |
| 当前实现计划与证据 | docs/plan/（当前 Batch Plan/Report；历史报告勿默认读） |
| 历史问题细节 | docs/problem/（仅命中时读） |

## 11. Current Next Action

> **当前唯一允许的下一步：批准创建 `feature/p3-1-event-contract` branch（git 写操作）→ 进入
> P3-1 Event Contract 的 L3 Spec 阶段（Spec → Readiness → Decision → Plan Review）。**
> - F9-P0 Core Implementation = **COMPLETE**（2026-10-01 用户决定）；Batch 1–8 全部
>   **PASS / FROZEN / MERGED**（B7 `619f6c7`、B8 `be6d625`；本地=远程一致）；Git Workflow
>   规则在 Harness（AGENTS.md §10 / PROCESS.md §12）；
> - **Batch9 = Deferred / Future P1**（用户决定停止推进）：不实施 round/query-identity
>   persistence、不建 `feature/f9-batch9-round-persistence`、不改 F9 runtime；
>   记录 = Batch9 Readiness §11 Decision Resolution + Final Completion Report；
> - 当前阶段 = **P3 Runtime Observability（Decision Closure 已落地 / 未实现）**：允许 P3-1 Spec 等
>   文档产物；禁止修改 `app/**` / DB / migration / 依赖、修改 P2-1/P2-2 frozen code 或 F8/F9 冻结
>   契约、绕过 `D-P3-001…008` 冻结约束、Commit 前扩大 scope；
> - P3 Decision Closure 已落地（`DECISION.md` `D-P3-001…008` + 本文件同步；docs-only commit，未 push）；
>   P3-1 前置链剩余项 = **branch 创建**（需用户批准），完成后进入 Spec 起草；
> - 未获用户指令前：不 Commit / Push / Merge；遇 frozen contract 冲突 → STOP 并报
>   COMPATIBILITY GAP。

## 12. Context Maintenance Rules

- 本文件**只保存"当前状态"**：当前 Feature/Batch/状态/允许动作/Next Action/权威文档指针。
- **不保存**：完整历史、完整 Spec、测试日志、实现细节、已失效的历史状态。
- Feature / Batch 完成后更新本文件（状态、Next Action、§9 基线）；历史信息留在对应
  Spec / Plan / Report，不复制回本文件。
- 与权威文档冲突时：以权威文档为准（AGENTS.md §1），**报告冲突**，不得让本文件覆盖权威。
- 本文件新增条目禁止"创造新规则"或"重新定义架构"——只能索引与摘要。
- 若本文件内容已无法在低上下文成本下回答 §「完成标准」的 10 问 → 精简本文件，
  而不是继续加规则。
