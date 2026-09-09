# F9-P0 Batch 4 — Follow-up Plan + Validation + Dedup Implementation Report

> 状态：**BATCH 4 PASS / FROZEN**（用户 2026-09-28 正式 Freeze；本批只交付 Follow-up Plan 层；
> 不执行研究；不宣布 F9-P0 COMPLETE）。
> 依据：F9-P0 Spec Rev2 §6/§6.1/§12/§13、Plan Rev2 §D4/§G/§R4/§J、Batch4 Readiness Review
> （CONDITIONAL → P1–P6 **ACCEPT** → READY FOR IMPLEMENTATION，2026-09-26）。
> 上游：Batch 1 Projection / Batch 2 Gap Detection / Batch 3 Semantic Gap Judge（均 PASS/FROZEN）。

## 1. Scope

- In：`app/f9/plan.py` —— Follow-up Plan proposal（可选 LLM，P2）、plan schema、identity 层级、
  deterministic validation、dedup/retry/re-query/re_verify 分类、plan_id fingerprint。
- Out（未实现/未触碰）：Targeted Research / Verification 执行 / F9 Orchestrator（Batch6）/
  F7 / Runtime 控制面（task/controller/budget/watchdog/cancel/deadline）/ 新 Agent / migration /
  修改 Batch 1–3 / F8 / F1–F7 / Spec·Plan。

## 2. P1–P6 Decisions（裁决落地）

- P1：**in-run in-memory ledger** —— 零新表/migration/research_rounds；dedup 只对调用方传入的
  history 判定；跨 execution/crash 持久化留给未来 additive Gate。
- P2：plan-proposal LLM（若启用）复用 F8 ctx：`ctx.make_handler()` +
  `ainvoke(config={"callbacks":[handler]})`；计费 +1 llm_call / +0 agent_step；任何
  provider/malformed/JSON/schema/未知字段/引用/bounds/deterministic 失败 →
  `PlanProposalFailure`（调用方按 R2-1 同 execute baseline）；GovernanceLimitExceeded /
  CancelledError 原样传播。
- P3：objective = bounded normalized text + 显式 target sq（objective_identity 绑定两者）；
  source_universe_identity = SHA256(tool+agent+CONFIG_VERSION)（无 secret）；time_window ∈
  {`NONE`, `YYYY-MM-DD/YYYY-MM-DD`}，禁止 wall-clock 生成。
- P4：canonicalize_query —— 可解析 http(s) URL → 复用 `app/research/normalize.canonicalize_url`
  （host 小写/去 tracking/query 排序；path 大小写保留）；非 URL → lowercase + whitespace→单空格
  + strip。
- P5：候选 = important==True 的 judged gap；排序 priority high>medium>low，同 priority 按
  Batch3 输入序；上限 `MAX_PLAN_CANDIDATES=16`（Batch8 校准常量）。
- P6：plan_id = SHA256(canonical_validated_plan)（sort_keys JSON；不用 UUID）。

## 3. Module / API

- `app/f9/plan.py`（新增；纯函数为主 + 一个可选 async LLM 入口）：
  - canonical：`canonicalize_query / canonicalize_objective / canonicalize_time_window /
    canonical_source_universe`
  - identity：`query_identity_of / objective_identity_of / source_universe_identity_of /
    time_window_identity_of / dedup_identity_of / plan_id_of`
  - pipeline：`select_candidates → parse_proposals → build_plan → apply_dedup`
  - LLM 入口：`propose_followup_plans(projection, gap_signals, judgments, plan_model, *, tool,
    agent, time_window, kind, history)`（须在 governance-active context）
  - 错误：`PlanValidationError`（纯函数确定性失败）、`PlanProposalFailure`（LLM/提议失败；
    F8 control 不包装）
- 常量：MAX_OBJECTIVE=600 / MAX_QUERY=800 / MAX_QUERIES_PER_GAP=3 / MAX_PLAN_CANDIDATES=16 /
  MAX_WHY=600 / MAX_STOP_CONDITION=400 / MAX_TARGET_CLAIMS=8 / CONFIG_VERSION="f9-plan-config-v1"。

## 4. Identity / Canonicalization Contract（§7 层级，概念分离）

