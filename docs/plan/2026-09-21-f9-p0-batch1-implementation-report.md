# F9-P0 Batch 1 — Research State Projection Implementation Report

> 状态：**BATCH 1 PASS / FROZEN**（用户 2026-09-22 正式 Freeze；本批只交付 Projection；
> 不宣布 F9-P0 IMPLEMENTATION COMPLETE）。
> 依据：F9-P0 Rev2 Spec（2026-09-19 §3.1）＋ Implementation Plan Rev2（2026-09-20 §E/Batch 1）。
> 范围：read-only / deterministic / bounded / stable ordering / no LLM / no side effects /
> 不修改 F1–F8 frozen schema / 不修改 F8 Runtime control semantics。

## 1. Files changed（本批交付物）

| 文件 | 类型 | 说明 |
|---|---|---|
| `app/f9/__init__.py` | 新增 | F9 orchestration plane 包（后续 Batch 2–8 同包扩展） |
| `app/f9/projection.py` | 新增 | Research State Projection 唯一实现（只读 SELECT + Python 显式排序） |
| `tests/_f9_helpers.py` | 新增 | 跨后端测试 seeding helpers（sqlite/PG 共用；直接 INSERT 固定 id/时间戳） |
| `tests/test_f9_projection.py` | 新增 | sqlite 快速单测（36 tests） |
| `tests/test_f9_projection_postgres.py` | 新增 | PostgreSQL gate（RESEARCH_DSN_TEST 门控；8 tests） |
| `docs/plan/2026-09-21-f9-p0-batch1-implementation-report.md` | 新增 | 本报告 |

零修改 F1–F8 文件；零新迁移/新表/新依赖；`git status` 唯一 tracked 修改为既有的
2026-09-19 Spec Rev1→Rev2 修订（前一会话产物，与本批无关，未触碰）。

## 2. API / module changes

```python
# app/f9/projection.py
project(run_id: str, round: int = 0, budget: Optional[dict] = None, *,
        store: Any = None, now: Optional[datetime] = None) -> dict
```

- `round` = F9 Orchestrator `current_round`（输入参数；绝不从 created_at/evidence 时间/行数推断）。
- `budget` = F8 `BudgetCounter.to_dict()` 同形**只读 snapshot dict**（`{"limits","counts"}`）；
  `budget=None` → counts=0 + fallback limits（Spec §3.1 "数据不足时按上限值"）。
- `store`（keyword-only）＝测试注入用 store；缺省进程级 research store。只经 `store.execute`
  （SELECT），不进 transaction，无任何写。
- `now`（keyword-only）＝fresh 判定参考时钟（缺省 UTC now；测试注入保证确定性）。
- 错误：`ProjectionError`（run 不存在 / store 不可用 / round 非法 / budget snapshot
  非法/不一致）——供后续 orchestrator 按 Spec §12（Projection failure → 同 F8 run 内
  baseline fallback）处理；本模块不自行降级（不基于空 state 伪造 "no evidence"）。
- 常量：`FRESHNESS_WINDOW`（1 天）、`BUDGET_KINDS`、`DEFAULT_SIZE_LIMITS`、`registrable_domain()`。

## 3. Projection schema（Batch 1 输出，字段集按 Rev2 十四项）

```jsonc
{
  "run_id": str,                        // 直读
  "round": int,                         // 输入参数（orchestrator current_round）
  "budget": {"limits": {...4 kinds}, "counts": {...}, "remaining": {...}},
                                        // remaining = limit - count（单调剩余）
  "sub_questions": [ {sub_question_id, parent_id, position, question, status,
                      required, evidence_count, source_count} ],
  "required_uncovered": [sub_question_id...],      // 缺失覆盖集（见 §3.1）
  "claims": [ {claim_id, sub_question_id, statement, claim_type, status, created_at,
               best_verdict, independent_flag, fresh,
               conflicts: [{conflict_id, conflict_type, genuine, status,
                            reconciliation_status, reconciliation_outcome}],
               evidence_refs: [{evidence_id, created_at, domain}]} ],
  "evidence_summary": {"count": int, "by_domain": {domain: count, ...}},
  "gap_signals": {},                    // 容器（false 缺省）；全部信号 = Batch 2
  "open_questions": [],                 // 容器（Batch 3 Judge 产出）
  "size_limits": {caps..., "omitted": {sub_questions, claims, evidence_refs, conflicts}}
}
```

