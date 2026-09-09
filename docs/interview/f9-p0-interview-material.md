# F9-P0 Interview Material（面试材料）— 深度研搜

> 定位：基于 **F9-P0 Core = COMPLETE** 的当前真实状态整理的面试材料（documentation-only，
> 零代码改动）。
> 素材来源（全部可回溯）：根目录 `README.md`、`docs/plan/2026-10-01-f9-p0-final-
> completion-report.md`、`PROJECT_CONTEXT.md`、`docs/spec/2026-09-19-f9-p0-…`。
> 纪律：只描述已实现并冻结的能力；**不新增未实现能力**；persistence / runtime recovery /
> real-provider E2E / production hardening 一律标注为 **Deferred / P1**。
> 目录：1. 一分钟介绍 · 2. 五分钟架构 · 3. 核心技术难点 · 4. 关键 Trade-off ·
> 5. 高频问题与回答 · 6. 简历项目描述版本。日期：2026-10-01。

---

## 1. 一分钟项目介绍（电梯版，可直接口述）

> 以下为约 60–90 秒的口述稿，配合一个真实用例。

「我做的项目叫『深度研搜』，是一个 **Evidence-Grounded 的多智能体深度研究系统**。

普通 LLM + Tools 的做法是：用户问一句，模型调几个工具，直接把答案吐出来——**答案没有
出处、声明没有验证、检索没有策略、执行没有约束**。

我的系统把这一层补成了完整的 **Research Runtime**，分三个层面：

第一，**Agent Runtime**——DeepAgents 一主三从：主智能体负责任务理解和调度，网络搜索、
MySQL 结构化数据、RAGFlow 私有知识库三个专家分别取数，最终生成 Markdown / PDF 报告。

第二，**Runtime Governance**——我用一个执行治理层（F8）管住整个任务生命周期：每个任务
有持久化的 TaskRecord，单一的 `Controller.execute(policy)` 管理预算、超时、取消和终态，
资源超限不会让任务失控地跑下去。

第三，也是我花精力最多的地方——**Research Intelligence**（F9-P0）。它把"检索"变成一条
受治理的 evidence-driven 闭环：检索结果先沉淀成可追溯的证据层（Evidence → Claim →
Citation → Verification → Conflict/Independence），然后系统**确定性判断哪里证据不足**、
**语义判断缺口是否值得补**、**定向补搜、增量验证**，直到证据足够才收敛，最后一次性
落库 finalize。

而且我不只是把功能做出来，还给它配了**行为评估和阈值校准**：用确定性的测试世界对比
baseline 和 adaptive 的行为质量（8 个场景全过），再用 61 个观测点的参数扫描证明当前
停止阈值就是最优——**阈值不是拍脑袋定的，是实验校准出来的**。

当前状态：F9-P0 Core 已宣告 COMPLETE，Batch1–8 全部冻结并合入 main；persistence /
跨执行恢复 / 真实模型端到端属于明确 deferred 的 P1 方向，我没有把它们算作已完成。」

---

## 2. 五分钟架构介绍（白板版）

> 给面试官的讲解稿；建议按时间分配：架构分层 1 分钟、治理 1 分钟、研究智能 1.5 分钟、
> 评估与校准 1 分钟、边界 0.5 分钟。可边讲边画下面的分层图。

### 2.1 一页分层图（与 README §3 一致）

