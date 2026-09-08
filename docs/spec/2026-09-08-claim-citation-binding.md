# Spec: 2026-09-08 — F2 Claim / Citation Binding & Validation（Design Spec · rev2）

> 状态：**Draft rev2 — 待用户 Review/Gate 后决定是否进入 Implementation**
> rev2 修订（2026-09-08，Spec-only）：① citation_key 稳定性语义重定；② R10 正式化；
> ③ validator 与 claim_status 职责分离；④ quote/locator 语义锁定。修订明细见 §19。
> 前置：F1（Research Artifact Foundation）冻结为 baseline。本 Spec **只设计、不建表、不改代码/依赖**。
> 阶段链：F1 `ResearchRun→SubQuestion→SearchQuery→Source→Evidence` ✅ → **F2 Claim←Evidence→Citation** → F3+

---

## 0. 需求覆盖映射

| 编号 | F2 要求 | 章节 |
|---|---|---|
| 1 | Architecture boundary | §1 |
| 2 | Entity model | §2 |
| 3 | SQL schema | §3 |
| 4 | FK / cardinality / uniqueness | §4 |
| 5 | ClaimEvidence relationship rationale | §5 |
| 6 | Citation model | §6 |
| 7 | Citation validation rules | §7 |
| 8 | Unsupported claim detection | §8 |
| 9 | Deterministic vs semantic validation boundary | §9 |
| 10 | Replay / idempotency strategy | §10 |
| 11 | Migration strategy | §11 |
| 12 | API / provenance extension | §12 |
| 13 | Test strategy | §13 |
| 14 | F2 AC criteria | §14 |
| 15 | Gate criteria | §15 |
| 16 | Risks / non-goals | §16 |
| 17 | 与 F1 schema 的兼容性说明 | §17 |
| 18 | 审核提示 | §18 |
| 19 | rev2 修订记录与决策汇总 | §19 |

---

## 1. Architecture boundary（F2 落在哪）

```text
Run 边界（run_deep_agent 不变）
 ├─ Checkpoint（执行态，冻结，不涉及）
 └─ Research Data Plane（F1 冻结）
      ├─ ResearchRun → SubQuestion → SearchQuery → Source → Evidence   （F1，append/候选证据）
      └─ F2 增量（本 Spec）：
           Claim（陈述） ←—— claim_evidences(M:N) ——→ Evidence（被选择为支撑）
           Citation（报告呈现锚点）: (Claim + Evidence → Source) 的引用条目
           Validator（deterministic 纯函数，只读计算 ViolationReport）
           Finalizer workflow（在 validator 结果之上执行 claim_status 演进——见 §12.2）
```

- F2 只新增 **artifact 层**（3 张表 + registry 写入守卫 + validator 只读规则 + provenance 扩展 + finalizer workflow）。
- **不改** Agent-visible context、工具签名/docstring、checkpoint、F1 schema 既有列。
- Evidence 保持 F1 的 "candidate / groundable content" 语义不变；"被使用"由 binding（claim_evidences）显式表达。

---

## 2. Entity model

### 2.1 Claim（陈述）
```text
claim_id           TEXT PK          # 系统生成（uuid4 hex，稳定 artifact identity）
run_id             TEXT FK → research_runs         # 根归属，直连 run（F1 铁律延续）
sub_question_id    TEXT FK → sub_questions         # 该陈述服务的子问题（root-only 现状下即 root）
statement          TEXT NOT NULL                   # 陈述正文（上限 2000 字符）
statement_sha      TEXT NOT NULL                   # sha256(statement)，内容级幂等键
claim_type         TEXT NOT NULL                   # 稳定枚举 ClaimType（保留为后续 Verification 扩展点；F2 validator 不消费其语义）
status             TEXT NOT NULL DEFAULT 'drafted' # 稳定枚举 ClaimStatus（见下；由 finalizer 演进，validator 不写）
created_at         TIMESTAMPTZ/TEXT NOT NULL       # 双后端（沿用 F1 列型约定）
metadata           JSONB/TEXT NOT NULL DEFAULT '{}'# 创建来源/上下文快照（agent/阶段/section 归属等——G6：section 放 metadata）
UNIQUE (run_id, statement_sha)
```
- **claim_type（入库但 F2 不消费）**：`FACT | STATISTIC | PREDICTION | COMPARISON | CAUSAL_CLAIM | OPINION | INFERENCE`——为 Verification 保留扩展点。
- **claim_status（F2 语义范围极小）**：`drafted`（默认）→ `validated`。
  - `validated` 的判定标准与**执行者**（非 validator）：claim 被 ≥1 citation 覆盖（进入报告且引用完整）即由 finalizer workflow 置为 validated；unsupported / unreferenced 保持 drafted。不引入 supported/contradicted（Verification 域）。
