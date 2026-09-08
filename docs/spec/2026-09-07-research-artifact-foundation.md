# Spec: 2026-09-07-research-artifact-foundation（F1 Research Artifact Foundation）

> 状态：**Draft — 待用户 Review/Gate 后进入 Implementation**
> 阶段：Phase 1（Phase 0 = Agent Persistence & Runtime Foundation，已收口）
> 前置：`ARCHITECTURE_CHANGE_REVIEW.md`（G1–G8）、`docs/spec/2026-09-03-postgres-checkpoint-migration.md`（Phase 0 实施，已收口）
> 纪律：本 Spec **不新增任何 Agent**、不改 Agent Orchestration / Checkpoint / Event 行为；F1 只建 **Research Data Plane**。
> 边界：F1 只实现 `ResearchRun → SubQuestion → SearchQuery → Source → Evidence`；Claim/Citation/Verification 属 F2，仅预留扩展边界。

---

## 0. 需求覆盖映射

| # | 要求 | 章节 |
|---|---|---|
| 1 | F1 目标与非目标 | §1 |
| 2 | Research Artifact 与 Checkpoint 边界 | §2 |
| 3 | 五实体数据模型 | §3 |
| 4 | PostgreSQL schema / FK / index / UNIQUE / JSONB 边界 | §4 |
| 5 | Source normalization / canonical URL 规则 | §5 |
| 6 | Source/Evidence ID 生成策略 | §6 |
| 7 | Artifact 生命周期 | §7 |
| 8 | Tool Result → Source/Evidence 数据流 | §8 |
| 9 | Web / DB / RAGFlow 接入 | §9 |
| 10 | raw result 与 structured artifact 关系 | §10 |
| 11 | provenance 查询路径 | §11 |
| 12 | transaction / idempotency | §12 |
| 13 | failure / partial artifact | §13 |
| 14 | 与 run_deep_agent / Task Runtime / Event Model 的关系 | §14 |
| 15 | 后续 F2 Claim/Citation 扩展边界 | §15 |
| 16 | 测试策略与 Acceptance Criteria | §16 |
| 17 | 文件变更清单 | §17 |
| 18 | Migration / rollback | §18 |

---

## 1. Goal & Non-Goals

### Goal（本阶段）
- 建立 Research Artifact Store（研究数据面 / research data plane），持久化
  `ResearchRun / SubQuestion / SearchQuery / Source / Evidence` 五个实体；
- 让 Web / DB / RAGFlow 三类工具的检索产物**确定性**进入 artifact（不经 LLM 生成 id/URL/时间），
  与 Agent 主链路解耦（fail-open）；
- 建立正/反向 provenance 链：`Run→SubQuestion→Query→Source→Evidence` 与反向反查；
- 回答两个问题：
  1. “这个 Agent 在这次 run 里获取过哪些 Source？”（F1-A）
  2. “后续某个结论可以依据哪个 Source 的哪一段内容？”（F1-B，Evidence 底座）
- 为 F2（Claim/Citation/Verification）预留模型与接口边界。

### Non-Goals（本阶段明确不做）
- ❌ Planner / Summarizer / Citation / Conflict / Verification Agent（不新增 Agent）
- ❌ Claim / Citation artifact 与绑定、Unsupported-claim/coverage 指标、stop gate
- ❌ Evidence Graph、图数据库
- ❌ Research 与 Agent context 的联动（Context Envelope / F4）——本阶段**不改 Agent 可见上下文**
- ❌ resume 产品入口 / side-effect idempotency 实现
- ❌ 大规模 retrieval fan-out、并行检索改造
- ❌ Redis / Neo4j / Vector DB / Kafka / 新基础设施
- ❌ 升级 LangGraph / LangChain / DeepAgents；重构 Task Runtime / Checkpoint / Event Model
- ❌ HTTP provenance API（保留到 P1 末尾/后续阶段，G8 裁决；本 Spec 只提供查询层函数）

---

## 2. Research Artifact 与 Checkpoint 的边界（分层契约）