```text
User Task
  ↓
[Presentation / API]  React + Vite 前端 ↔ FastAPI server.py（/api/task · WS · 上传/下载）
  ↓
[Runtime Layer — F8 Governance + Checkpoint]
  GovernanceController.execute(policy)   ← 唯一 lifecycle / budget / deadline / cancel / terminal 权威
  TaskRecord ledger（task_id · run_id=F1 同源 · status）
  BudgetCounters：agent_step / llm_call / tool_call / search_call（search ⊆ tool）
  GovernanceEvent (task_id, seq) + replay cursor
  LangGraph Checkpoint Saver（AsyncSqliteSaver / AsyncPostgresSaver）
  ↓
[Agent & Tools Layer]  Main Agent（run_deep_agent = baseline primitive）
   ├─ Sub-agent：network_search（Tavily）
   ├─ Sub-agent：database_query（MySQL）
   ├─ Sub-agent：knowledge_base（RAGFlow）
   └─ Tools：文件读取 / Markdown / PDF
  ↓
[Research Intelligence Layer — F9-P0（app/f9，单一 governed execution 内）]
  Round 0 → Projection → Deterministic Gap → Semantic Judge → Validated Plan →
  Targeted Research → Incremental Re-verify → Stop/Continue → Final Synthesis
  失败语义：Judge/Plan 失败 → 同一 execute 内 fallback baseline（fail-open = open to baseline）
  ↓
[Evidence / Research Plane — F1–F7（app/research）]
  ResearchRun → SubQuestion → Query → Source → Evidence → Claim → Citation →
  Verification（F3）→ Conflict（F4）→ Corroboration（F5）→ Reconciliation（F6）
  → Research State（F7 finalize_run 恰一次）
  持久化：PostgreSQL（生产基线）/ SQLite（测试）；三平面迁移分离
```

讲解顺序建议：

1. **先讲要解决的问题**：深研究任务的本质是"多源、多轮、需要验证和收敛"，普通
   RAG/单轮 Agent 覆盖不了。
2. **讲三层职责**：Runtime 管"能不能安全跑完"，Agent 管"怎么取数"，Research
   Intelligence 管"取什么、够不够、何时停"；Research Plane 管"证据事实本身"。强调
   **四层互不越界**：F9 不新建第二 Runtime，`research_round` 只是编排计数。
3. **讲单执行体这个最关键的不变量**：整个 adaptive loop 只允许一次
   `Controller.execute(policy)`；Judge 的 LLM 调用走显式 governance callback 计入预算；
   F7 finalize 只出现在 normal completion（恰好一次），cancel/timeout/budget 路径不触发。
4. **讲评估如何证明行为正确**：确定性 scripted world + 双执行（baseline vs adaptive，
   各自独立 execute、同 policy、同 world）+ 8 维 rubric（no-pad verdict）。
5. **讲校准结论**：5 参数 × 61 观测点 → 质量成本不敏感、维持默认值。
6. **诚实收尾**：哪些是 deferred（persistence、恢复、真实 provider E2E、生产加固），
   展示工程判断力而不是隐瞒边界。

---

## 3. 核心技术难点

> 每个难点按「问题 → 方案 → 为什么难 → 验证」组织；全部为已实现事实。

### 3.1 单执行体内的自适应循环（不新增第二 Runtime）

- 问题：多轮检索 + 多轮验证 + 多轮 LLM 判断，很容易被实现成"每轮一个任务/每轮重建
  budget"，于是预算、取消、超时对每一轮都失效，且无法形成单一生命周期。
- 方案：F9 Spec §2.2 冻结 **single execute**——一次 `Controller.execute(policy)` 内含
  round0 → loop → synthesis；多轮 `agent.astream` 共享同一 task_id/run_id/BudgetCounter/
  deadline/cancel 上下文；Judge LLM 调用经 `ctx.make_handler()` 显式挂入 governance
  callback 计费（不计 agent_step）。
- 为什么难：编排层容易"借治理之名绕治理"；约束必须落在架构层面并被对抗测试锁死。
- 验证：`tests/test_f9_orchestrator.py` 17 个测试含治理对抗矩阵（judge 无 callback 可被
  发现、round 内不 reset counter/watchdog、round 内不生成新 task/run、F8 terminal 后 F9
  不继续）。

### 3.2 F7 finalize 恰好一次（normal 完成路径唯一接线点）

- 问题：多轮 + fallback + 失败语义下，"最终落库"容易被执行两次或零次。
- 方案：F7（既有 `finalize_run`）只挂在 **orchestrator normal completion**；round 内禁止
  F7；F8 cancel/timeout/budget_exceeded/recursion/agent failure → **不执行 F7**。
