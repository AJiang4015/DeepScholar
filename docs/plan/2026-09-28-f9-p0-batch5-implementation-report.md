# F9-P0 Batch 5 — Targeted Research + Incremental Verification Implementation Report

> 状态：**BATCH 5 PASS / FROZEN**（用户 2026-09-29 正式 Freeze；本批只交付 Targeted Research +
> 增量 F3 Verification；不编排 Batch6/F4–F6/F7；不宣布 F9-P0 COMPLETE）。
> 依据：F9-P0 Spec Rev2 §10/§12、Plan Rev2 §I Batch5、Batch5 Readiness（CONDITIONAL）＋
> Implementation Plan（APPROVED，2 non-blocking closures）＋用户裁决 D-A～D-D 与 D-C′（2026-09-28）。
> 分级：L2（无 L3 触发项）。

## 1. Scope / Deliverables

- 新增：`app/f9/targeted.py`、`tests/test_f9_targeted.py`（15）、`tests/test_f9_targeted_postgres.py`
  （真实 PG gate，3）、本报告。
- 零修改：`projection/gaps/judge/plan.py`、`tavily_tool.py`、`app/research/*`（F1–F6）、F8、
  既有 tests、Spec/Plan、migration、依赖、Batch1–4。

## 2. Design（数据流与 seam）

`Controller.execute → execute_targeted_plan(plan, run_id, search_tool, verifier, verify_spec, store)`
1) research_ctx.set(run_id, target_sq)；2) provenance/store 快照；3) 对 plan 中 status∈{accepted,
retry} 且 kind==research_query 的 query 调 `internet_search.ainvoke(config callbacks)`（D-A；
工具自带 F1 ingest，同 run）；4) 快照 diff → 本轮新增 evidence；5) target claims 经
`bind_new_evidence`（幂等、cap 8）→ 6) `semantic_verify_claim(allowed=newly[:8], budget cap 8,
verifier=…)`（F3）；7) bounded result（含 ledger_updates 供 Batch6 回填 Batch4 ledger）。

复用面（实读核实）：internet_search 自身 ingest（tavily_tool）、research_ctx、registry
(bind/append/upsert)、F3 `semantic_verify_claim`（allowed_evidence_ids/verifier/budget 注入
seam）、provenance/store 只读快照。无第二 ResearchRun/run_deep_agent/F7。

## 3. D-C′ — GovernedRealVerifier（Plan §8 落地）

- F3 public seam：`semantic_verify_claim(verifier=…)`；`app/f9/targeted.py` 内新增
  `GovernedRealVerifier(BaseVerifier 协议)`：respond() 经 LangChain ChatModel **invoke**
  `config={"callbacks":[handler]}`（handler = ctx.make_handler()）。
- 计费语义实测：+1 llm_calls / +0 agent_steps；无裸调（对抗测试断言 model.calls==1 且 llm==1）。
- F3 parse/normalize/fingerprint/idempotency 不变（respond 只返回原始文本；spec 相同 →
  fingerprint 与 F3 RealLLMVerifier 一致）；**未修改 F3/F8 frozen 文件**。
- 构造守卫：VERIFY_REAL_LLM=1 或测试注入 model；否则 executor 走 F3 默认 FakeVerifier。

## 4. Snapshot Diff / Binding 语义（closure §二）

- new Evidence ≠ new Binding：快照 diff 给出本轮新增 evidence；**candidate 只来自 executor 本轮
  实际新增 binding**（`bind_new_evidence`，已绑定/已加幂等跳过，cap=D-B 8）。
- 场景测试（helper 层精确覆盖）：E1 已绑目标 claim A、E2 未绑、E3 已绑其它 claim B →
  A 新增 binding=[E2,E3]（E1 重复幂等排除，非"全部 new Evidence 即候选"）；B 新增=[E1,E2]
  （E3 已绑 B 排除）；重复调用 → []（无重复候选/行）。
- 说明：完整 executor 层面，快照 diff 已把"上一轮已绑 evidence"排除出本轮新集；helper 层对抗
  覆盖"本轮新集中混入已绑定 id（未来多 producer/race）"的幂等去重。

## 5. Output Contract（= Plan §12；字段实测）

`{run_id, plan_id, executed_queries[](query/dedup_identity/kind/status/sources_added/
evidence_added/error), verification[](claim_id/status(verified|no_op|failed)/new_bindings/
candidates/truncated/items/aggregate/verdict_rows[](evidence_id/status/verdict/error/reused)),
added_evidence_ids[](按 (created_at,evidence_id) 升序), ledger_updates[], failures[], size{}}`
- deterministic：无随机/无 wall-clock；executed 顺序=plan 顺序；verification 按 claim_id 升序。

## 6. Failure Semantics

