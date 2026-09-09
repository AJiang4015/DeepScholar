# F9-P0 Batch 5 — Targeted Research + Incremental Verification：Implementation Readiness Review

> 状态：**READINESS REVIEW（read-only）→ 结论 CONDITIONAL（见 Final Decision）**。
> 分级：PROCESS.md §11 **L2**（用户裁定；若发现 L3 触发项 → §11.3 Stop→Reclassify，本审未发现）。
> 范围：只判断 Batch 5 是否具备进入独立 Implementation Plan 的条件；不实现、不改任何
> 代码/tests/Spec/Plan/Harness/F8/F1–F7/Batch1–4。

---

## 1. Scope

- In（Readiness 论证范围）：Targeted Research（plan 驱动查证）+ Incremental Verification
  （F3 增量）+ 增量 artifact 写回同 run + 结构化结果供后续 Projection/Stopping。
- Out（不实现/不触碰）：Batch 6 Orchestrator、Batch 7 F7 seam、Batch 8 Eval；Judge/Gap/Plan/
  Dedup（Batch2–4 已 FROZEN）；新 Runtime/Controller/Agent/基础设施/UI/migration/F1–F8 schema。

## 2. Current Architecture Compatibility

- 复用面（已实读核实）：
  - `internet_search` 工具（app/tools/tavily_tool.py）在 `research_ctx` 存在时**自行注册**
    SearchQuery/Source/Evidence（record/upsert/append，artifacts_guard fail-open）→ Targeted
    Research 可直接复用该既有 ingest 链路，无需新增抽取逻辑、无 schema 变更、不绕过 registry。
  - `app/research/context.py`：run 级 (run_id, sub_question_id) ContextVar；`set/get/reset`
    公开 → 同一 run 内按 plan target 切换 sq 无需 API 改动。
  - F3 `semantic_verify_claim(claim_id, …)`（默认 Fake；VERIFY_REAL_LLM 门控 real）、
    fingerprint 幂等 UNIQUE(run,claim,evidence,fingerprint)（0003）→ 增量 append-only。
  - F4/F5/F6 claim 级 public（detect_claim_conflicts / compute_claim_corroboration /
    reconcile_claim_conflicts + run 级入口）可选增量复用。
  - registry：bind_claim_evidence（幂等、(claim_id,evidence_id)）、append_evidence、provenance
    读取——均为显式 run_id public API。
- F7 位置（已实读 main_agent.py）：`finalize_run` 只在 `run_deep_agent` astream 正常结束后
  执行一次；run_deep_agent 每次调用都会 create_run_and_root + set_run_status + F7 →
  **不可用于逐轮 targeted 执行**（会新建第二个 ResearchRun 并提前 finalize，违反 R6/R12）。
- 因此 Targeted Research 执行 seam ≠ run_deep_agent；可选项见 §5 决策 D-A。

## 3. F8 Seam

- 全部执行发生在单个 `Controller.execute(task_id, coroutine, policy)` 内：Batch5 模块被未来
  Batch6 orchestrator 在该 coroutine 中调用；共享 task_id/run_id/GovernanceExecution/
  BudgetCounter/deadline/cancel。
- 新 LLM/tool/search 调用进入 F8 accounting 的方式：经 `ctx.make_handler()` 注入 config
  callbacks（与 Batch3/4 同模式）。**直接工具调用**：`tool.ainvoke(input, config={
  "callbacks":[handler]})` 触发 on_tool_start → search/tool 双槽（internet_search ∈
  search_tool_names）由冻结 handler 计数（callbacks.py 已核实）→ 无需改 F8。
- 发现需改 F8 Controller/Budget/TaskRecord/runtime lifecycle → 立即 L3；本审未发现。

## 4. Research Plane Seam

| 需要（R2） | 复用 API | 结论 |
|---|---|---|
| 创建/追加 SubQuestion | registry 仅 root（create_run_and_root） | Batch5 plan 锚定现有 sq（今日仅 root）→ **不需要**新建 SQ；未来子问题分解需 additive registry API（非阻塞，见 §Non-blocking N1） |
| 创建 SearchQuery | 工具自注册 or registry.record_search_query | ✅ |
| Source 幂等/Evidence | registry.upsert_source / append_evidence（工具或直接） | ✅ |
| Claim–Evidence binding | registry.bind_claim_evidence | ✅ |
| F3 Verification | semantic_verify_claim / semantic_verify_run | ✅ |
| F4/F5/F6 读取 | claim 级 public + provenance | ✅ |

未发现必须修改 F1–F6 frozen contract 之处；无绕过 store 直接写内部表的设计。

## 5. Targeted Research Contract

- 输入 = Batch4 ValidatedPlan（Plan Rev2 §6/§Batch4 输出）：
  `plan_id / target_sub_question_id / objective / queries[](query+query_identity+dedup_identity+
  kind+status) / priority / verification_target_claim_ids / stop_condition / why / gap_refs`。
