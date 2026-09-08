# F5 Capability Audit, Direction Decision & Spec rev2 — Independent Evidence Corroboration

> 状态：**F5 Spec rev2 — 待 F5 Final Gate Review；禁止 implementation**
> rev2 修订（Spec-only）：① Claim 内 global clustering；② deterministic clustering 算法锁死（含 chaining）；
> ③ source_profile 仅 descriptive；④ independent_count / cross-side independence 正式定义；
> ⑤ deterministic vs optional-review failure 语义锁死；⑥ Context Envelope 补齐 identification metadata；
> 另关闭 Open Questions（§A-OQ）并强化 AC。修订明细见 §R。
> 红线：本轮未改任何 app/migration/tests/Agent/Tool/Checkpoint/依赖/基础设施；仅修改本 Spec 文件。

---

## 0. 审计与选型结论（rev1 保留，仍有效）

F1–F4 已建成"证据链+结构校验+语义验证+冲突发现"的分析面；下一个真实 correctness gap 是
**来源独立性计量**：转载/同源被误当独立 corroboration，冲突两侧独立性未知。
方向 A（Independent Evidence Corroboration / Source Independence Measurement）为主能力；
B（Resolution）依赖 A 暂缓；C（Research Control）跨 runtime 后置；D（Claim Graph）明确不做。
（完整审计见 rev1 §1-§3，本文件 rev2 聚焦 Spec 修订。）

---

# Spec: F5 Independent Evidence Corroboration · rev2

## 1. One-line positioning

> 计量"Claim 的 SUPPORTS/CONTRADICTS 证据背后有几个真正独立的信息源簇；转载/同源是否被误当独立
> corroboration；冲突两侧来源簇是否独立"——只计量、只呈现、不裁决。

## 2. Problem Statement

- 转载链 A→B→C→D 被误当 4 个独立来源（corroboration 被高估）；
- F4 confirmed 冲突两侧可能出自同一原始来源（非真正独立反证）或真正独立来源（应保留 contested）；
- 报告/未来 resolution 缺少"独立簇计数 + descriptive 质量分布"输入。

## 3. F1–F4 Capability Audit
见 rev1 §1 表格（冻结代码层面，无变化）。

## 4. Gap Analysis
见 rev1 §4（无变化）。

## 5. Goals（rev2 强化）
1. **Claim 作用域 global clustering**：一个 claim 收集其全部相关 evidence/source，**统一聚类**，
   再投影到 SUPPORTS / CONTRADICTS（不得分别聚类）；
2. deterministic clustering v1 算法锁死（§9a），可复现、无实现自由发挥；
3. independent_count = distinct global cluster count；cross-side independence 基于 cluster 交集；
4. descriptive source_profile（无 authority/reliability 语义）；
5. 持久化新 artifact，指纹可复现；失败语义锁定（§11b）；只读消费。

## 6. Non-goals（写死）

> **F5 measures independence; it does not score trust.**
> independent_count 是"独立来源簇数量"的计量信号，不是 confidence、truth probability、
> authority score 或 reliability score。

- ❌ Winner/ranking/weighting/Source Reliability score-as-truth/Resolution/Reconciliation；
- ❌ 修改 run loop/Agent（C）或 Graph（D）；❌ same-verdict 语义；❌ 跨 claim 依赖；
- ❌ 新 Agent/基础设施/LLM framework；❌ ALTER F1–F4 表；❌ confidence 概率化。

## 7. Terminology
- source cluster（同源簇）：判定为转载/镜像/同机构发布的一批 source 集合；
- source_count：该 claim 相关 evidence 所引用的 distinct source_id 数；
- independent_count：distinct **global cluster id** 数；
- corroboration：SUPPORTS 侧独立簇数（计量，非真值保证）；
- source_profile：descriptive statistics（无评分语义）；
- independent_between_sides：F4 confirmed 冲突两侧 cluster id 无交集（仅计量，非 winner/可信信号）。

## 8. Capability boundary
- 分析面后处理、只读消费者 + 新 artifact；不进 Agent loop；输出供报告展示与未来 resolution/replan 引用。

