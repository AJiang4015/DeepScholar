# Spec: 2026-09-10 — F4 Conflict Detection（Detection + Representation + Provenance）· rev2

> 状态：**F4 Spec rev2 — 待 F4 Final Gate Review；禁止 implementation**
> rev2 修订（Spec-only）：① 锁死 genuine/conflict_type invariant；② verification FK 删除策略裁决
> （ON DELETE SET NULL + signal 快照）；③ 补充 F4 artifact integrity invariants；④ explicit_pairs
> controlled/internal contract 定案；⑤ 锁死 F3/F4 semantic boundary（detector 不重估 support）；
> 另关闭 Open Questions Q1–Q4（§23）。修订明细见 §25。
> 阶段链：F1 ✅ → F2 ✅ → F3 ✅(Frozen) → **F4 Conflict Detection** → F5+（Resolution/Reliability…）
> 红线：只分析/审计/设计；不改 app、migration、tests、Agent/Tool/Checkpoint、依赖、基础设施；
> 本轮仅修改本 Spec 文件。

---

## 0. 一句话定位

F3 回答"每条 Evidence 是否支持该 Claim"；F4 回答下一层：

> **"contested 到底由哪两条 Evidence 构成？它们是真矛盾（CONTRADICTION / INCONSISTENCY），
> 还是只是语境/时间/范围差异（CONTEXTUAL/TEMPORAL/SCOPE）或本就无冲突（NO_CONFLICT）？"**

F4 = **Conflict Detection（发现 + 结构化表示 + provenance）**；**不做 Resolution**。

---

## 1. Problem Statement

同 rev1（pair 级信息丢失、真冲突 vs 差异无区分、结构化溯源缺失）。rev2 无实质变化。

---

## 2. Current Capability（现状审计 F1–F3）

同 rev1 §2（保留）。关键结论不变：Evidence 无立场；F3 verdict 是面向 claim 的立场信号；
`contested` 是聚合状态而非 artifact；F4 把 pair 事实物化为 artifact。

---

## 3. Gap Analysis

同 rev1 §3（保留）。

---

## 4. Goals

同 rev1 §4（保留）；追加明确一条：

> 定义并强制 **F4 artifact integrity invariants**（§10b）与 **genuine/conflict_type invariant**（§10c），
> 保证 conflicts 表在任何状态下不自相矛盾、不悬空、不重复。

---

## 5. Non-goals（写死，不变）

Resolution / Source Reliability / Winner Selection / Reconciliation / Replan / Planner / 新 Agent /
Agent Runtime / Agent-visible context / Tool / Checkpoint / Graph / Memory / 新 infra / 新 LLM framework /
Eval 平台 / ALTER Frozen 表——全部排除（同 rev1 §5）。

---

## 6. Terminology

同 rev1 §6；补充：
- **Invariant**：conflicts 行必须始终满足的完整性/一致性条件（§10b/§10c），与状态无关。

---

## 7. Conflict Semantics

同 rev1 §7 的类型表保留，并追加 **invariant 化约束（rev2 必须修订 1）**：

```text
candidate:  conflict_type = NULL ; genuine = NULL
confirmed:  conflict_type ∈ {CONTRADICTION, INCONSISTENCY} ; genuine = true
rejected:   conflict_type ∈ {CONTEXTUAL_DIFFERENCE, TEMPORAL_DIFFERENCE,
                             SCOPE_DIFFERENCE, NO_CONFLICT} ; genuine = false
failed:     conflict_type = NULL ; genuine = NULL
```

- **genuine 不允许独立表达与 conflict_type 矛盾的语义**：任何 write 都按此 invariant 校验；
- DB 层以应用一致性 invariant 强约束（registry/detector 唯一写路径校验 + 测试矩阵锁定），
  见表注释与 §10c。

---

## 8. Detection Granularity

同 rev1 §8：identity = `(run_id, claim_id, evidence_a_id, evidence_b_id) + detector_fingerprint`
（a<b 规范化）。不做跨 claim（Q3 关闭，§23）。