- **Claim 是否允许独立于 Evidence 存在？→ 允许。** 理由：
  1. unsupported claim 是合法研究产物，Validation 负责显式检出而非写入层禁止；
  2. OPINION/INFERENCE 天然可能无外部证据；
  3. F1 原则"artifact 记录真实发生"适用。

### 2.2 Evidence（F1 实体，F2 只读不改）
不变。binding 只"引用"它，永不修改 evidence 行。
**语义锁定（rev2，§6.4/§7-R8 依据）**：
- `content` = candidate evidence snapshot（工具返回内容片段，F1 语义）；
- `locator` = **source-level location**（证据在来源中的定位：url#片段 / db:{query_id} / ragflow:{assistant} / 上传文件页等）。

### 2.3 ClaimEvidence（support binding，§5 详述）
```text
binding_id         TEXT PK
run_id             TEXT FK → research_runs   # 冗余直连（run 隔离/归档单点）
claim_id           TEXT FK → claims
evidence_id        TEXT FK → evidences
created_at         TIMESTAMPTZ/TEXT NOT NULL
metadata           JSONB/TEXT NOT NULL DEFAULT '{}'
UNIQUE (claim_id, evidence_id)
```
不加 role/weight（Conflict/Verification 域，F2 不预占）。

### 2.4 Citation（§6 详述；rev2 后**不再持久化呈现编号**）
```text
citation_id        TEXT PK            # 系统生成（uuid4 hex）＝ Citation 的稳定 artifact identity
run_id             TEXT FK → research_runs
claim_id           TEXT FK → claims
evidence_id        TEXT FK → evidences
quote              TEXT NULL          # 从 Evidence.content 中选出的报告引用片段（≤500，可空；非空时必须是 content 连续子串，R8）
locator            TEXT NULL          # 可选定位信息：报告侧/来源侧（如 "sec:2"、"page:7"、章节锚点）；自由文本
metadata           JSONB/TEXT NOT NULL DEFAULT '{}'
created_at         TIMESTAMPTZ/TEXT NOT NULL
UNIQUE (run_id, claim_id, evidence_id)   # 同一 claim-evidence 只产生一条 citation（幂等）
```
**明确不存在的列**：`citation_key`（呈现编号）、`quote_start/quote_end`（最小模型，G 要求不加）。
- 呈现编号 `[n]` **不属于 DB 实体**：由 report rendering/finalization 对 run 的 citation 集合按确定性排序即时生成（§6.3）。

---

## 3. SQL schema（双后端，0002 迁移草案——本阶段**不创建**）

```sql
-- 0002_claim_citation（草案，rev2：citations 无呈现编号列）
CREATE TABLE IF NOT EXISTS claims (
    claim_id        TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES research_runs(run_id) ON DELETE CASCADE,
    sub_question_id TEXT NOT NULL REFERENCES sub_questions(sub_question_id),
    statement       TEXT NOT NULL,
    statement_sha   TEXT NOT NULL,
    claim_type      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'drafted',
    created_at      TIMESTAMPTZ/TEXT NOT NULL,
    metadata        JSONB/TEXT NOT NULL DEFAULT '{}',
    UNIQUE (run_id, statement_sha)
);
CREATE INDEX IF NOT EXISTS idx_claims_run ON claims(run_id);
CREATE INDEX IF NOT EXISTS idx_claims_subq ON claims(sub_question_id);

CREATE TABLE IF NOT EXISTS claim_evidences (
    binding_id  TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES research_runs(run_id) ON DELETE CASCADE,
    claim_id    TEXT NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
    evidence_id TEXT NOT NULL REFERENCES evidences(evidence_id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ/TEXT NOT NULL,
    metadata    JSONB/TEXT NOT NULL DEFAULT '{}',
    UNIQUE (claim_id, evidence_id)
);
CREATE INDEX IF NOT EXISTS idx_claim_evidences_run ON claim_evidences(run_id);
CREATE INDEX IF NOT EXISTS idx_claim_evidences_evidence ON claim_evidences(evidence_id);

CREATE TABLE IF NOT EXISTS citations (
    citation_id TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES research_runs(run_id) ON DELETE CASCADE,
    claim_id    TEXT NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
    evidence_id TEXT NOT NULL REFERENCES evidences(evidence_id) ON DELETE CASCADE,
    quote       TEXT,
    locator     TEXT,
    metadata    JSONB/TEXT NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ/TEXT NOT NULL,
    UNIQUE (run_id, claim_id, evidence_id)
);
CREATE INDEX IF NOT EXISTS idx_citations_claim ON citations(run_id, claim_id);
```

