# F9-P0 Spec — Evidence-Driven Adaptive Research Loop（Revision 2）

> 状态：**SPEC（设计阶段；未实现）。Revision 2 = 闭环 Rev1 Architecture Review 的 5 项
> Non-blocking Issues（R2-1…R2-6），达到可直接进入 Implementation 的状态。**
> 冻结前提：F1–F8 全部语义冻结、不重审；F9 不新增第二 Runtime 控制面。
> Rev1 变更点以 [R1-*] 标注；Rev2 变更点以 [R2-*] 标注。

## 0. Revision 记录
- **Rev1（R1-1..R1-7）**：Lifecycle Boundary；Projection Semantic Contract；fail-open=open-to-
  baseline；Round State；Query Dedup/Re-query；Stopping（budget=ceiling）；边界保持。
- **Rev2（本版）**：
  - R2-1 Judge failure 语义冻结（adaptive unavailable → baseline research/synthesis，同一 F8
    execution；删“fallback baseline/stop”模糊表达）
  - R2-2 Projection：evidence_summary 去 authority；required_uncovered=缺失集；best_verdict 排序键
  - R2-3 §2.2 Implementation Constraints（单 execute、共享 ctx、judge 计费、F7 单点）
  - R2-4 Query identity 实现语义（objective/source_universe/time_window/duplicate key/retry/re_verify）
  - R2-5 Round State legal transitions / failure routing / 非法 transition
  - R2-6 全文一致性检查（§9/§13/§14 文字最小修正）

## 1. 目标（一句话）
把 retrieval loop 变为 **evidence-grounded adaptive research loop**：Research State →
deterministic Projection → Deterministic Gap Signals → Semantic Gap Judgment → Validated Follow-up
Plan → Targeted Research → Incremental Re-verification → Semantic + Deterministic Stopping →
F7 finalization，全程在**单个**受 F8 治理的 governed execution 内。

## 2. 主架构
```text
一次 governed run = 一个 F8 Task（task_id/run_id/budget/deadline/cancel/terminal 权威）
  Round 0 初始 broad research（复用 deep agent/subagents/tools）
  F9 Loop（每轮）：Projection → Gap Signals → Judge → Plan→Validate → Research → Verify → Evaluate
  Final synthesis → F7 finalization（normal completion 恰一次）
```

### 2.1 [R1-1] F8/F9 Lifecycle Boundary（冻结）
```text
F8 Controller（execute(policy)） —— 唯一 lifecycle/budget/deadline/cancel/terminal 权威
  └─ coroutine（F9 Research Orchestrator）
       ├─ Round 0 research（agent graph invocation #1）
       ├─ Loop：Gap Detection → Follow-up Plan → Targeted Research（graph invocation #N，同 task）
       ├─ Final Synthesis（graph invocation #M）
       └─ normal return → F7 finalization
```
- 一个完整 F9-P0 Loop = 一个 F8 Controller-governed execution（单 TaskRecord/task_id/run_id=
  F1 同源/单 BudgetCounter/单 watchdog/deadline）。
- `research_round` = F9 编排状态（非新 Runtime Task）；不得每轮重建 TaskRecord / Runtime Budget /
  独立 watchdog。
- F8 的 deadline、cancellation、max_agent_steps、max_llm_calls、max_tool_calls、max_search_calls、
  terminal authority **覆盖整个 F9 Loop**。
- 多次 graph invocation 若存在，仍属同一 Controller execution / task_id / run_id / 预算上下文
  （可经 thread checkpointer 延续对话）；**禁止以重 invoke graph 绕过 F8**。

### 2.2 [R2-3] Implementation Constraints（正式约束）
- **Controller**：整个 F9-P0 Loop 只允许一次 `Controller.execute(policy)`；
  **禁止** `round → controller.execute(...)`；禁止嵌套 Controller.execute。
- **Graph invocation**：允许多次 `agent.astream(...)`，全部共享同一 task_id / run_id /
  BudgetCounter / deadline / cancellation context / terminal authority；不得绕过 F8 counters。
