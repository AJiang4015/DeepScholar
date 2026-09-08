# F9-P0 Spec — Evidence-Driven Adaptive Research Loop（Revision 1）

> 状态：**SPEC（设计阶段；未实现）。Revision 1 按 Architecture Review CONDITIONAL 闭环 3 核心 + 3 次要问题。**
> 上游：F9 Research Intelligence Architecture Audit（PASS/READY）；F9-P0 Spec Rev0。
> 冻结前提：F1–F8 全部语义冻结、不重审；本 Spec 只在冻结底座上设计闭环；不新增第二 Runtime 控制面。
> 版本：Rev1（本文件）；Rev1 修改点以「[R1-*]」标注。

## 0. Revision 记录（Rev0 → Rev1）
| # | 修改 | 落点 |
|---|---|---|
| R1-1 | 明确 F8/F9 Lifecycle Boundary（单 governed execution；round=编排状态；预算/超时/取消覆盖全循环；多次 graph invocation 同 task/run/budget） | §2/§3（新增 §2.1 Lifecycle Boundary） |
| R1-2 | Projection 字段语义逐一冻结（类型/来源/规则/deterministic/unknown 规则/内部 or API） | §4（Semantic Contract 表） |
| R1-3 | fail-open 重定义为 open-to-baseline（非 degraded adaptive）；新增 Failure Matrix | §12 |
| R1-4 | 新增 Research Round State 生命周期（orchestration 态，非 F8 TaskStatus） | §7 |
| R1-5 | Query dedup / re-query 语义（不是“永不重复”；区分合理 re-query） | §6.1 |
| R1-6 | Stopping：budget 是 hard ceiling 非 completion target | §9 |
| R1-7 | 保留边界：无 Generic/Planner/Citation/Conflict Agent；无 Redis/Kafka/Neo4j/VectorDB；不改 F8；F9 非第二 governance | §15/§17 |

## 1. 目标（一句话）
把 retrieval loop 变为 **evidence-grounded adaptive research loop**：
Research State → deterministic Projection → Deterministic Gap Signals → Semantic Gap Judgment →
Validated Follow-up Plan → Targeted Research → Incremental Re-verification →
Semantic + Deterministic Stopping → F7 finalization —— 全部运行在**单个**受 F8 治理的 governed execution 内。

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
- **一个完整 F9-P0 Research Loop = 一个 F8 Controller-governed execution**（一个 TaskRecord、
  一个 task_id、一个 run_id=F1 同源、一个 BudgetCounter 上下文、一个 watchdog/deadline）。
- `research_round` 是 **F9 orchestration 状态（编排计数）**，不是新的 Runtime Task；F9 **不得**
  每轮重建 TaskRecord / 重新建立 Runtime Budget / 重启独立 watchdog。
- F8 的 wall-clock deadline、cancellation、max_agent_steps、max_llm_calls、max_tool_calls、
  max_search_calls、terminal authority **覆盖整个 F9 Loop**（从 Round 0 到 Final Synthesis）。
- 若实现确需多次 graph invocation：它们仍是**同一个 Controller execution / 同一 task_id /
  同一 run_id / 同一预算上下文**内的连续调用（可经 thread checkpointer 延续对话）；
  **禁止**以“重新 invoke graph”绕过 F8 budget/deadline/cancellation（每次 invocation 消耗同一
  F8 counters；deadline 为单调单一来源）。
- 角色分离保持：F8=How much/How long/Can execute；F9=What is missing/What to investigate next；
  F3–F6=What evidence means；F7=What final research state is。

## 3. Research State Projection（deterministic）
原则：有限、稳定、可序列化、task-specific；绝不全量 dump Research DB / parent history。
管道：`Research DB → deterministic Projection → Gap Signals / Semantic Judgment`；
**F9 Judge 永不直接读完整 Research DB 或 parent message history。**

