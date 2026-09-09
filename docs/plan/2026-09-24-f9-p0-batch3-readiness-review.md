# F9-P0 Batch 3 — Semantic Gap Judge：Implementation Readiness Review / Architecture Review

> 状态：**READINESS REVIEW（read-only，无代码改动）→ 结论 CONDITIONAL（见 §14）**。
> 范围：只评估 Batch 3 是否 Implementation Ready；不修改任何生产代码 / Batch 1・2 / F8 /
> F9 Spec・Plan / 不新增 migration / 不实现 Judge。
> 依据：F9-P0 Spec Rev2（2026-09-19 §2.2/§5/§6/§7/§9/§12/§17）、Plan Rev2（2026-09-20
> §D1/D2/§A/§D/§J/§I Batch3）、F8 frozen 实现（`app/runtime/governance/{callbacks,context,
> controller,counters}.py`）、Batch 1・2 冻结报告。

---

## 1. Context / Scope

- 加载：AGENTS.md / PROJECT_CONTEXT.md / PROCESS.md / PROBLEM.md（First read）；F9 Spec Rev2、
  Plan Rev2、Batch 1・2 reports、F8 governance 实现与 Testing 相关条款（Selective）。未扫描历史 docs。
- 现状确认：Batch 1（Projection）**PASS/FROZEN**（09-22）；Batch 2（Gap Detection）**PASS/FROZEN**
  （09-24）；Batch 3 未开始。
- 本 Review 允许：read-only inspection、contract/compat 分析、测试矩阵设计、本报告。禁止一切代码/文档
  （除本报告）改动。

## 2. Current Frozen Baseline

- F1–F8 语义冻结；PostgreSQL 唯一生产基线与最终 Gate；SQLite 仅快速单测（SQLite PASS ≠ PG PASS）。
- F8：`Controller.execute(task_id, coroutine, *, policy=None)` 是唯一 lifecycle/budget/deadline/
  cancel/terminal 权威；governed 路径绑定单 BudgetCounter + GovernanceExecution(ContextVar) +
  watchdog；terminal funnel/CAS/pending 冻结。
- Batch 1：`project(run_id, round=current_round, budget=snapshot)` → 10 键 Projection
  （claims/sub_questions/budget/gap_signals…）。Batch 2：`detect_gaps(projection)` → 9 类
  deterministic signals（RU1…BU1，rule id + subject_type/subject_id）。
- F9 冻结工程决策（Plan Rev2 §0/R1–R5）：单 execute、Judge 显式 callback 计费、fallback 同协程、
  query_identity ≠ dedup_identity、Batch7 只验 F7 seam、round=编排计数不注入冻结 BudgetCounter。

## 3. Batch 3 Contract

- 职责（Spec §5 + §2.2）：对 Batch 2 已发现的 deterministic gap 做 **LLM semantic judgment**——
  足够性判断 + 缺口是否值得追查 + 后续研究方向所需语义信息；judge 属非业务回合。
- 允许：LLM semantic judgment；对 deterministic gap 语义解释；基于已有 Research State（projection
  facts）判断是否值得进一步研究；F8 GovernanceCallback 计费；遵守 F8 timeout/cancel/deadline/budget。
- 禁止（Spec/Plan 检查后确认）：新 Agent/Runtime/Controller/Task/BudgetCounter；自维护 budget/
  timeout/cancel；Targeted Research / Query generation / Follow-up Plan / Query dedup / Verification
  orchestration / F7 finalization / 自动下一轮。

## 4. F8 Governance Seam（重点，代码级核实）

管道路径正确性（无需第二 Controller）：

```text
Controller.execute(task_id, f9_orchestrator(...), policy)
  → _execute_governed：单 BudgetCounter + GovernanceExecution(ctx, task_id, run_id, counter,
    recursion_limit) + watchdog（单调 deadline）+ run_id 前绑定回填
  → f9_orchestrator（同一 asyncio future）内含 baseline / Projection / Gap / Judge
```

逐项核实（实读 `controller.py:445-486/_execute_governed:546+`、`context.py`、`callbacks.py`）：