| 维度 | Checkpoint（Phase 0，`app/runtime/checkpoint.py`） | Research Artifact（F1，`app/research/`） |
|---|---|---|
| 职责 | Execution State：graph state / checkpoint / resume / replay-time-travel | Research Knowledge / Provenance：Run→SubQuestion→Query→Source→Evidence |
| 表族 | `checkpoint_migrations/checkpoints/checkpoint_blobs/checkpoint_writes`（官方 saver setup() 自管） | `research_*`（仓库自管，版本化迁移，schema_migrations 表） |
| 逻辑键 | thread_id / checkpoint_id | run_id（研究身份，独立于执行身份但相关联） |
| 写入来源 | LangGraph 运行时（每步） | 工具/运行生命周期钩子（显式调用） |
| 可靠性策略 | fail-fast（初始化/建表失败即报错） | **fail-open**（研究面故障不阻断 Agent 主链路，见 §13） |
| 模块依赖 | 无 app 内依赖 | 不依赖 `app/agent`；除 pydantic/stdlib/驱动外无强依赖 |

铁律（沿用 ACR §八）：
1. **不把 Source/Evidence 等塞进 LangGraph message state / checkpoint**；
2. Research Artifact 不依赖 message history 才存在，可独立查询；
3. checkpoint 与 artifact 通过 `(thread_id, run_id)` 关联，但**互不依赖**（artifact 可在无 checkpoint 时存在，反之亦然）。

---

## 3. 数据模型（pydantic + DDL 双定义）

> 字段语义以本表为准；DDL 见 §4。`research_runs` 为根，**所有子表 FK 直连 research_runs**（run 隔离/归档单点），同时保留逐级 FK。

### ResearchRun
```text
run_id: str (PK)          # 系统生成（§6）
thread_id: str            # 会话身份（对应 run_deep_agent 的 session_id/thread_id）
question: text            # 用户原始问题（截断上限见 §10）
status: str               # planned|running|finished|failed|cancelled（稳定枚举）
started_at: timestamptz / finished_at: timestamptz (nullable)
plan: jsonb|text (nullable)      # 预留：后续 Planner 的 plan 工件（当前为 NULL）
budget: jsonb|text (nullable)    # 预留：search/token/step 预算账本（当前为 NULL）
metadata: jsonb|text (default {})
```
索引：`research_runs(thread_id)`、`research_runs(status)`。

### SubQuestion
```text
sub_question_id: str (PK) # 系统生成
run_id: str (FK → research_runs, ON DELETE CASCADE)
parent_id: str (FK self, nullable)   # 子问题树（当前仅 root，预留树形）
position: int             # run 内序号（root=0）
question: text
rationale: text (nullable)
status: str               # planned|asked|answered|skipped|failed
assigned_agent: str (nullable)  # 计划给哪个 specialist（预留；当前由运行时实际 agent 决定）
UNIQUE(run_id, position)
```
> 本阶段运行时**没有 Planner**，因此每个 run 自动建一条 **root SubQuestion**（question = 用户原始问题），
> SearchQuery 挂到该 row——provenance 链在“无规划”情况下依然成立；后续 F9 引入分解时向树追加子节点即可。

### SearchQuery（一次检索执行，append 语义）
```text
query_id: str (PK)        # 系统生成
run_id: str (FK → research_runs)
sub_question_id: str (FK → sub_questions)
query: text               # 检索词 / SQL 文本（不含参数值）/ RAG 提问
agent: str                # network_search | database | ragflow | main（稳定枚举）
tool: str                 # internet_search | execute_sql_query | get_table_data | create_ask_delete | ...
topic: str (nullable)     # Tavily topic（web 专用）
seq: int (nullable)       # run 内执行序（来源见 §14，可为空）
fetched_at: timestamptz   # runtime/工具层记录
metadata: jsonb|text (default {})   # max_results/include_raw_content/rows/columns 等调用参数快照
```
索引：`search_queries(run_id)`、`search_queries(sub_question_id)`。
> 幂等说明：同一工具调用被**恢复/重放**属未来 side-effect idempotency 范畴（F 后续），本阶段 append（每次执行 = 一条 query 记录，fetched_at 不同），见 §12/§15。

### Source（F1-A：来源工件）
```text
source_id: str (PK)       # 系统生成
run_id: str (FK → research_runs)
query_id: str (FK → search_queries, nullable？否——本阶段一律由检索产生，NOT NULL)
source_type: str          # web | db | ragflow | upload（稳定枚举）
title: text               # web=结果标题；db=“数据库查询结果”；rag=助手名/文档标识；upload=文件名
canonical_url: text (nullable)   # 仅 web；§5 归一化后
locator: text             # web=raw url；db=query_id 标识；rag=ragflow:{assistant_id}; upload=文件标识
canonical_key: text       # 归一化唯一键（§5）——UNIQUE 载体
fetched_at: timestamptz   # runtime 记录
agent: str                # 实际执行 agent（web 工具=network_search 等）
publisher: text (nullable)      # web 可用时（解析自结果/域名启发式），否则 NULL
published_at: timestamptz (nullable)
raw_metadata: jsonb|text (default {})   # 原始载荷快照（score/raw_content 截断等，见 §10）
UNIQUE(run_id, source_type, canonical_key)
```
索引：`sources(run_id, source_type)`、`sources(canonical_key)`。