```text
query_identity       = SHA256(canonicalize_query(query))
objective_identity   = SHA256(target_sq_id \x1f canonical_objective)
source_universe_identity = SHA256(tool \x1f agent \x1f CONFIG_VERSION)
time_window_identity = "NONE" | "YYYY-MM-DD/YYYY-MM-DD"
dedup_identity       = SHA256(\x1f.join(query_identity, objective_identity,
                                        source_universe_identity, time_window_identity))  # ≠ query_identity
plan_id              = SHA256(json(sort_keys, canonical_validated_plan))                  # ≠ dedup_identity
```

确定性证据：同输入 → 同 identity（测试覆盖大小写/空白/URL tracking/query 排序/任一 component
改变 → dedup 变化）；无随机/无 wall-clock/无 LLM 顺序依赖。

## 5. Plan Schema（Spec §6 + 内部 metadata 分离）

- public：`plan_id / target_sub_question_id / objective / queries[] / priority /
  verification_target_claim_ids / stop_condition / why / gap_refs`
- queries[] 条目（内部 identity metadata 同条）：`query / kind(research_query|re_verify) /
  query_identity / dedup_identity / status(accepted|retry) / attempt`
- 派生规则（deterministic）：target_sq = claim→其 sq；sq→自身；否则 root required（无 → plan
  None 跳过）；verification_target_claim_ids = claim 自身 或 该 sq claims（sorted、cap 8，绝不取
  LLM 提供）；why 缺省 = judgment reason；stop_condition 缺省 = `gap:<id>:resolved`。
- 每 plan 锚定 ≥1 有效 Batch2 gap/Batch3 judgment；禁止越界字段。

## 6. Validation（fail-closed）

- 引用：gap_id ∈ 当前 gaps；judgment ∈ 当前 judgments 且 important==True；target sq / claims ∈
  本 run（cross-run 引用在 select_candidates / parse_proposals / build_plan 三层均拒）。
- schema：未知字段 / 缺 objective·queries / 枚举（priority、time_window、kind）非法 /
  wrapper 缺失 → 拒。
- bounds：objective/query/why/stop_condition 长度、queries/plan/target-claims 数量 → 常量上限。
- 顺序：validation 通过后才生成 identity（query/dedup/plan_id）；partial plan 绝不进入下游。

## 7. Dedup / Retry / Re-query / Re-verify

| 情形 | 判定（classify_query/apply_dedup） |
|---|---|
| duplicate | 同 dedup_identity 且历史最近 entry outcome=succeeded（sources_produced）→ 从 plans 移除并记 `rejected_duplicates` |
| retry | 同 dedup_identity 且历史 outcome=failed 且无新 source → 保留，attempt=last+1，identity 不变 |
| re-query | 任一 identity component 改变 → 新 dedup_identity → 正常 accepted（非 retry） |
| re_verify | kind 独立 namespace：re_verify 历史与 research_query 历史互不参与对方 dedup |

## 8. Plan-proposal LLM / F8 seam（P2）

- 计费路径与 Judge 相同（unclassified → acquire_llm_call：+1 llm / +0 agent_step；单 handler）；
- provider failure / malformed / validation 失败 → `PlanProposalFailure`（partial 丢弃）；
- GovernanceLimitExceeded / CancelledError / F8 timeout 不吞 → Controller 收敛
  budget_exceeded / cancelled / timed_out；
- 无 important candidate → 不调 LLM（与 A2/Judge 同纪律）；
- 同 execution 身份：测试断言 task_id/run_id 一致、counters_snapshot llm=1/step=0。

## 9. Tests（34 passed，`tests/test_f9_plan.py`）

- Canonicalization/Identity（10）：非 URL/URL canonical（复用 normalize）、empty 拒、objective
  绑定、source universe 稳定无 secret、time window NONE/显式/非法、identity 层级分离
  （dedup≠query）、dedup 对各 component 敏感、plan_id deterministic 且 ≠ dedup。
- Candidate selection（4）：important 过滤、priority 单调 + 同 priority 输入序、cross-run 拒、
  MAX_PLAN_CANDIDATES cap。
- build_plan（4）：契约字段、claim target、unimportant/missing judgment 拒、无 researchable
  target → None、unknown gap/字段/bounds 拒。