| 要求 | 结论 | 证据 |
|---|---|---|
| Judge 与主研究共享 task_id / run_id / GovernanceExecution / BudgetCounter | ✅ | execute 对任意 coroutine 绑定唯一 ctx；f9_orchestrator 在 ctx 内执行（context.py enter 语义） |
| 共享 deadline / cancellation | ✅ | watchdog 与 future 都在同一 execution；Judge 的 await 在 future 内 → F8 cancel/timeout 原生作用于 Judge |
| Judge LLM call 计入 max_llm_calls | ✅ | `ctx.make_handler()` → GovernanceCallbackHandler(counter…)；`model.ainvoke(config={"callbacks":[handler]})` 触发 on_llm_start → acquire 路径（见 §7） |
| Judge 不增加 agent_step | ✅ | 冻结分类（callbacks.py classify_llm_start）：judge 直调无 langgraph_node → `unclassified` → **只 acquire_llm_call**、不计 agent_step（fail-safe，Spec §3 冻结） |
| Judge timeout/cancel 走 F8 control plane | ✅ | 无独立 Judge task/domain；deadline→watchdog terminalize(timed_out)；cancel→future 取消→funnel(cancelled) |
| Judge failure 不创建新 task/Controller | ✅ | D1/R1：orchestrator 内 try→baseline 分支同一 coroutine；无第二条 submit 路径 |
| Judge failure 不把 F8 task 误标 failed | ✅ | Spec §12(8)：仅当 F8/runtime failure semantics 触发才 failed；orchestrator 捕获 judge 异常转 baseline，不上抛非控制类异常 |

**Seam 兼容性结论：现有 F8 API 足够，无需修改冻结 F8。** `Controller.execute(task_id, coroutine, policy)`
接受任意 coroutine（含 orchestrator）；`ctx.make_handler()` 返回单实例 handler 可直接挂 ainvoke。

## 5. Judge Failure / Fallback Semantics（R2-1）

- Spec §12 R2-1（冻结）：Judge failure = 停止当前 adaptive decision，**fallback 到同一 F8 governed
  execution 内的 baseline research/synthesis**；不基于 partial judge 结果生成 plan；不创建新 task/
  Controller/Budget；不重建 watchdog；baseline 正常完成 → F7 finalize 恰一次；仅当 no-progress /
  deterministic stopping 触发才 STOP/NO_PROGRESS；不得因 Judge failure 直接把 F8 task 标 failed。
- 代码架构支持性：✅ 无需改 F8 —— fallback 是 orchestrator coroutine 内部的分支（Plan §A/R1），
  全部 F8 资源已由外层 execute 提供；Judge 自身只 await 一次 LLM 调用。
- 特殊路径（实现指南，非新协议）：`GovernanceLimitExceeded`（judge 触发 llm 超限）与
  `CancelledError`（F8 cancel）属于 F8 control 信号 → orchestrator **不得吞并**，应让其在 F8 层收敛
  （budget_exceeded/cancelled）；普通 judge 异常才走 baseline fallback。

## 6. Input / Output Contract

- 输入（bounded）：Batch 2 GapSignals + Batch 1 Projection（二者已含 run_id/round/budget facts）。
  Judge 可读 projection 中 claims 的 statement/verdict/independent/fresh/conflicts 与
  required/evidence 计数（全部 bounded，无 evidence 正文、无 source 全文、无 message history）。
- 输出 schema：Spec **未给出结构化 Judge 输出字段**（见 §12 Ambiguity A1）。Spec §5 只写
  "足够性判断 + 候选 follow-up（query/objective/priority/expected evidence/reason/verification target）"，
  与 §6 Follow-up Plan Contract（含 queries[]）及用户本轮 §10 边界（Judge 只出 semantic judgment：
  important/reason/priority/semantic_need，**不含** query/tool/source 选择）存在表述冲突 →
  需用户裁决（A1），裁决后方可锁 Judge schema。
- Deterministic validation 位置：Batch 3 模块内（LLM 输出 → schema 校验 → subject 引用校验 →
  归一化；任何 LLM 输出不得直接充当 Follow-up Plan）。

## 7. LLM / Callback / Budget Compatibility

- `handler = ctx.make_handler()`（context.py:46-54）与 `await model.ainvoke(messages, config=
  {"callbacks": [handler]})`：langchain-core ChatModel.ainvoke 支持 config callbacks → 兼容。
- 计费实际路径（callbacks.py:125-148）：judge 调用无 langgraph 元数据 → `unclassified` →
  `acquire_llm_call()`（llm +1，agent_step 0，符合 Plan §D "Judge: agent_step 0 / llm_call +1"）。
  **副作用**：每次 judge 计费同时写一条 degraded diagnostic（`llm_classification_unresolved`，
  callbacks.py:142-148）——F8 冻结分类 fail-safe 的固有行为；不修改 F8。属 cosmetic 噪音（§12 N1）。