### 3.1 [R1-2] Projection Semantic Contract（字段逐一冻结）
| 字段 | 类型 | 来源 | 计算/判断规则 | Deterministic | Unknown/Null | 数据不足时 | 内部/API |
|---|---|---|---|---|---|---|---|
| run_id | str | TaskRecord.run_id(F1) | 直读 | 是 | 不允许 | run 必已绑定（F1 fail-closed） | API |
| round | int | orchestrator 计数 | 每轮 +1（F9 层） | 是 | 不允许 | 0 | API |
| budget | dict | F8 counters 摘要 | 直读 counter.count/limit + 单调剩余 | 是 | 不允许 | 上限值 | API |
| sub_questions | list | research schema | 该 run 的 SubQuestion 行 | 是 | 空列表 | 空 | API |
| required_uncovered | list[id] | schema | 根/必答子问题中无 evidence 或无 source 者（代码规则） | 是 | 空 | 空 | API |
| coverage_est | — | — | **删除（Rev1）**：无法由确定性给出可信百分比；避免 LLM 解释 | — | — | — | — |
| claims[] | list | research schema | 该 run 的 Claim 行（受限字段投影） | 是 | 空 | 空 | API |
| best_verdict | enum{SUPPORT,CONTRADICT,INSUFFICIENT,UNVERIFIABLE,null} | verify 最新行（fingerprint 幂等） | 取该 claim 最新 verdict（append-only，不推断；无 verify 行→null） | 是 | null | null（绝不猜） | API |
| independent_flag | bool/null | F5 corroboration 行 | **仅=source independence（来源间独立判定）**；≠ source authority/可信度；无 F5 行→null | 是 | null | null | API |
| fresh | bool/null | 最近 Evidence.created_at | `now - max(source 捕获时间) <= FRESHNESS_WINDOW(默认 1 天，常量)`；无 source→null | 是 | null | null | 内部字段（Rev1 明确基准=创建时间+常量窗口） |
| conflicts[] | list | F4/F6 行 | 未缓解冲突（按 claim 关联 + reconciliation 状态） | 是 | 空 | 空 | API |
| evidence_summary | dict | schema 聚合 | count、按域/权威域名计数（domain 分类 deterministic） | 是 | — | 0 | API |
| gap_signals | dict | §5 deterministic signals | 布尔信号集（代码计算） | 是 | 缺省 false | false | 内部→转 API（稳定后） |
| open_questions | list | 语义 judge 上一轮 + 确定性未覆盖 | 见 §5.2 | 半 | 空 | 空 | 内部字段 |
| size_limits | dict | 常量 | token 预算（envelope 上限） | 是 | — | 默认 | 内部字段 |
- 规则：任何含 null 的字段不允许被 LLM 自由补值；LLM 只能基于非 null 字段做 judgment；
  projection 内**不注入** evidence 原文堆叠（仅元数据 + 由 envelope 按需选载的 top-K 摘要）。

## 4. Supervisor vs 子代理看到什么
- Supervisor/orchestrator：projection + budget + gap_signals（决策级视图）。
- Follow-up 执行（复用 main/subagents）：Task-specific Envelope = {target sub-question、objective、
  候选 query、必读 evidence id+摘要、citation 提示、去重约束}。
- 不注入：完整 evidence 正文堆叠、父对话全量、research DB dump、冲突表原文。

## 5. Gap Detection
### 5.1 Deterministic Gap Signals（代码计算，判据冻结）
- required sub-question 未覆盖；claim 无 evidence；evidence 数 < MIN_EVIDENCE（常量）；
  verification=INSUFFICIENT/UNVERIFIABLE；存在未缓解 critical conflict；
  source independence 不足（独立源数 < 阈值）；citation coverage < 阈值；
  query/source 重复率过高；budget 接近上限（剩余 < 阈值）。
### 5.2 Semantic Gap Judgment（仅此部分由 LLM）
- “当前证据是否足以回答研究问题？”（YES/NO+confidence）；不足时给 候选 query+objective+priority
  +expected evidence+reason+verification target。
- 边界：deterministic signals 给出候选与合规上限；LLM 只做排序/补充/措辞；结果进 Plan 再验证。