### Evidence（F1-B：Source 中被纳入研究面的一段内容）
```text
evidence_id: str (PK)     # 系统生成
run_id: str (FK → research_runs)
source_id: str (FK → sources)
sub_question_id: str (FK → sub_questions)
content: text             # 摘录文本（上限见 §10；web=result content/raw 摘录；db=结果快照；rag=回答文本）
locator: text             # url#片段 / db:{query_id} / rag:{assistant} / upload:{file}#{页码}
extraction_method: str    # tool_result_item | web_result | db_result | rag_answer | upload_parser（稳定枚举）
metadata: jsonb|text (default {})   # score/row_idx/chunk_idx/语言等
created_at: timestamptz
```
索引：`evidences(run_id, source_id)`、`evidences(source_id)`、`evidences(sub_question_id)`。
> Evidence 语义（诚实口径）：本阶段把“工具实际返回给 Agent 的内容片段”结构化入库，
> 即**候选证据（groundable content）**。“最终结论到底用了哪条”是 F2 Claim-Evidence 绑定
> 的职责；F1 不假装知道“被使用”。

---

## 4. PostgreSQL / SQLite schema 设计（含边界分析）

### 4.1 双后端 DDL
- Research 表族独立于 checkpoint 表族；DDL 文件按后端各一份、语义一致：
  - `db/migrations/{0001_research_foundation}.sqlite.sql`
  - `db/migrations/{0001_research_foundation}.postgres.sql`
- `schema_migrations(version PK, applied_at)` 通用迁移表（两后端同构）由轻量 runner 维护（零新增依赖，§18）。
- 列差异仅限 JSON 类型与自增：
  - sqlite：JSON 字段用 `TEXT NOT NULL DEFAULT '{}'`（应用层 `json.dumps` 写入，读取 `json.loads`；sqlite JSON1 校验可选，v1 靠 pydantic 层校验）；
  - postgres：JSON 字段用 **`JSONB NOT NULL DEFAULT '{}'`**（便于后续 provenance 统计与 metadata 过滤索引）。

### 4.2 JSONB 使用边界（沿用 ACR §6 红线）
| 放 JSON 字段（不透明/扩展载荷） | **禁止**放入 JSON 字段（必须是真实列/FK） |
|---|---|
| research_runs.plan/budget/metadata；query.metadata；source.raw_metadata；evidence.metadata | run_id、thread_id、query/source/evidence 的**外键引用**；text 正文列；时间戳；状态枚举；agent/tool 枚举 |
- 理由：关系必须可 JOIN/可约束/可索引；JSON 只承载无关系语义的快照。
- 索引规则：本阶段只对真实列建 B-tree/UNIQUE；**不为 JSONB 建 GIN**（无查询需求；F2 出现 metadata 过滤需求时再评估）。

### 4.3 PK/FK/UNIQUE/事务/幂等摘要
| 维度 | 决策 |
|---|---|
| PK | 全部 uuid4 hex（§6），服务端生成 |
| FK | 全链 FK；`run_id → research_runs` 直连；子表 `ON DELETE CASCADE` 仅经显式 `delete_run` 路径使用，业务写入不删除 |
| UNIQUE | `sources(run_id, source_type, canonical_key)`（Source 幂等 upsert）；`sub_questions(run_id, position)` |
| 事务 | 每次 registry 写操作 = 单事务（见 §12） |
| 幂等 | 天然键 + upsert（§12） |

---

## 5. Source Normalization / Canonical URL 规则（deterministic，无 LLM）

`app/research/normalize.py`（纯函数，stdlib：`urllib.parse` + `hashlib`）：

### 5.1 canonical_key 生成（按 source_type 分派）
```text
web:    canonical_key = canonicalize_url(raw_url)
db:     canonical_key = f"db:{query_id}"            # locator=query_id；同 run 同查询可复用
ragflow: canonical_key = f"ragflow:{assistant_key}" # assistant_key = 助手名归一化（小写/去空白）＋问题 hash？——
        # 决策：Source 粒度 = (run, ragflow, assistant)；同一助手多轮提问共享同一 Source，
        # 每轮提问单独 evidence（content=回答）。assistant_key 从助手名归一化。
upload: canonical_key = f"upload:{file_hash or 文件名归一化}"
```

