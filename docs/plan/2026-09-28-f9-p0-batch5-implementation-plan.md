# F9-P0 Batch 5 — Targeted Research + Incremental Verification：Implementation Plan

> 状态：**IMPLEMENTATION PLAN READY**（待用户 Review；不进入 Implementation）。
> 依据：F9-P0 Spec Rev2 §10/§2.2/§12、Plan Rev2 §I Batch5、Batch5 Readiness（CONDITIONAL）
> ＋用户裁决 **D-A（直接调 internet_search）/ D-B（≤8 candidates per claim）/ D-C（real-LLM
> verifier 挂 F8 handler）/ D-D（Batch5 只做 F3 增量，F4–F6 归 Batch6）**。
> 分级：L2（PROCESS §11）；触发 §12 条件即 Stop→L3。
> 本文件 = How to implement；不复制 Spec。

## 1. Scope

- In：新模块 `app/f9/targeted.py`：接收 Batch4 ValidatedPlan → 对 accepted/retry query 直接调用
  现有 `internet_search`（D-A）→ 捕获同 run 新增 Evidence → 对 target claims 按 D-B 做 bounded
  incremental F3 verification（`semantic_verify_claim`）→ 返回 bounded deterministic result。
- Out：不创建 Agent/Graph/Runtime/Controller/task/BudgetCounter/migration；不触发 F7；不新建
  ResearchRun；不编排 F4–F6（D-D）；不产下一轮 Plan；不改任何 F1–F8 frozen 文件（D-C 走 F3
  公共 seam 注入，见 §8）。

## 2. Existing API Inventory（本 Plan 只调用以下 public seam；均已实读核实）

- Research context：`app/research/context.py` `set/get/reset_research_context`（run_id +
  sub_question_id；同一 run 内切换 sq 无需改 API）。
- Artifact ingest（Targeted 查询 = **既有工具内部 ingest**，D-A 禁重实现）：
  `app/tools/tavily_tool.py::internet_search`（@tool；在 research_ctx 存在时经
  `research_reg.artifacts_guard` 调 `record_search_query / upsert_source / append_evidence`；
  run_id 与 sq 取自 ctx）。
- Registry 显式 seam（binding 阶段）：`registry.bind_claim_evidence(run_id, claim_id,
  evidence_id, …)`（幂等，(claim_id,evidence_id)）。
- F3 claim 级：`semantic_verify_claim(claim_id, *, spec, allowed_evidence_ids, budget
  (VerifyBudget.max_evidence_per_claim), verifier(BaseVerifier), confidence_threshold,
  timeout_ms)`；fingerprint 幂等 UNIQUE(run,claim,evidence,fingerprint)（0003）；默认 Fake、
  real 由 spec.provider/VERIFY_REAL_LLM 门控。
- F3 只读：`list_verifications`、`aggregate_claim_verdict`（可选结果增强）。
- Provenance 只读（snapshot diff）：`provenance.list_evidences(run_id)`、
  `get_claim_chain(claim_id).evidences`（python 再按 (_ts, id) 确定性重排）。
- 未来 Batch6 接线：Batch4 `plan.py` 数据结构（plan_id/gap_refs/objective/queries[query,
  query_identity,dedup_identity,kind,status]/verification_target_claim_ids/target_sub_question_id）。

## 3. Architecture / Data Flow（每步标注现有 public API）

```text
F8 Controller.execute(task_id, f9_orchestrator(...), policy)   [唯一执行体]
  └─ Batch6 调用 Batch5（后续编排）: run_targeted_plan(plan, ...)   （本批不实现 Batch6）
      1) research_ctx.set_research_context(run_id, plan.target_sub_question_id)
      2) snapshot pre = evidences(run) & bound(target claims)          [provenance 只读]
      3) for q in plan.queries where status∈{accepted,retry}:
            handler = GovernanceExecution.make_handler()               [F8]
            payload = await internet_search.ainvoke({"query": q.query},
                        config={"callbacks":[handler]})                [D-A；F8 计 search+tool]
            → 工具内部 ingest SearchQuery/Source/Evidence（同 run_id）
            → outcome = {query, dedup_identity, kind, ok, sources_added, evidence_added}
      4) post = evidences(run)；new_eids = sorted(post − pre)          [deterministic]
      5) for claim in sorted(plan.verification_target_claim_ids) (⊆ run):
            new_bound = bind_new_evidence(claim, new_eids ∩ 未 bound, cap=D-B 8)
              [registry.bind_claim_evidence 幂等；snapshot diff 判“本轮新增”]
            if new_bound: semantic_verify_claim(claim, allowed_evidence_ids=new_bound[:8],
                              budget=VerifyBudget(max_evidence_per_claim=8),
                              verifier=governed-verifier-or-Fake)      [F3]
      6) result = bounded TargetedRoundResult（§12）→ 返回给 Batch6（投影/stopping 输入）
```