- Dedup semantics（4）：accepted/retry/duplicate、re_verify namespace 独立、re-query 新
  identity、apply_dedup 保留顺序 + retry attempt+1 + duplicate 拒绝清单。
- Proposal parsing & LLM（6+）：valid 顺序、8 类 failure、P2 accounting（+1llm/0step/无裸调、
  degraded diagnostic 存在）、无候选不调 LLM、malformed/provider → PlanProposalFailure、
  budget → GovernanceLimitExceeded（model 0 调）、无 ctx 拒。
- Governance seam（3）：同 execute 计费 completed、proposal failure → 同 execute baseline
  completed、timeout → timed_out（llm 已计 1）。

## 10. AC → Test → Evidence

| AC（§15/§18） | Implementation | Test | Evidence |
|---|---|---|---|
| Plan generation（valid important→plan；unimportant→no） | select_candidates + build_plan | selection/build 类 | 34 passed 内逐项断言 |
| deterministic ordering & bounded | (priority, input order) + 常量 cap | selection/parsing 类 | cap 与单调断言 |
| Validation fail-closed（malformed/unknown/missing/引用/cross-run/bounds） | 三层校验 | 多 failure 用例 | PlanValidationError/PlanProposalFailure |
| Identity determinism & 层级 | canonical+sha 纯函数 | identity 类（10） | 确定性/敏感度断言 |
| Dedup/retry/re-query/re_verify | classify/apply（ledger 注入） | dedup 类（4） | 状态与 namespace 断言 |
| LLM callback accounting & 失败语义 | propose_followup_plans | LLM/seam 类 | llm=1/step=0；GLE 不吞；failure→failure 异常 |
| F8 seam 单执行体 | 复用 Controller.execute | seam 3 例 | completed/cancelled/timed_out 身份 |
| Regression Batch1–3 & F8 | 未改冻结代码 | 全套件 | sqlite 679 passed / PG 74 passed |

## 11. Regression

- sqlite 全量：**679 passed / 85 skipped / 0 failed**（= 645 + Batch4 34；排除 mysql 环境污染
  文件既有已知项；Batch 1–3 与 F8 sqlite 套件全绿）。
- PG 双 DSN（research F1–F7 + governance + F9 projection/gaps）：**74 passed / 0 failed**
  （governance 表先 reset）。Batch4 无 DB 集成（P1 in-memory）→ 无 PG 测试文件（同 Batch3 §4）。
- ruff / format / compileall（app/f9）全绿。

## 12. Limitations

1. P1 范围内 dedup 仅对**传入 ledger** 有效（跨 execution/crash 持久化未做，future additive
   Gate）；search_queries 现有 schema 无 identity/objective/success 列（事实记录，未改 F1）。
2. gap_id 仍为派生 identity（Batch2 FROZEN 无独立列）。
3. MAX_* / CONFIG_VERSION / priority 次序等为初始常量，Batch8 Eval 校准前非冻结协议值。
4. plan-proposal LLM real provider E2E 受凭据限制（fake ChatModel 验证协议/计费，同 F3/F9 纪律）；
   每次 LLM 计费产生 `llm_classification_unresolved` degraded diagnostic（F8 冻结分类固有，未改）。
5. T11/T13 类的真实 baseline（run_deep_agent）与 F7 exactly-once 仍属 Batch 6/7 分层验证。

## 13. Scope Audit

- 新增：`app/f9/plan.py`、`tests/test_f9_plan.py`、本报告。无 PG 测试（理由 §11）。
- 零改动：`projection.py`、`gaps.py`、`judge.py`、F8、F1–F7、Spec/Plan、API/frontend/依赖/
  migration、tests（既有未改）、Batch 5+。
- git diff/status：仅上述新增 + 既有未提交产物（未触碰）。

## 14. Final Decision

```text
BATCH 4 PASS / FROZEN（2026-09-28 用户正式 Freeze）
```

实现与验证证据（P1–P6、identity/dedup 契约、测试结果、Limitations、Scope Audit）原样保留，
无重写。本批未宣布 F9-P0 IMPLEMENTATION COMPLETE。下一阶段 F9-P0 Batch 5 — Targeted Research
+ Incremental Verification 为 PENDING：等待用户显式指令，不得自动开始。