### 5.2 canonical URL 规则（web）
1. `urllib.parse.urlsplit`；scheme 归一化小写，http→https 不强制（保留原始但比较时统一 https 优先不去重 http/https 差异——v1 规则：**同 host+path+query 仅 scheme 不同视为同源**，保留首个）；
2. host 小写；去除 userinfo 与默认端口（:80/:443）；
3. 去 fragment；去常见 tracking 参数（`utm_*`, `fbclid`, `gclid`, `ref`, `spm`, `_ga`…白名单小集合）；
4. query 参数**排序**后重拼（稳定序）；path 去尾部 `/`（根路径保留 `/`）；
5. 不跳转解析、不做域名 whois/punycode（v1 范围外，文档化）。

### 5.3 校验与完整性
- canonical_key 为空/无法解析 → registry 拒绝并返回 `None`（不落库），调用方记日志；DB/RAG/upload 恒有确定性 locator 不产生此问题。
- `validate_source_record()` 纯函数：必填 run_id/source_type/agent/fetched_at；title 缺失置默认。

---

## 6. ID 生成策略

- `app/research/ids.py`：`new_run_id()/new_sub_question_id()/new_query_id()/new_source_id()/new_evidence_id()`
  → 一律 `uuid.uuid4().hex`（32 字符，无外部依赖）。
- **禁止 LLM 生成或传递任何 id**：agent 只允许引用 registry 返回的 id；解析层绝不从模型文本提取 id。
- 说明：uuid7 有更优排序性但需新增依赖；本阶段 uuid4 + 时间列满足排序需求，后续如需再评估（不引入新依赖为本 Spec 红线之一）。

---

## 7. Artifact 生命周期

```text
run_deep_agent 开始（run 边界）
  → research.create_run(thread_id, question)          status=planned（同线程 run 结束覆盖/多 run 并存：每次任务新 run_id）
  → research.create_root_sub_question(run_id, question)
  → 工具执行期：registry 写入 search_queries/sources/evidences（run_id 由 run 上下文/显式传入）
  → run 终态（R3 终态事件同一处）：status=finished|failed|cancelled + finished_at
```
- run 复用：同 thread 新任务 = 新 ResearchRun（与 checkpoint 的 thread 级执行态区分）；旧 run 数据保留可查。
- run 上下文传递：`run_deep_agent` 在创建 run 后把 `run_id` 写入 **research 自有 ContextVar**（`app/research/context.py`，stdlib），工具侧经 `get_current_run_context()` 读取；显式参数优先（测试友好），ContextVar 为运行期便利。
- 终态归属与 R3 相同位置（`RunTerminalGuard` 分支），改动最小（§14）。

---

## 8. Tool Result → Source / Evidence 数据流（统一接入契约）

```text
Tool 执行（@tool body）
  ├─ 原有逻辑不变：调用外部服务、拼装返回给模型的内容字符串
  ├─ 新增（deterministic、try/except 包裹、fail-open）：
  │    ctx = research.get_current_run_context()        # (run_id, sub_question_id) 或无 → 跳过
  │    if ctx:  # 仅 web/rag 的 query 需新 query_id；db 复用传入
  │      query_id = registry.record_search_query(run_id, sub_qid, agent, tool, query, params_meta)
  │      source_id = registry.upsert_source(run_id, query_id, SourceRecord(...))   # canonical 归一化
  │      registry.append_evidence(run_id, source_id, sub_qid, EvidenceRecord(...)) # 内容摘录（截断）
  └─ 返回内容与改造前**完全一致**（模型不可见 artifact）
```

| 阶段 | 说明 |
|---|---|
| 改造前 | `Tool Result → Agent Message → Final Report`（artifact 不存在） |
| F1 后 | `Tool Call → Source/Evidence → Artifact Store`（旁路）；Agent Message 不变；Final Report 的 grounding 待 F2 |

---

## 9. 三类 Agent / 工具接入明细