## 4. Targeted Research Design（模块 `app/f9/targeted.py`）

- 纯编排 + F8 计费；不重实现 ingestion/planner/judge/gap/dedup（复用 Batch4 plan 状态）。
- 常量：`MAX_VERIFY_CANDIDATES_PER_CLAIM = 8`（D-B；Batch8 校准前固定，禁止实现期放大）；
  `MAX_QUERIES_PER_ROUND`（=plan 自带 ≤ MAX_QUERIES_PER_GAP*候选，执行时以 plan 为准上限）；
  输出 `MAX_RESULT_*`（见 §12）。
- 查询执行函数签名（规划）：
  `async def execute_query(query: str, *, sub_question_id: str, tool=None, search_ctx=True)
   -> dict`：进入 governance ctx（handler 取自 `get_governance_execution().make_handler()`），
  调用 `tool or internet_search` 的 ainvoke(config callbacks)；普通异常 → outcome failed 记录，
  **GovernanceLimitExceeded / CancelledError 不捕获（上抛 F8）**。
- run 身份纪律：不 create_run_and_root；不 set_run_status；research ctx 只 set/reset 当前
  (run_id, sub_question_id)；run_id 取 plan 所在 execution 的既有 run（由 orchestrator 传入，
  与 TaskRecord.run_id 同源）。

## 5. Incremental Verification Design

- candidates = “本轮新增 binding 中属于 target claim 的 Claim–Evidence”（确定性）：
  - 新增 binding 判定 = bind_claim_evidence 调用前的 bound 集快照（get_claim_chain）与本次
    绑定后差集；重复绑定（幂等返回既有）不计数、不重复候选。
  - ordering：new_eids 按 (evidence created_at, evidence_id) 升序；claim 按 (claim_id) 升序。
- cap（D-B）：每 claim 至多 8 个本轮候选；超 8 只取前 8 并记 `truncated` 计数（不作伪 verdict）。
- 调用 F3：`semantic_verify_claim(claim_id, allowed_evidence_ids=newly_bound[:8],
  budget=VerifyBudget(max_evidence_per_claim=8, include_opinion=False), verifier=…)`；
  不改 F3 semantics；verdict/status/error 原样保留（SUPPORTS/INSUFFICIENT/CONTRADICTS/
  UNVERIFIABLE/ABSTAIN；failed/pending）。
- no-op：无 target claim 命中（不在 run/不存在/守卫拒绝）或无新增 binding → 跳过验证并记录
  no_op（不产伪数据）。
- result 聚合：aggregate（aggregate_claim_verdict）可并入（只读）。

## 6. Candidate Selection（§5 已定义；此处固化边界）

- 输入候选集合 = plan.verification_target_claim_ids（Batch3/4 已保证 ∈ 本 run）；
- 与 actual 对齐 = 仅保留本轮实际新增 ≥1 binding 的 claim（新 evidence 先绑定后验证）；
- 无合法 candidate 或 binding 全部重复 → no-op；
- 验证行失败（provider/malformed）→ F3 failed 行原样（status/error），Batch5 不裁决 truth。

## 7. Identity / Dedup

- 复用 Batch4：`plan_id`/`query_identity`/`dedup_identity`/`kind`/`attempt`；Batch5 不重定义。
- 执行过滤：只跑 plan 中 status ∈ {accepted, retry}；duplicate 已在 Batch4 剔除
  （rejected_duplicates），若执行期 ledger 竞态发现 duplicate → 跳过并记 skipped(reason)。
- re_verify：Batch5 不产生 re_verify（已有 evidence 复验留后续 feature；如出现则走 Batch4
  kind 语义由编排层处理）。
- ledger 回填：executor 输出 `ledger_updates[]`（{dedup_identity, kind, outcome,
  sources_produced, attempt}）由 Batch6 写回 in-memory ledger（Batch4 P1 语义）。

## 8. F8 Governance Seam（含 D-C）

- 单 execute/单 ctx/单 counter/deadline/cancel（不变式）；无新 control plane。
- search/tool 计费：internet_search.ainvoke + config callbacks → handler.on_tool_start
  （internet_search ∈ search_tool_names → tool+search 双槽）。Fake 无 LLM。
