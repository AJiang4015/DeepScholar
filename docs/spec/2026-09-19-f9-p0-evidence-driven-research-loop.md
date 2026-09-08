# F9-P0 Spec — Evidence-Driven Adaptive Research Loop

> 状态：**SPEC（设计阶段；未实现）**。上游：F9 Research Intelligence Architecture Audit
> （PASS/READY；Level 2→3 断点 = execution-time evidence→decision feedback）。
> 冻结前提：F1–F8 一切语义冻结、不重审；本 Spec 只在冻结底座之上设计闭环，不改 F1–F8，
> 不新增 Runtime 控制面（无第二 budget/cancel/timeout/lifecycle/sequencer）。
> 文件名约定：docs/spec/2026-09-19-f9-p0-evidence-driven-research-loop.md

## 1. 目标（一句话）
把 retrieval loop 变为 **evidence-grounded adaptive research loop**：
Research State → Projection → Deterministic Gap Signals → Semantic Gap Judgment →
Validated Follow-up Plan → Targeted Research → Incremental Re-verification →
Semantic + Deterministic Stopping → F7 finalization（全程跑在单个受 F8 治理的 governed task 内）。

## 2. 主架构（冻结候选）
```text
一次 governed run（= 一个 F8 task；F8 budget/deadline/cancel/terminal 管整个研究运行）
  │
  ├─ Round 0：初始 broad research（复用现有 deep agent / subagents / tools；evidence 入库）
  ├─ F9 Research Loop（每轮）：
  │     Research State Projection（deterministic）
  │     Gap Detection（deterministic signals + LLM semantic judgment）
  │     Follow-up Plan（LLM 提议 → deterministic validator）
  │     Targeted Research（同 agent/子代理，注入 Task-specific Envelope，受 F8 计数/预算）
  │     Incremental Re-verification（新 evidence → bind → F3–F6 增量）
  │     Updated Research State
  │     Stopping Decision（semantic 建议 + deterministic policy → STOP/FOLLOW-UP）
  ├─ Final synthesis round（复用 agent；产出最终答案）
  └─ F7 finalization（仅 normal completion；一次）
```
- 单生命周期：整个 F9 循环 = **一个** governed F8 task 的正常执行；F7 只在循环正常结束后触发一次。
- 每个 round 复用 main agent + 现有 subagents/tools（**不新增 Planner/Verification/Conflict/Citation/
  Summarizer Agent**）；语义 gap 判定由一个 focused LLM call（judge）完成，不做新 Agent。

## 3. Research State Projection（deterministic）
- **原则**：有限、稳定、可序列化、task-specific；绝不全量 DB dump；绝不复制父对话全量。
- Input 源：run_id/sub-questions/queries/sources/evidence/claims/citations/verification/conflicts/
  reconciliation（research plane）+ F8 runtime counters 摘要。
- Output 结构（stable envelope，JSON）：
```text
{
  "run_id", "round", "budget": {"agent_steps", "llm_calls", "tool_calls", "search_calls",
                                  "wall_clock_remaining_sec"},
  "sub_questions": [{id, question, status, coverage_est}],
  "coverage": {"answered": bool, "required_uncovered": [id...]},
  "claims": [{claim_id, text, claim_type, evidence_count, citation_count,
              best_verdict, conflict_flag, independent_flag}],
  "evidence_summary": {"count", "sources": {domain: n}, "fresh": bool},
  "conflicts": [{id, summary, type, status, independent}],
  "gaps_signals": {...code-computed booleans...},
  "open_questions": [prioritized],
  "size_limits": {"max_tokens": N}
}
```
- Ordering：claims 按 未验证>冲突>独立排序；sources 按新鲜度/域多样性。
- Truncation/过滤：evidence 原始文本**不注入** projection——只留 摘要元数据 + top-K 代表句
  （token 预算内）；原始 source/evidence 只在 follow-up 读取子代理目标时经 envelope 选载。
- Run isolation：一切查询以 run_id（= governed TaskRecord.run_id，F1 同源）隔离；deterministic。

## 4. Supervisor vs 子代理看到什么
- **Supervisor/judge（main loop）**：看到 compact projection + budget 摘要 + gap signals（决策级视图）。
- **Follow-up 执行（复用 main/subagents）**：看到 Task-specific Envelope = {目标子问题、research
  objective、候选 query、必读 evidence id 与摘要、citation 提示、禁止重复的 query 集合}。
- **不注入**：完整证据正文堆叠、父对话全量、research DB dump、内部 conflict 表原文。

## 5. Gap Detection
### 5.1 Deterministic Gap Signals（代码计算，作为 judge 的输入与 policy 依据）
- required sub-question 未覆盖 / claim 无 evidence / evidence 数不足(阈值)
- verification=INSUFFICIENT 或 UNVERIFIABLE / unresolved critical conflict /
  source independence 不足 / citation coverage 不足 / query·source 重复率过高 / budget 接近上限
