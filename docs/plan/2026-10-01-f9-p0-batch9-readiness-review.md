# F9-P0 Batch 9 — Readiness Review + Decision Closure

> 状态：**READINESS REVIEW + DECISION CLOSURE（read-only）→ 结论 CONDITIONAL（见 §10）→
> D-A 已裁决（见 §11 Decision Resolution，2026-10-01 用户决定：停批收尾、persistence 归 P1）**。
> 依据：用户 2026-10-01 指令（Batch8 完整 Git 生命周期后评估 Batch9；只做 Readiness /
> Decision Closure；不实现）。基线：main = `be6d625`（Batch8 merged；本地=远程同步；
> Batch1–8 全部 PASS/FROZEN）。
> Git Workflow：本阶段不创建 feature branch、不 Commit/Push/Merge。
> 日期：2026-10-01。

---

## 1. F9-P0 当前能力盘点（Batch1–8 后）

| 能力（Spec §15 In） | 状态 | 批 |
|---|---|---|
| projection（只读 research state） | ✅ | B1 |
| deterministic gap signals | ✅ | B2 |
| single judge LLM call（显式 callback） | ✅ | B3 |
| validated follow-up plan + dedup | ✅ | B4 |
| targeted research（复用 subagents） | ✅ | B5 |
| 增量 re-verification（F3） | ✅ | B5 |
| semantic+deterministic stopping | ✅ | B6 |
| 结构化 why | ✅ | B4 |
| orchestrator（round0→adaptive→fallback→synthesis→F7 once） | ✅ | B6 |
| Eval harness（deterministic scripted） | ✅ | B7 |
| stopping/threshold calibration | ✅ | B8 |
| **round state / query identity 持久化（research plane）** | ❌ 未实现（B6 D3 in-memory；B8 明确排除为后续候选） | — |

另：`_govadapt.py`（real-provider adapter）= DEFERRED/environment-limited（明确不进 Gate）；
Spec OQ4 unresolved-gaps 在最终答案可见性 = 未裁决（F7 边界 feature）。

## 2. Batch9 Candidate（优先级比较）

候选 A：**Round/Query-Identity Persistence（research plane，schema additive）**
- 价值：D3 冻结 round in-memory 的恢复/可审计缺口；跨 execute/crash 续算与 eval 复核基础；
  Spec §15 In 列项；最自然"补齐 F9-P0 剩余 scope 唯一实现项"。
- 代价：schema additive + migration + persistence/recovery 语义 → 高风险（触碰 Batch6 D3
  边界、projection/plan dedup 读取面）。

候选 B：**F9-P0 Completion / Final Gate（宣告 IMPLEMENTATION COMPLETE）**
- 价值：收尾文档/审计（无生产代码或极小）；正式关闭 F9-P0 阶段。
- 代价：近乎纯流程；若此时宣告而 persistence 未做，与 Spec §15 In 不完全一致 → 需明确
  "F9-P0 core COMPLETE；persistence 属 P1/future"。

候选 C：real-provider supplementary E2E（无凭据环境限制）
- 价值：补 Batch7 Gate 的真实 provider 佐证；但受凭据硬限制 → 非可自主 Gate。

候选 D：unresolved-gaps 最终答案可见性（F7 边界）
- 价值：产品可见性；但需 F7/输出边界裁决 → 独立 feature，非主干。

**优先级判断**：最自然、最有价值的下一批是 **候选 A（round/query-identity persistence）**
——它是 Spec §15 In 唯一未实现大项、与 D3 已知限制直接对应、并支撑恢复与更可靠 eval。
候选 B 作为 F9-P0 收尾可后置或并入 A 完成后的 Final Gate。C/D 不构成主干。