- 超限：llm_calls 达限时 on_llm_start raise `GovernanceLimitExceeded(llm_calls)`（raise_error=True）
  → ainvoke 传播 → orchestrator 不应吞并 → controller 收敛 budget_exceeded（Spec：预算耗尽属
  F8 terminal，Judge 不自行兜底）。
- provider retry / timeout / malformed：F8 冻结层无 judge 专用 retry；Spec §12 未定义 judge 级 retry
  → Ambiguity A3（单次尝试→fallback，或 verifier 式确定性重试 1 次，待裁决）。请求级 timeout 未 spec；
  judge 超时最终由 F8 watchdog deadline 覆盖（timed_out）。
- Budget exhausted before/during Judge：before → judge 不调用（orchestrator 依 gap/budget 判定，
  见 A2）；during → GovernanceLimitExceeded → F8 budget_exceeded。

## 8. Context Boundary

- Judge 输入 = Projection + GapSignals + 显式 task 提示；**永不读** parent message history / Agent
  context / tool payload / 无界 evidence；禁止隐式全局读取（research DB 只在 projection 构建期读取）。
- 结构化 Context Envelope 优先（Batch1 已产出决策导向状态）；不存在"因缺 history 而复制 context"
  的动机——projection 自带所需事实。
- Evidence 内容只允许 projection 的 evidence_refs（id/created_at/domain，无正文）。

## 9. Batch 4 Boundary

- Batch 3 输出 semantic judgment（每 gap：值得与否/why/priority/semantic_need/（待 A1 裁决的补充
  字段）），到此为止；不产 executable follow-up plan。
- 禁止流入 Batch 3 输出：search query / source·tool selection / targeted research task / retry
  query / dedup identity（属 Batch 4 Follow-up Plan + validation + dedup，Batch 5 targeted）。
- Spec §5 "候选 follow-up（query/objective/…）" 与 §6 Plan Contract 的归属（Judge 或 Plan 提议）
  冲突 → A1 裁决后 Batch 4 边界即闭。

## 10. Persistence / Round Semantics

- Round = F9 orchestration state（Spec §7），不是 task/runtime/controller；多 LLM invocation 共享
  task_id/run_id/GovernanceExecution/BudgetCounter/deadline/cancel（§4 表）。
- Persistence：Spec/Plan **未强制** Judge 结果落库；Spec §17 Open Q5 将 projection/round-state
  持久化留作 additive（research_rounds）实施 Gate 裁决、不承诺。→ **Batch 3 不新增表/migration**；
  Judge 结果本轮内内存传递；如需跨轮/跨崩溃持久化，属后续 additive 决策（A5）。
- 若未来持久化：idempotency 建议 (run_id, round)，不修改 F1–F8 schema（仅 additive research-plane）。

## 11. Test Matrix（设计，不写测试代码）

| Case | 预期 | 依赖 |
|---|---|---|
| N1 Normal：存在 gap → Judge 成功 → 有效 semantic judgment | judge 1 次 llm_call；0 agent_step；输出经 schema+引用校验通过 | scripted/fake chat model（env 无真凭据，同 F3 FakeVerifier 纪律） |
| N2 No gap：无 deterministic signal | 按 A2 裁决：跳过 Judge（deterministic 足够）或仍调 1 次做 sufficiency/stop 确认 | A2 |
| N3 Malformed：LLM 非 JSON/缺字段 | deterministic reject → 按 A3 转 judge failure → baseline fallback（同一 execute） | A3 |
| N4 Provider failure | 单次/重试策略按 A3；fallback baseline；不标 F8 failed | A3 |
| N5 Judge timeout | F8 watchdog deadline → timed_out（不伪造 completed） | F8 冻结 |
| N6 Cancel during Judge | F8 cancel → cancelled；judge await 在 future 内被取消 | F8 冻结 |
| N7 max_llm_calls exhausted（before/during Judge） | before：judge 不调（A2）；during：GovernanceLimitExceeded → budget_exceeded，orchestrator 不吞 | F8 冻结 |
| N8 Invalid references：Judge 引用不存在 claim/sub-question/gap | deterministic reject（对 projection/gaps 集合校验） | — |
| N9 Isolation：run A 不能引用 run B 实体 | 校验基于单 projection 的 subject 集合；cross-run 拒绝 | Batch1 run 作用域 |
| N10 计数对抗：judge 无 callback → llm 未 +1 → fail（Plan §J 要求） | 显式测试发现裸调 | Plan §J |
| N11 多次 judge：逐次 +1 llm、不重置 counter | counter 单调 | F8 |
| N12 Regression：Batch 1/2 FROZEN 行为不变 | 现有 suite 全绿（sqlite + PG gate） | — |

