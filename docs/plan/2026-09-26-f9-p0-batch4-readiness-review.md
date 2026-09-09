# F9-P0 Batch 4 — Follow-up Plan + Validation + Dedup：Implementation Readiness Review

> 状态：**READINESS REVIEW（read-only）→ 结论 CONDITIONAL（见 §14）**。
> 范围：只评估 Batch 4 是否 Implementation Ready；不实现 Batch 4；不修改任何代码/测试/文档
> （除本报告）。FROZEN：Batch 1–3 PASS/FROZEN；Batch 4 = PENDING → NOW START（本指令 = 只做
> Readiness 第一阶段）。

---

## 1. Scope

- 允许：read-only inspection、contract/compat 分析、风险清单、测试/裁决建议、本报告。
- 禁止：修改 `app/f9/*`、F8、F1–F7、Batch 1–3、tests、migration、dependency、API/frontend、
  新 Agent/Runtime/Controller、Batch 4 implementation。

## 2. Source Documents（按 Loading Policy 选择性读取，未扫描历史）

- F9-P0 Spec Rev2（2026-09-19）：§5/§6/§6.1/§7/§9/§12/§13/§17
- F9-P0 Plan Rev2（2026-09-20）：§0 D4/§G/§R4/§D（budget 表）/§A/§J/§I Batch4
- Batch 1–3 reports + Batch3 Freeze（PROJECT_CONTEXT 一致确认）
- 代码：`app/f9/{projection,gaps,judge}.py`、`app/runtime/governance/{controller,context,
  counters,callbacks}.py`、`app/research/registry.py`（record_search_query/0001 schema）、
  `app/research/normalize.py`、`db/migrations/0001_*.sql`、tests helpers

## 3. Current Frozen Inputs

- Batch 1 Projection：10 键（claims/sub_questions/required_uncovered/budget…）
- Batch 2 GapSignals：9 类 rule signal，每项 `{type,rule,subject_type,subject_id,reason,detail}`；
  稳定 gap identity = `type|subject_type|subject_id`（judge.py `gap_id_of`，Batch3 report §12.2）
- Batch 3 JudgeResult（A1 冻结）：`{round, judged, judgments[{gap_id,important,reason,priority,
  semantic_need}]}`；**不含任何 query/objective**；语义需要仅以 `semantic_need` 表达
- F8：单 `Controller.execute(task_id, coroutine, policy)`；judge 计费模式已证
  （ctx.make_handler + ainvoke callbacks；unclassified → +1 llm / +0 agent_step）

## 4. Batch 4 Responsibility Boundary

职责：Batch2 gaps + Batch3 judgments（+ 必要时 projection facts）→ **Follow-up Plan →
deterministic validation → dedup** → "validated executable research intent"（结构性、非执行）。

必须避免（当前代码/契约检查后确认无越界倾向）：不重做 semantic judgment；不是 Targeted
Research / Verification executor；不是 Orchestrator；不是 F8 Runtime Controller；不创建新 Agent。
Plan 属**研究编排数据结构**，由未来 orchestrator（Batch 6）在单次 `Controller.execute` 内驱动。

边界确认：Spec §6 的 Follow-up Plan Contract = `plan_id / target_sub_question_id / objective /
queries[] / priority / verification_target_claim_ids / stop_condition / why`（结构化入 Research
State）——这是 Batch 4 输出契约（用户 A1 已把 query/objective 从 Judge 移入 Plan 层）。

## 5. Query Identity Review

Plan D4/R4 已冻结（不重发明）：

```text
query_identity       = SHA256(canonicalize(query))           # 只表示 query 本身
objective_identity   = SHA256(target_sub_question_id + normalize(objective))
source_universe_identity = SHA256(provider + 库标识 + 配置指纹)
time_window_identity = 显式窗口；缺省 sentinel "NONE"（不依赖当前时间）
dedup_identity       = SHA256(query_identity + objective_identity +
                              source_universe_identity + time_window_identity)   # ≠ query_identity
canonicalize: 空白→单空格 strip；大小写 lower（URL host 按 URL 规则保留原义）；
              URL 去跟踪参数并按参数排序；re_verify 独立 namespace
```