---

## 9. Candidate Generation

同 rev1 §9，并加入 **explicit_pairs controlled contract（rev2 必须修订 4）**：

```text
explicit_pairs
   ↓ 仅绕过 F3 verdict-based candidate generation
   不能绕过：F2 structural Gate；same-run；same-claim；ClaimEvidence binding；
             pair normalization（a<b）；evidence_a != evidence_b
```

- explicit_pairs 定位为 **controlled/internal advanced entry**，不默认公开（Q4 关闭）；
- 其余（候选=SUPPORTS×CONTRADICTS、cited 优先、max_pairs_per_claim、max_semantic_calls、幂等复用）不变。

---

## 10. Data Model（0004 新增 conflicts；零 ALTER）· rev2

```text
conflict_id          TEXT PK                    # uuid4 hex
run_id               TEXT FK → research_runs        ON DELETE CASCADE
claim_id             TEXT FK → claims               ON DELETE CASCADE
evidence_a_id        TEXT FK → evidences            ON DELETE CASCADE   # a < b
evidence_b_id        TEXT FK → evidences            ON DELETE CASCADE
verification_a_id    TEXT FK → verifications        ON DELETE SET NULL  # rev2 裁决
verification_b_id    TEXT FK → verifications        ON DELETE SET NULL
detector_spec        JSONB/TEXT NOT NULL
detector_fingerprint TEXT NOT NULL
candidate_source     TEXT NOT NULL              # 'verdict_opposed' | 'explicit'
status               TEXT NOT NULL DEFAULT 'candidate'
conflict_type        TEXT NULL
genuine              INTEGER/BOOLEAN NULL
rationale            TEXT NULL
error                TEXT NULL
created_at / completed_at TIMESTAMPTZ/TEXT
metadata             JSONB/TEXT NOT NULL DEFAULT '{}'
UNIQUE (run_id, claim_id, evidence_a_id, evidence_b_id, detector_fingerprint)
```

- **verification FK 删除策略（rev2 正式裁决）**：`verification_a_id` / `verification_b_id`
  → **ON DELETE SET NULL**。理由：Conflict identity 不依赖 verification_id；verification 删除后
  Conflict artifact 保留、外键置 NULL；**原始 F3 signal 快照（verdict_a/verdict_b 等）在写入时
  同步存入 `metadata`**，避免 provenance 完全丢失。

### 10b. F4 artifact integrity invariants（rev2 必须修订 3，锁定）

任意状态的 conflicts 行必须满足：

```text
run_id == claim.run_id
evidence_a.run_id == run_id
evidence_b.run_id == run_id
evidence_a != evidence_b
evidence_a_id < evidence_b_id
ClaimEvidence(run_id, claim_id, evidence_a_id) 存在
ClaimEvidence(run_id, claim_id, evidence_b_id) 存在
```

若 verification_a/b 存在（非 NULL），进一步满足：

```text
verification_a.run_id == run_id
verification_a.claim_id == claim_id
verification_a.evidence_id == evidence_a_id
verification_b.run_id == run_id
verification_b.claim_id == claim_id
verification_b.evidence_id == evidence_b_id
```

- 强制执行点：registry/detector **唯一写路径**（写入/更新前校验）+ 双后端测试矩阵断言
  （DB 层不设复合 CHECK 触发器——保持 DDL 可移植与 runner 幂等，见 Open/风险说明）；
- 单列 FK 仅保证存在性；上述**跨表一致性**由写前守卫与 validator 层承担（与 F2 R5/R6 同模式）。

### 10c. genuine/conflict_type invariant（rev2 必须修订 1）

写路径按 §7 状态机校验；唯一写入口确保任意状态满足：

| status | conflict_type | genuine |
|---|---|---|
| candidate | NULL | NULL |
| confirmed | ∈ {CONTRADICTION, INCONSISTENCY} | true |
| rejected | ∈ {CONTEXTUAL_DIFFERENCE, TEMPORAL_DIFFERENCE, SCOPE_DIFFERENCE, NO_CONFLICT} | false |
| failed | NULL | NULL |