- **Judge**：Judge LLM call 必须挂入同一 governed execution 的 GovernanceCallback 路径并计入
  `max_llm_calls`；禁止裸 model call 绕过 GovernanceCallback/Counter。Judge 是否计
  `agent_step`：**按 F8 已冻结现有语义**，本 Spec 不重定义（judge 属非业务回合 → 不额外计
  agent_step 的默认理解须以 F8 callback 分类实现为准）。
- **F7**：仅位于 `F9 orchestrator normal completion → F7 finalization`；round 内禁止 F7；
  F8 terminal / cancel / timeout / budget failure 路径不得执行 F7。

## 3. Research State Projection（deterministic）
管道：`Research DB → deterministic Projection → Gap Signals / Semantic Judgment`；
**Judge 永不直接读完整 Research DB / parent message history**。

### 3.1 [R1-2][R2-2] Projection Semantic Contract
| 字段 | 类型 | 来源 | 计算/判断规则 | Deterministic | Unknown/Null | 数据不足时 | 内部/API |
|---|---|---|---|---|---|---|---|
| run_id | str | TaskRecord.run_id(F1) | 直读 | 是 | 不允许 | — | API |
| round | int | orchestrator 计数 | +1/轮（F9 层） | 是 | 不允许 | 0 | API |
| budget | dict | F8 counters 摘要 | 直读 count/limit + 单调剩余 | 是 | 不允许 | 上限值 | API |
| sub_questions | list | research schema | 该 run SubQuestion 行 | 是 | 空 | 空 | API |
| required_uncovered | list[id] | schema | 仅“缺失覆盖”集：根/必答子问题无 evidence 且无 source；**不表示 sufficiency**；insufficiency 由 gap_signals 表达 | 是 | 空 | 空 | API |
| claims[] | list | research schema | Claim 行（受限投影） | 是 | 空 | 空 | API |
| best_verdict | enum{SUPPORT,CONTRADICT,INSUFFICIENT,UNVERIFIABLE,null} | F3 verification 行 | = 该 claim **最新 succeeded verification** 的 verdict；排序键固定 (created_at, verification_id) 升序取末；failed verification 不成为 latest；无 succeeded → null | 是 | null | null（不猜） | API |
| independent_flag | bool/null | F5 corroboration 行 | 仅=source independence；≠ authority；无 F5 行→null | 是 | null | null | API |
| fresh | bool/null | 最近 Evidence.created_at | `now - max(source 捕获时间) <= FRESHNESS_WINDOW(默认 1 天，常量)`；无 source→null | 是 | null | null | 内部字段 |
| conflicts[] | list | F4/F6 行 | 未缓解冲突 | 是 | 空 | 空 | API |
| evidence_summary | dict | schema 聚合 | count + **按 domain 确定性计数**；**不引入 authority/credibility**；domain 分类失败 → 归 `unknown_domain` 桶并计数 | 是 | — | 0 | API |
| gap_signals | dict | §5 | 代码布尔信号 | 是 | 缺省 false | false | 内部→API（稳定后） |
| open_questions | list | judge 上一轮 + 确定性未覆盖 | §5.2 | 半 | 空 | 空 | 内部 |
| size_limits | dict | 常量 | token 上限 | 是 | — | 默认 | 内部 |

补充规则：
- domain 来源：Evidence.source 的 URL host（normalize 后 eTLD+1 或配置表）；normalization 失败/未知
  → `unknown_domain`；**不做 source authority/reliability 判断**（不偷渡 F5/F6 之外语义）。
- `required_uncovered`：有 evidence 但 insufficient **不属于** uncovered；sufficiency 仅由
  `gap_signals`（evidence count < MIN）表达。
- null 字段禁止 LLM 补值；projection 不含 evidence 原文堆叠。
- **不修改 F3 schema**：best_verdict 读取规则仅定义于 F9 Projection（F3 verification 行现有
  created_at 与 verification_id；无 verification_id 的唯一单调键时以 created_at+行序兜底，实现层定）。