### 3.1 实现层裁决（Spec §3.1 "实现层定"授权，本报告为唯一权威 schema 说明）

1. **派生字段位置**：`best_verdict / independent_flag / fresh / conflicts` 为 claim-scoped，
   嵌于 `claims[]` 条目内（逐 claim 自包含）；F4/F6 冲突天然 claim-scoped
   （`conflicts.claim_id`），故不另设重复的顶层 conflicts 列表（避免膨胀；非 DB dump）。
2. **required（必答）判定**：root sub-question（`parent_id IS NULL`）= required。F1–F8 schema
   无必答标记列 → 只读判定，不加列。
3. **best_verdict**：F3 verification 中 `status='succeeded' AND verdict IS NOT NULL` 的行按
   `(created_at ASC, verification_id ASC)` 排序取**末** = 最新 succeeded；failed 不参与；
   无 succeeded → `null`。verdict 值**原样透传**（SUPPORTS/CONTRADICTS/INSUFFICIENT/
   UNVERIFIABLE…，不翻译 F3 语义）。
4. **independent_flag**：只读 F5 corroboration **complete** 行（逐 claim 取 computed_at 最新，
   同刻按 corroboration_id），映射 `support.independent_count >= 2` → True；无 complete 行 →
   `null`。不引入 authority/reliability_score。
5. **fresh**：claim bound evidence 最新 `created_at`（解析后 UTC），
   `now - max(created_at) <= FRESHNESS_WINDOW(1 天)`；无 bound evidence → `null`。
6. **conflicts（未缓解）**：F4 `status='confirmed'` 冲突且**不存在** F6 complete +
   outcome ∈ {SAME_ORIGIN_CONTRADICTION, DETAIL_INCONSISTENCY} 的缓解行；
   无 reconciliation / failed / GENUINE_CONTESTED → 未缓解（对齐 F7 "disabled ≠ reconciled"，
   不把"未计算"当已缓解）。条目带 reconciliation_status/outcome 供后续判断。
7. **gap_signals={} / open_questions=[]**：随 schema 输出（"缺省 false/空"），
   全部信号计算（含 §4 约束的 `budget_near`——唯一合法来源即本批 `budget.remaining`）
   留给 **Batch 2（Deterministic Gap Detection）**，不在本批越界实现。

## 4. Deterministic ordering rules（所有集合 Python 显式排序；不依赖 DB 默认顺序）

| 集合 | 排序键（升序） | 截断 |
|---|---|---|
| sub_questions | `(position, sub_question_id)` | 取前 64（omitted 计数） |
| required_uncovered | `(position, sub_question_id)`（同 sub_questions） | 随 sub_questions 上限 |
| claims | `(created_at, claim_id)` | 取前 64（omitted 计数） |
| best_verdict 候选 | `(created_at, verification_id)` | 取末（最新） |
| claim evidence_refs | `(created_at, evidence_id)` | 取最新 8 条（omitted 计数） |
| claim conflicts | `(created_at, conflict_id)` | 取前 8 条（omitted 计数） |
| corroboration 行（逐 claim） | `(computed_at, corroboration_id)` | 取末（最新 complete） |
| reconciliation 行（逐 conflict） | `(computed_at, reconciliation_id)` | 取末（最新） |
| evidence_summary.by_domain | domain 键字典序 | — |