- 验证：Batch6 F7-once adversarial matrix 覆盖 8 个非 normal 路径（含取消、超时、预算、
  fallback 后再次 finalize 等）；Batch7 s1/s6/s8 也在行为层断言 finalize==1。

### 3.3 确定性缺口信号 + 语义判断的分工（不把"是否足够"全丢给 LLM）

- 问题：纯靠 LLM 判断"证据够不够"不可复现、不可测试；纯靠规则又太僵。
- 方案：**Deterministic Gap Detection 先行**（必答未覆盖 / claim 无 evidence /
  evidence < MIN_EVIDENCE / INSUFFICIENT·UNVERIFIABLE / 未缓解 conflict / 独立源不足 /
  citation 不足 / 重复率高 / 预算临近），再把投影（无 authority、无原文堆叠）交给单次
  Judge LLM 做语义判断。
- 为什么难：要保证 Judge **永不直读完整 Research DB / parent history**（上下文工程
  纪律），且 projection 的 `best_verdict` 排序键等必须是确定性规则而不是"取最新一条"。
- 验证：F9 projection/gaps 测试 + Batch7 场景断言（s2 缺必答 → 定向补足；
  s1 已足够 → plans==0，防止"为指标加搜"）。

### 3.4 Query Identity ≠ Dedup Identity

- 问题：判断"这条检索是不是重复"很容易把 query 本身当作去重键，导致同 query 换目标/
  换来源/换时间窗时被误杀，或 retry 被当成新 research。
- 方案：`query_identity = SHA256(规范化 query)`（只表示 query 本身）；`dedup_identity =
  SHA256(query_identity + objective_identity + source_universe_identity + time_window)`；
  canonicalize 防 whitespace/case/tracking 参数绕过；retry 不产生新 identity（attempt
  metadata）；re-verify 独立 namespace。
- 验证：Batch4 dedup 专项测试断言两者不同、防绕过、retry 语义。

### 3.5 行为评估不"自证循环"（deterministic eval + no-pad verdict）

- 问题：评估 adaptive 是否"更好"，若用 adaptive 自己的输出来证明，或允许"相等也算过"，
  评估就失真。
- 方案：确定性 scripted world（query→evidence 路由，`results_for` 显式 vs `results_any`
  兜底），baseline 与 adaptive **各自独立 `Controller.execute`**（独立 governance store /
  research DB / task / run），同 world、同 policy；8 维 rubric，verdict 要求 adaptive 在
  ≥1 个 grounding 维度 **严格 >** baseline 且 total ≥ baseline——**相等即 False（no-pad）**。
- 为什么难：要让"公平起点"成立（adaptive round0 与 baseline 同初始查询），又不为指标
  鼓励加搜（s1 断言 plans==0）。
- 验证：eval 16/16、场景 8/8（Batch7）。

### 3.6 阈值校准作为工程决策过程

- 问题：停止阈值（MIN_EVIDENCE、轮数、无进展容忍度）若有多个合理档位，"选哪个"若靠
  拍脑袋，将来无法辩护。
- 方案：复用 eval harness 做参数敏感性扫描——5 参数 × 2–3 档 × 5 代表场景 =
  **61 观测点**，每点独立 research db + governance；运行期覆盖参数（**不改生产默认值
  文件**），每档结束恢复并断言。
- 结论（如实）：质量/成本对 5 参数不敏感；唯一可测影响 = MIN_EVIDENCE 对停止轮次
  （1→2.6 / 2→3.0 / 3→3.4）；**维持现状默认值、production defaults unchanged**。
- 验证：Batch8 4 个测试（恢复机制 / 结构 / 单调性 / Pareto 判定）。

### 3.7 三层持久化纪律与生产基线选择

- 问题：checkpoint、governance、research 三类数据若混在一个迁移体系里，或把 SQLite 当
  生产结论，会出现"测试过了生产挂"。