| Agent | 工具 | Source 注册（source_type=web） | Evidence 注册（extraction_method） |
|---|---|---|---|
| 网络搜索助手 | `internet_search(query, topic, max_results, include_raw_content)` | 遍历 `result.results[]`：每项 `upsert_source(title/url/score…)`（raw url 存 locator，canonical_key=canonicalize_url）；`publisher/published_at` 从结果字段取（无则 NULL） | 每结果一条 `content`（优先 raw_content? 若 include_raw_content=True 且超长则截断到 §10 上限，否则取 content 摘要），metadata={score} |
| 数据库助手 | `execute_sql_query(query, params)` / `get_table_data(table_name)` | query 先 `record_search_query(agent=database, tool=…)`；Source：source_type=db、locator=query_id、title=“数据库查询结果”、canonical_key=`db:{query_id}`、raw_metadata={rows, columns_head, sql_sha256}（**不含 params 值**，遵守审计纪律） | 一条结果快照 evidence（content=返回的 CSV 文本截断，locator=`db:{query_id}`） |
| RAGFlow 助手 | `create_ask_delete(chat_name, question)` | Source：source_type=ragflow、title=chat_name、canonical_key=`ragflow:{norm(chat_name)}`、raw_metadata={assistant_id}（无文档级 citation 时如实为空） | 每轮提问一条 evidence（content=回答文本截断） |
| （Upload 读取，预留） | `read_file_content` | 本阶段不自动注册（File→Source 归后续文档分析场景），接口预留 | 预留 |

接入位置：各自 `app/tools/*.py` 工具函数体内、返回值前追加 registry 调用块；**不改函数签名/docstring**（ARCHITECTURE §6 LangChain 工具签名红线）。

---

## 10. raw result 与 structured artifact 的关系

1. **raw result 属于 Agent 上下文/消息历史**（现状不动）；**artifact 属于研究数据面**——两者解耦，模型看不到 artifact，artifact 不读消息历史（§2 铁律）。
2. 容量治理（防止 artifact 库变成第二个垃圾场）：
   - `evidence.content` 上限 **8_000 字符**（超出截断 + metadata.truncated=true + source 的 raw_metadata 存更小快照）；
   - `source.raw_metadata` 上限 ~2_000 字符 JSON（超限仅存概要 + `raw_dropped=true`）；
   - `research_runs.question` 上限 2_000 字符。
3. 完整原始正文（如 include_raw_content 全文）**不承诺入库**（v1）：需要全文时按 source.locator/raw url 重取是后续 Context Engineering 的职责（F4 预留）。
4. 记录与消息的一致性：F1 不解析/比对消息内容；证据以工具返回为权威快照。

---

## 11. Provenance 查询路径（函数层，本阶段无 HTTP）

`app/research/provenance.py`：
```text
get_run(run_id)
list_sub_questions(run_id) / get_chain_by_evidence(evidence_id):
      evidence → (source, search_query, sub_question, run)    # 反向反查（F1-C 核心）
list_sources(run_id, *, source_type=None)                      # “这个 Agent 获取过哪些 Source？”
list_evidences(run_id, *, source_id=None)
```
- 全部**只读、显式 run_id 作用域**；不允许跨 run 无过滤查询（run isolation 纪律）。
- 返回值使用 pydantic schema（`.model_dump()` 直出 dict/JSON）。
- HTTP 端点不在本阶段（G8：P1 末尾再评估 `GET /api/research/...`）。

---

## 12. Transaction / Idempotency

- Store 接口（sync-first，见 §14/§16 线程说明）：
  - `sqlite`：进程级单连接（`check_same_thread=False` + WAL）+ `threading.RLock`，每次 registry 写操作在 `BEGIN…COMMIT` 内；
  - `postgres`：psycopg3 sync，**每次 registry 操作一个短连接 + 单事务**（v1 避免 pool 复杂度；吞吐需求出现前不引入 AsyncConnectionPool，生产后续评估）。
- 每次操作 = 单事务：`record_search_query` / `upsert_source` / `append_evidence` 各自独立提交；
  `create_run + root sub_question` 同事务（run 头原子）。
- 幂等：`sources` 按 `UNIQUE(run_id, source_type, canonical_key)` 做 `INSERT … ON CONFLICT DO NOTHING` 后 `SELECT`（返回既有 source_id）——同一 run 内重复检索同一 URL 收敛到同一 Source；
  query/evidence 为 append（允许重复执行产生多行；跨执行去重/重放幂等属后续 side-effect idempotency，§15）。
- run 隔离：repository 层所有写函数强制带 run_id 且内部校验 run 存在（不存在 → `ResearchRunNotFoundError`，工具捕获后 fail-open）。

---

## 13. Failure / Partial Artifact

- **研究面 fail-open（总原则）**：registry/store 任何异常只影响 artifact，不阻断 Agent 主链路——与 monitor fail-open 同哲学。
  实现：工具内注册调用统一包 `research_guard()`（try/except → logging.warning + return），
  出错不改变工具返回值。