---

## 4. FK / cardinality / uniqueness（决策表）

| 关系 | 基数 | FK | 决策理由 |
|---|---|---|---|
| claim → research_runs | N:1 | claims.run_id | 全实体直连 run；run 删除 CASCADE |
| claim → sub_questions | N:1 | claims.sub_question_id | 归属问题域 |
| evidence → sources | N:1 | （F1） | 不变 |
| claim ↔ evidence | **M:N** | claim_evidences 双 FK | §5.1 |
| citation → claims | N:1 | citations.claim_id | 每条 citation 服务一个陈述 |
| citation → evidences | N:1 | citations.evidence_id | 锚定具体证据（经 evidence→source 溯源） |
| citation → research_runs | N:1 | citations.run_id | 冗余直连 + run 作用域 |
| claim_evidences UNIQUE(claim_id,evidence_id) | — | — | 绑定幂等（§10） |
| claims UNIQUE(run_id,statement_sha) | — | — | 内容级幂等（§10） |
| citations UNIQUE(run_id,claim_id,evidence_id) | — | — | 引用幂等；**无呈现键列** |

- `ON DELETE CASCADE` 只服务"显式删 run/清理"路径。
- 跨表一致性（同 run）由 registry 写入守卫 + validator 规则承担（§7）。

---

## 5. ClaimEvidence relationship rationale

### 5.1 M:N，不是 1:N
- 一个 Claim 可由多条证据支撑；一条 Evidence 可支撑多个 Claim（同一数据多角度使用）。1:N 强制"一条证据只服务一个陈述"，不符实际；M:N join 同时让"unbound candidate evidence"可查询（unsupported 分析依赖）。

### 5.2 绑定方
- 绑定由**确定性 registry API** 创建（显式 claim_id/evidence_id + 同 run 校验）——纯引用操作，无 LLM。
- 未来 semantic selection（"哪条证据真支撑陈述"，Verification 域）的产物仍写入 claim_evidences，schema 不变（扩展点）。

### 5.3 selected/used 与 F1 candidate 边界
| 概念 | 载体 | 语义 |
|---|---|---|
| candidate evidence | evidences 行（F1，不变） | 工具返回内容片段，"可供 grounding" |
| **selected/used** | claim_evidences 行存在 | 该 evidence 被某 claim 显式选中为支撑 |
| 报告呈现引用 | citations 行（quote/locator） | 进入最终报告的引用条目；编号 [n] 渲染时生成 |
- evidence 无 "used 标记列"：绑定存在与否即事实源。

---

## 6. Citation model

**定义**：Citation 是 **Claim 在最终报告中的可验证呈现锚点**，同时绑定 claim 与 evidence
（source 经 evidence→source 单路径可达）；**不是 Source URL 复制**。

### 6.1 身份与编号分离（rev2 核心修正）
| 概念 | 载体 | 性质 |
|---|---|---|
| **artifact identity** | `citation_id`（uuid4 hex） | 稳定、不可变、与顺序无关；是 citation 的领域身份 |
| **presentation identifier `[n]`** | **不持久化** | report rendering/finalization 时对 run 的 citation 集合按确定性排序即时生成 |
- **不把 `[n]` 写入数据库**：避免"后续新增/插入 Evidence/Citation 导致既有引用编号变化"，也避免 replay 重新编号问题（编号是每次渲染的派生视图，不承诺跨渲染稳定）。
- 若未来某处确需把编号随报告快照落库，必须在 **report artifact**（报告产物，非 citation 领域表）中冻结，与本表无关（当前不引入）。