代码可复用：`app/research/normalize.py`（canonicalize_url / strip_tracking_params / query 排序）
对 URL 型 query 可直接复用；纯文本 query 需新 canonicalize（见 §12 P4）。
实现歧义点（不自行裁决，见 §12）：objective 文本的"normalize"与 target 编码、source universe
的"provider+库标识+配置指纹"具体取值、显式 time_window 的表示格式。

## 6. Dedup / Retry / Re-query / Re-verify Semantics

四者不可混（审查结论，符合 §6.1/Plan R4 冻结语义）：

| 情形 | identity | 处理 |
|---|---|---|
| 相同 dedup_identity 的普通重复计划 | 不变 | **duplicate → 拒绝**（若上次成功产生 source） |
| 同一 identity 因执行失败再试 | **不变** | **retry**：attempt/retry metadata；仅当上次失败且无新 source |
| 需要重新验证已有证据 | 独立 namespace | **re_verify**：不参与 research_query 的 dedup |
| 真正改变 objective/source universe/time window/query | 新 identity | 新计划（非 duplicate） |

Batch 2/3 均不产生 query → 本批首次引入 query/plan 记录；"上次执行成功/失败"需 ledger 或
DB 事实（见 §11 持久化歧义 P1）。

## 7. Plan Validation Review

确定性 validation 边界（fail-closed，对齐 §12 "Plan validation failure → 拒绝 plan；无 plan →
fallback baseline"）：

- gap/semantic-judgment 引用 ⊆ 本 run 的 Batch2 signals 与 Batch3 judgments（composite gap_id
  必须一致；unknown/missing/cross-run → 拒绝）——复用 Batch3 的引用校验模式；
- 不允许越界字段（仅 Plan Contract 字段；与 Judge 相同的未知字段 fail-closed 纪律）；
- query/objective/source_universe/time_window 长度与结构 bounded（常量上限，report 记录）；
- 不要求每个 gap 都有 plan（部分 gap 可 judged 不重要）；但每个 plan 必须锚定 ≥1 有效 gap；
- 非法/partial plan **绝不静默进入执行**；无效 → 丢弃 →（调用方）baseline fallback。

## 8. Batch 3 → Batch 4 Seam

- Batch3 输出（gap_id/important/reason/priority/semantic_need）可直接作 Batch4 输入：
  `semantic_need` = **planning input（决定 objective/目标）**，不是 executable query；
- 不重做 semantic judgment；只把 important==True（或按 priority 排序后的）gap 转成候选计划；
- gap_id 一致性：Batch3 judgments 的 gap_id 即 Batch2 派生 identity（judge.py `gap_id_of`），
  同源同值——已验证（代码同函数、同输入派生）；
- unknown/missing/cross-run reference → validation 拒绝（Batch3 模式复用）。

## 9. F8 Governance Seam

- Batch 4 是 orchestration 数据结构 + 纯函数 +（若含）plan-proposal LLM 调用；生命周期仍完全
  归现有 Controller：不创建第二 Controller/BudgetCounter/Task/Runtime/watchdog/cancel/deadline/
  execution budget（§13 风险 Q13：✅ 全部仍由 Controller 管理）。
- 若 Batch4 含 plan-proposal LLM（Plan §A "LLM 提议 → deterministic validation"），调用必须走
  同一 `ctx.make_handler()` + `ainvoke(config={"callbacks":[handler]})`（judge 已验证模式）；
  其计费行在 Plan §D budget 表中**缺失**（无 "plan proposal" 行）→ 歧义 P2。

## 10. Determinism Review

- identity：全部 SHA256 over canonical 字符串 → 确定性、跨后端一致；与 SQLite/PG 无关；
- ordering：plan/candidate 排序用显式键（priority 次序 + 输入顺序 + id），**不用 LLM 输出顺序**
  作最终依据；dict/list 一律显式排序；
- 不使用 wall clock（time_window 只能经显式输入或 `NONE` sentinel）；无随机（plan_id 建议
  fingerprint/SHA 派生，UUID 若用需决策）；
- 若读取 Research DB 历史（dedup ledger 由 DB 提供时）：SELECT 显式排序 + 与 Projection 相同
  的双后端类型归一纪律 → SQLite/PG 语义一致（Batch1 已立范式）；
- 结论：可保持 deterministic（受 §12 歧义约束）。

## 11. Persistence Review