## 4. Supervisor vs 子代理看到什么
- Orchestrator/Supervisor：projection + budget + gap_signals。
- Follow-up 执行：Task-specific Envelope（target sub-question、objective、候选 query、必读
  evidence id+摘要、citation 提示、去重约束）。
- 不注入：evidence 正文堆叠、父对话全量、research DB dump、冲突原文。

## 5. Gap Detection
- Deterministic signals：必答未覆盖；claim 无 evidence；evidence < MIN_EVIDENCE；
  INSUFFICIENT/UNVERIFIABLE；未缓解 critical conflict；独立源数不足；citation coverage 不足；
  query/source 重复率过高；budget 接近上限。
- Semantic judgment（LLM）：足够性判断 + 候选 follow-up（query/objective/priority/expected
  evidence/reason/verification target）。

## 6. Follow-up Plan Contract
plan_id / target_sub_question_id / objective / queries[] / priority / verification_target_claim_ids /
stop_condition / why（结构化入 Research State）。

### 6.1 [R1-5][R2-4] Query Identity / Dedup / Re-query 实现语义
- `query_identity` = normalized query 的 SHA-256。
- `objective_identity` = SHA-256(`target_sub_question_id` + normalized objective text)。
- `source_universe_identity` = deterministic fingerprint(provider/库标识 + 配置指纹)。
- `time_window_identity` = 显式声明窗口；未声明时用固定 sentinel `NONE`（**不依赖当前时间**）。
- `duplicate key` = query_identity + objective_identity + source_universe_identity +
  time_window_identity；同组合重复且上一次**成功产生结果** → duplicate（拒绝）。
- **retry**：provider failure/retry 不产生新 query_identity；用 attempt/retry metadata；
  仅当上一执行失败且**未产生新 source** 时允许 retry；不误判为新 research query。
- **re-verify** 与 research search 分离 namespace/kind：`research_query` vs `re_verify`
  互不参与对方 dedup。
- 说明：以上 identity 是 **research-plane idempotency/dedup identity**，不是新 Runtime identity。

## 7. [R1-4][R2-5] Research Round State（orchestration 态；非 F8 TaskStatus）
```text
ROUND_STARTED → PLAN_PROPOSED → PLAN_VALIDATED → RESEARCH_EXECUTED →
EVIDENCE_UPDATED → VERIFICATION_UPDATED → ROUND_EVALUATED →
  FOLLOW_UP | STOPPED | NO_PROGRESS
```
**Legal transitions**
- ROUND_STARTED → PLAN_PROPOSED
- PLAN_PROPOSED → PLAN_VALIDATED；PLAN_PROPOSED → ROUND_EVALUATED（Judge failure / 无 plan）
- PLAN_VALIDATED → RESEARCH_EXECUTED；PLAN_VALIDATED → ROUND_EVALUATED（plan execution blocked）
- RESEARCH_EXECUTED → EVIDENCE_UPDATED；RESEARCH_EXECUTED → ROUND_EVALUATED（全失败且无
  evidence update）
- EVIDENCE_UPDATED → VERIFICATION_UPDATED；EVIDENCE_UPDATED → ROUND_EVALUATED（无需 verification）
- VERIFICATION_UPDATED → ROUND_EVALUATED
- ROUND_EVALUATED → FOLLOW_UP / STOPPED / NO_PROGRESS
- FOLLOW_UP → 下一轮 ROUND_STARTED

**Failure routing**
- Search 全失败：RESEARCH_EXECUTED(failed) → ROUND_EVALUATED → 无合法 follow-up/no-progress →
  NO_PROGRESS。
- Verification failure：不伪造 verdict；VERIFICATION_UPDATED → affected claim=UNVERIFIABLE →
  ROUND_EVALUATED。
- Judge failure：按 R2-1 —— 停止 adaptive decision → baseline research/synthesis（同一 F8
  execution），不继续 adaptive round；仅当 no-progress/deterministic stopping 触发才 STOP/
  NO_PROGRESS。