- **D-C real-LLM verifier 接线（不改 F8、不改 F3 文件）**：
  - F3 公共 seam = `semantic_verify_claim(verifier=BaseVerifier)` 与 `allowed_evidence_ids`；
  - 新实现 **Batch5 层 BaseVerifier adapter**（`GovernedRealVerifier`）：respond(payload, hint)
    用 langchain OpenAI-compatible ChatModel（复用 OPENAI_BASE_URL/OPENAI_API_KEY；VERIFY_REAL_LLM
    =1 才构造）`await model.ainvoke(msgs, config={"callbacks":[handler]})` 返回文本；
    F3 仍做全部 deterministic parse/normalize（未触碰）；spec 相同 → fingerprint/idempotency
    与 F3 RealLLMVerifier 一致；
  - FakeVerifier 继续用于确定性测试；
  - **Compatibility note（提交用户确认，见 §18）**：F3 自带的 RealLLMVerifier 走原生 openai
    SDK（无 callback seam）；Batch5 采用 F3 公开 verifier 注入 seam 的 governed adapter 满足
    D-C，**不修改任何 frozen 文件**。若用户裁定 D-C 必须改造 F3 的 RealLLMVerifier 本身 →
    属 frozen contract 变更 → 立即 BLOCKED/L3（本 Plan 不预设该路径）。
- 对抗测试：real adapter LLM 调用必须使 counters llm_calls+1（用 fake chat model 验证接线）。

## 9. Context Engineering

- 输入 bounded：plan（plan_id/gap_refs/objective/target_sq/queries/verification targets，含
  各自上限常量）+ 目标 sq 的 question snippet（≤400）→ 仅传 search query 给工具；无 parent
  message history / 无全量 ResearchRun / 无 tool 原始 payload 拷贝 / 无 evidence 正文堆叠。
- 新 evidence 的 F3 上下文由 F3 内部构造（claim+evidence content ≤ F3 自身 bound），Batch5 不
  复制。

## 10. Failure Semantics

- ordinary（工具 provider error / malformed / F3 verifier 普通失败 / no candidate）→ 记入
  result（executed_queries[].failed、verification[] 行 failed、no_op），不中断、不吞后继续；
  属 same-execution outcome（Batch6 后续 R2-1 语义）。
- F8 control：GovernanceLimitExceeded / CancelledError / F8 timeout → **不捕获**，向 Controller
  传播（budget_exceeded / cancelled / timed_out）；无第二 task。
- 禁止：把 F8 control 包装成 ordinary。

## 11. Persistence Boundary

- 零新表/migration；全部写入走 F1–F6 现有表与 registry/verify public API（工具 ingest +
  bind_claim_evidence + verification 行）。
- 若实现发现必须新增结构（research_rounds / targeted ledger / 新表）→ 立即报告
  compatibility gap 并 STOP（对齐 §9/Readiness）。

## 12. Batch 5 Output Contract（bounded deterministic；Batch6 消费）

```jsonc
{
  "run_id": str,
  "plan_id": str,
  "executed_queries": [ { "query": str, "query_identity": str, "dedup_identity": str,
      "kind": "research_query", "status": "executed|skipped_duplicate|failed",
      "tool_ok": bool, "sources_added": int, "evidence_added": int, "error": str|null } ],
  "verification": [ { "claim_id": str, "new_bindings": int, "truncated": bool,
      "candidates": int, "items": int, "aggregate": dict|null,
      "status": "verified|no_op|failed" } ],
  "added_evidence_ids": [str],          // sorted (created_at, evidence_id)
  "ledger_updates": [ { "dedup_identity": str, "kind": str, "outcome": "succeeded|failed",
      "sources_produced": bool, "attempt": int } ],
  "failures": [ { "kind": str, "detail": str } ],
  "size": {"max_queries": int, "max_candidates_per_claim": 8, "truncated": bool}
}
```

- 排序键全部显式；无随机/无 wall-clock；verdict 内容只引用 F3 行（无复制正文）。

## 13. File Change Plan

- 新增：`app/f9/targeted.py`（常量/快照 diff/bind/execute/verify/result 组装 + GovernedRealVerifier
  adapter）；`tests/test_f9_targeted.py`（含 F8 seam）；本 Plan。
- 零修改：`projection.py/gaps.py/judge.py/plan.py`、`app/tools/tavily_tool.py`、
  `app/research/*`、F8、`tests` 既有文件、Spec/Plan、migration、依赖。
- 不新增 PG 测试文件（Batch5 直接复用 F1/F3 store 语义；增量验证的 PG 一致性以 F1–F4 既有
  PG 门控 + sqlite 语义测试为准；如需 run 级 PG 快照 diff 一致性验证，可在实施期决定是否加
  最小 PG 测试——默认 sqlite + 既有 PG 套件回归）。

## 14. Test Plan（`tests/test_f9_targeted.py`；fake search tool 注入避免真实网络）