- Spec §13 说 idempotency "以 PostgreSQL 唯一约束 + F8 事务实现"（plan/query identity 持久、
  可重放）；Spec §17 Open Q5 说 projection/round-state 持久化为 **additive Gate 裁决、不承诺**；
  两者并存 → **表层张力（不自行裁决）**。
- 现状事实（实读代码）：`search_queries` 无 objective/source_universe/time_window/dedup_identity
  列、无成功标记（成功 = 该 query 有 linked sources 可推断）；objective 文本任何地方未持久化
  → **跨 execution/跨 crash 的 dedup 需 additive schema（research-plane 表/列），属未来 Gate**。
- 最小闭合建议（P1）：Batch 4 采用 **in-run in-memory ledger**（run 内多轮去重/retry 判定，
  由 Batch6 orchestrator 持有一个 execution 周期），**不新增 table/migration/research_rounds**；
  跨 execution 持久化去重并入 Spec §17 Q5 additive Gate（用户裁决后另立批次）。

## 12. Risks / Ambiguities

需用户裁决（Conditional 依据；每个附影响 + 最小闭合建议）：

| # | 裁决项 | 影响 | 最小闭合建议 |
|---|---|---|---|
| P1 | dedup/plan ledger 范围 | 是否需要 DB/新 schema | **in-run in-memory ledger**（本批不建表）；跨 execution 持久化并 additive Gate（Spec Q5） |
| P2 | plan-proposal LLM 的 budget 行与失败归口 | Plan §D 无此行；§12 仅"validation 失败" | 计费 = judge 同模式（+1 llm/+0 step，显式 callback）；proposal LLM 失败/无合法 plan → 同 execute baseline fallback（R2-1 类推） |
| P3 | objective / source_universe / time_window 的具体 representation | identity 稳定性与跨模块一致 | objective = normalized text(≤N)+target sq_id 编码；source_universe = SHA256(tool+agent+固定 CONFIG_VERSION，不含 secret/env 基址)；time_window = `NONE` 或显式 `YYYY-MM-DD/YYYY-MM-DD` |
| P4 | canonicalize_query 对"含 URL 的 query 文本"规则 | 与 Plan R4 "URL host 保留原义"一致 | 解析为 URL（scheme 存在）→ 复用 normalize.canonicalize_url；否则全文本 lowercase+单空格 strip |
| P5 | 哪些 judged gap 进入 plan 候选（important 过滤/priority 排序；low 是否留档） | plan 生成语义 | 候选 = important==True 的 gap，按 priority(high>medium>low) + 输入序确定性排序；validation 后数量/query budget 上限常量（Batch8 校准） |
| P6 | plan_id 生成 | 幂等 identity | 建议 deterministic fingerprint（SHA256 over plan 内容/候选 tuple）；UUID 若采用需裁决 |

非阻塞事实（记录）：gap_id 为派生 identity（Batch2 FROZEN 无独立列，Batch3 report §12.2 同因）。

## 13. Blocking / Non-blocking Issues

- **无代码级 blocking**：Batch 1–3 / F8 / F1–F7 public API 均可复用；无"需改冻结契约才能适配"
  之处（§4/§5/§7/§9/§10）。
- **Contract 级 CONDITIONAL（需裁决）**：P1–P6（§12）。其中 P1（持久化/dedup 范围）与
  P2（plan-proposal LLM 计费+失败归口）影响实现边界最深；P3/P4 影响 identity 一致性；
  P5/P6 属结构性小决策。
- 未发现 Spec/Plan 与现有 frozen implementation 的**阻塞性冲突**（H 的冲突检查项结果：
  §13 与 §17 Q5 的持久化张力 = 需要用户裁决的 ambiguity，不是已实现代码冲突）。

## 14. Final Readiness Decision

```text
BATCH 4 READINESS: CONDITIONAL
```

- 结构条件全部满足（可复用 Batch1–3 契约与 F8 seam，无需改动冻结代码）；
- 实现前需用户裁决 P1–P6（§12）；最小闭合建议已给出，**不自行修复/不自动开始实现**；
- 若裁决采纳建议，Batch 4 范围 = `app/f9/plan*.py`（identity/validation/dedup 纯函数 +
  plan-proposal LLM 显式 callback 模式）+ 单元/回归测试 + report，无 schema/migration、
  无第二 Runtime 控制面。

**STOP。** 未实现 Batch 4；等待用户对 P1–P6 的裁决与 Batch 4 Implementation 指令。