**诚实审视（用户要求）**：是否真的需要 Batch9？——F9-P0 core（研究执行 + deterministic
eval + calibration）已完整且可运行。Persistence 是 **spec 声明的 scope 项** 但已被多轮
Readiness 明确推迟为"future independent candidate"，且其实现会触碰 D3 冻结边界（需 L3 路径）。
因此 Batch9 若做 = **L3 级、schema additive、明确边界**；若用户认为 F9-P0 core 已达成、
persistence 不阻塞核心价值 → 亦可宣布 F9-P0 core COMPLETE 并停批。本 Readiness 将两者列为
显式选项（见 §10 Verdict 与 §9 Decisions）。

## 3. 为什么 A 优先于其它

- 唯一未实现的 Spec §15 In 项（其余全部 ✅）；
- 直接消解 Batch6 D3 "round in-memory，恢复能力留未来" 这一唯一显式 known limitation
  （与 glue duplication、_govadapt 等"已知不修"项性质不同——D3 limitation 是"留未来"而非
  "不修"）；
- 为 future 的 crash-recovery / cross-execution eval / 审计提供 research-plane 事实基础；
- B/C/D 要么是流程收尾、要么受凭据/边界限制，不构成同等级的实现缺口。

## 4. 已被覆盖、不应重复建设（用户 Q4）

- F7-once / cancel/timeout/budget adversarial → Batch6 matrix 已覆盖；
- F4/F5/F6 claim 语义 → F3–F6 模块测试 + Batch6 F456 changed-only 已覆盖；
- deterministic eval/rubric/Gate → Batch7 已覆盖；calibration → Batch8 已覆盖；
- fallback baseline（judge/plan failure）→ Batch6 已覆盖；
- **不重复**：无需再建 Eval/calibration/fresh scenario 批；无需重测已冻结模块。

## 5. Frozen Boundary Compatibility（用户 Q5/Q6）

- 若 Batch9 = A（persistence）：**触碰 Batch6 D3 "round in-memory" 冻结边界**（需在
  research plane additive schema，不改 F8/checkpoint；不改 orchestrator 编排语义，仅为其
  round 状态提供持久化载体 + 恢复读取 seam）→ **L3**；不改 F8/F1–F7/Agent topology/schema
  既有表（纯 additive 迁移）。
- 不触碰：F8 runtime、F1–F7、Batch7 eval 契约、Batch8 calibration 结论。
- 无参数/protocol thaw（若涉及参数仅新持久化配置，非改已有默认值）。

## 6. 是否需要新 Agent / infra / DB / vector / graph（用户 Q7）

**不需要。** 默认假设成立：持久化走 **research plane 既有双后端 schema additive**
（F1–F8 的 sqlite/postgres 迁移体系），不引入新基础设施/DB/vector/graph；无新 Agent。

## 7. L2/L3 Classification（用户 Q8）

- **候选 A → L3**：schema additive（migration）+ 触碰 Batch6 D3 冻结边界（round 持久化）+
  recovery/identity 语义 → 按 PROCESS §11 L3 需 Readiness（Contract 闭合）+ Decision
  Closure + Adversarial/Integration Verification + User Freeze。
- 候选 B（F9-P0 Final Gate 宣告，纯文档/审计）→ **L1/L2**（无生产代码或极小）。

## 8. 候选 A：In Scope / Out of Scope / Frozen / Open / Decisions / Gate

- **In Scope**：research plane additive 表（round 状态、query identity 已存在？——query
  identity 属 Batch4 dedup，可能仅需持久化 ledger/round 摘要）；migration（sqlite+PG，
  零 ALTER 既有表）；恢复/续算 seam（只读，不改 orchestrator 执行语义）；回归 + PG gate。
- **Out of Scope**：改 F8/checkpoint；改 run_deep_agent/orchestrator 编排；改 Batch1–8
  冻结语义；_govadapt；real-provider 实现；跨 session memory；Claim–Evidence Graph；
  UI/infra/新 Agent。
- **Frozen Boundaries**：F8、F1–F7、Batch6 D3（round in-memory —— 持久化为**旁路载体**，
  不改变 orchestrator 运行期 in-memory 语义）、Batch7/8 结论、schema 既有表。