### 5.2 Semantic Gap Judgment（LLM，仅此部分）
- “当前证据是否足以回答研究问题？”（YES/NO+confidence）
- “若不足，最值得补什么证据 / 下一轮查什么”（候选 query+objective+priority+expected evidence+
  reason+verification target）
边界：deterministic signals 决定“有哪些 gap 候选 & 合规上限”；LLM 只做语义排序/补充；两者输出进入
Follow-up Plan 再验证。

## 6. Follow-up Plan Contract
```json
{
  "plan_id": "<round>-<n>",
  "target_sub_question_id": "...",
  "objective": "...",
  "queries": [{"query": "...", "expected_evidence": "...", "reason": "..."}],
  "priority": 1..N,
  "verification_target_claim_ids": ["..."],
  "stop_condition": "primary-source confirmation | resolve conflict | ...",
  "why": "INSUFFICIENT evidence → need primary-source confirmation"
}
```
- LLM 输出：plan 候选（结构化）。
- Deterministic validator：查 query 去重（与历史 query 集合）、query 数 ≤ max_per_round、target
  sub-question 存在、objective 非空、无“无限 query”（轮次/数量 cap）；非法/重复 → 拒绝该条并记录
  reason（进 provenance）；整轮无合法 query → 视为 no-progress 信号 → 触发 stopping 评估。
- “为什么继续搜”原因结构化写入 Research State（plan.why + signals），**不只存在于 LLM history**。

## 7. Loop Safety（谁保证什么）
- **F8 保证（硬）**：agent_steps/llm/tool/search/deadline/cancel/terminal —— 硬上限，
  整循环一个 governed task；研究轮不新增第二硬预算。
- **F9 Research Policy 保证**：max_research_rounds（默认小，如 ≤3，可配置）、query 去重、
  evidence novelty 检查（同一 query 命中且无新 source → novelty=0）、repeated-gap detection
  （同一 gap 信号连续 ≥2 轮未缓解 → 标记僵局）、diminishing-return（新增 source 无新域/无新 claim
  → 触发停止评估）、no-progress detection（全轮无合法 plan 或查询全重复 → 停止）。
- 计数区分：`research_round` 是 F9 policy 的循环计数（在 orchestration 层，非 F8 治理 counter），
  与 F8 `agent_step`（决策回合/LLM 调用预算）不同维度：F8 管资源硬上限，F9 管“研究收敛轮数”。
  不得把它当 F8 新 counter 注入冻结 BudgetCounter。

## 8. Re-verification（复用 F3–F6，不重造）
- 触发：follow-up 产生新 evidence → 先与候选 claim bind（registry.bind_claim_evidence）→ 增量验证：
  只对 **binding 变化的 claim** 重跑 verify（fingerprint = claim+evidence 集合哈希，未变则复用旧
  verdict，跳过）。
- 冲突/独立性/调和：冲突集合变化才增量跑 F4/F5/F6（以（claim/evidence 指纹）判定增量）；不整库重跑。
- 新旧 verdict 共存：保留 append-only 历史（不改行），取最新；verification failure → 该 claim 保持
  UNVERIFIABLE + fail-open（不伪造）。
- Idempotency：verification fingerprint 幂等（同输入同结果不重算）；plan fingerprint/query identity
  （query 规范化哈希）保证崩溃恢复不重复搜索/验证（复用 PostgreSQL 唯一约束 + F8 事务）。

## 9. Stopping Policy
- **Hard Stop（强制，F8）**：max_steps/llm/tool/search/deadline/cancel → 直接终态；F9 不得绕过。
- **Semantic Stop（建议）**：LLM 输出 recommendation（sufficient / insufficient）。
- **Deterministic Policy（最终裁决）**：仅当
  `required coverage 达标 AND 无未缓解 critical conflict AND claims 支持充分 AND budget 仍有富余` 才
  允许 STOP；否则 FOLLOW-UP（受轮数/novelty/僵局 guard 约束）；无法满足且触顶 → 记录 unresolved
  gaps 后 STOP（可见性留给最终答案）。
- 语义 stop 是**建议**，deterministic policy 验证后才生效（LLM 不能绕过 F8）。

## 10. Fail-open / Fail-closed 矩阵
| Component | Semantics |
|---|---|
| Research projection | FAIL-OPEN：投影失败 → 该轮退化为“仅 budget/方向指令”，不阻断执行 |
| Gap detection（deterministic） | FAIL-OPEN：信号缺失 → 仅跑 LLM 建议；全失败 → 进入停止评估 |
| LLM semantic judgment | FAIL-OPEN：judge 失败/超时 → 以 deterministic signals 兜底（无新 plan → 评估停止） |
| Follow-up plan validation | FAIL-CLOSED：非法/重复 plan 不执行 |
| Search execution | 复用 F8/tool fail-open（单 query 失败不影响其它） |
| Re-verification | FAIL-OPEN（verdict 失败 → UNVERIFIABLE，不伪造） |
| Research policy | FAIL-CLOSED（轮数/去重/僵局 guard 始终生效） |
| F8 governance | 不变（硬上限权威） |
| Final answer | 仅 F7 normal completion；若 F9 阶段无法收敛 → 仍产出带 unresolved-gaps 注记的答案 |
**关键决策**：F9 intelligence 失败（projection/judge/plan）**不 terminal fail** —— 降级为普通
（非 evidence-adaptive）Agent execution 继续，F8 照常收敛；只有 F8 硬控制才终态。