## 12. Risks / Compatibility Gaps / Ambiguities

无 **blocking** 的 F8/代码兼容缺口（§4/§7 核实全部兼容）。需用户裁决的 **ambiguity**（不自行补协议）：

- **A1（阻塞实施决策）Judge 输出 schema 与 §5/§6 归属**：Spec §5 语义判断含 "候选 follow-up
  （query/objective/priority/expected evidence/…）" 与 §6 Plan Contract（queries[]）及用户 §10
  边界（Judge 只出 important/reason/priority/semantic_need）不一致；Judge vs Plan 是否两次 LLM 调用
  未在 Spec 明确（Plan §A 拆两节点）。→ 影响：Judge 输出字段契约与 Batch4 边界。
- **A2 无 deterministic gap 时 Judge 是否调用**（或直接 deterministic stop/semantic 确认）。
- **A3 Judge 输出 malformed / provider failure 的 retry 策略**（单次 vs 确定性 1 retry，参照 F3
  verifier 纪律）与是否一律归 judge failure→baseline。
- **A4 judge 模型/配置**：model 选择（与 main 同栈或注入 judge_model）、temperature 等无 Spec 要求；
  建议 semantic 允许概率性、输出结构经 validation（格式化可 0/low temperature，非 Spec 约束）。
- **A5 Judge 结果持久化**：本轮不落库；additive research_rounds 是否后续做（Spec §17 Q5）需 Gate
  裁决，不影响本批实现。
- **N1（非阻塞，代码事实）** judge 经 handler 计费每次产生 `llm_classification_unresolved` degraded
  diagnostic（F8 冻结分类 fail-safe）。可接受为日志噪音；若希望静默需 F8 分类扩展——**禁止改 F8**，
  记录并保持。

## 13. Scope Audit

- 本 Review 未修改：`app/f9/*`（projection/gaps 冻结）、`app/runtime/*`、`app/research/*`、Batch 1・2、
  F8、API、frontend、依赖、migration、Judge/Orchestrator/Plan 实现。唯一新增：本报告文档。
- 未创建 Agent/Runtime/schema；未跑测试（无代码变更，验证不适用；FROZEN 基线未动）。

## 14. Final Decision

```text
CONDITIONAL
```

Batch 3 具备进入实现的全部**结构性条件**：F8 Governance Seam 与 D2 调用模式**代码级兼容**
（单 execute、共享 ctx/counter/deadline/cancel、judge llm+1/agent_step 0、失败→同 run baseline、
无第二 Runtime/Task/Controller）；输入 bounded；验证位置明确；无需改 F8；无需 migration。

需要你裁决后再开始实现（每个均附影响与最小闭合）：

| # | 裁决项 | 影响 | 最小闭合 |
|---|---|---|---|
| A1 | Judge 结构化输出字段及 §5"候选 follow-up"归属（Judge vs Plan 两阶段） | Judge schema、Batch4 边界 | 确认 Judge 输出 = semantic judgment（important/reason/priority/semantic_need/stop 建议等，无 query/objective/queries）；Follow-up plan（含 queries）归 Batch4 单列 LLM 提议 |
| A2 | 无 deterministic gap 时 Judge 是否仍调用 | 轮次流、budget | 定为：无 gap → 仍可 1 次 semantic sufficiency/stop 确认（预算允许时）或 deterministic STOP（二选一） |
| A3 | malformed/provider-failure 的 retry 与 fallback 归口 | 失败语义细节 | 定为 judge 输出校验失败=judge failure → 同 run baseline；不引入独立 retry（或沿用 F3 单重试纪律，择一） |
| A4 | judge_model 注入方式 / 无温度等配置要求 | 实现接口 | 定为同 OpenAI-compatible 栈、模型经参数注入、无温度硬约束（结构化经 validation） |
| A5 | Judge 结果是否落库（本轮不落库；research_rounds additive 后续裁决） | 持久化边界 | 确认本批 in-memory，不建表 |

裁决完成后即可进入 Batch 3 Implementation（本 Review 未发现需修改 F1–F8 或 Batch 1・2 契约之处）。

**停止。** 未实现 Judge，未进入 Batch 4；等待 Review。
