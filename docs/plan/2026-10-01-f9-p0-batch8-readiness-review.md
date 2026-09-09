# F9-P0 Batch 8 — Stopping / Threshold Calibration：Readiness Review + Decision Closure

> 状态：**READINESS REVIEW + DECISION CLOSURE（read-only）→ 结论 CONDITIONAL（见 §12）**。
> 依据：用户 2026-10-01 指令（Batch8 候选收敛 = Stopping/Threshold Calibration；只做
> Readiness + D1–D4 Closure + Constant Reference Audit；不建 branch/不实现/不 Commit）。
> 基线：main = `619f6c7`（Batch7 merged；Batch1–7 全部 PASS/FROZEN）。
> Git Workflow（AGENTS.md §10 / PROCESS.md §12）：本批未创建 feature branch。
> 日期：2026-10-01。

---

## 1. Batch8 Candidate

**F9-P0 Batch8 = Stopping / Threshold Calibration**（Eval 校准研究策略常量）。

不纳入本批：round/query identity persistence（保留为后续独立候选）；新 Agent；新
infrastructure；F8 Runtime redesign；扩功能式 scope。

## 2. Problem / Motivation

- F9 自适应循环的 stopping/budget 行为由临时常量控制，多处标注 "Batch8 Eval 校准前非冻结"
  （gaps.py MIN_EVIDENCE/BUDGET_NEAR_RATIO；orchestrator.py RESEARCH_POLICY_DEFAULTS；
  plan.py 常量；Batch2 report §Limitations 3 显式声明）。
- Spec §17 OQ3：stopping/阈值常量由 Eval 校准。Batch7 已交付 deterministic eval
  harness + rubric —— 正是校准所需工具。
- 当前值未经数据驱动验证；目标为受控校准实验 → 推荐值集 + 方法 + 回归证据。

## 3. Proposed Scope

1. 用 Batch7 harness/world 对 5 参数做确定性校准实验（grid/组合，多 world 防过拟合）；
2. 产出校准报告：before/after 指标表、推荐值、方法学；
3. 仅当证据明确更优且参数属 CALIBRATABLE 时，才做最小默认值改动并复跑 Batch7 Gate 与
   F9/F8 回归；否则不产代码 diff，只固化报告 + 可重复 regression；
4. 测试：校准实验以可重复测试落地（deterministic，无 real provider）。

## 4. Non-Scope

- round/query identity persistence、schema/migration；
- 新 Agent / infra / UI / Agent topology；
- F8 Runtime / F1–F7 / Batch6 orchestrator 编排语义 / Batch7 eval 契约修改；
- 修改 frozen tests 以适配参数；
- 以"更多搜索/更多轮次"为优化目标（禁止）；
- 为产生代码 diff 强行调参。

## 5. Architecture Impact

纯 research-intelligence 行为调优：改动面限 F9 批内非冻结常量 + 校准实验 + 报告；
不触 runtime control plane / research plane schema / bridge(F7) / orchestrator 编排语义。

## 6. Frozen-Boundary Compatibility

- 不突破：F8 / F1–F7 / Batch1–5 frozen 语义；Batch6 orchestrator 编排行为（policy 值可
  注入覆盖是 D5 冻结设计，非改编排）；Batch7 eval 契约（校准**复用** harness，不改语义）。
- 触发条件：若校准需改 Batch2/4 冻结断言或 projection/plan 契约、或需改 Batch6 frozen
  编排逻辑 → **STOP → COMPATIBILITY GAP → L3/Decision Closure**。

## 7. D1–D4 Decision Closure

### D1 — Batch8 主线：CLOSED
Stopping / Threshold Calibration。排除持久化/新 Agent/infra/F8 redesign。

### D2 — Calibration Scope：CLOSED（5 参数）
`MIN_EVIDENCE`、`BUDGET_NEAR_RATIO`、`max_research_rounds`、`max_no_progress_rounds`、
`repeated_gap_threshold`。
不把 plan candidate/query/target 上限作为主校准对象。
校准目标：stopping correctness、research continuation、budget-pressure behavior、
quality/cost trade-off。禁止以加搜/加轮为目标。

### D3 — Result Handling：CLOSED（不预设改码）
- 若现状已是合理 Pareto 点 → 不改默认值；固化 calibration report + 可重复 regression；
  Freeze。
- 若实验证明他值更优 → 仅改 CALIBRATABLE 参数默认值；提供 before/after；复跑 Batch7
  Gate 与 F9/F8 regression；不得为 diff 而调参。