## 11. Idempotency / Recovery（崩溃于 research round N）
- round identity：run 内全局递增 round_id（持久于 research plane 状态行，非内存）。
- plan fingerprint：round_id+目标+queries 哈希 → 重启后 plan 幂等。
- query identity：规范化 query 哈希唯一 → 不重复执行（PostgreSQL 唯一约束，现有 research store）。
- verification fingerprint / state transition：沿用 §8 + F1/F8 既有注册 API 幂等。
- 恢复路径：新进程以同一 governed run 重进？—— crash 属 F8 之后 startup/sweeper future 范围；
  本 Spec 要求“半程状态均可幂等重放/续算”，不引入 Redis/Kafka/Neo4j。

## 12. Context Engineering（正式模式）
`Research DB → Projection → Task-specific Envelope → Supervisor/Subagent`；禁止父对话全量复制；
evidence 选择按 token 预算 + 相关性（deterministic 排序）；source metadata/citation/verification
summary 入 envelope。

## 13. 与 F8 的关系（复用，不重造）
整个 F9 循环是一个 **governed task 的正常执行体**（main agent 增 orchestration 阶段或等价薄层），
使用同一 TaskRecord/terminal/governance_events/replay/run_id identity；不新增第二 budget/cancel/
timeout/lifecycle/sequencer。若实现层需把 deep agent 分为多轮调用，属同一线程/run 的多次 graph
执行（checkpointer 续对话），仍由同一 F8 task 治理。

## 14. Eval Acceptance（对比 baseline 于相同 F8 budget）
- Baseline：Agent→Search→Answer；F9：Agent→Evidence State→Gap→Targeted Search→Verify→Stop。
- 指标：unsupported claim rate↓、citation coverage↑、evidence sufficiency↑、conflict coverage↑、
  redundant searches↓；search/tool/llm/token/runtime 不显著超支；answer quality 不低于 baseline。
- 通过标准示例：在相同 F8 budget 内 F9 相对 baseline 至少改善 grounding 指标 ≥1 项且无效率显著
  退化（具体阈值 Eval 阶段定）；**证明是 grounding 改善而非单纯多搜几轮**。

## 15. Scope
### In Scope
evidence→decision projection；deterministic gap signals；semantic gap judgment（单个 judge LLM call）；
validated follow-up plan（query 提议/去重/轮数）；targeted research（复用 main/subagents）；
增量 re-verification（F3–F6 复用）；semantic+deterministic stopping；structured “why” 进 research
state；相应 projection/API 只读层；Eval harness。
### Out of Scope
Neo4j/VectorDB/Redis/Kafka；multi-instance；distributed scheduler；generic Planner/Verification/
Conflict/Citation/Summarizer Agent；large-scale retrieval fanout；cross-session research memory；
Claim–Evidence Graph 基础设施；UI redesign；F8 runtime redesign；F9-P1/P2。

## 16. Architecture Decision
主架构（§2 管线）**采用并冻结**：
F9-P0 = Research State Projection → Deterministic Gap Signals → Semantic Gap Judgment →
Validated Follow-up Plan → Targeted Research → Incremental Verification → Semantic+Deterministic
Stopping（全程单 governed F8 task、F7 一次、fail-open 降级普通执行、F8 硬控制权威）。

## 17. Open Questions（留给 Architecture Review）
1. 实现 seam：F9 循环放 run_deep_agent 内 orchestration 薄层，还是独立 research-orchestrator
   协程作为该 task 的执行体（两者都单 task/单 F7）？需代码级可行性与 monitor 事件兼容确认。
2. judge 调用消耗 F8 llm_calls 的归属与上下文预算（proposal：judge 计入同一 governed task 的
   llm_calls，非业务回合不计 agent_step）。
3. stopping 判定阈值/权重（deterministic 规则初版值）由 Eval 校准。
4. unresolved gaps 在最终答案中的可见性模板（F7 finalization 前注入注记是否触碰 F7 边界）。
5. 若需要新 projection 表 → 属 schema additive，待 F9 Spec Review 裁决（当前不承诺）。

## 18. Decision
**F9-P0 主架构冻结（§2/§16）**：形成 evidence → gap → targeted follow-up → verification →
stopping 的受控闭环，不破坏 F1–F8；确定性优先、复用 Supervisor、不新增常驻 Agent、不新增 Runtime
控制面。等待 Architecture Review 后进入实现（本轮仅 Spec）。