- 执行（建议 seam，待 D-A 裁决）：对每条 status∈{accepted,retry} 的 query，在同一 run 内以
  research_ctx=(run_id, target_sq) + governance handler 调用既有 search tool（或 D-A 备选）；
  不重新规划、不产新 query（禁止 Planner/Judge/Gap/Dedup 语义重实现）。
- Query 执行结果不做语义解释（普通 failure 由 §7 定义）；每条 query 记录 outcome
  {dedup_identity, kind, outcome, sources_produced, evidence_added} → 供 Batch4 ledger 由
  orchestrator 回填（retry/duplicate 语义维持 Batch4）。
- R10：仅传 validated plan + bounded context（无 parent history/无 Agent 全量 context）。

## 6. Incremental Verification Contract（关键候选对齐——本审冻结，不在实施期临时定）

- 触发：本轮实际新增的 Evidence（executor 收集 new_eid 集）与 plan 的
  `verification_target_claim_ids`（仅当 claim ∈ 本 run）。
- 对齐规则（候选集合，deterministic）：`candidates = target claims that got ≥1 new binding
  本轮`；新增 binding 由 executor 对每个 (target_claim × 本轮 new_eid) 调
  `registry.bind_claim_evidence`（幂等；cap 常量 ≤8/claim 建议，Batch8 校准）产生。
- 验证：对 candidates 调 `semantic_verify_claim`（默认 budget=每 claim 全部 bound evidence 或
  上限常量；Fingerprint 幂等保证 append-only，不改 F3 语义）。
- 无合法 candidate（无 target claim / binding 守卫拒绝 / 无新 evidence）→ **跳过验证并记录
  no-op**（不产伪 verdict；对齐 F7 "disabled≠verified" 纪律）。
- F3 failure / ABSTAIN / CONTRADICTS 向 F9 返回方式：不改 F3——verification 行 status
  failed/pending 与 verdict（SUPPORTS/CONTRADICTS/INSUFFICIENT/UNVERIFIABLE/ABSTAIN）原样进入
  Batch5 result；ABSTAIN/CONTRADICTS 是否触发后续 gap/conflict 由 Batch2/6 的既有确定性/语义
  层消费，Batch5 不裁决。
- F4–F6 增量时机：Spec §10 "冲突集合变化才增量跑" —— 是否在 Batch5 内执行
  （detect_claim_conflicts/compute_claim_corroboration/reconcile on changed claims，real
  gated）或移交 Batch6 evaluator → **决策 D-D**。

## 7. Identity / Dedup

- 严格复用 Batch4：query_identity / dedup_identity / plan_id 及 kind、retry attempt 语义；
  Batch5 不重新定义 identity、不新增 namespace。
- duplicate：Batch4 已从 plan 剔除（rejected_duplicates），executor 只执行 accepted/retry；
  若 ledger 事后显示 duplicate（race）→ executor 跳过并记录（保守）。
- re_verify：Batch5 不含 re_verify 触发面（re-verify 已有 evidence 属后续 feature；如需要则
  作为独立 kind 走 Batch4 语义，本审无触发）。

## 8. Budget / Timeout / Cancellation

- 所有 tool/search（targeted）经 handler 计费（tool+search 双槽）；verification real-LLM（若
  VERIFY_REAL_LLM）经 F3 verifier 既有链路（verify.py fake/controlled；其 LLM 调用的 F8 计费
  归口在 Phase 文档待实现期接线——见 D-C）。
- 只读 budget：Batch1 projection.budget / BudgetCounter.to_dict() 已够（Batch2/4 已消费），
  无 compatibility gap。
- cancel/timeout/budget：Batch5 无独立 watchdog/cancel——F8 单执行体原生覆盖（Batch3/4 已证
  模式）；同步 registry 写入按 F1 fail-open/事务边界短写。

## 9. Failure Semantics（§R7 八类）

| 情形 | 处理 |
|---|---|
| 1 ordinary targeted failure（工具异常） | 该 query 记 failed（outcome=failed,sources=False）→ 继续其它 query；同 execute |
| 2 verification failure（F3 行 failed） | 原样保留 rows（不改 F3）；候选 claim 记 verification failed；不伪造 verdict |
| 3 malformed research result（工具返回不可解析） | 该 query outcome=failed + reason；不中断 |
| 4 duplicate query | 跳过（见 §7） |
| 5 no valid candidate | 验证跳过 + no-op 记录 |
| 6 GovernanceLimitExceeded | **不吞** → 向 Controller 传播 → budget_exceeded |
| 7 cancellation | **不吞**（CancelledError 原样）→ cancelled |
| 8 timeout | **不吞** → F8 watchdog → timed_out |

无第二 task/controller；普通 failure 走同 execute 内 orchestrator 的 R2-1 fallback/round 语义
（Batch6 接线，Batch5 只保证不吞 F8 control 并输出结构化 outcome）。

## 10. Context Boundary