- 方案：三平面分离（checkpoint 表族由官方 saver 自管、禁止仓库迁移触碰；TaskRecord +
  GovernanceEvent 走 `db/governance_migrations`；research plane 走 `db/migrations
  0001–0006`）；**PostgreSQL = 唯一生产基线与最终 Gate backend**，SQLite 仅快速测试；
  SQLite PASS ≠ PostgreSQL PASS。
- 验证：每批回归 sqlite + PG 双跑；Batch5 起含真实 PG gate；最终 sqlite 731/88、PG 81。

---

## 4. 关键设计 Trade-off

### 4.1 为什么不做 persistence（round/query-identity 落库）——现在不做

- 现状（事实）：F9 round state / follow-up plan 在 orchestrator 内 **in-memory**
  （Batch6 D3）；research plane 事实（Evidence/Claim/…）本身是持久化的；F8
  TaskRecord/events 也是持久的。**缺的是"F9 编排状态（round 进度）的跨 execution
  载体"**。
- 为什么 defer：F9-P0 的价值主张是"执行 + 行为验证 + 校准闭环"；persistence 若进 P0 会
  触碰 Batch6 D3 冻结边界，需要 schema additive migration + 恢复语义，属于新的 L3 风险
  面；用户决定整体归 **P1**（若实施：research plane additive 表 + migration、零 ALTER
  既有表、首版倾向 write-only audit + 只读查询，恢复留 future）。
- 面试表述：能清晰说出"**编排状态临时化是有意为之的边界**，证据与治理事实是持久的；
  补持久化的正确路径与风险点我已分析清楚（见 Batch9 readiness）"——这是加分项而不是
  减分项。

### 4.2 为什么 deterministic eval 不等于 real provider

- 现状（事实）：Gate 主路径 = deterministic scripted world；**real provider E2E 未执行**
  ——开发/评审环境没有有效凭据，绝不伪造真实模型证据。
- Trade-off：确定性世界可复现、可断言、可进 CI；但 scripted world 覆盖不到真实模型的
  概率性/风格行为，rubric 粒度也是有限近似（如 required 语义惩罚缺失）。
- 诚实表述：这是 supplementary limitation（Batch7 §8 / Batch8 §10 已记录）；凭据环境就绪
  后作为补充证据，而不是替换确定性 Gate。

### 4.3 为什么保持默认参数（不为了"看起来调过参"而改）

- 现状（事实）：61 点扫描显示默认档质量不劣于任何邻档、成本合规；改 MIN_EVIDENCE=1
  仅省 0.4 轮但放宽 required 语义，改 3 增轮且触发 no_progress——**没有严格更优档**。
- 工程意义：参数以实验证据决定、**禁止为过测试/为 diff 临时调参**（D5 冻结纪律）；
  "不改"本身就是校准的结论。

### 4.4 为什么不用 Redis / Kafka / Neo4j / VectorDB / 分布式

- 现状（事实）：单实例 governed execution；事件回放走 task-scoped replay cursor +
  WS since_seq catch-up（够用）；关系型 + 显式约束已满足幂等。
- Trade-off：分布式/图/向量会引入运维与一致性复杂度，而 F9-P0 的核心假设是"单一受治理
  执行体"。multi-instance / lease / heartbeat / orphan reclaim 产品化是文档化的 future，
  不是当前缺口。

### 4.5 为什么做 Eval Harness 而不是只跑通 demo

- 论证（README §8 核心句）：**不是为了证明代码能运行，而是验证 Research Intelligence
  是否改善行为质量**。unverified claim、缺必答、冲突、独立性不足这类问题只有行为级
  场景才能暴露；rubric + no-pad + dual execution 让"adaptive 更优"成为可证伪的断言。

### 4.6 Batch 门控 / Freeze / Git 工作流治理（工程方法 Trade-off）

- 事实：F1–F8 语义冻结不重审；F9-P0 按 Batch1–8 门控（Readiness → 实现 → 验证 →
  User Freeze → Commit/Push/Merge 逐步批准）；main = frozen integration baseline。
