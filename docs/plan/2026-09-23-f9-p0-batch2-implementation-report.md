# F9-P0 Batch 2 — Deterministic Gap Detection Implementation Report

> 状态：**BATCH 2 PASS / FROZEN**（用户 2026-09-24 正式 Freeze；本批只交付 Deterministic Gap
> Detection；不宣布 F9-P0 COMPLETE）。
> 依据：F9-P0 Rev2 Spec（2026-09-19 §5/§3.1）＋ Implementation Plan Rev2（2026-09-20 §F/D5）。
> 上游：F9-P0 Batch 1 Research State Projection（**PASS / FROZEN**，用户 2026-09-22 Freeze）。
> 纪律：只做规则判定、无 LLM/语义、只读 F8 snapshot、不修改 F1–F8、不实现 Batch 3+。

## 1. Scope

In：把 Batch 1 Projection 输出（Research State facts + current_round + F8 budget snapshot）
经**显式规则**转换为 Deterministic Gap Signals；纯函数；稳定排序；bounded；no side effect。

Out（禁止）：LLM / Semantic Judge / 自然语言语义 / 新 Agent / Planner / Verification Agent /
Targeted Research / Follow-up Plan / Query generation / 新 Runtime·Controller·BudgetCounter /
修改 F8 / F1–F8 schema 变更 / F7 finalization / Orchestrator / 自动下一轮研究 /
Batch 3–8 任何实现。

## 2. Implementation

- 新增 `app/f9/gaps.py`：`detect_gaps(projection: dict) -> dict`（纯函数，唯一入口）。
  - 校验 projection 为 Batch 1 契约对象（10 个顶层键 + budget 三子键）；违例 → `GapDetectionError`
    （显式失败，不静默猜测；供后续 orchestrator 按 F9 failure semantics 处理）。
  - 逐规则生成信号 → 全量排序 `(type, subject_type, subject_id)` → 截断至 `MAX_SIGNALS=256`
    （honest：`total_signals` = 截断前总数、`truncated` 布尔、`counts` = 已输出计数）。
  - 输出：`{round, signals[], counts{}, total_signals, truncated}`。
  - 无 DB import / 无 store 参数 / 无 transaction / 无 LLM / 无时钟（fresh 在 Batch 1 注入，
    Batch 2 不再取时间）→ 全纯、确定性、零副作用。
- `projection.py`（FROZEN）**零改动**；`__init__.py` 零改动。
- 常量（D5 初始值，Eval 校准前，模块顶层可测）：`MIN_EVIDENCE=2`、
  `BUDGET_NEAR_RATIO=0.2`、`MAX_SIGNALS=256`。

## 3. Deterministic Signal Contract

每条：Input Facts → Explicit Rule(rule id) → Signal；无"感觉有缺口"路径。

| type | rule | 输入事实（Batch1 字段） | 规则 | 边界/空·null 行为 |
|---|---|---|---|---|
| `required_uncovered` | RU1 | `sub_questions[].required`、`required_uncovered[]` | 必答子问题无 evidence 且无 source → 信号（缺失覆盖集） | 逐 id 一条；不在 required 集合内的 id 不发 |
| `required_low_evidence` | RE1 | required sq `evidence_count` | 0<evidence_count<MIN_EVIDENCE → 信号（sufficiency 用 gap 表达，Spec §3.1 注） | ==0 归 RU1（不重复）；≥MIN 不发；非 required 不发 |
| `claim_no_evidence` | CE1 | claim `evidence_refs` | len==0 → 信号 | 命中后跳过该 claim 其余 claim 级信号 |
| `claim_unverified` | CV1 | claim `evidence_refs`、`best_verdict` | 有 evidence 且 best_verdict is None（无 succeeded verification） | 无 evidence 由 CE1 覆盖 |
| `claim_verdict_insufficient` | CV2 | claim `best_verdict` | best_verdict ∈ {INSUFFICIENT, UNVERIFIABLE} → 信号 | ABSTAIN/CONTRADICTS/SUPPORTS 不发（Spec §5 只列两类；见 §9） |
| `unresolved_conflict` | CF1 | claim `conflicts[]`（Batch1 已排除缓解项） | 未缓解 confirmed conflict >0 → 信号（detail=conflict_ids+count） | "critical"无 Spec 定义 → 不设 severity |
| `independent_sources_insufficient` | IS1 | claim `independent_flag` | 有 evidence 且 flag==False（F5 已算 <2 独立簇） | None=未计算 → **不发**（不把 not-computed 当结论）；True 不发 |
| `stale_evidence` | FR1 | claim `fresh` | fresh==False（最新 bound evidence 超 FRESHNESS_WINDOW）→ 信号 | None（无 evidence）由 CE1 覆盖 |
| `budget_near` | BU1 | `budget.remaining/limits/counts` | remaining==0 或 limit>0 且 remaining/limit ≤ BUDGET_NEAR_RATIO → 每 kind 一条 | limit=0 → remaining=0 → near（确定性）；snapshot 只读 |