### D4 — Frozen Reference Audit：CLOSED（矩阵见 §8）
审计规则遵守：**测试引用参数 ≠ 参数自动 frozen**；判断测试冻结的是"参数值本身"还是
"参数产生的行为"。审计结论见 §8（MIN_EVIDENCE / BUDGET_NEAR_RATIO 单独论证）。

## 8. Constant Reference Audit Matrix

| 参数 | definition | production refs | test refs | spec/plan refs | reference semantics | frozen by behavior? | classification |
|---|---|---|---|---|---|---|---|
| `MIN_EVIDENCE=2` | gaps.py:52 | gaps.py:158/166 | test_f9_gaps.py:183–193（**符号引用 `f9g.MIN_EVIDENCE`**，RE1 行为断言）；postgres:123 | Batch2 report §30/§40/§107（"D5 初始值，Eval 校准前不视为冻结值"）；Spec §5 "evidence < MIN_EVIDENCE" | RE1 = 0<ev<MIN → required_low_evidence；≥MIN 不发；==0 归 RU1 | 测试冻结**行为**（0<ev<MIN→RE1），**非数值 2**（符号引用） | **CALIBRATABLE** |
| `BUDGET_NEAR_RATIO=0.2` | gaps.py:55 | gaps.py:271/279 | test_f9_gaps.py:281–318（**硬编码 0.2 边界行为**：32/40=0.2 inclusive、limit=0 域、snapshot 不变）；postgres:196 | Batch2 report §69/§107（"阈值（含边界 0.2/耗尽/0 limit）"；"Eval 校准前不视为冻结值"） | remaining/limit ≤ ratio → budget_near；≤ inclusive；0/0 域确定性 | 测试用**具体比例构造行为边界**（0.2 inclusive）→ 改默认值会破坏这些边界测试的**数值期望** | **REQUIRES_DECISION_CLOSURE**（值改则须同步更新行为边界测试——属测试对"当前行为快照"的固定，非协议冻结；改前需裁决是否接受更新测试） |
| `max_research_rounds=3` | orchestrator.py:45 | orchestrator.py:338（while r<pol） | 无直接值断言；orchestrator 测试经 `research_policy` 注入覆盖（test_f9_orchestrator.py:270）；eval s6 用 policy=3 | Spec §9 "默认 ≤3，可配置"；Batch6 report（D5 临时默认） | adaptive 硬上限轮数 | 否（**注入覆盖即冻结设计**；默认值未被断言） | **CALIBRATABLE** |
| `max_no_progress_rounds=1` | orchestrator.py:46 | orchestrator.py:356 | 无直接值断言（stopping reason 字符串在 eval/orchestrator 断言） | Spec §9 no-progress 语义；Batch6 D5 | 连续无进展轮数阈值 | 否（语义冻结：no-progress 触发；数值可调） | **CALIBRATABLE** |
| `repeated_gap_threshold=2` | orchestrator.py:47 | orchestrator.py:362 | 无直接值断言（"repeated_gap"字符串出现在 summary errors） | Spec §9 repeated-gap（同 gap ≥2 轮未缓解→僵局） | 同 gap 僵局阈值 | 否（语义冻结：僵局触发；数值可调） | **CALIBRATABLE** |

**MIN_EVIDENCE 专项论证**：位于 Batch2 gaps 逻辑，但 Batch2 report §107 显式声明其为 D5
初始常量、"Eval（Batch 8）校准前不视为冻结值；实现为模块顶层常量，变更只影响行为、不动
契约"。测试对 `f9g.MIN_EVIDENCE` 为**符号引用**（test_f9_gaps.py:189,193），冻结的是
"RE1 行为关系"（0<ev<MIN → 信号、==0 → RU1、≥MIN → 不发），不是数值 2。→ **CALIBRATABLE**
（改值后上述符号引用测试继续通过；RE1/RU1 行为语义不变）。

**BUDGET_NEAR_RATIO 专项论证**：Batch2 report §107 同样声明 Eval 校准前非冻结值；但
test_f9_gaps.py 的 TestBudgetNear 以**具体 0.2 边界**（counts=32/40=0.2 inclusive）与
limit=0 域构造断言。这些测试冻结的是"以当前 ratio 构造的行为快照边界"。改 ratio 会改变
"0.2 是否 near"的判定 → 测试数值期望失效。结论：改 ratio 需**同步更新该行为边界测试**
（属测试随参数校准更新，非解冻协议）；改前需用户裁决。→ **REQUIRES_DECISION_CLOSURE**
（conditional calibratable）。