- F8 cancel/timeout/budget：F8 立即取得 Runtime terminal authority；
  **F8 terminal ≠ F9 STOPPED**；F9 round state 可保留持久状态，但 Runtime truth 由 F8
  TaskStatus 决定。

**非法 transition**：fail-closed（不执行下一状态、记录 diagnostic、不创建新 Runtime Task、
不绕过 F8 terminal authority）。

## 8. Loop Safety（谁保证什么）
- F8（硬）：steps/llm/tool/search/deadline/cancel/terminal。
- F9 Research Policy：max_research_rounds（默认 ≤3，可配置）；query 去重（§6.1）；
  evidence novelty（同 query 命中无新 source→0）；repeated-gap（同 gap ≥2 轮未缓解→僵局）；
  diminishing-return（无新域/新 claim→停止评估）；no-progress（无合法 plan 或全重复）。
- `research_round` 是 F9 编排计数（≠ F8 agent_step），不注入冻结 BudgetCounter。

## 9. [R1-6] Stopping Policy
- Hard Stop（强制，F8）：任何 F8 上限/超时/取消 → 终态。
- Semantic Stop（建议）：LLM recommendation。
- Deterministic Policy（终裁）：必答覆盖达标 AND 无未缓解 critical conflict AND claims 支持充分
  → STOP；否则 FOLLOW-UP（受轮数/novelty/僵局 guard）。条件长期不满足由 no-progress/diminishing
  收敛 STOP，不无限搜。
- **budget 是 hard ceiling 不是 completion target**：F9 可在仍有预算时因 evidence sufficient /
  claims grounded / 无实质 gap / diminishing return / semantic stop / no-progress 正常停止；
  “还有 budget”不得作为继续理由。
- 一致性（R2-6）：Judge failure 不触发 §9 的“semantic STOP 推荐”，而是进入 R2-1 baseline
  fallback（见 §12/§7）。

## 10. Re-verification（复用 F3–F6）
- 新 evidence → bind → 仅 binding 变化的 claim 增量 verify（fingerprint 幂等）；冲突集合变化才
  增量跑 F4–F6；verdict append-only 取最新（排序键 §3.1）；verification failure → UNVERIFIABLE。

## 11. Context Engineering
`Research DB → Projection → Task-specific Envelope → Supervisor/Subagent`；禁止父对话全量复制。

## 12. [R1-3][R2-1] Failure Semantics
**fail-open = open to baseline execution，NOT open to degraded adaptive intelligence。**
- Projection 失败：不基于 partial/invalid projection 做 adaptive follow-up 决策；adaptive loop →
  unavailable；fallback 同一 F8 run 的 baseline Agent execution；F8 继续生效。
- **[R2-1] Judge failure（冻结）**：= **停止当前 adaptive decision，但 fallback 到同一个 F8
  governed execution 内的 baseline research/synthesis**。
  1) 不基于失败/partial judge 结果生成 follow-up plan；
  2) 不继续当前 adaptive loop；
  3) 不创建新 Runtime Task；
  4) 不重建 F8 Controller/BudgetCounter/watchdog；
  5) baseline 与 F9 共享 task_id / run_id / F8 counters / deadline / cancellation / terminal
     authority；
  6) baseline 正常完成 → 仍属 F8 normal completion → **F7 finalization 恰一次**；
  7) 仅当 no-progress / deterministic stopping policy 明确触发才进入 F9 STOP/NO_PROGRESS；
  8) 不得因 Judge failure 直接把整个 F8 task 标记 failed，除非已有 F8/runtime failure semantics
     被触发。
- Plan validation：fail-closed。Search/Tool/Verification：沿用 F8/F3–F6 semantics。
- F9 failure 不得绕过 F8 cancellation/timeout/budget。