- 意义：把"AI 写代码不可控"问题用流程治理缓解——冻结边界、单一 feature branch、
  禁止 rewrite history；面试可讲为"我如何让一个长期 Agent 驱动的系统保持可控"。

---

## 5. 面试高频问题与回答

### Q1. 这个系统和 LangChain / DeepAgents 的官方 demo 有什么区别？

不是换壳。三层都做了超出 demo 的事：(1) Runtime 层自建 F8 执行治理（task ledger、四类
预算、CAS 终态、durable events 回放）——官方 demo 没有；(2) Research Intelligence 层
实现 evidence-driven adaptive loop（projection/gap/judge/plan/targeted/verify/stop）——
demo 是单轮 tool calling；(3) Evidence Plane 把检索结果结构化沉淀并验证（claim
binding/verification/conflict/independence）。而且**行为是被评估和校准过的**，不是
"看起来能跑"。

### Q2. Multi-Agent 在哪里？为什么需要多个 Agent？

一主三从：主 Agent 负责理解任务、规划、调度、汇总；三个子 Agent 是**工具能力边界**
（网络/结构化 DB/私有知识库），各持有自己的 tool 集与 prompt。多 Agent 的价值不是"数量
多"，而是**按信息源隔离能力与上下文**；F9 不新增常驻 Agent 类型（不用 generic
Planner/Verifier Agent），判断能力以确定性与受治理的 LLM 调用形式内嵌在编排里。

### Q3. Agent Runtime 是什么？为什么需要它？

Runtime = 执行环境 + 生命周期管理。对应 F8：每个任务一个 TaskRecord；单一
Controller.execute(policy) 统一裁决 budget（agent_step/llm/tool/search + wall-clock）、
cancel、timeout、终态收敛（CAS 单 winner）；durable GovernanceEvent 可回放。**没有
Runtime，长任务会失控**——预算无法约束、取消不可靠、状态不可审计。

### Q4. Research Intelligence 到底是什么？

把"检索"从一次性动作变成**由证据状态驱动的决策循环**：投影当前证据状态（确定性）→
判断缺口（规则 + 语义）→ 决定是否值得补 → 定向补 → 只验证受影响的声明 → 判断收敛
或继续。一句话：**决定"取什么、够不够、何时停"，而不是"把搜索工具塞给模型随便调"**。

### Q5. 为什么需要 Judge？直接让主 Agent 判断不行吗？

Judge 的输入是**受控投影**（不是完整 DB / parent history），语义冻结为单次结构化
LLM 调用并经 governance callback 计费——它把"语义判断"变成一个**可计费、可失败、
可回退的独立阶段**。Judge 失败 ≠ 任务失败：fallback 到同一执行内 baseline。若混在主
Agent 里，判断不可观测、预算不透明、失败路径不清晰。

### Q6. Evidence / Verification / Conflict / Independence 如何闭环？

检索产出 Source → Evidence；声明（Claim）与证据绑定（Citation）；新证据只对受影响的
claim 做增量语义验证（F3，verdict 五态，append-only 取最新 succeeded）；冲突集合变化才
增量跑 F4（conflict）→ F5（corroboration，只计独立性）→ F6（reconciliation）。
**任何最终结论都能反查到原始来源**（provenance 链）。

### Q7. 系统怎么知道"什么时候该停"？

三层：(1) F8 硬上限（budget/timeout/cancel）永远先触发；(2) semantic stop 是 LLM 建议；
(3) deterministic policy 终裁：必答覆盖达标 AND 无未缓解 critical conflict AND claims
支持充分 → STOP。还有收敛守卫：max_research_rounds / no-progress / repeated-gap /
diminishing-return。**budget 是 ceiling 不是 target**——"还有预算"不是继续搜的理由。

### Q8. 如何保证 Agent 行为可靠（而不是"看着对"）？

三点：(1) 关键路径确定性优先（projection、gap、去重、停止终裁都是代码规则）；
(2) 语义阶段受治理（Judge/Plan 显式计费、失败回退 baseline、不伪造 verdict）；
(3) 行为被测试：8 个 deterministic 场景 + no-pad rubric + 治理对抗测试 + 61 点校准。