- ordinary（tool 异常 / malformed / bind 守卫失败 / F3 provider→failed 行 / no candidate）→
  记入 outcome（executed failed、verification failed 行、failures、no_op），继续后续合法 query；
- F8 control（GovernanceLimitExceeded / CancelledError / timeout）→ 不捕获，向 Controller
  传播（budget_exceeded/cancelled/timed_out 实测）。

## 7. Tests（AC → Test → Evidence）

| AC | Implementation | Test | Evidence |
|---|---|---|---|
| D-A 单 execute 内 targeted+verify、无第二 run | execute_targeted_plan 不建 run/set status | normal seam + sqlite/PG | research_runs count==1；completed |
| snapshot diff 只收本轮新增 | 快照 diff + _sorted_new | valid/multi/empty | evidence_added==1..；无新→no_op |
| duplicate/re_verify 不执行 | status/kind 过滤 | duplicate/reverify-skip | tool calls==1（仅 research accepted） |
| 幂等（binding+F3 fingerprint） | bind_new_evidence + F3 | idempotent/binding 对抗 | 二轮行数不变；重复 helper 返回 [] |
| D-B ≤8 candidates、确定性截断 | cap 8 | cap_8 | candidates==8、verification 恰 8 行 |
| F3 5 verdict 原样 | 透传 rows | all_verdict_types | 每 verdict succeeded 行 |
| ordinary verifier failure 保留 | F3 failed 行透传 | verifier_failure | row status failed/verdict None/error 保留 |
| D-C′ +1llm/+0step、无裸调 | GovernedRealVerifier | D-C adversarial | llm==1、steps==0、model.calls==1；未设 flag+未注入 model → RuntimeError |
| F8 budget/cancel/timeout 不吞 | 不捕获 GLE/Cancel | seam search-budget | GLE→budget_exceeded、tool body 0 |
| no-op 语义 | claims 无新增 binding → no_op | zero-candidate | no_op 无伪 verdict |
| 真实 PG 闭环 | — | PG gate 3 | run_id 一致/幂等/cap/排序/无跨 run 污染（77 PG 全绿） |

## 8. Regression

- sqlite 全量：**694 passed / 88 skipped / 0 failed**（=679+15；Batch1–4 与 F1–F8 sqlite 全绿）。
- PG 双 DSN：**77 passed / 0 failed**（74 + Batch5 PG 3；governance 表先 reset）。
- ruff / format / compileall（app/f9）全绿；git diff/status 仅新增 3 文件（含测试）＋本报告。

## 9. Deviations from Plan（如实记录）

1. D-C′ 传输层：Plan 写 `model.ainvoke`；F3 verifier 协议为**同步 respond**（_verify_pair 同步）
   → adapter 用同步 `model.invoke(config callbacks)`（on_llm_start 计数路径相同，计费语义一致）。
2. 默认 search_tool 惰性导入（模块导入不依赖 TAVILY_API_KEY；默认仍 D-A 的 internet_search）。
3. closure 场景在 helper 层精确测试（§4 说明；executor 快照 diff 已保证"旧绑定不入新集"）。
4. PG ordering 断言以 DB 实际 (created_at, evidence_id) 键为准（非纯字典序）。

## 10. Limitations

1. 真实 provider E2E（Tavily search / real verifier）受凭据环境限制：TAVILY_API_KEY 为机器级
   变量但**未进入本会话进程 env**（探测确认）→ 测试全部用 fake ingest tool / FakeVerifier /
   注入 fake chat model；D-A/D-C′ 的协议与计费 seam 已测，真网络 E2E 需凭据环境。
2. 每 target claim cap 8 为 D-B safety bound（Batch8 校准前非冻结协议值）。
3. re_verify 属 Batch5 scope 外（独立 namespace 已有 evidence 复验留后续 feature；executor 对
   kind=re_verify 一律 skip 并记录）。
4. ledger 回填（in-memory）由未来 Batch6 完成；本批只输出 ledger_updates。

## 11. Scope Audit

- 新增 3 文件 + 本报告；未触碰：Batch1–4 五模块、tavily_tool、app/research/*、F8、tests 既有
  文件、Spec/Plan、migration/依赖、UI、Harness、Batch6+ 代码；无 F7 调用（代码路径审计）。

## 12. Final Decision

```text
BATCH 5 PASS / FROZEN（2026-09-29 用户正式 Freeze）
```

实现与验证证据（AC→Test→Evidence、D-C′/snapshot-binding 语义、PG Gate、Deviations、
Limitations、Scope Audit）原样保留，无重写。本批未宣布 F9-P0 IMPLEMENTATION COMPLETE。
下一阶段 F9-P0 Batch 6 — Round Orchestrator 为 PENDING：等待用户显式指令，不得自动开始。