### 6.2 渲染编号的确定性规则（render util，deterministic，不落库）
```text
render_citations(run_id) → list[(citation_id, n)]
排序键：citations.created_at ASC, citation_id ASC（全序稳定）
n = 1..N 按排序后位置
```
- 同一 snapshot 数据下多次渲染编号一致；新增 citation 只会向后追加序号，**已存在 citation 的 identity 不变、历史渲染快照不变**。

### 6.3 设计问题回答
| 问题 | 决策 |
|---|---|
| 绑定 Claim？ | 是（citations.claim_id） |
| 绑定 Evidence？ | 是（citations.evidence_id，span 级锚定） |
| 同时绑定？ | 是——单一 (claim, evidence)；多证据 = 多行 |
| 为何不冗余 source_id？ | evidence→source 单路径已保证溯源；冗余列会产生"source 与 evidence 不同源"的新一致性问题 |
| locator | 可选报告/来源定位（如 sec:2、page:7），自由文本；**不是** source 地址、**不是** 编号 |
| quote / span | quote = 从 evidence.content 选出的引用片段（≤500，可空）；span 权威载体是 evidence 行（content+locator）；**不新增** quote_start/end |
| source reference | 经 evidence.source_id 可达，不复制 URL |
| validation 最小字段 | run_id/claim_id/evidence_id/quote(可选)/locator(可选)——存在性 + 同 run + quote 子串规则（§7） |

### 6.4 Citation 与 ClaimEvidence 的分工
- claim_evidences = 研究内支撑关系（可能未被报告引用）；
- citations = 报告级引用条目（存在性规则：每条 citation 必须有对应 binding，R2）；
- 不合并：生命周期与语义不同（research support vs report artifact）。

---

## 7. Citation / 引用完整性 validation rules（deterministic，只读）

> **validator 职责（rev2）**：`validate_run(run_id) -> ViolationReport` 为**纯函数/只读**——
> 只计算结果，**不产生任何 DB side effect、不更新 claim.status**。状态演进由 finalizer workflow（§12.2）执行。

| rule | 名称 | 检查 | 级别 |
|---|---|---|---|
| R1 | dangling citation | citation 的 claim_id/evidence_id 不存在（写入层 FK 兜底；validator 读端审计） | error |
| R2 | citation 无支撑绑定 | (claim_id, evidence_id) 不在 claim_evidences | error |
| R3 | unsupported claim | claim 没有任何 claim_evidences binding（= 无 selected/used evidence） | warning |
| R4 | invalid evidence reference | evidence_id 不存在 / evidence.source_id 无对应 source | error |
| R5 | cross-run reference | claim.run_id ≠ evidence.run_id 或 evidence.run_id ≠ source.run_id | error |
| R6 | claim/evidence 不同 run | claim.run_id ≠ evidence.run_id（R5 特化；registry 写入时即拒绝） | error |
| R7 | locator 不合法 | locator 非空但含控制字符或超长（>200） | error |
| R8 | quote 非证据子串 | quote 非空且不是 evidence.content 的连续子串（精确包含） | warning |
| R9 | duplicate claim | 同 (run_id, statement_sha) 重复（registry 幂等收敛，写入层处理） | n/a（写入层） |
| **R10（rev2 正式化）** | **unreferenced bound claim / citation coverage** | claim 存在 ≥1 claim_evidences binding，但没有任何 citation → 该 claim 未进入报告引用 | warning |

- R3 vs R10 区分（rev2 明确）：
  - **R3** = claim **没有任何 Evidence binding** → unsupported claim；
  - **R10** = claim **有 Evidence（bound），但未被任何 Citation 覆盖** → citation coverage warning（bound 却未入报告 = 潜在遗漏）。
- ViolationReport 结构：`{run_id, errors[], warnings[], counts:{claims, unsupported(R3), unreferenced(R10), cited}}`。