## 9. Data / Artifact model（0005 新增 corroborations；clusters v1 不拆表）

```text
corroborations:
  corroboration_id PK, run_id FK, claim_id FK,
  method_spec JSONB NOT NULL, method_fingerprint TEXT NOT NULL,
  status TEXT NOT NULL,            # 'complete' | 'failed'（单状态 artifact，无 partial）
  error TEXT,
  computed_at TEXT/TS,
  metadata JSONB NOT NULL DEFAULT '{}',   # review_failed/聚类版本等
  global_clusters: [{cluster_id, cluster_key, member_source_ids[], member_evidence_ids[],
                     member_side: 'support'|'contradict'|'both'}],   # claim 内统一（rev2）
  support:   {source_count, independent_count, cluster_ids[]},
  contradict:{source_count, independent_count, cluster_ids[]},
  conflicts_independence: [ {conflict_id,
                             side_a:{cluster_ids[], independent_count},
                             side_b:{cluster_ids[], independent_count},
                             independent_between_sides: bool} ],
  source_profile: { source_type_distribution:{}, domain_distribution:{},
                    publisher_distribution:{}, freshness_distribution:{}, agent_distribution:{} },
  UNIQUE (run_id, claim_id, method_fingerprint)
```

- **cluster identity 在 claim scope 内统一**：support/contradict 只是 global cluster 的投影视图；
- v1 不拆 corroboration_clusters 表（Open Q1 关闭：单表 + JSON metadata；cluster membership 用
  `global_clusters[].member_source_ids` 稳定可复现表示）。

### 9a. Deterministic clustering algorithm v1（rev2 锁死）

输入：claim 全部被验证(verdict∈{SUPPORTS,CONTRADICTS})证据 → 其引用 sources 全集 S（按 canonical_key 去重后）。

规则（按优先级顺序，命中即同簇）：
| rule | 定义 | 阈值/判定 |
|---|---|---|
| R-URL | canonical_key 相同 | 必同簇 |
| R-DOMAIN | 同 domain 且 path 去尾斜杠后前 3 段相同 或 path 为空 | 同簇 |
| R-TITLE | title 归一化（小写、去空白/标点/日期后缀）后 相等 或 Jaccard(titleA,titleB) ≥ 0.85 | 同簇 |
| R-SHINGLE | 各侧 evidence.content 的 5-gram shingle 集：Jaccard ≥ 0.60 | 同簇（转载正文近似） |

- 规则命中关系：**OR 语义**（任一键命中即归并）；同一对 source 不重复判定；
- **transitive chaining（rev2 锁死）**：使用 **deterministic union-find**：所有 pairwise 判定
  按 (source_id_a, source_id_b) 字典序严格遍历一次并 union；最终簇 = union-find 的 connected
  components。算法结果完全由输入决定 → `A≈B、B≈C、A≠C` 时 A、B、C 归同一簇（明确、非实现自由裁量）；
- cluster_key 生成：簇内 member source 的 canonical_key 字典序最小者 + `#<n>`（n=进程无关的确定性序号，
  按最小 key 排序后分配），保证跨运行可复现；
- determinism/reproducibility：输入（source 集合与 evidence content）不变 ⇒ 相同簇划分与
  cluster_key；方法版本经 method_spec→method_fingerprint 固定（Open Q5：method v1 = `corroboration.v1`）。

复杂度：pairwise 判定 ≤ (|S|²) 但受 `max_sources_per_claim`（默认 50）上限与 shingle 抽样（固定窗口）约束（§14）。

## 10. State model
- 单条 corroboration：`complete | failed`（无 partial）；`pending` 不落盘（计算完成后写入）；
- optional review 的失败**不改变** complete（见 §11b）。