## 4. Batch 1 Interface Usage

- 唯一输入为 `project()` 输出 dict（FROZEN 契约，未改）。
- `round` 与 budget snapshot 在 Batch 1 `project(run_id, round=current_round, budget=snapshot)`
  已绑定 → Batch 2 直接透传 `round`、读取 `budget`（不设第二参数入口，避免双来源/双 counter
  风险）；若 orchestrator 需要更新快照，语义是先重新 project 再 detect（Batch 6 裁决）。
- 消费字段清单见 §3 表；不使用 projection 的 evidence 正文/statement（无内容堆叠）。

## 5. F8 Budget Boundary

- `detect_gaps` 只读取 `projection["budget"]`（= F8 `BudgetCounter.to_dict()` 只读快照内容
  counts/limits/remaining）；模块内无 BudgetCounter、无 acquire、无写。
- 测试：真实 `BudgetCounter`（sqlite 整链）acquire ×N → `to_dict()` 前后相等、`detect`×2 相等；
  PG 上 snapshot dict 未变 + 行数零写。
- F9 round 仅透传输出，不是 runtime execution；未创建 task/controller/watchdog/cancel domain。

## 6. Tests

- `tests/test_f9_gaps.py`（sqlite/纯函数，29）：空/null 行为、契约违例、RU1/RE1/CE1/CV1/CV2/
  CF1/IS1/FR1/BU1 逐信号边界与"不误报"（ABSTAIN/None 语义）、多信号单 claim、budget_near
  阈值（含边界 0.2/耗尽/0 limit）、snapshot 不可变、确定性（重复+乱序构造）、bounded
  （monkeypatch MAX_SIGNALS=5 → truncated/total/kept）、整链 pipeline（空 run→RU1；
  DB 行数零写；真实 BudgetCounter 只读）。
- `tests/test_f9_gaps_postgres.py`（PG gate，RESEARCH_DSN_TEST 门控，5）：PG vs sqlite
  rich scenario 整链 detect 逐字节相等；reverse 插入序相等；required coverage 语义链
  （RU1→RE1→无信号）；claim 信号边界（IS1/CF1、failed 不参与 verdict）；budget_near 只读
  + PG 零写。

## 7. Cross-DB Evidence

- Batch 2 不直接访问 research DB；跨 DB 语义一致 = Projection 跨 DB 一致（Batch 1 已证）
  ⇒ detect 输出一致。PG gate：同 id/时间戳 state 在真实 PG16 与 sqlite 上
  `project→detect` 输出 `==` 且 `json.dumps(sort_keys)` 相等（5 tests，均真跑 PG，非 skip）。
- sqlite 全量回归 621 passed / 85 skipped / 0 failed；PG 双 DSN 回归（research F1–F7 +
  governance + F9 projection + F9 gaps）74 passed / 0 failed（governance 表族先 reset）。

## 8. AC → Test → Evidence