- 分层错误：
  - 配置错误（backend 非法/缺 DSN）→ 首次访问抛可读 `ResearchConfigError`，`research_guard` 转 fail-open + 日志；
  - store 不可达（PG down / sqlite 文件只读）→ 同上 fail-open；registry 缓存 `disabled` 状态避免每调用重试风暴（定时重试窗口 30s 可选）；
  - 单条 upsert 失败（约束冲突等）→ 记录 warning，不影响同批其他行。
- Partial 状态：run 头已建但中途写入失败 → run.status 仍正常推进，缺失证据如实（不补造）；
  run 级失败 → `failed`/`cancelled` + finished_at；**不留半成品承诺**（artifacts 只含真实产生的记录）。
- 恢复/重放：checkpoint resume 重放工具调用会再次执行注册 → 产生重复 query/evidence 行（append 语义）；
  v1 接受重复并文档化；side-effect idempotency 契约（migration spec §4.10）后续落地时以
  `(run_id, task_call_id)` 幂等键收敛（本阶段只预留 metadata 字段位 `task_call_id`，不实现）。

---

## 14. 与现有运行时的关系

| 现有件 | 关系/改动 |
|---|---|
| `run_deep_agent`（唯一 run 边界） | 仅加 **2 个轻量钩子**（不动其执行契约）：① 入口 `create_run + root sub_question` 并把 run_id 写入 research ContextVar（与既有 thread/session/run ContextVar 并存、独立 reset）；② 终态分支（RunTerminalGuard 位置）更新 run.status。**不得绕过 run_deep_agent 的红线不变** |
| Task Runtime（R4 规划，本工作树未含） | 解耦：Task Runtime 管任务生命周期（TaskStatus/RunOutcome）；ResearchRun 状态是**研究面自己的账本**，不反向驱动 Task 状态机。若 R4 落地，run_deep_agent 钩子自然复用，无需改本设计 |
| Event Model（R3 monitor） | 不新增 WS 事件类型、不改信封；研究面写入失败只打日志。event_id/run_id 可与 research run_id 同源（run_deep_agent 的 run_id 复用到 research_runs.run_id——**同一 run_id 贯穿 execution 与 research 层**，作为唯一关联键，事件侧不动） |
| Checkpoint（Phase 0） | 不共表、不共迁移、不共事务；仅共享 run_id/thread_id 关联语义 |
| ContextVars（thread/session/run） | research 自带 context（§7），不动 api/context 与 monitor |

> 关联键定案：`research_runs.run_id == 该次 run_deep_agent 的 run_id`（同一 uuid4），
> thread_id 亦同源——provenance 与执行事件（R3 seq/run_id）天然可 JOIN，但存储物理解耦。

---

## 15. 后续 F2 扩展边界（只预留，不实现）

- F2 将新增 `claims` / `claim_evidences`（M:N）/ `citations` 表与 `verifications`（Verification 产出）；
  F1 通过以下方式为其铺路：
  1. `evidences.evidence_id/source_id/content/locator/sub_question_id` 即 F2 绑定目标（claim→evidence→source 外键落点现成）；
  2. registry 与 store 保持**实体正交**：加表 = 新 migration 文件（0002…），不加横切逻辑；
  3. Evidence `metadata.task_call_id` 字段位预留（§13），不实现；
  4. Source 的 reliability/cluster_id 列**本阶段不建**（F6 加列或加侧表），避免空列债务。
- 明确不做：不在本阶段定义 Claim schema / coverage 指标 / unsupported-claim 检测。

---

## 16. 测试策略与 Acceptance Criteria

### 测试策略
- 默认后端 **sqlite（_testtmp 临时文件）**，全部用例无外部服务；
- PG 变体测试沿用 Phase 0 门控纪律：`RESEARCH_DSN_TEST` 指向独立测试库才跑（skipif）；
- 纯函数优先：`normalize.py`（URL 矩阵）、`ids.py`、schema 校验、canonical 规则——无 DB；
- store/registry 用确定性 fixtures（固定 Tavily/DB/RAG 假返回）断言落库与幂等；
- 并发：多线程并发写 sqlite（锁）与 PG（可选）基本隔离测试；
- fail-open：monkeypatch store 抛错 → 工具返回不变、research_guard 不抛出。