---

## 8. Unsupported claim & coverage detection

- **unsupported（R3）**：claims 中无任何 claim_evidences 的 claim。检出后**不删除/不改写**。
- **coverage（R10）**：有 binding 但无 citation 的 claim；报告完整性视角的遗漏提示。
- **覆盖率派生指标**（供给 F5，不在 F2 实现产品化）：`cited_claims / bound_claims`、`bound_claims / total_claims`。
- 消费方（未来）：report finalizer 对 unsupported 显式标注"证据不足"；对 R10 决定是否补引/标注。
- F2 交付 = 检测能力（R1–R10 规则）与报告视图（violations + counts），不含自动修复。

---

## 9. Deterministic vs semantic validation boundary

| 判定 | 类型 | F2 范围 |
|---|---|---|
| 引用存在性/同 run/quote 子串/locator 合法/unsupported/coverage | deterministic（Python 规则） | ✅ |
| "quote 是否真正支持 statement" | semantic（LLM，Verification F8） | ❌ |
| claim_type 与证据形态匹配（STATISTIC↔数值等） | semantic（可规则辅助） | ❌ 未来（claim_type 入库但 F2 不消费） |
| 冲突/矛盾 | semantic（对齐器 + LLM） | ❌ |
- 原则：引用图+文本可判定的全部 deterministic；语义判断留 Verification 阶段；F2 不引入隐式 LLM。

---

## 10. Replay / idempotency strategy

| 实体 | 策略 |
|---|---|
| claims | 内容级幂等：`UNIQUE(run_id, statement_sha)`，ON CONFLICT DO NOTHING → 返回既有 claim_id（语义相同陈述重复 = 无信息重复；与 F1 query/evidence append 语义的差异是**有意设计**：那些是新的执行/检索，claims 是同一 finalize 产物） |
| claim_evidences | 幂等：`UNIQUE(claim_id, evidence_id)` |
| citations | 幂等：`UNIQUE(run_id, claim_id, evidence_id)`；identity = citation_id(uuid)，**与顺序/编号无关**（rev2） |
| query/evidence（F1） | 维持 append（既有承诺不变） |
| 呈现编号 [n] | **不持久化** → 不存在 replay 重编号问题；每次渲染按 §6.2 确定性排序派生（rev2） |

**task_call_id 兼容性**：F2 不引入（外部副作用幂等域，Phase 0 §4.10 契约，后续阶段）；
若未来引入，拦截在工具执行前，与 claims/citations 的内容级自然键去重正交、不冲突。

---

## 11. Migration strategy

- 0002 仅新增 3 张表 + 索引，零 ALTER F1 表；走 F1 幂等 runner（schema_migrations）。
- 双后端同版本文件；research_* 表族 app-owned；checkpoint 表族 saver-owned，F2 不触碰。
- rollback：移除 0002 双文件 + 按需 DROP 测试库表；不触碰 0001 数据。

---

## 12. API / provenance / report integration

### 12.1 Registry（deterministic 写入 + 守卫）
- `create_claim(run_id, sub_question_id, statement, claim_type, metadata) -> claim_id`（同 run 同 sha 幂等；守卫：run/subq 存在且同 run）
- `bind_claim_evidence(run_id, claim_id, evidence_id, metadata) -> binding_id`（守卫：claim/evidence 存在且同 run）
- `create_citation(run_id, claim_id, evidence_id, quote=None, locator=None, metadata=None) -> citation_id`
  （写前守卫：claim/evidence 存在且同 run，且 **(claim_id,evidence_id) 必须已 binding（R2）**；幂等返回既有）
- `apply_validation_outcome(run_id, report)`：finalizer workflow 专用——依据 validator 结果把
  **有 ≥1 citation 覆盖** 的 claim 置为 `validated`（其余保持 drafted）。**仅此函数写 claim.status**；
  validator 本身不写。

### 12.2 Validator 与 finalizer 职责（rev2 澄清）
```text
validate_run(run_id) -> ViolationReport      # 纯函数：读 + 规则计算，零 side effect
finalizer workflow（调用方）：
   report = validate_run(run_id)
   ...人工/上层决策...
   apply_validation_outcome(run_id, report)  # 唯一 status 演进入口（drafted → validated）
```
- validator 不 import 写路径；任何"更新"都必须经 finalizer/registry workflow。

