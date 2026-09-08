# Spec: 2026-09-09 — F3 Semantic Verification（Claim 是否真被 Evidence 语义支持）· rev2

> 状态：**Draft rev2（Conditional Approve 修订版）— Review/Gate 通过前不实现**
> rev2 修订（Spec-only）：① Citation Coverage 改为默认候选策略而非硬前置；
> ② SemanticVerdict 与 VerifyStatus 拆分；③ confidence 语义澄清；④ Evidence Context 收紧
> source 边界；⑤ 统一 partial/failure 语义（partial 仅 batch 层）。修订明细见 §23。
> 阶段链：F1 ✅(Frozen) → F2 ✅(Frozen) → **F3 semantic verification** → F3+（Conflict Resolution 等）
> 红线：非"再造 Verification Agent 调 LLM"；不新增 Agent、不改 Orchestration / Agent-visible
> context / Tool / Checkpoint / F1/F2；本阶段零代码、零 migration、零依赖改动。

---

## 0. 分层定位

```text
F2（已冻结）：“这条 Citation 是否存在？Claim 有没有绑定 Evidence？quote 是否来自 Evidence？
             有没有 citation coverage？”            → deterministic validation（R1–R10）
F3（本 Spec）：“这条 Evidence 的内容，是否真的足以支持这个 Claim？” → semantic verification
```

- **被引用（cited）≠ 被语义验证（verified）**——绝不允许从 citation 存在性推导"已 verified"。
- F2 结构 Gate 与 F3 候选/预算策略是**不同维度**（§3/§4 修正），不再相互隐含。

---

## 1. Architecture boundary

F3 落在 **Research Data Plane 的一个只读消费者 + 一个可持久化产物**：

```text
research plane（F1/F2 冻结）
  claims / claim_evidences / evidences / citations / validate(R1–R10)
        │ 输入（只读）
        ▼
semantic_verify(run_id, claim_id)   ← 服务层（app.research 内，future 模块 verify.py）
  ├─ 结构 Gate（F2 errors）＋ deterministic pre-filter/budget（§3/§10）
  ├─ per-(claim,evidence) LLM judgment（边界 §7；仅作窄任务）
  ├─ 结果持久化 verifications（§2/§6；单条只有 pending/succeeded/failed）
  └─ claim 级派生聚合（只读；不写 claims）
```

- **不新增 Agent**：Verifier 是 artifact 平面内的确定性编排 + 可替换 LLM 通道，
  与 DeepAgents/run_deep_agent 无关，不进 Agent 回路、不进 Agent-visible context。
- 调用发生在 **run 之后/研究后处理**（finalize 工作流），非 Agent 执行回路。

---

## 2. Entity model（新增：verifications；零 ALTER F1/F2）· rev2

```text
verification_id      TEXT PK                # uuid4 hex（稳定 artifact identity）
run_id               TEXT FK → research_runs
claim_id             TEXT FK → claims
evidence_id          TEXT FK → evidences    # 验证粒度 = (claim, evidence) 对（已定案，见 §22）
verifier_spec        JSONB/TEXT NOT NULL    # provider/model/prompt_version/temperature 等快照
verifier_fingerprint TEXT NOT NULL          # sha256(verifier_spec 规范串)
verdict              TEXT NULL              # SemanticVerdict（§5）；仅 status=succeeded 时非空
rationale            TEXT                   # 理由（截断 ≤2000，可选）
confidence           REAL NULL              # 辅助信号（§7b 语义；允许 NULL，禁止伪造）
status               TEXT NOT NULL          # VerifyStatus：pending | succeeded | failed（§5b/§12）
error                TEXT                   # 仅 status=failed 时的原因快照（§12，不含敏感内容）
created_at / completed_at  TIMESTAMPTZ/TEXT
metadata             JSONB/TEXT NOT NULL DEFAULT '{}'
UNIQUE (run_id, claim_id, evidence_id, verifier_fingerprint)
```

- 单条 (claim,evidence) 同一 spec 重跑 → 收敛同一条（§11）；spec 升级 = 新 fingerprint = 新行。
- claim 级状态不改 claims 表（F2 冻结），由查询派生聚合（§6）。

---

## 3. Candidate 与 Gate（rev2：分离"结构 Gate"与"候选/预算"）