### Acceptance Criteria
| AC | 内容 | 验证 |
|---|---|---|
| F1-AC1 | `create_run + root sub_question` 同事务；run_id 与 run_deep_agent 的 run_id 一致（单测显式传值） | registry 测试 |
| F1-AC2 | Web：一次 fake Tavily 返回（N 结果）→ 1 query + N sources（URL 归一化命中 5.2 规则）+ N evidences（content 截断上限生效） | tools/registry 测试 |
| F1-AC3 | DB：execute_sql_query fake 返回 → 1 query（agent=database）+ 1 source（canonical_key=db:query_id）+ 1 evidence 快照；**不含 params 值** | 同上 |
| F1-AC4 | RAGFlow：一问 → 1 source（canonical_key=ragflow:assistant）+ 1 evidence | 同上 |
| F1-AC5 | 幂等：同 run 重复注册同 URL → 同 source_id（ON CONFLICT） | registry 测试 |
| F1-AC6 | 反向链：evidence_id → source → query → sub_question → run 五跳可达 | provenance 测试 |
| F1-AC7 | run 作用域隔离：跨 run 查询必须带 run_id 过滤；不存在 run 写入抛 `ResearchRunNotFoundError` | 测试 |
| F1-AC8 | fail-open：store 抛错时工具返回值不变、无异常上抛；registry disabled 缓存生效 | monkeypatch 测试 |
| F1-AC9 | 双后端 schema 等价：sqlite/postgres DDL 文件执行后 `schema_migrations` 记录一致、`PRAGMA/`表清单一致（PG 部分门控） | migration runner 测试 |
| F1-AC10 | 既有回归：Phase 0 全仓（215 passed / 6 skipped）保持 + 新增用例全绿；ruff/compileall 通过 | CI/本地 |
| F1-AC11 | `app/research` 不 import `app.agent`；工具签名/docstring 未改（diff 核对） | grep + diff review |
| F1-AC12 | 文档同步：ARCHITECTURE/DECISION(D 新条目)/ROADMAP/TESTING/.env.example | diff review |

---

## 17. 文件变更清单

### 新增（New）
```text
app/research/
├── __init__.py
├── config.py        # RESEARCH_STORE=sqlite|postgres|disabled；RESEARCH_DB/RESEARCH_DSN 解析（纯函数）
├── ids.py           # uuid4 hex id 生成
├── normalize.py     # canonical URL / canonical_key（纯函数）
├── schemas.py       # pydantic 实体（Run/SubQuestion/Query/Source/Evidence + 枚举）
├── context.py       # research 自有 ContextVar（run_id/sub_question_id），显式参数优先
├── store.py         # Store 抽象 + sqlite 实现（RLock+WAL）+ postgres 实现（短连接单事务）+ disabled
├── migrations.py    # 轻量 migration runner（schema_migrations；按后端执行 db/migrations/*）
├── registry.py      # create_run/root/question/record_search_query/upsert_source/append_evidence + research_guard
└── provenance.py    # 只读查询（§11）
db/migrations/0001_research_foundation.sqlite.sql
db/migrations/0001_research_foundation.postgres.sql
tests/test_research_normalize.py
tests/test_research_store_sqlite.py
tests/test_research_registry.py
tests/test_research_provenance.py
tests/test_research_failopen.py
tests/test_research_migrations.py
tests/test_research_postgres.py        # skipif RESEARCH_DSN_TEST
docs/spec/2026-09-07-research-artifact-foundation.md   # 本文件
```

### 修改（Modify，最小化）
| 文件 | 改动 |
|---|---|
| `app/agent/main_agent.py` | 仅 run 边界两钩子（§7/§14）：入口 create_run+root+context set；终态 update status（含 finally reset research context） |
| `app/tools/tavily_tool.py` | 返回前加 registry 块（§9），签名/docstring 不变 |
| `app/tools/db_tools.py` | 同上（execute_sql_query / get_table_data） |
| `app/tools/ragflow_tools.py` | 同上（create_ask_delete） |
| `.env.example` | RESEARCH_STORE / RESEARCH_DB / RESEARCH_DSN / RESEARCH_DSN_TEST 说明 |
| 文档（ARCHITECTURE/DECISION/ROADMAP/TESTING/R2?不需要） | §同步 |

### 依赖
- **零新增**（stdlib + 已有 pydantic；驱动 sqlite3 stdlib / psycopg 已有）。

---

## 18. Migration / Rollback

- Research 表族新建，无旧数据迁移。
- runner（`app/research/migrations.py`，零依赖）：
  - 选择后端 DDL 文件；`schema_migrations` 版本表记录已执行版本（幂等，重跑跳过）；
  - sqlite：store 首次初始化自动执行；postgres：registry 首次初始化或显式 `python -m app.research.migrations` 执行（幂等）。