---

## 11. State / Verdict Model

同 rev1 §11 + §10c invariant；claim 级派生汇总（只读，不写 claims）不变；
仍**不引入** `CONFLICT_UNRESOLVED` 产品状态。

---

## 12. Detection Algorithm

同 rev1 §12；追加：detector 输出先过 invariant 校验再落库（§10c）；
gate 失败/候选复用/retry×1/单条失败不阻断 等语义不变。

---

## 13. Semantic / LLM Boundary（rev2 必须修订 5：锁死 F3/F4 边界）

> **F4 detector SHALL NOT re-evaluate Claim↔Evidence support status（F3 职责）。**

- detector 唯一职责：给定 Evidence A + Evidence B 的 pairwise relationship 分类
  `CONTRADICTION / INCONSISTENCY / CONTEXTUAL_DIFFERENCE / TEMPORAL_DIFFERENCE / SCOPE_DIFFERENCE / NO_CONFLICT`；
- F3 的 SUPPORTS/CONTRADICTS 仅作为 **candidate/context signal**（进 Envelope 的 signals 字段），
  **不在 F4 中重新计算或改判**；
- 其余边界（deterministic vs semantic 分工）同 rev1 §13。

---

## 14. Context Envelope

同 rev1 §14（最小包；source_meta/signals 仅 identification；禁 Agent 上下文）。
F3 verdicts 仅以 `signals` 原样透传，detector 不得改判（§13）。

---

## 15. Idempotency

同 rev1 §15（身份含 fingerprint；a<b 规范化；复用/重试同 artifact；升级新行；不引入 task_call_id）。

---

## 16. Failure / Retry

同 rev1 §16（retry×1、failed 置 NULL type/genuine、单条不阻断、batch partial 仅 run 层）。

---

## 17. Provenance

- Claim → (Evidence A | B) → Source（既有链扩展 pair）；
- Conflict → verification_a/b（FK，删除置 NULL）+ **metadata 内 F3 signal 快照**（rev2 落定）；
- Conflict → detector_spec/model/prompt_version/fingerprint；
- 提供 `conflict_chain(conflict_id)` / `list_conflicts(...)`（只读）。

---

## 18. Budget / Complexity Control

同 rev1 §18（|C|×|S| 受限 + max_pairs_per_claim + max_semantic_calls；无全量 n²）。

---

## 19. SQLite / PostgreSQL requirements

0004 双后端 DDL（FK ON DELETE CASCADE / SET NULL 双端一致；列型差异沿用约定）；runner 幂等（→0004）；
checkpoint 表族边界；F1/F2/F3 零 ALTER。

---

## 20. API design（设计，不实现）

同 rev1 §20（detect_claim_conflicts / detect_run_conflicts / list_conflicts / conflict_chain /
claim_conflict_summary）。explicit_pairs 仅受控入口。

---

## 21. Test strategy

同 rev1 §21，追加 invariant 矩阵：
- §10b 完整性 invariant 违反用例（跨 run/跨 claim/未 binding/同 evidence/a>b/verification 错位）逐一拒绝；
- §10c genuine/conflict_type 全状态矩阵（4 状态 × 类型组合）；
- verification 删除 → 外键置 NULL 且 metadata signal 快照保留；
- detector SHALL NOT 改判：fake detector 若尝试输出 SUPPORTS/CONTRADICTS → 解析失败/拒绝路径。

---

## 22. Acceptance Criteria · rev2