| 维度 | 语义 |
|---|---|
| **结构 Gate** | `validate_run(run_id).errors`（R1/R2/R4/R5/R6/R7/R9 等 error）→ 该 run 的 semantic verification **硬前置门**：errors 非空时拒绝写入 verifications。R3/R10 为 warning，**不自动阻止**单条 (claim,evidence) 语义验证 |
| **候选关系** | `claim_evidences` binding 是验证候选集合（默认 allowed_evidence_ids = 该 claim 全部 binding 的 evidence） |
| **候选优先级（默认策略，非逻辑必要）** | `citations` 覆盖的 evidence 作为默认 **prioritization / budget 排序**（先验证被引用证据）；不是 verification 的逻辑必要条件 |
| **显式候选** | `allowed_evidence_ids` 可显式指定（支持验证未引用但已 binding 的证据，供 eval/质量检查） |
| **不可推导** | 任何场景都**不允许**把"有 citation"推导为"已经 verified"；citation 只影响预算顺序 |

```text
input  = VerifyRequest(claim_id, run_scope=run_id,
                       allowed_evidence_ids=None,   # None → 默认取 binding 证据并优先 cited
                       use_quote_first=True,        # 有 quote 时优先用 quote 定位片段
                       verifier_spec=default, budget=..., timeout_ms=...)
```

---

## 4. Deterministic vs semantic boundary

| 能力 | 归属 |
|---|---|
| 结构完整性 R1–R10、引用存在性、coverage | F2 deterministic（已有） |
| evidence 文本是否支持/不足/矛盾/不可判 claim | **F3 semantic**（LLM 窄任务） |
| quote 子串、span 定位、fingerprint 幂等、Gate、budget、超时/重试/解析 | F3 **deterministic**（代码） |
| 跨 evidence 冲突标记（verdict 互斥） | F3 deterministic 聚合（不裁决） |
| 裁决谁对、调和、生成最终说法 | ❌ 后续 Conflict Resolution 阶段 |

---

## 5. Decision model（rev2：Verdict 与 Status 分离）

### 5a. SemanticVerdict（semantic 层输出，仅 5 种，无 ERROR）
| verdict | 含义 |
|---|---|
| `SUPPORTS` | evidence 内容实质支持 claim |
| `INSUFFICIENT` | 内容相关但不足以确立 claim（范围/口径/数量不足） |
| `CONTRADICTS` | evidence 内容与 claim 冲突 |
| `UNVERIFIABLE` | 环境/信息不足导致无法判断（区别于模型主动 abstain） |
| `ABSTAIN` | 模型主动拒绝或系统低置信 policy 触发（§7b） |

### 5b. VerifyStatus（执行层状态，仅 3 种）
`pending | succeeded | failed`
- semantic 成功 → `status=succeeded` 且 `verdict ∈ 5 种之一`；
- execution/provider/parse 失败 → `status=failed` 且 `verdict=NULL` + `error` 快照；
- **`partial` 不是单条 status**：属 batch/run 层执行结果（§12）。
- claim 级派生聚合（只读）：全 SUPPORTS→supported；存在 CONTRADICTS→contested；
  存在 INSUFFICIENT/UNVERIFIABLE/ABSTAIN 且无冲突→partially_supported；存在缺失/failed→unverified 子集（不伪造 verdict）。

---

## 6. Persistence decision（明确：需要）

- **是**：verification（spec/verdict/status/失败）必须持久化（§2），支撑 eval、报告可信度与
  `Claim → Verification Result → verifier/model/config`（§13）。零 ALTER F1/F2；聚合只读派生。
- 清理：随 run CASCADE；单条删除不影响 claim。

---

## 7. LLM invocation boundary

### 7a. 通道与接入
- `VerifierSpec { provider, model, prompt_version, temperature, max_tokens, timeout_ms }`
  配置驱动（沿用现有 OpenAI 兼容栈的最小适配，零新依赖倾向）。**禁止**挂在 DeepAgents/Agent 里。
- 何时介入：通过结构 Gate + deterministic pre-filter（§10）且 budget 允许。
- 输入不含完整 message history / Agent 会话；只含 §8 包；结构化 JSON 输出；失败按 §12。