无参数属 **FROZEN**；无参数需立即 STOP。

## 9. Calibration Experiment Design

- **复用** Batch7 `app/f9/eval/*`（world/harness/rubric/scenarios）；
- 多 deterministic worlds/scenarios 交叉（防单一 scripted world 过拟合）：在现有 8
  scenarios 基础上扩展变体 world（不同 seed/evidence 拓扑/budget 余量）；
- baseline（run_deep_agent）与 adaptive（f9_orchestrator）同 world/task/policy 双跑；
- 对 5 参数做受控 grid/组合扫描：例如 MIN_EVIDENCE∈{1,2,3}、BUDGET_NEAR_RATIO∈
  {0.1,0.2,0.3}、max_research_rounds∈{2,3,4}、max_no_progress_rounds∈{1,2}、
  repeated_gap_threshold∈{2,3}；
- 指标（quality 与 cost 同评）：required coverage、citation coverage、verification
  coverage、unresolved conflict 行为、redundant/unnecessary research、stopping
  correctness、cost compliance、budget utilization；
- 判定：以 rubric 方向 + cost 约束选 Pareto 点；记录 before/after；
- real provider 不作为 Gate；无凭据时继续为 limitation；
- 产出可重复 calibration methodology（网格定义 + 运行 + 汇总脚本/测试）。

## 10. Risks / Limitations

- 常量已被既有测试间接引用（尤其 BUDGET_NEAR_RATIO 边界测试）→ 值改动需同步测试，须
  用户裁决（D4 已标 REQUIRES_DECISION_CLOSURE）；
- MIN_EVIDENCE 符号引用测试改值安全，但需全量回归确认无行为漂移；
- deterministic world 校准结论对 real provider 迁移性有限；
- 防过拟合：多 world 交叉；防"为指标加搜/加轮"：cost 维度并评。

## 11. L2/L3 Classification

- 仅校准实验 + 报告 + CALIBRATABLE 参数 additive 默认值调整 → **L2**。
- 若需解冻 Batch2 行为边界测试（BUDGET_NEAR_RATIO）或扩展 projection/plan 契约 → **L3**
  （需 Decision Closure 后实施）。

## 12. Implementation Readiness Verdict

**CONDITIONAL — 校准（L2 路径）前置条件具备**：
- Batch7 harness = 现成校准工具；常量 = 已声明可校准对象（Batch2 report §107 / Spec OQ3）；
- 需先经用户确认 D1–D4、认可 BUDGET_NEAR_RATIO 的 REQUIRES_DECISION_CLOSURE 处理（值改动
  时同步行为边界测试的裁决），并批准创建 feature branch。
- 持久化等候选被 D1 明确排除，不阻塞本 verdict。

## 13. Recommended Feature Branch

```text
feature/f9-batch8-calibration
```

## 14. Implementation Boundary

- In：校准实验（复用 Batch7 eval）+ calibration report +（条件性）CALIBRATABLE 参数
  默认值最小调整 + 相应测试/回归更新；
- Out：生产/frozen/runtime/schema/UI/infra/Agent 改动；Batch6/7 frozen semantics；
  persistence；扩 scope；
- 遇 frozen 冲突 → STOP → COMPATIBILITY GAP → L3/Decision Closure。

## 15. Explicit STOP / Next Action

本 Readiness + Decision Closure 完成，保持 `main`（无 feature branch、无代码/测试改动、
无 Commit/Push/Merge）。等待用户 Review：

1. 确认 D1–D4（尤其 D4 中 BUDGET_NEAR_RATIO = REQUIRES_DECISION_CLOSURE 的处理）；
2. 批准后创建 `feature/f9-batch8-calibration` → Implementation（校准实验 + 报告 +
   条件性调参）→ Verification → Report → Freeze。

```text
COMPATIBILITY GAP: 无（Readiness 阶段）
Affected frozen contract: —
Reason: 5 参数审计无 FROZEN；MIN_EVIDENCE 等 4 项 CALIBRATABLE；BUDGET_NEAR_RATIO
REQUIRES_DECISION_CLOSURE（改值须同步行为边界测试，非解冻协议）
Required decision: 用户确认 D1–D4 与 BUDGET_NEAR_RATIO 处理方式
```