### 12.3 Provenance 扩展
- `get_claim_chain(claim_id)`: claim → claim_evidences → evidences → sources（含 quote/locator）
- `list_claims(run_id)` / `list_citations(run_id)`（按 created_at, citation_id 排序）
- `render_citations(run_id)`: §6.2 确定性编号视图（不落库）

### 12.4 Report integration（设计契约，不实现）
- 报告方用 `list_citations` + `get_claim_chain` 渲染"结论 → 引用卡片"；编号用 `render_citations` 派生 `[n]`；
- `[n]` 只存在于渲染产物（报告快照），不回流 citation 表；杜绝"引用不存在的来源"（R1/R2/R7 前置）；
- F2 不改 Agent-visible context、不引入 Verification Agent。

---

## 13. Test strategy

- 沿用 F1 双后端纪律：sqlite 默认全量；PG 用例 `RESEARCH_DSN_TEST` 门控（独立库）。
- 新增（实施阶段）：`tests/test_claim_registry.py`、`tests/test_claim_evidence_binding.py`、
  `tests/test_citation_registry.py`、`tests/test_validate_rules.py`（R1–R10 全矩阵 fixture）、
  `tests/test_claim_provenance.py`（链 + render_citations 确定性）、PG 镜像（skipif）。
- render_citations 测试：同一 snapshot 两次渲染编号一致；新增 citation 只追加序号且已有编号不变；
  identity 与编号无关（插入顺序颠倒不影响已输出快照外的排序稳定性）。
- validator 纯度测试：`validate_run` 调用前后 claims.status 不变（mock 断言无写）。

---

## 14. F2 AC criteria

| AC | 内容 |
|---|---|
| F2-AC1 | claims：创建 + `(run_id,statement_sha)` 幂等（重复 create 同 claim_id）；type/status 枚举受控 |
| F2-AC2 | claim_evidences：M:N 双向；`(claim_id,evidence_id)` 幂等；跨 run 绑定写前拒绝（R6） |
| F2-AC3 | citations：创建 + `(run_id,claim_id,evidence_id)` 幂等；无 binding 引用被写前守卫拒绝（R2）；**identity=citation_id 稳定，无呈现编号列** |
| F2-AC4 | validator 规则 **R1–R10** 全矩阵 fixture 通过（sqlite 全量；PG 镜像门控）；validator **零 side effect**（status 不变） |
| F2-AC5 | unsupported（R3）与 unreferenced coverage（R10）分别检出；artifact 不被改写 |
| F2-AC6 | provenance：claim→evidence→source 链可达；citation 视图含 quote/locator；`render_citations` 确定性编号（重复渲染一致） |
| F2-AC7 | F1 回归：既有全部 tests 绿（270 passed/1 skipped 双 DSN 状态不变）；ruff/compileall 绿 |
| F2-AC8 | 0002 migration 幂等 + 与 0001/checkpoint 表族边界（PG 探针） |
| F2-AC9 | 文档同步（ARCHITECTURE/DECISION/ROADMAP/TESTING） |
| F2-AC10 | finalizer workflow：`apply_validation_outcome` 是唯一 status 演进入口（validator 无写） |

---

## 15. Gate criteria

1. 实体/基数决策无异议（Claim 可无证据；M:N；Citation=claim+evidence 锚点；不冗余 source 列）；
2. **rev2 编号语义**认可：citation_id 稳定、`[n]` 不落库、render_citations 确定性派生；
3. deterministic/semantic 边界认可（validator 无 LLM、无写）；
4. replay 策略认可（claims 内容级去重 vs F1 append 差异；不引入 task_call_id）；
5. R1–R10 规则集 + 双保险（写前守卫 + validator 兜底）分工确认；
6. F1 兼容红线认可 → 方可进入 Implementation（届时才创建 0002 迁移）。

---

## 16. Risks / non-goals