## 11. Algorithm / workflow（rev2）
```text
compute_claim_corroboration(claim_id)
 1. gate: validate_run(run_id).errors 为空
 2. 收集 claim 全部被验证证据 → sources 全集（S）
 3. 执行 §9a deterministic global clustering（union-find）
 4. 投影：各 cluster → side∈{support,contradict,both}
 5. source_count / independent_count（=distinct cluster）按侧统计
 6. F4 confirmed conflicts → 两侧 cluster_ids → independent_between_sides = 交集为空
 7. source_profile descriptive 统计（§9b）
 8. optional LLM cluster-review（默认 OFF，Fake reviewer 测试；real 须显式启用）
 9. 持久化（fingerprint 幂等）
```

### 9b. source_profile（rev2：仅 descriptive，无评分）
```text
source_profile: {
  source_type_distribution,      # web/db/ragflow/upload 计数
  domain_distribution,           # 域名出现计数（仅展示，非 authority）
  publisher_distribution,        # publisher 计数（可为空）
  freshness_distribution,        # published_at/fetched_at 桶（<1y/1-3y/未知等）
  agent_distribution             # network_search/database/ragflow/upload 计数
}
```
明确：`source_profile ≠ reliability score`；`domain ≠ authority score`；
`independent_count ≠ confidence`；`independent_count ≠ truth probability`。

### 11b. Failure / retry（rev2 锁死）
```text
deterministic 计算成功 + optional review 失败
    → status = complete；metadata.review_failed = true（携带 reason）

deterministic 计算本身失败（gate/读取/聚类异常）
    → status = failed + error
```
- optional reviewer failure **不得**把整个 corroboration artifact 置 failed；
- Fake reviewer 默认（不入 Gate 的确定性通道）；real LLM controlled/off（`VERIFY_REAL_LLM=1`）。

## 12. Deterministic vs semantic boundary
- deterministic：聚类 v1、union-find、计数、分布统计、F4 冲突两侧独立、provenance、指纹；
- semantic（可选、受控、默认关）：跨域名转载/镜像的 cluster review（is_same_cluster + rationale），
  **只影响 cluster 划分的复核，不产生任何信任评分**。

## 13. Context Envelope（optional LLM review；rev2 补齐 identification metadata）
输入允许：
```text
sourceA/sourceB: { title, url, domain, publisher, published_at }
evidenceA/B content（≤8000/侧，必要时）
review instruction（is_same_cluster? 输出 {same: bool, rationale}）
```
锁死：不接 Agent history；不接 run plan；不接其他 claims；source metadata 仅 identification/context；
**不提供 authority score；reviewer 不做 reliability ranking**。

## 14. Idempotency / Budget / complexity
- identity=(run_id, claim_id, method_fingerprint)；同指纹收敛；方法升级=新行；
- 不引入 task_call_id；
- 聚类 pairwise ≤ max_sources_per_claim(默认 50)²；shingle 固定窗口；LLM review ≤ max_review_pairs(默认 0=off)。

## 15. Provenance
Claim → corroboration（global_clusters → sources → evidences）；F4 conflict → 两侧 cluster 独立性；
corroboration → method_spec/fingerprint/computed_at。

## 16. SQLite / PostgreSQL
0005 双后端同语义；runner 幂等；F1–F4 零 ALTER；checkpoint 表族边界保持。

## 17. API design（设计）
```text
compute_claim_corroboration(claim_id, *, method_spec?, reviewer?) -> profile
compute_run_corroborations(run_id, *, ...) -> batch summary
get_corroboration(claim_id) -> profile
conflict_independence(conflict_id) -> sides summary
list_corroborations(run_id, *, claim_id?)
```

## 18. Test strategy
- 聚类矩阵：R-URL/R-DOMAIN/R-TITLE/R-SHINGLE 各自触发；转载跨 SUPPORTS/CONTRADICTS 共享簇；
  A≈B、B≈C、A≠C → union-find 归同一簇（确定性断言，与实现顺序无关）；
- 计数：4 sources（2 转载同簇）→ source_count=4、independent_count=3（规范示例）；
- cross-side：两侧 cluster 交集为空 → true；共享簇 → false；
- source_profile 无 score 字段断言；review 失败 → complete+review_failed；deterministic 失败 → failed；
- Fake reviewer；幂等/升级；PG 镜像；F1–F4 回归。