### 7b. confidence 语义（rev2 澄清）
- `confidence` 是 **verifier/模型提供的辅助信号，不具统计概率语义**；
- threshold 是 **deterministic acceptance policy**（系统侧）：
  `confidence < threshold → verdict=ABSTAIN`（policy 结果，不意味着模型 confidence 是客观概率）；
- `NULL` 合法：无该信号时不得为满足 schema 而伪造数值（status=succeeded 且 verdict 非空时可 NULL）。

---

## 8. Context construction（rev2 收紧 source 边界）

```text
Evidence Context（LLM 唯一输入包，代码组装）
  claim:     { statement, claim_type, claim_id }
  evidence:  { content（≤8000，F1 已截断）, quote?（≤500,若存在）,
               source_meta: { title?, canonical_url/locator?, published_at? }   ← 仅标识
               extraction_method }
  instruction: “基于 evidence.content / quote 判断是否足以支持 claim；只输出 JSON
               {verdict, rationale, confidence}；INSUFFICIENT/UNVERIFIABLE/ABSTAIN 合法，
               禁止编造支持。source 标题/URL/元数据只用于标识，不得作为证据内容。”
```

- **factual basis 边界**：`Evidence.content` / `Citation.quote` 是 semantic judgment 的
  **事实依据**；`source.title / canonical_url / published_at / locator` 仅作
  **provenance / identification**，不是独立证据内容。
  > Source metadata is provenance context, not evidentiary content.
- 禁止 verifier 仅依据 source title/URL/元数据推断 claim 被支持（prompt + 输入构造双层保证）。
- 不混入：message history、其他 claim 结论、run 计划、用户 prompt 原文（claim 本身除外）。

---

## 9. Conflict boundary（F3 检测/标记，不解决）

- 同一 claim 两条 evidence 分别 SUPPORTS / CONTRADICTS → F3 只记录两组 verdict+rationale，
  聚合视图标记 `contested`；
- 交叉 claim 冲突：不做对账，留给 Conflict Resolution；
- 非目标：winner 选择、调和表述、CONFLICT_UNRESOLVED 产品语义、Source Reliability 参与裁决。

---

## 10. Budget / cost control（rev2：候选策略表述修正）

- **不是每条都验证**：deterministic pre-filter / budget：
  1. 验证候选 = 该 claim 的 binding evidence；**默认优先已绑定且被 citation 覆盖的**（候选优先级策略，
     非逻辑必要条件）；`allowed_evidence_ids` 可显式覆盖；
  2. R3/R10 warning 不阻止单条验证（§3）；
  3. run/claim/evidence 与 token/调用上限：`max_claims_per_run`、`max_evidence_per_claim`、
     `total_llm_calls`、`max_tokens_total`；超限即停并返回 batch 统计（含 budget_exhausted）；
  4. OPINION 默认跳过（可配）；ABSTAIN/UNVERIFIABLE 不重试；failed 按 §12 确定性重试 1 次。
- pre-filter 零 LLM 成本。

---

## 11. Replay / idempotency

- 自然键 `UNIQUE(run_id, claim_id, evidence_id, verifier_fingerprint)`；
- status=succeeded 复用；status=failed 可在 spec 不变下重试（仍收敛同条，更新为 succeeded/failed 终态）；
- spec 升级 = 新 fingerprint → 新行（支持模型对照/eval）；
- **不引入 task_call_id**；无 round 字段（重新验证 = 换 spec）。

---

## 12. Failure semantics（rev2：统一 partial 归属）

**单条 (claim,evidence) artifact 只允许 `pending / succeeded / failed`。**
`partial` 只属于 **batch/run 层执行结果**（如 `semantic_verify_run()` 返回的
execution summary：`{succeeded, failed, skipped, budget_exhausted}`）。

| 场景 | 单条落库 | batch/run 层 |
|---|---|---|
| LLM timeout / provider failure | status=failed，verdict=NULL，error 快照；确定性重试 1 次（退避）仍失败则终态 failed | 计入 failed |
| malformed output | 解析重试 1 次（提示纠正）→ 仍失败 status=failed | 计入 failed |
| low confidence（<threshold） | policy → `status=succeeded, verdict=ABSTAIN`（§7b；非失败） | 计入 succeeded(ABSTAIN) |
| UNVERIFIABLE | status=succeeded, verdict=UNVERIFIABLE | 计入 succeeded(UNVERIFIABLE) |
| 部分证据失败、其余成功 | **不回写单条 partial**；各自独立落库 | run/claim batch 返回 partial summary |
| claim 聚合遇缺失/失败 verification | 聚合标记 unverified/partial 子集，**不伪造 verdict** | — |