- 输入 bounded：validated plan + 现有 Projection 事实指针（不复制 evidence 正文/history）；
  每条 query 只带 objective/query 与目标 sq 的 question snippet（≤ 常量）。
- 不产生第二条 message history 复制；工具 ingest 由既有工具实现承担。

## 11. Persistence Boundary

- 默认零新表/migration；artifact 增量全走 F1–F6 既有表与 registry/verify public API。
- 若实现期发现必须新增（research_rounds / targeted ledger / plan 持久化 / 新表）→ 立即报告并
  升级（L3 检查点），不直接改（对齐 P1/§17 Q5 additive Gate）。

## 12. Batch 5 → Batch 6 Boundary

- Batch5 输出（bounded/deterministic/结构化）建议：
  `{run_id, round, executed_queries[], verification[] (claim/aggregate/rows/no-op), 
   added_evidence_ids, ledger_updates[], failures[]}`——供下一轮 Projection 重投影与
  Stopping 消费；不驱动 round/不写 round state（Batch6）。
- Batch6 职责：持有 round/ledger、编排调用 Batch1–5、fallback baseline、stopping；
  Batch5 模块保持无编排状态。

## 13. Blocking Issues

- 无（代码级可在冻结契约内实现：单 execute、工具+F3 复用、零 schema/identity/runtime 改动）。

## 14. Non-blocking Issues

1. N1：无 registry public API 新建**子** SubQuestion（今日 root-only）——Batch5 用不到
   （plan 锚定现有 sq）；未来树化需要 additive API（frozen F1，另行裁决）。
2. N2：ARCHITECTURE.md §3 红线"不得绕过 run_deep_agent 直接调 main_agent.astream"与 F9 Spec
   §2.2（同一 governed execution 内多次 astream）并存——Batch5 建议 seam 避开该红线（工具级
   + 单 run ctx），不触发；main-agent 级 astream 复用属 Batch6 orchestration seam（需在 Batch6
   readiness 前裁决 ARCHITECTURE 回填/例外，2026-09-22 治理审计已记录 ARCHITECTURE 落后）。
3. N3：real-LLM（verifier/search provider）E2E 受凭据限制（Fake 验证协议与计费，F3/F9 纪律）。
4. N4：工具 ingest 的 evidence 落 plan target sq 依赖 research_ctx 指向（今日 root；未来多 sq
   由 orchestrator 切换 ctx，无 API 改动但需纪律）。

## 15. Required Decisions（进入 Implementation Plan 前需用户裁决；均非代码 blocking）

- **D-A（执行 seam）**：Targeted 执行 = 直接调用既有 `internet_search` 工具
  （tool.ainvoke + handler callbacks + research_ctx）【推荐】 vs network 子代理图 astream。
  影响：计费/单 run/ownership 边界。推荐 A 保持最小并规避 N2 红线。
- **D-B（binding/cap 常量）**：每轮每 claim 新增 binding 上限常量（建议 8）与验证 budget
  常量（建议默认全 bound evidence，或按 F3 VerifyBudget cap）——Batch8 校准前固定实现值。
- **D-C（real-verifier 的 F8 计费接线）**：VERIFY_REAL_LLM=1 时 F3 verifier 内部 LLM 调用是否
  挂 GovernanceHandler（同 ctx）计费（spec §2.2 语义；实施时接线并加对抗测试）。
- **D-D（F4–F6 增量归属）**：Batch5 是否执行 changed-claim 的 F4/F5/F6 增量（detect/
  corroborate/reconcile 各 claim 级 real gated），或留 Batch6 evaluator 统一跑。推荐 Batch5 含
  F3 增量 + 结构化触发事实；F4–F6 全量/增量编排移交 Batch6（本批只读其结果以输出
  conflict-universe 变化信号）。

## 16. L2/L3 Classification Check

- L2 成立：跨模块/Research Plane 行为 Feature 级；复用 F1–F6/F8 public contract，零 schema/
  migration/identity/runtime/concurrency 修改，无 frozen 改动（与用户裁定一致）。
- L3 触发项检查：未发现（§3/§11/§15 无 F8/frozen/persistence 修改需求；N2 红线在推荐 seam
  下不触发）。若 D-A 选择触及 main_agent.astream 或实现期发现需改 F8/F1 契约 → 立即 §11.3
  Stop → Reclassify L3。

## 17. Final Decision

```text
BATCH 5 READINESS: CONDITIONAL
```

- 结构条件满足：可在当前 F8 + F1–F6 frozen contract 内实现（单 execute、工具 ingest 复用、
  F3 claim 级增量、零 schema）；未发现 L3 触发项（维持 L2）。
- 进入独立 Implementation Plan 前需用户裁决 **D-A / D-B / D-C / D-D**（§15）；裁决后可产出
  Batch5 Implementation Plan → Plan Review → Implementation → Report → User Review/Freeze。

**STOP。** 未创建 Implementation Plan，未实现 Batch 5；等待用户 Review 与对 D-A–D-D 的裁决。