时间戳键先经 `_ts()` 解析为 **UTC aware datetime**（PG timestamptz 受 session timezone
影响返回 +08:00 等偏移——真实观测；sqlite 存 TEXT 原样；两者表示同一时刻 → 统一 UTC 再比较
与输出），保证 SQLite/PostgreSQL 逻辑顺序一致且输出表示一致。`genuine` 读取时 `bool()` 归一
（sqlite INTEGER 0/1 vs PG BOOLEAN）。同刻+异 id 平局由 id 打破（测试覆盖）。

## 5. Budget snapshot source（F8 Truth，只读）

- `project(budget=...)` 只消费调用方传入的 F8 `BudgetCounter.to_dict()` 同形 dict；
  模块**不创建/不重置/不递增**任何 counter，不按搜索次数维护第二份预算；
  `projection() × N` 不引起 counter 增长（sqlite/PG 均有测试）。
- snapshot 校验：非 dict / 缺 limits·counts / 缺任一 kind / 非 int / count>limit → `ProjectionError`。
- F8 现有 read-only API（`BudgetCounter.to_dict()`）**足够** → 无 compatibility issue，
  未修改 F8 frozen contract。

## 6. Tests

- `test_f9_projection.py`（sqlite，36）：schema/默认值；重复运行一致；插入顺序无关
  （rich scenario 正/反序两 store 输出全等）；同刻异 id 稳定序；best_verdict 4 情形；
  required_uncovered 4 情形；domain 计数（含 unknown_domain）；fresh（窗内/窗外/边界/无
  evidence）；independent_flag（无行/≥2/单簇/failed 忽略）；conflicts（无 rec /
  GENUINE_CONTESTED / 缓解 outcome / failed rec / 排序）；bounded（claims/evidence refs/
  conflicts/sub_questions 各超限截断 + omitted 诚实计数）；budget（真实 BudgetCounter 快照
  ×2 不增长、None 默认、非法 snapshot 拒绝）；错误路径（missing run / round / store disabled）；
  JSON 可序列化。
- `test_f9_projection_postgres.py`（PG gate，8）：cross-DB 逻辑等价（**相同 id/时间戳** state
  分别 seed PG 与 sqlite → projection 逐字节一致）；cross-DB 插入顺序无关（PG 反序 vs sqlite
  正序）；best_verdict 语义；required_uncovered；conflicts+reconciliation；independent_flag
  +fresh；budget snapshot 只读 + 错误；domain 桶（含 co.uk 二级后缀与 unknown_domain）。

## 7. SQLite result

```
tests/test_f9_projection.py .................. 36 passed
tests/ 全量（排除 test_db_tools_mysql_integration.py） 592 passed, 80 skipped, 0 failed
```

## 8. PostgreSQL result（真实 PG16，192.168.127.101 独立测试库）

```
tests/test_f9_projection_postgres.py ........ 8 passed   （RESEARCH_DSN_TEST 门控）
```

## 9. F1–F8 regression

- sqlite 全量：**592 passed / 0 failed**（含全部 F1–F8 sqlite 套件与 step3/step4 governance
  套件；排除 `test_db_tools_mysql_integration.py`，原因见 Known limitations）。
- PostgreSQL：research-plane PG 文件（research/f2/semantic_verify/conflict/corroboration/
  reconciliation/bridge）+ governance PG 文件（postgres/postgres_gate/postgres_final）全部
  通过（68 passed 中的 research 组与重置后 governance 组 33 passed）。
- F9 Batch 1 文件加入/剔除均不改变上述结果（`--ignore` 对照实验证实 19 个 step3 失败在
  无任何 F9 代码时同样出现）。

## 10. Known limitations

1. **suite-order env pollution（环境性，非本批缺陷）**：当进程内先执行
   `test_db_tools_mysql_integration.py`（本环境 MySQL 可达 → 激活）时，其 import 链
   （`app/tools/db_tools` → `load_dotenv()`）污染 `OPENAI_API_KEY` 进程环境，使随后的
   F8 step3 wiring/integration 测试（lazy import `app.agent.main_agent` 建真实 OpenAI client）
   在完整目录级运行中失败 19 项。证据：无任何 F9 代码时全量复现同样 19 失败；单独/子集运行
   均通过；排除该文件后 592 passed / 0 failed。属既有测试隔离问题（P006 方向），
   本批未改动相关代码。