- research plane fail-open：verifier 不可用不影响 Agent/报告主链路（仅产 unverified 状态）。

---

## 13. Provenance

```text
Claim → Evidence → Source                          （F2 get_claim_chain）
Claim → Verification Result → verifier/model/config （verifications 行 + verifier_spec 快照：
        provider/model/prompt_version/fingerprint/created_at）
```
- verdict 可溯源到具体 spec 与时间戳；eval 用该链做模型版本对照。

---

## 14. API contract（设计，不实现）

```text
semantic_verify_run(run_id, spec?, budget?) -> execution summary（含 succeeded/failed/skipped/budget_exhausted）
semantic_verify_claim(claim_id, spec?, allowed_evidence_ids?) -> VerifyResult
list_verifications(run_id, *, claim_id?, verdict?) -> list[..]
aggregate_claim_verdict(claim_id) -> {supported|contested|partially_supported|unverified}
```
- 全部显式 run 作用域；只读聚合；写入仅经 verification 持久化唯一路径。

---

## 15. Test strategy

- deterministic：Gate/pre-filter/budget/fingerprint 幂等/重试/解析/聚合/冲突标记——fixtures + sqlite 全量；
  LLM 通道用 **fake verifier**（注入 verdict/异常），不依赖真实模型；
- PG 镜像（RESEARCH_DSN_TEST）：schema/幂等/聚合同语义；
- Gate 测试：run 存在 R1/R2/R6 error 时入口拒绝；R3/R10 warning 不阻止单条验证；
- 状态机测试：succeeded+verdict5 种、failed+verdict=NULL；无单条 partial。

---

## 16. Evaluation strategy（设计，不实现系统）

- verdict×人工标注一致率；ABSTAIN 率；spec 对照 verdict 漂移；与 citation coverage 解耦度量；
- confidence 不作为客观概率参与指标（只做 policy 阈值展示）。
- 数据：verifications 自带 spec 快照 + run 隔离 → 天然标注/对照集。

---

## 17. AC criteria

| AC | 内容 |
|---|---|
| F3-AC1 | `verifications` 表 0003 双后端迁移幂等，零 ALTER F1/F2；边界探针 |
| F3-AC2 | 结构 Gate：errors 非空拒绝写入；R3/R10 warning 不阻止单条验证 |
| F3-AC3 | SemanticVerdict=5 种、VerifyStatus=3 种；succeeded↔非空 verdict；failed↔verdict NULL；**无单条 partial** |
| F3-AC4 | 幂等：(run,claim,evidence,fingerprint) 收敛同 id；换 spec 新行；failed 重试终态正确 |
| F3-AC5 | failure：timeout/malformed/provider/low-confidence(→ABSTAIN policy)/部分失败各路径正确；partial 仅 batch summary |
| F3-AC6 | 冲突：SUPPORTS+CONTRADICTS → contested（只标记） |
| F3-AC7 | provenance：claim→verification→spec 链可达 |
| F3-AC8 | budget/pre-filter：优先 cited（默认策略）+ allowed_evidence_ids 显式覆盖 + 上限即停 |
| F3-AC9 | source metadata 不作为 evidentiary content（prompt/构造双保险测试） |
| F3-AC10 | 双后端 sqlite/PG 同语义；F1/F2 全量回归不破；文档同步 |

---

## 18. Gate criteria（Review 通过条件）

1. §3 结构 Gate / 候选关系 / coverage=默认预算策略 / allowed_evidence_ids 认可；
2. §5 Verdict(5)–Status(3) 分离与 §12 partial 仅 batch 层认可；
3. §7 LLM 边界与 §8 source 边界认可；
4. §6 独立 verifications 表持久化认可（零 ALTER）；
5. §10 budget / pre-filter 认可；
6. §11 fingerprint 幂等（无 task_call_id）认可；
7. §22 保留 Open Questions 的裁决（real-LLM adapter 是否允许、ABSTAIN/UNVERIFIABLE 边界、confidence nullable=YES）
→ 通过后才进入 Implementation（届时才创建 0003 与 fake-verifier 测试）。