| Acceptance Criterion | Implementation | Test | Evidence |
|---|---|---|---|
| 每个 signal 有 Input→Rule→Signal | gaps.py 规则函数 + rule id | 逐信号边界测试 | sqlite 29 passed；PG 5 passed |
| Deterministic（同 state → 同输出） | 纯函数+排序 | test_repeat_identical / stable_order / PG 等价 | 双库输出逐字节相等 |
| Bounded + 稳定排序 | MAX_SIGNALS 截断 + (type,subject_type,subject_id) 排序 | truncation/order 测试 | truncated/total 断言 |
| Budget 只读、不维护 counter | 无 counter、只读 snapshot | 真实 BudgetCounter / snapshot 不可变测试 | sqlite+PG 断言 |
| 空/null projection 语义明确 | 空→空信号；契约违例→GapDetectionError | test_empty…/violation | 29 项内断言 |
| 无副作用 | 无 DB 访问/写、无时钟 | DB 行数前后相等测试 | sqlite+PG 断言 |
| 不越 Batch 2 scope | 无 LLM/Agent/Runtime/迁移/修改 F1–F8 | 代码审查 + git diff | §11 |

## 9. Known Limitations

1. **Compatibility gap（FROZEN Batch 1 契约外，非本批缺陷）**：Spec §5 的
   "citation coverage 不足" 与 "query/source 重复率过高" 所需事实（citation 计数、query
   去重统计）不在 Batch 1 Projection 契约内 → 本批**不实现、不伪造**（not-computed ≠
   no-gap）。建议：Batch 4 提供 query dedup 事实后补"重复率"信号；citation 覆盖需要 additive
   projection 扩展（需用户裁决，不解冻 Batch 1）。
2. verdict 直读仅将 INSUFFICIENT/UNVERIFIABLE 判为结构性不足（Spec §5 列举集）；
   ABSTAIN（低置信）与单侧 CONTRADICTS 不产生信号——语义边界留给 Judge/Batch3（避免越界
   扩展协议）。已在 CV2 测试锁定该边界。
3. D5 thresholds（MIN_EVIDENCE=2 / BUDGET_NEAR_RATIO=0.2 / MAX_SIGNALS=256）为初始常量，
   Eval（Batch 8）校准前不视为冻结值；实现为模块顶层常量，变更只影响行为、不动契约。
4. `budget_near` 在 limit=0 时恒 near（remaining=0 的确定性表述）。
5. 信号去重为 projection 粒度（每 claim/sq/kind 至多一条）；同一 subject 多类型信号共存
   （如实列举，非错误）。

## 10. Scope Audit

- 新增文件：`app/f9/gaps.py`、`tests/test_f9_gaps.py`、`tests/test_f9_gaps_postgres.py`、本报告。
- 未修改：`projection.py`/`__init__.py`（Batch 1 FROZEN）、`app/runtime/governance/*`、
  任何 F1–F8 文件/schema/迁移、任何 Agent/Runtime/API/前端/依赖/测试行为。
- 无 LLM/无新 Agent/无新 Runtime/无 schema migration/无 Batch 3+ 实现。
- git 差异仅新增 4 文件（含本报告）＋先前任务既有未提交产物（未触碰）。

## 11. Review Findings

- Adversarial 自问（TESTING §8 相关项）：空/畸形输入 → GapDetectionError 或空信号（测试）；
  无 evidence claim → 只 CE1（不叠加）；F5 未计算 → 不误报 IS1；failed verification →
  不参与 best_verdict（无 CV1/CV2 误报）；budget snapshot 恶意超限/缺键 → 契约校验拒绝；
  Projection 超 Batch1 契约 → 显式异常不静默；无副作用（DB 行数不变、snapshot 不变）。
- 未发现需要修改 Batch 1 契约或 F8 的问题；发现 1 个 compatibility gap（§9.1，如实报告，
  未自行解冻 Batch 1）。

## 12. Final Decision

```text
BATCH 2 PASS / FROZEN（2026-09-24 用户正式 Freeze）
```

实现与验证证据保留，无额外重构；compatibility gap（§9.1）保持现状并保留记录。本批未宣布
F9-P0 IMPLEMENTATION COMPLETE；未进入 Batch 3–8。等待用户是否下达 Batch 3（不自动开始）。