- Rollback：research_* 为新增表族，回滚 = 撤销代码改动 + 按需 `DROP` 测试/开发库表；
  生产库在 F1 上线后如需回退，提供 `0001_research_foundation.down.*.sql`（按 run 清理函数与 drop 表），
  不触碰 checkpoint 表族与既有数据。
- 阶段演进：F2 加表走 0002…；runner 幂等保证顺序执行。

---

## 19. Risks / Limitations

| 风险 | 缓解 |
|---|---|
| 工具内注册代码增加主链路表面积 | registry 调用全部经 `research_guard`（fail-open）；每调用 ≤ 几次本地/短连接事务，sqlite 默认 <ms 级 |
| Evidence 内容截断导致 grounding 信息损失 | 截断阈值 + metadata.truncated + locator 保留重取路径；F2 绑定用 evidence_id 不依赖全文本 |
| 无 Planner → SubQuestion 只有 root | 明确文档化（§3）；F9 扩展为树 |
| 重复执行 append 行（恢复/重放） | v1 接受 + task_call_id 字段位预留；idempotency 后续阶段实现 |
| 研究面被当作消息历史依赖 | §2 铁律 + 文档 + AC-11 强制边界 |
| PG 真实环境未测（同 Phase 0 限制） | sqlite 全量自动测试 + PG 门控测试 + 运行环境验证协议（§16） |
| JSONB 与 TEXT 双后端 DDL 漂移 | 同一版本两文件 + runner 双后端一致性测试（AC-9） |

---

## 20. 审核提示（给用户）

1. **SubQuestion 无 Planner 的口径**是否接受（root 单行先立链，F9 再树化）？
2. **Evidence 范围**采纳“工具返回内容片段 = 候选证据（groundable content）”口径，真正的“被使用”留给 F2——是否认可？
3. **fail-open**：研究面故障不阻断 Agent 主链路（对齐 monitor fail-open）——是否认可（而非 run 启动 fail-fast）？
4. **文件布局**：`app/research/` 8 模块 + `db/migrations/` 双后端 DDL + runner 是否与你的 F1 预期一致（可建议合并/拆分）？
5. **run_id 复用 run_deep_agent 的 run_id 作为唯一关联键**（research_runs.run_id == 执行 run_id）——是否认可？
6. `0001` DDL 草案是否需要先在 Spec 里给出完整 SQL 预览（可加 §4.4），还是实施阶段直接落 migration 文件？

---

## 21. 下一步

用户 Review/Gate 后进入 Implementation（实现顺序：normalize/ids/schemas → store/migrations → registry/context → tools 接入 → run_deep_agent 钩子 → provenance → 测试矩阵 → 文档同步），完成后停下游下一个 Gate（F1 验证 → F2）。

---

## 22. 实施记录（2026-09-07，用户 G-F1-1~6 批准后执行）

| 项 | 结果 |
|---|---|
| 代码改动 | 新增 `app/research/`（config/ids/normalize/schemas/context/store/migrations/registry/provenance）；`db/migrations/0001_research_foundation.{sqlite,postgres}.sql`；修改 `app/tools/{tavily,db,ragflow}_tools.py`（返回前旁路注册，签名/docstring 不变）、`app/agent/main_agent.py`（run 两处轻量钩子 + research context set/reset）；`.env.example`/`.gitignore` |
| 依赖 | **零新增**（stdlib + 已有 pydantic/psycopg） |
| 测试 | 新增 tests/test_research_{normalize,store_sqlite,registry,provenance,failopen,postgres}.py + tests/conftest.py |
| 自动测试（锁定版本隔离环境） | F1 相关 **43 passed, 2 skipped**（skip=PG 门控 RESEARCH_DSN_TEST）；**全仓 258 passed, 8 skipped**（Phase0 215/6 → 含 F1 净增 43 passed/2 skipped）；ruff/compileall 通过 |
| DDL / schema 等价 | sqlite/postgres 双文件同一版本；migration runner 幂等（applied=0001）；FK/UNIQUE/index 见文件；PG 实际执行待用户环境（AC-9 门控部分） |
| fail-open 实测 | disabled / store 故障 / missing-run / guard 中途异常均验证：工具语义不变 |
| provenance 五跳 | evidence→source→search_query→sub_question→run 测试通过（web/db 两型） |
| replay duplicate | append 语义文档化（恢复重放会重复 query/evidence；task_call_id 字段位预留，不实现） |
| 文档同步 | ARCHITECTURE.md §2/§3/§5/§7；DECISION.md D011；ROADMAP.md 状态行；TESTING.md §1（RESEARCH 门控）；本 spec §22 |