---

## 19. Risks

| 风险 | 缓解 |
|---|---|
| LLM verdict 漂移 | spec 快照 + fingerprint 对照；ABSTAIN 通道；eval 指标后置 |
| 语义判断混入结构判断 | 分层硬边界 + Gate（AC2）+ prompt 禁结构动作 |
| coverage 误当已验证 | §0/§3 不可推导红线 + AC2/AC8 测试 |
| 成本失控 | pre-filter + 上限 + 确定性重试次数；allowed_evidence_ids 控制 |
| conflict 越界 | §9 + AC6 |
| 单条 partial 误用 | §5b/§12 + AC3/AC5 状态机测试 |
| 新 Agent/编排耦合 | §1 + 独立 verifier 服务不进 Agent 回路 |

---

## 20. Explicit non-goals

- ❌ 不新增 Agent / 不改 Orchestration / 不入 Agent-visible context
- ❌ 不实现 Conflict Resolution（winner/调和/CONFLICT_UNRESOLVED 产品语义）
- ❌ 不实现 Source Reliability / Replan / Claim Graph / Memory
- ❌ 不引入 task_call_id / 新基础设施
- ❌ 不实现完整 Eval 系统
- ❌ 不改 F1/F2/Phase0 schema、migration、代码；不改 Tool 签名/依赖

---

## 21. F1/F2 compatibility

- 新增 0003（verifications）+ 派生只读聚合；F1/F2 表零 ALTER；
- 依赖 F2：claim_evidences（候选对）、citations.quote（默认优先 span）、validate_run（Gate）、
  evidence.content/locator（不变）；claim.status 语义不变（F3 不写 claims）。

---

## 22. Open Questions（已清理）

**已关闭（本轮裁决）**
- Verification 粒度固定为 `(claim, evidence)`。✅
- Spec 文件名确定：`docs/spec/2026-09-09-semantic-verification.md`。✅

**保留待决（进入 Implementation 前需裁决）**
1. ABSTAIN vs UNVERIFIABLE 的**严格语义边界**（建议：ABSTAIN=模型/系统主动拒绝；UNVERIFIABLE=环境与信息客观不足）；
2. controlled real-LLM adapter 是否允许在 F3 Implementation 中出现（建议：仅受控/离线调用，默认全 fake 先行）；
3. `confidence` nullable：建议 **YES**（§7b 已按 YES 表述，待确认）。

---

## 23. rev2 修订记录（Spec-only，本轮）

| # | 修正 | 落点 |
|---|---|---|
| 1 | Coverage = 默认候选优先级/预算策略，非 verification 硬前置；结构 Gate 仅 F2 errors；binding=候选；allowed_evidence_ids 显式；R3/R10 warning 不阻止单条；禁止"cited⇒verified" | §0/§3/§10/§17-AC2/AC8 |
| 2 | SemanticVerdict(5，无 ERROR) 与 VerifyStatus(pending/succeeded/failed) 拆分；failed→verdict NULL；partial 仅 batch/run 层 | §2/§5/§12/§17-AC3/AC5 |
| 3 | confidence=辅助信号（非统计概率）；threshold=deterministic policy；NULL 合法禁伪造 | §7b/§2/§16 |
| 4 | content/quote=factual basis；source metadata=provenance 非证据内容；禁止仅凭 metadata 判支持 | §8/§17-AC9 |
| 5 | 单条状态仅 pending/succeeded/failed；partial 属 batch summary（succeeded/failed/skipped/budget_exhausted）；claim 聚合不伪造 verdict | §2/§5b/§12/§17-AC3/AC5 |

---

## 24. 汇报速览（按 Gate 要求）

1. **Spec 变更摘要**：§23（5 项修订）；
2. **更新后的 decision model / schema / failure semantics**：§5（Verdict5/Status3）、§2（verifications schema）、§12（failure+partial 归属）；
3. **AC / Gate**：§17（AC1–AC10）、§18（7 项 Gate）；
4. **Open Questions**：§22（2 项已关闭 + 3 项待决）；
5. **STOP**：本轮只修改了 F3 Spec 文档；未创建 0003、未写 Python/SQL、未改依赖、未触碰 F1/F2 冻结代码。