| AC | 内容 |
|---|---|
| F4-AC1 | 0004 双后端幂等；零 ALTER F1/F2/F3；表族边界 |
| F4-AC2 | 候选生成 SUPPORTS×CONTRADICTS（a<b、dedup、无 n²、cited 优先） |
| F4-AC3 | Gate：errors 拒绝；无 F3 verification 默认无候选 |
| F4-AC4 | ConflictStatus 4 态（无 partial）+ **§10c invariant 全状态矩阵** |
| F4-AC5 | semantic 类型全路径（fake）+ retry×1/二次失败 |
| F4-AC6 | 幂等：identity 收敛；升级新行；failed 同 spec 重试同 artifact |
| F4-AC7 | **§10b integrity invariants 全项写前校验测试** |
| F4-AC8 | **verification FK 删除 → SET NULL + metadata signal 快照保留** |
| F4-AC9 | Context Envelope 边界 + **detector 不改判 support（SHALL NOT）** |
| F4-AC10 | 双后端 sqlite/PG 同语义；F1/F2/F3 回归；文档同步 |

---

## 23. Open Questions（rev2 关闭与保留）

**已关闭（Gate 裁决）**
- Q1 confidence：**F4 不引入 confidence/概率/评分字段**（genuine 由 invariant 承担判定语义）。
- Q2 same-verdict inconsistency：本阶段默认候选域仅 SUPPORTS×CONTRADICTS；同 verdict pair 不纳入，
  留后续扩展（F4 架构可容纳，但不实现）。
- Q3 cross-claim conflict：F4 不做。
- Q4 explicit_pairs：按 §9 controlled/internal contract 关闭（非默认公开入口）。
- verification FK 删除策略：SET NULL + signal 快照（§10/§17/AC8）锁定。

**保留（进入 Implementation 前待确认，非阻塞）**
- conflict_type 词表是否需在 DDL 层加枚举约束（建议应用层枚举 + 测试，保持 DDL 可移植）；
- metadata signal 快照的精确字段集合（verdict_a/b + verification_id + status 建议集）；
- 预算默认值（max_pairs_per_claim=12、max_semantic_calls 上限数值）由实现配置化确认。

---

## 24. Migration / Compatibility Strategy

- 0004 仅新增 conflicts；FK：run/claim/evidence CASCADE，**verification_a/b SET NULL**；
- rollback 与表族边界同 rev1；兼容：只读消费 F3 verification；不写 claims/verifications。

---

## 25. rev2 修订记录与 Self Review

| # | 修订 | 落点 |
|---|---|---|
| 1 | genuine/conflict_type invariant 锁死（状态矩阵 + 写路径校验） | §7/§10c/§11/§22-AC4 |
| 2 | verification FK 删除策略正式裁决：ON DELETE SET NULL + metadata signal 快照 | §10/§17/§22-AC8/§24 |
| 3 | F4 artifact integrity invariants（同 run/同 claim/binding/a<b/≠/verification 对齐） | §10b/§22-AC7 |
| 4 | explicit_pairs = controlled/internal advanced entry（仅绕过候选生成，不绕过任何 integrity） | §9/§20/§23-Q4 |
| 5 | F3/F4 semantic boundary 锁死：detector SHALL NOT re-evaluate support | §13/§14/§22-AC9 |
| 6 | 关闭 Open Questions Q1–Q4 + verification FK 项 | §23 |

**Self Review**：① invariant 双字段由唯一写入口 + 测试矩阵锁定，DDL 保持可移植（不在 DB 加 CHECK 触发器，
避免跨后端 DDL 漂移——已在 AC/风险标注）；② SET NULL + metadata 快照消除悬空与 provenance 丢失双风险；
③ integrity 全部可由确定性代码校验，无 LLM 依赖；④ detector 不改判边界以 prompt 与输入构造双保险；
⑤ 无 ALTER Frozen 表、无新 Agent/infra；⑥ 遗留风险：跨后端 DB 级约束不可移植 → 依赖写路径守卫与
测试矩阵（与 F2 R5/R6 同模式，接受并文档化）。

**Scope Audit**：本轮仅修改 `docs/spec/2026-09-10-conflict-detection.md`；未创建 0004、未写代码/测试、
未改 Agent/Tool/Checkpoint/依赖/基础设施。

**STOP**：F4 Spec rev2 + Self Review 完成，等待 F4 Final Gate Review；Gate 通过前不实现。