- **Open Questions**：持久化表 granularity（per-round vs 摘要）；恢复触发点（startup
  orphan 处理属 F8 future？or 仅写入不自动恢复）；query-identity ledger 是否需落库；
  是否允许 orchestrator 读取持久化 round（还是仅审计/eval 用）。
- **Decision Closure Items**：D-A 是否做 A（或停批/收尾）；D-B persistence 语义范围
  （write-only audit vs read-for-resume）；D-C schema 边界（additive 表名/键）；
  D-D 恢复能力是否入本批（否则写 + 只读查询，恢复留 future）。
- **Verification / Gate**：migration 双后端幂等 + 零 ALTER 断言；持久化 round 写入/读取
  往返；与 projection/gaps 输入一致；sqlite+PG 回归；Batch7/8 Gate 不受影响；
  ruff/compileall。

## 9. Decision Closure Items（汇总）

| # | Item | 选项 |
|---|---|---|
| D-A | Batch9 是否实施（A）或停批收尾（B） | A（L3 persistence）/ B（F9-P0 Final Gate 宣告） |
| D-B | persistence 语义 | write-only audit（推荐首版）/ read-for-resume（future） |
| D-C | schema additive 边界 | 新 research-plane 表 + migration，零 ALTER 既有表 |
| D-D | 恢复/续算 | 首版仅写 + 只读查询；自动恢复归 future |

## 10. Readiness Verdict

**CONDITIONAL — 候选 A（Round/Query-Identity Persistence, L3）具备明确依据与边界**，
但需用户裁决 D-A（是否实施 A vs 停批收尾 B）。核心判断：
- F9-P0 **core**（执行 + deterministic eval + calibration）已完整可运行 → 若用户认为
  persistence 非核心价值，可宣布 **F9-P0 core COMPLETE**（候选 B）并停止 F9-P0 批次推进；
- 若用户选择补齐 Spec §15 In 剩余项 → 按 L3 走候选 A（独立 feature branch
  `feature/f9-batch9-round-persistence`，另行批准后实施）。

```text
COMPATIBILITY GAP: 无（Readiness 阶段）
Affected frozen contract: Batch6 D3（round in-memory）—— 候选 A 若实施需 L3 路径以
additive persistence 载体旁路，不改 orchestrator 运行期语义；不触碰 F8/F1–F7/其余 frozen
Required decision: D-A（实施 A vs 停批收尾 B）；D-B~D-D（若 A）
```

等待用户 Review（选择候选 A / B 或其它）；未批准前不创建 branch、不实现、不 Commit/Push/Merge。

---

## 11. Decision Resolution（2026-10-01，用户裁决）

| # | Item | 裁决 |
|---|---|---|
| D-A | Batch9 是否实施（A）或停批收尾（B） | **停批收尾（B 方向）**：不实施 Round/Query-Identity Persistence（候选 A）作为 F9-P0 阶段开发；**F9-P0 Core = COMPLETE**（用户 2026-10-01 决定） |
| D-B | persistence 语义 | 不适用（本阶段不实施）；若未来 P1 实施，倾向 write-only audit（见候选 A 讨论） |
| D-C | schema additive 边界 | 不适用（本阶段不实施）；未来 P1 = research plane additive 表 + migration，零 ALTER 既有表 |
| D-D | 恢复/续算 | 不适用（本阶段不实施）；自动恢复归 future |

后续动作（用户指令）：
- Research persistence / Runtime recovery / Real provider E2E / Production hardening 全部
  归 **Deferred / Future（P1）**，不在 F9-P0 阶段继续开发；
- 不创建 Batch9 feature branch；不修改 F9 runtime / Batch1–8 frozen code；
- 收尾文档：`docs/plan/2026-10-01-f9-p0-final-completion-report.md`（F9-P0 Final
  Completion Report，宣告 COMPLETE）；PROJECT_CONTEXT.md 收敛（F9-P0 Core = COMPLETE /
  Batch1–8 = FROZEN·MERGED / Batch9 = Deferred·P1 / Current phase = Documentation）；
- 后续进入 README / Interview Material（Documentation / Presentation 阶段）。