## 6. Follow-up Plan Contract
plan_id / target_sub_question_id / objective / queries[] / priority / verification_target_claim_ids /
stop_condition / why（“为什么继续搜”，结构化入 Research State，不只存在于 LLM history）。
### 6.1 [R1-5] Query Dedup / Re-query 语义
- query_identity = 规范化 query 哈希（normalize URL/参数/空白后 sha256，存 research plane）。
- 禁止：**同一 identity 在同一 (objective, source-universe, time-window) 内重复**（无新增价值）。
- 允许（视为 re-query 而非 duplicate，需满足条件）：
  - 新 objective（objective 指纹不同）；
  - 新 source universe（不同源/不同 provider/不同库）；
  - 新时间范围（如“近 24h 更新”）；
  - provider failure/retry（同 identity 但上一执行失败且未产生新 source）；
  - 新证据出现后为验证目的重新定位同一来源（reason 标注 re-verify）。
- Validator 按上述原则拒绝真重复、放行合理 re-query，并记录 reason。

## 7. [R1-4] Research Round State（orchestration 态；非 F8 TaskStatus）
```text
ROUND_STARTED → PLAN_PROPOSED → PLAN_VALIDATED → RESEARCH_EXECUTED →
EVIDENCE_UPDATED → VERIFICATION_UPDATED → ROUND_EVALUATED →
  FOLLOW_UP（下一轮） | STOPPED | NO_PROGRESS
```
- 持久于 research plane 状态（round 记录行）；重启后按 state 幂等恢复（§13）。
- 明确：这不是 F8 TaskStatus、不新增 Runtime lifecycle；F8 status 仍仅 running→terminal。

## 8. Loop Safety（谁保证什么）
- F8（硬）：steps/llm/tool/search/deadline/cancel/terminal 覆盖全循环。
- F9 Research Policy：max_research_rounds（默认 ≤3，可配置）；query 去重（§6.1）；evidence novelty
  （同 query 命中无新 source→novelty=0）；repeated-gap（同 gap ≥2 轮未缓解→僵局标记）；
  diminishing-return（新增 source 无新域/新 claim→触发停止评估）；no-progress（无合法 plan 或全重复）。
- `research_round` 是 F9 编排计数（≠ F8 agent_step：后者=决策回合/LLM 调用预算维度）；不注入冻结
  BudgetCounter；F8 资源上限仍是唯一硬约束。

## 9. [R1-6] Stopping Policy
- **Hard Stop（强制，F8）**：任何 F8 上限/超时/取消 → 终态；F9 不得绕过。
- **Semantic Stop（建议）**：LLM 输出 recommendation。
- **Deterministic Policy（最终裁决）**：仅当 必答覆盖达标 AND 无未缓解 critical conflict AND claims
  支持充分 → STOP；否则 FOLLOW-UP（受轮数/novelty/僵局 guard）。
- **budget 是 hard ceiling，不是 completion target**：F9 可在仍有预算时因 evidence sufficient /
  claims grounded / 无实质 gap / diminishing return / semantic stop / no-progress 正常停止；
  “还有预算”**不得**作为继续研究理由。

## 10. Re-verification（复用 F3–F6）
- 新 evidence → bind_claim_evidence → 仅对 binding 变化的 claim 增量 verify（fingerprint 幂等）；
  冲突集合变化才增量跑 F4–F6；不整库重跑。verdict append-only，取最新；verification failure →
  claim 保持 UNVERIFIABLE（fail-open 于 evidence semantics）。

## 11. Context Engineering（正式模式）
`Research DB → Projection → Task-specific Envelope → Supervisor/Subagent`；禁止父对话全量复制；
evidence 选择按 token 预算+相关性（deterministic 排序）；source metadata/citation/verification
summary 入 envelope。

## 12. [R1-3] Failure Semantics（fail-open 精确定义）
**fail-open = open to baseline execution，NOT open to degraded adaptive intelligence。**
- Projection 失败：不基于 partial/invalid projection 做 adaptive follow-up 决策；
  adaptive loop → **unavailable**；fallback 现有 baseline Agent execution；F8 继续生效。
- Judge 失败：不产生未验证 follow-up plan；fallback baseline/stop（安全策略），不猜测。
- Plan Validation：仍 **fail-closed**。
- Search/Tool/Verification：沿用 F8/F3–F6 现有 semantics。
- F9 failure 不得绕过 F8 cancellation/timeout/budget。