### Failure Matrix
| Failure | F9 行为 | 继续 Adaptive Loop | Fallback Baseline | F8 是否生效 |
|---|---|---|---|---|
| Projection failure | adaptive=unavailable；记录 diagnostics | No | Yes（同一 run） | Yes |
| Judge failure | 停止本 decision；baseline research/synthesis（同一 F8 run） | No（本 decision/round） | Yes | Yes |
| Plan validation failure | 拒绝 plan（fail-closed）；记 reason | 视守卫（可重提合法 plan） | 无 plan 时 Yes | Yes |
| Search failure | 单 query 失败跳过 | Yes（剩余合法 plan） | — | Yes |
| Verification failure | claim=UNVERIFIABLE | Yes | — | Yes |
| F8 budget exceeded | — | No | N/A | Terminal(budget_exceeded) |
| F8 timeout | — | No | N/A | Terminal(timed_out) |
| F8 cancellation | — | No | N/A | Terminal(cancelled) |

## 13. Idempotency / Recovery
round identity（run 内持久）、plan fingerprint、query identity（§6.1）、verification fingerprint、
state transition identity —— 幂等可重放；以 PostgreSQL 唯一约束 + F8 事务实现；不引入
Redis/Kafka/Neo4j。
- 一致性（R2-6）：§13 “半程状态可幂等续算”指**同一次 governed execution 内**或进程崩溃后由
  后续 startup/future 机制以持久状态续算；不违反 §2.1 单 execution（崩溃后自动恢复属 F8
  startup/sweeper future 范围，不承诺 run 内新建第二 task）。

## 14. Eval Acceptance
- Baseline: Agent→Search→Answer；F9: Agent→Evidence State→Gap→Targeted Search→Verify→Stop。
- 指标：unsupported claim rate↓、citation coverage↑、evidence sufficiency↑、conflict coverage↑、
  redundant searches↓；search/tool/llm/token/runtime 不显著超支；answer quality ≥ baseline。
- 通过标准：同 F8 budget 下 F9 至少改善 ≥1 项 grounding 指标且无显著效率退化；
  以 grounding/收敛质量为主要判据，**不以“搜索次数增加”为指标**（R2-6：Eval 不鼓励为指标
  而加搜）。

## 15. Scope
In：projection；deterministic gap signals；单 judge LLM call；validated follow-up plan；
targeted research（复用 main/subagents）；增量 re-verification；semantic+deterministic stopping；
结构化 why；只读 projection/API；Eval harness；round state / query identity 持久化（research plane）。
Out：Neo4j/VectorDB/Redis/Kafka；multi-instance；distributed scheduler；generic/Planner/
Verification/Conflict/Citation/Summarizer Agent；large-scale fanout；cross-session memory；
Claim–Evidence Graph 基础设施；UI redesign；F8 redesign；F9-P1/P2；新独立 Runtime Task。

## 16. Architecture Decision
§2/§2.1/§2.2 冻结：单 governed execution 内形成 evidence→gap→follow-up→verification→stopping
闭环；确定性优先、复用 Supervisor、不新增常驻 Agent、不新增 Runtime 控制面。

## 17. Open Questions（不阻塞实现）
1. 实现 seam：orchestrator 放 run_deep_agent 薄层 vs 独立 research-orchestrator 协程（均符合
   §2.1/§2.2；任选，实现前定）。
2. judge 的 llm_calls 计入方式（callbacks 注入路径）实现层细化（语义已冻结）。
3. stopping/阈值常量由 Eval 校准。
4. unresolved-gaps 在最终答案的可见性（F7 边界）——实现该 feature 时另行裁决（不影响 loop 核心）。
5. projection/round-state 持久化的 schema additive（research plane）——实现 Gate 裁决，不承诺。
**阻塞 Implementation 的 Open Question：无。**

## 18. Decision
**[R2] SPEC REVISION 2 COMPLETE — READY FOR IMPLEMENTATION REVIEW** ——
R2-1..R2-6 全部闭环（见 Revision 记录与 Closure Matrix 输出），全文一致性检查通过；
F1–F8 冻结契约零破坏；无 Generic/Planner Agent、无新 Runtime Task/Governance、无新基础设施。
等待 Implementation Review / 下达实现指令。