- Targeted：valid query（工具写入 artifact 且同 run_id）；多 query 顺序/去重/retry/re-query/
  re_verify 语义（executor 只执行 accepted/retry）；snapshot diff 只收本轮新增 evidence；
  无新 evidence（全重复/无结果）→ added=0、verification no-op；确定性 & bounded（截断标记）。
- Incremental verification：0/1/>8 candidates（cap 截断）；ordering (ts,id)；全 5 verdict 类型
  透传；fingerprint 幂等（重复执行不新增行）；F3 普通失败行保留；Fake verifier 全确定性。
- F8 seam（Controller.execute 单执行体，复用 _make_controller 模式）：normal completed；
  max_search_calls / max_tool_calls → budget_exceeded（GovernanceLimitExceeded 上抛，不吞）；
  cancel → cancelled；deadline → timed_out；tool call 计数 llm?（search 双槽）经 handler。
- **D-C 对抗测试**：GovernedRealVerifier（fake chat model 注入）在 governed ctx 内 1 次 LLM
  → llm_calls +1 / agent_steps +0；malformed verifier 输出 → F3 failed 行；VERIFY_REAL_LLM=1
  + 无 handler → 测试发现裸调（llm 未 +1）→ fail（Plan §J 纪律同款）。
- Regression：F1–F8 governance sqlite + Batch1–4 suite + PG 双 DSN（research+governance+F9）。

## 15. Regression Plan

- sqlite 全量（排除 mysql 污染文件，既有已知项）应 ≥ Batch4 基线（679）＋ Batch5 新增；
- PG 双 DSN：research F1–F7 + governance + F9 projection/gaps（74）保持全绿；
- ruff/format/compileall（app/f9）；git diff/status 审计。

## 16. L2/L3 Escalation Conditions

以下任一触发 → **STOP → Reclassify L3**（PROCESS §11.3），不得以 L2 继续：修改 F8 或
F1–F7 frozen contract/文件；新 migration/schema；修改 identity（query/dedup/plan 或
task/run/thread）；新 Agent/Runtime/Controller/task lifecycle；修改 budget/watchdog/cancel；
concurrency/recovery/replay；安全边界。Plan/Readiness 判定：当前无触发（维持 L2）。

## 17. Acceptance Criteria

- AC1：同一 Controller.execute 内完成 targeted+verify；无第二 run/task/controller（断言 handles/
  run_id）。
- AC2：只调用 §2 inventory 列出的 public API；不改 frozen 文件（diff 审计）。
- AC3：D-A：使用 internet_search（注入 fake 时同 seam）；工具失败 → outcome failed 非吞。
- AC4：D-B：per claim ≤8 candidates，超出截断并记 truncated（断言）。
- AC5：D-C：real adapter LLM 经 handler 计费（对抗测试 +1 llm / +0 step；无裸调）。
- AC6：F8 control 信号不吞（budget/cancel/timeout 终态断言）。
- AC7：输出 contract 完整/确定性/bounded（重复运行相等、JSON 可序列化）。
- AC8：F1–F8 与 Batch1–4 回归全绿；Batch1–4 文件未变。
- AC9：不触发 F7（无 finalize 调用断言/代码路径审计）。

## 18. Open Questions / Compatibility Gaps

1. **D-C′（需用户 Review 确认）**：D-C 通过 F3 公共 `verifier=` 注入 seam 的 Batch5 层
   GovernedRealVerifier 满足；若裁定必须改造 F3 自带 RealLLMVerifier → BLOCKED/L3（见 §8）。
2. N（Readiness §14 延续）：registry 无“新建子 SubQuestion”public API（今日 root-only；
   Batch5 不用；未来树化 additive）；ARCHITECTURE.md §3 红线与 F9 多次 astream 的并存处理归
   Batch6 readiness（Batch5 seam 已避开）。均非本批 blocker。

## 19. Implementation Sequence（获批准后）

1. `targeted.py` 常量 + snapshot/bind 纯函数 → 单测（0/1/>8、ordering、幂等）。
2. execute_query（fake tool 注入）→ targeted 单测（valid/multi/duplicate-skip/retry/failure）。
3. verify 编排 + GovernedRealVerifier adapter → F3/Fake 单测 + D-C 对抗测试。
4. F8 seam 集成（budget/cancel/timeout/normal）+ regression + ruff/compile。
5. Batch5 Implementation Report（AC→Test→Evidence）→ User Review/Freeze（不自动进入 Batch6）。

## Final Status

```text
IMPLEMENTATION PLAN READY
```

等待用户 Review（含 D-C′ 确认）；未进入 Implementation；未创建 Batch5 实现代码/报告。