### Non-goals
- ❌ Verification / Conflict / Source Reliability / Replan / Graph / Memory / Planner；新 Agent；新基础设施；
- ❌ F1 schema/Evidence 语义修改、Tool 签名、Agent-visible context；
- ❌ LLM 校验或 claim 自动抽取（structured extraction 留后续）；
- ❌ report 渲染产品化 / coverage 指标产品化（F5 提供）；❌ 持久化呈现编号。

### Risks
| 风险 | 缓解 |
|---|---|
| 内容级去重（sha）误伤同义不同句 | 去重键=逐字 sha；同义不同句各成 claim（合理）；语义归并属后续域 |
| 渲染编号随数据变化（rev2 已消除主风险） | [n] 不落库；每快照渲染确定性排序；历史报告快照各自冻结 |
| validator/status 职责混淆 | §12.2 分离 + F2-AC4/AC10 强制纯度与单入口 |
| F1 冻结被"顺手改" | §17 红线表 |

---

## 17. 与 F1 schema 的兼容性说明（红线）

| F1 元素 | F2 影响 |
|---|---|
| 五张表 | **零修改**（无 ALTER） |
| evidences.content/locator | 只读引用；quote 快照另存 citations |
| registry/provenance/store/migrations | 只增函数与 0002 文件；现有签名不变 |
| 既有测试 | 不改 |
| schema_migrations | 追加 0002 |
| Agent-visible context / tools / checkpoint | 不触碰 |
| run_id correlation | claims/citations 直连 research_runs，同 correlation identity |

---

## 18. 审核提示（Gate 已答复 6 项：G1–G6 PASS）

6 项 Gate 结论已确认（Claim 可无 Evidence / 拆两表 / 不冗余 source_id / 内容级去重 / R2 双保险 / section 进 metadata）。
若 rev2 修订（§19）无异议，即可批准进入 F2 Implementation。

---

## 19. rev2 修订记录与决策汇总（2026-09-08，Spec-only）

### 修改项（对应四项修订要求）
| # | 修订 | 落点 |
|---|---|---|
| 1 | citation_key 稳定性语义重定：**移除 citations 呈现编号列**；citation_id=稳定 artifact identity；`[n]` 由 render_citations 在渲染时确定性派生、不落库、不承诺跨渲染稳定 | §2.4/§3/§4/§6.1-6.2/§10/§14-AC3/§15 |
| 2 | R10 正式定义（unreferenced bound claim / citation coverage，warning）；R3=R3 无绑定 unsupported 区分；同步 §8/§13/AC4 引用 | §7/§8/§13/§14-AC4/AC5 |
| 3 | validator 纯函数化 + claim_status 演进移交 finalizer（`apply_validation_outcome` 唯一写入口） | §2.1/§7 intro/§12.2/§14-AC4/AC10/§15 |
| 4 | quote/locator 语义锁定：evidence.content=候选快照、evidence.locator=source-level；citation.quote=content 子片段（≤500，R8 子串）；citation.locator=可选报告/来源定位；不新增 quote_start/end | §2.2/§2.4/§6.3/§7-R8 |

### 保留不变（用户确认项）
Claim 可无 Evidence；ClaimEvidence M:N 表示 selected/used；Citation 双绑定、不冗余 source_id；
deterministic 校验无 LLM；semantic 支持判断留 Verification；claims `(run_id,statement_sha)` 幂等；
不引入 task_call_id；F1 五表零 ALTER；0002 仅 3 张新表；不新增 Agent/基础设施；不改 Agent-visible context；
claim_type 枚举保留为扩展点且 F2 validator 不消费其语义；不进入 Verification/Conflict/Reliability/Replan/Graph/Memory。

### 汇报速览
1. **修改项**：见上表（4 项）;
2. **最终数据模型**：claims / claim_evidences / citations（无呈现编号列），schema 见 §3；
3. **R1–R10 完整规则**：§7（R3=unsupported，R10=coverage warning，validator 只读）；
4. **citation_key 最终语义**：不存在（rev2 移除）——identity= citation_id；呈现 [n] 渲染时派生（§6.1–6.2）；
5. **validator/status 职责边界**：validator 只算 ViolationReport；status 演进仅经 finalizer 的 `apply_validation_outcome`（§12.2）；
6. **更新后的 AC/Gate**：§14（新增 AC10 纯度/单入口）、§15（新增 rev2 编号语义 Gate）。