### Q9. 如果 Judge 或 Plan 失败了怎么办？

fail-open = **open to baseline，不是 open to degraded adaptive**：同一 F8 governed
execution 内 fallback baseline research/synthesis；不新建 task/run、不重置 counters、
F7 仍恰一次。Projection 失败同理。Verification 失败则对应 claim 标 UNVERIFIABLE，
不伪造。

### Q10. 评估怎么做的？为什么可信？

确定性 world + dual execution（baseline vs adaptive 各自独立 execute）+ 8 维 rubric +
no-pad verdict（相等即 False）。8 个场景覆盖"已足够不加搜 / 缺必答定向补 / 声明重验 /
冲突 / 独立性 / 收敛 / 预算临近 / Judge 失败回退"。16/16 测试、8/8 场景 PASS。
**诚实边界**：world 是 scripted，非真实模型语料——real-provider E2E 因无凭据环境
未纳入 Gate（deferred）。

### Q11. Calibration 具体怎么做的？结论是什么？

复用 eval harness：5 停止参数 × 2–3 档 × 5 代表场景 = 61 观测点，运行期覆盖参数（不改
生产默认值文件）。结论：质量/成本不敏感；MIN_EVIDENCE 影响停止轮次；**维持默认值**。
意义：阈值是实验决策；"不改"也是结论，防止为 diff 调参。

### Q12. 为什么 PostgreSQL 是"唯一生产基线"？SQLite 不行吗？

SQLite 只做快速测试；CAS/race/持久化/replay 语义必须用真实 PG 验证（并发唯一约束、
事务、回放游标等行为 SQLite 覆盖不到）。规则：SQLite PASS ≠ PostgreSQL PASS；PG 全量
81 测试独立为最终 Gate。

### Q13. 有什么你没做、为什么？（主动暴露边界）

四块 deferred：(1) persistence——F9 编排状态 in-memory（证据/治理事实本身持久），跨
execution 续算属 P1，需 additive migration；(2) runtime recovery / orphan reclaim——
F8 文档化 future；(3) real-provider E2E——无凭据环境，确定性 Gate 为准，不伪造；
(4) production hardening——multi-instance、sweeper 产品化等。**能精确说出"没做什么与
为什么"比含糊宣称完整更可信。**

### Q14. 如果给你一周继续做，你优先做什么？为什么？

首选 P1 persistence（round/query-identity 落库）：它是 Spec §15 In 唯一未实现项、直接
消除"编排状态 in-memory"这一已知边界，并为跨执行审计/续算打基础；路径已分析清楚
（additive 表 + 双后端 migration + 零 ALTER，write-only audit 起步），实现前需决策
closure（L3）。次选：把 eval world 扩展到更多场景语料；或凭据就绪后补 real-provider
补充证据。

### Q15. 这套系统最难的工程问题是什么？（追问引导）

诚实回答建议：**"在单一受治理执行体内把确定性规则、受治理的 LLM 语义判断、多轮检索、
增量验证与恰一次 finalize 拧成一个不变量，并用对抗测试锁住"**——因为任何一层越界
（新建 runtime、裸调模型、重复 F7、round 里 reset 预算）都会静默破坏可治理性；这类
bug 不是"功能错"，而是"治理被绕过"，最容易被 demo 掩盖。

### Q16. 数据/知识这块怎么治理？（若问 RAG/知识库）

私有知识走 RAGFlow（独立子 Agent），结构化走 MySQL，公开走 Tavily；上传文件由主 Agent
读取。Research Plane 会做 URL host 归一化与 domain 计数，但**明确不做 authority/
credibility 判断**（不偷渡 F5/F6 之外语义）——知识可信度模型有意识地被限制在
verification/independence 之内。

### Q17. 会不会无限搜？成本怎么控？

不会：确定性停止终裁 + 轮数/无进展/重复 gap/收益递减守卫；F8 四类预算 + wall-clock 是
硬上限且无 overshoot；budget 是 ceiling 不是 target。成本合规是 eval rubric 一维并有
s7（near-budget）场景断言 counters ≤ limits。