### Failure Matrix
| Failure | F9 行为 | 继续 Adaptive Loop | Fallback Baseline | F8 是否生效 |
|---|---|---|---|---|
| Projection failure | loop=unavailable；记录 diagnostics | No | Yes | Yes |
| Judge failure | 不产 plan；按安全策略 stop/fallback | No（本轮） | Yes | Yes |
| Plan validation failure | 拒绝该 plan（fail-closed）；记 reason | 视守卫（可重提新 plan） | 无 plan 时 Yes | Yes |
| Search failure | 单 query 失败跳过（fail-open 于工具语义） | Yes（剩余 plan） | — | Yes |
| Verification failure | claim=UNVERIFIABLE（不伪造） | Yes | — | Yes |
| F8 budget exceeded | — | No | N/A | Terminal(budget_exceeded) |
| F8 timeout | — | No | N/A | Terminal(timed_out) |
| F8 cancellation | — | No | N/A | Terminal(cancelled) |

## 13. Idempotency / Recovery
round identity（run 内持久）、plan fingerprint、query identity（§6.1）、verification fingerprint、
state transition identity —— 幂等可重放；崩溃恢复以 PostgreSQL 唯一约束 + F8 事务；
不引入 Redis/Kafka/Neo4j。进程崩溃后自动恢复属 F8 startup/sweeper future 范围（本 Spec 要求
“半程状态可幂等续算”，不承诺自动续跑）。

## 14. Eval Acceptance
- Baseline: Agent→Search→Answer；F9: Agent→Evidence State→Gap→Targeted Search→Verify→Stop。
- 指标：unsupported claim rate↓、citation coverage↑、evidence sufficiency↑、conflict coverage↑、
  redundant searches↓；search/tool/llm/token/runtime 不显著超支；answer quality ≥ baseline。
- 通过标准：同 F8 budget 下 F9 至少改善 ≥1 项 grounding 指标且无显著效率退化（阈值 Eval 阶段定）；
  证明是 grounding 改善而非单纯多搜几轮。

## 15. Scope
### In Scope
projection；deterministic gap signals；single judge LLM call；validated follow-up plan；
targeted research（复用 main/subagents）；增量 re-verification（F3–F6 复用）；
semantic+deterministic stopping；结构化 why 进 research state；只读 projection/API；Eval harness；
round state / query identity 持久化（research plane）。
### Out of Scope
Neo4j/VectorDB/Redis/Kafka；multi-instance；distributed scheduler；generic/Planner/Verification/
Conflict/Citation/Summarizer Agent；large-scale fanout；cross-session memory；Claim–Evidence Graph
基础设施；UI redesign；F8 redesign；F9-P1/P2；新独立 Runtime Task。

## 16. Architecture Decision
§2/§2.1 主架构冻结（R1-1）—— 单 governed execution 内形成 evidence→gap→follow-up→verification→
stopping 闭环；确定性优先、复用 Supervisor、不新增常驻 Agent、不新增 Runtime 控制面。

## 17. Open Questions（不阻塞/需 Review）
1. 实现 seam：orchestrator 放 run_deep_agent 薄层 vs 独立 research-orchestrator 协程作该 task 执行体
   （两者均单 task/单 F7）——需代码可行性确认（非阻塞：任选均符合 §2.1 边界）。
2. judge 调用归属：计入同一 governed task 的 llm_calls（非业务回合不计 agent_step）——待实现层定
   （不影响边界冻结）。
3. stopping 阈值（MIN_EVIDENCE/独立源阈值等）由 Eval 校准。
4. unresolved gaps 在最终答案的可见性模板（是否触碰 F7）——F7 边界问题，待 Review 裁决。
5. projection/round-state 是否需要 schema additive（research plane 新行/列）——仅当实现需要时提出，
   本 Spec 不承诺（与 F9 实现 Gate 一并裁决）。
- **阻塞 Implementation 的 Open Question：无**（上述均可在实现开始前或实现中按冻结边界裁定）。

## 18. Decision
**[R1] SPEC REVISION READY FOR ARCHITECTURE REVIEW** —— R1-1（Lifecycle Boundary）、R1-2
（Projection Semantic Contract）、R1-3（fail-open=open-to-baseline + Failure Matrix）三核心闭环；
R1-4..R1-6 次要项已补齐；边界与角色分离保持不变。等待下一轮 Architecture Review。