2. **截断语义**：claims/sub_questions/evidence_refs/conflicts 超过 `size_limits` 时确定性
   截断并把数量记入 `size_limits.omitted`（不把截断当全量）。截断本身不阻塞后续 gap/judge
   的正确性（omitted>0 可被 Batch 2 作为额外信号）。
3. **registrable domain 为近似实现**：内置二级公共后缀配置表 + 通用末两标签回退，非完整
   PSL；确定性成立（同输入同输出），跨 DB 一致（Spec 允许 "eTLD+1 或配置表"）。
4. **fresh 使用真实时钟**（Spec §3.1 语义要求 `now - max(capture)`）；determinism 在相同
   时钟下成立，测试注入 `now`。极端边界（窗口边缘翻动）由调用方注入时钟或接受 Spec 语义。
5. **时区表示**：PG session timezone 会影响 timestamptz 返回偏移（本机 +08:00）；Projection
   在读取边界统一归一 UTC，**不修改 F1 store/任何 frozen 代码**，仅 Projection 输出确定。

## 11. Scope deviations

**无**。未实现 Judge / Gap Detection / Follow-up Plan / Query Dedup / Targeted Research /
Round Orchestrator / F7 integration / 新 Agent / 新 Runtime / 新 Controller / 未修改 F8
counter semantics / 未修改 F7 / 无 UI / 无 Redis/Kafka/Neo4j/VectorDB / 无 F1–F8 schema
breaking change；未发现需要修改 F1–F8 frozen contract 的情形（无 Compatibility Issue）。

## 12. Batch 1 Implementation Gate（13 项自查）

| # | 检查项 | 结果 | 证据 |
|---|---|---|---|
| 1 | Projection read-only | PASS | 只经 `store.execute`（SELECT）；无 transaction 写；测试禁用 store 抛错不伪造空状态 |
| 2 | deterministic | PASS | 重复运行输出全等；插入顺序无关（正/反序双 store 全等，sqlite+PG） |
| 3 | bounded | PASS | size_limits 上限 + omitted 计数；claims/ev/conflicts/subq 超限测试 |
| 4 | stable ordering | PASS | 显式键表（§4）；同刻异 id 测试；不依赖 DB 默认顺序 |
| 5 | round 来自 current_round 输入 | PASS | `project(run_id, round=...)`；无 created_at/计数推断（测试 round=3/0 透传） |
| 6 | budget 来自 F8 read-only snapshot | PASS | 仅消费 `BudgetCounter.to_dict()` 形 dict；None 走上限值；无第二 counter |
| 7 | Projection 不修改 F8 counter | PASS | 真实 BudgetCounter：projection×2 前后 to_dict 相等（sqlite+PG） |
| 8 | required_uncovered 语义 | PASS | 仅 root/required 无 evidence 且无 source；4 情形测试 |
| 9 | best_verdict 排序 | PASS | succeeded-only、(created_at, verification_id) 取末；failed 排除；4 情形测试 |
| 10 | 未引入 authority/reliability | PASS | 仅读 F5 independent_count/domain 计数；无 authority_score/reliability_score 字段 |
| 11 | SQLite / PostgreSQL 通过 | PASS | 36（sqlite）＋ 8（PG）；cross-DB 逐字节等价 |
| 12 | F1–F8 regression 通过 | PASS | sqlite 全量 592 passed/0 failed；PG research+governance 组全通过（见 §9） |
| 13 | 未越过 Batch 1 scope | PASS | §11；gap_signals={} 信号实现留给 Batch 2 |

## 13. 最终状态

```text
BATCH 1 PASS / FROZEN（2026-09-22 用户正式 Freeze）
```

实现与验证证据保留，无额外重构。本批未宣布 F9-P0 IMPLEMENTATION COMPLETE。等待用户是否
下达 Batch 2（不自动开始；Batch 2–8 均未启动）。