### Q18. 代码量/测试规模？（便于量化）

Backend：FastAPI + governance + research plane + F9 orchestration/eval；前端 React；
测试 67 个 pytest 文件族，sqlite 全量 731 passed / 88 skipped / 0 failed，PG 81
passed；eval 16 + 场景 8 + 校准 61 观测点；每批 ruff / compileall / diff-check PASS。
（数字以报告为准，可查 `PROJECT_CONTEXT.md` §9。）

---

## 6. 简历项目描述版本

### 6.1 主版本（约 200 字，可作项目栏正文）

**深度研搜 — Evidence-Grounded 多智能体研究系统（Python / FastAPI / LangGraph /
DeepAgents / PostgreSQL）**

独立设计并实现面向深度研究任务的多智能体系统：DeepAgents 一主三从（网络搜索 / MySQL
结构化数据 / RAGFlow 私有知识库）完成多源检索与 Markdown/PDF 交付；自研 F8 执行治理层
（单一 Controller.execute(policy) 管理任务生命周期、四类运行预算、超时/取消与 CAS 终态，
durable event 可回放）；在其上实现 F9-P0 evidence-driven 自适应研究闭环——确定性证据
投影与缺口检测 + 受治理语义 Judge + 定向补搜 + 增量声明验证（F3 verdict 五态 / F4
冲突 / F5 独立性 / F6 归并）+ 确定性收敛停止，最终 F7 finalize 恰一次。以确定性
baseline-vs-adaptive 行为评估（8 场景 8 维 rubric，no-pad）与 61 观测点阈值校准证明
行为质量。PostgreSQL 为唯一生产基线（SQLite 仅测试）。回归 sqlite 731 passed / 88
skipped / 0 failed、PG 81 passed。F9-P0 Core 已冻结宣告 COMPLETE。

### 6.2 要点子弹（简历条目 / 作品集）

- **架构**：Runtime(治理) / Agent(执行) / Research Intelligence(编排) / Evidence
  Plane(事实) 四层分离；单一受治理 execution 贯穿多轮研究。
- **Agent**：DeepAgents 一主三从 + Tavily/MySQL/RAGFlow/文件工具；baseline primitive
  与 adaptive 同执行体可回退。
- **治理**：F8 持久化 TaskRecord、预算 counters（agent_step/llm/tool/search + 时限）、
  取消/超时、CAS 单 winner 终态、GovernanceEvent 回放。
- **研究智能**：projection → 确定性 gap → 语义 Judge（governed 计费）→ validated
  plan + dedup（query identity ≠ dedup identity）→ targeted research → 增量验证 →
  deterministic stopping。
- **可追溯**：ResearchRun → … → Claim/Verification/Conflict/Independence → F7
  finalize_run 恰一次；任何结论可反查来源。
- **评估与校准**：确定性 world 双执行 + 8 维 rubric（no-pad）；8/8 场景、16/16 测试；
  61 观测点校准 → 维持默认值（production defaults unchanged）。
- **质量**：sqlite 731/88/0、PG 81/0、ruff/compileall/diff-check 全绿、Compatibility
  Gap = None；F9-P0 Core COMPLETE（Batch1–8 FROZEN/MERGED）。
- **边界（如实）**：编排状态持久化/跨执行恢复/真实 provider E2E = P1 deferred（有明确
  实施路径与风险分析，未计作已完成）。

### 6.3 一句话总结（适合 HR/首屏）

「把『LLM + Tools 单轮问答』做成了『可治理、可验证、可追溯、行为经过评估与校准的
多智能体深度研究 Runtime』——不是又一个 RAG demo。」

---

> 以上内容全部基于当前已冻结实现与文档；未包含任何未实现能力。
> 如需 1 分钟口述录音稿 / 5 分钟白板稿的进一步打磨、或与 `面经-深度研搜.md` /
> `导学-深度研搜.md` 的对齐，另行指示。