## 19. Acceptance Criteria（rev2 强化）
| AC | 内容 |
|---|---|
| AC1 | 0005 幂等 + 零 ALTER + 表族边界 |
| AC2 | 同 claim 的 SUPPORTS/CONTRADICTS 使用**同一 global clustering**（投影视图） |
| AC3 | 转载跨两侧 → 同一 cluster（cluster_id 相同） |
| AC4 | independent_count = distinct global cluster count（示例 A/B 同簇,C,D 独立→4/3 正确） |
| AC5 | cross-side independence = 两侧 cluster_id 交集为空（否则 false）；**不解释为 winner/可信** |
| AC6 | A≈B、B≈C、A≠C → union-find 归同簇，行为 deterministic 且符合 Spec |
| AC7 | optional review 失败 → status=complete + metadata.review_failed；deterministic 失败 → failed |
| AC8 | source_profile 仅 descriptive（无 authority/reliability score 字段） |
| AC9 | independent_count 不被解释为 confidence/truth probability（Non-goal 措辞锁定） |
| AC10 | 双后端同语义；F1–F4 回归；文档同步 |

## 20. Migration / compatibility
0005 新增 corroborations；rollback 移除；F1–F4 只读消费；不写其表。

## A-OQ. Open Questions（rev2 已关闭）
1. clusters 拆表？→ **不拆**：v1 单表 + JSON metadata（global_clusters 稳定可复现表示）。✅
2. similarity thresholds → v1 默认值锁定：R-TITLE Jaccard ≥0.85；R-SHINGLE Jaccard ≥0.60；
   union-find 全量 pairwise（实现可配置化，但默认值即 Spec 契约）。✅
3. authority → F5 v1 **不做 authority scoring**；仅 descriptive source_profile。✅
4. LLM review → 默认 OFF；Fake reviewer 测试；real controlled probe 不进自动化 Gate。✅
5. method v1 → `corroboration.v1`；method_fingerprint = sha256(canonical method_spec)；
   方法升级 = 新 fingerprint/新行。✅

保留（非阻塞）：shingle 长度/窗口大小（默认 5-gram/固定 4k 抽样）与 freshness 分桶边界可调参确认。

## R. rev2 修订记录与 Self Review
| # | 修订 | 落点 |
|---|---|---|
| 1 | Claim 内 global clustering（统一聚类 → side 投影） | §5/§9/§9a/AC2/AC3 |
| 2 | deterministic 算法锁死（规则优先级/阈值/union-find chaining/cluster_key/reproducible） | §9a/AC6 |
| 3 | source_profile 仅 descriptive（无 authority/score） | §9b/AC8 |
| 4 | source_count / independent_count 与 cross-side independence 正式定义 + 规范示例 | §7/§9/§9a/AC4/AC5 |
| 5 | deterministic 失败 vs optional review 失败语义锁定 | §11b/AC7 |
| 6 | Context Envelope 补齐 identification metadata + 边界锁死 | §13 |
| — | Non-goal 核心原则 "measures independence, not scores trust" 加入 | §6/AC9 |

**Self Review**：① global clustering 消除"两侧各自聚类导致转载跨侧漏判"；② union-find 显式固定
chaining（A≈B≈C 必同簇），实现无自由裁量；③ 所有数值/评分语义被 Non-goal 与字段约束锁死；
④ review 失败不影响 deterministic 主结果；⑤ 复杂度受上限与抽样约束；⑥ 误判以"只计独立数+展示簇成员"
缓解，用户可见可审；⑦ 零 Runtime/Agent 触碰、零 ALTER F1–F4。

**Scope Audit**：本轮仅修改 `docs/spec/2026-09-11-f5-audit-and-independent-corroboration.md`；
未创建 0005、未写代码/测试、未改 Agent/Tool/Checkpoint/依赖/基础设施。

**STOP**：F5 Spec rev2 + Self Review 完成，等待 **F5 Final Gate Review**；Gate 通过前不实现。
