# P3_DECISION_CLOSURE_REPORT

**P3 Runtime Observability Plane — Decision Closure（仅决策阶段，不实现）**

- 报告日期：2026-10（本会话）
- 任务分类：**L1（文档级决策产物）** —— 产出 Decision Closure 与 `DECISION.md` 追加提案；
  **零代码 / 零 DB / 零 migration / 零依赖 / 零 branch / 零 commit**（§9 给出遵守证据）
- 基线：`main` @ `8d9531d`（P2-2 merge：`884a681` + `8d9531d`）
- 上游（输入）：`P3_RUNTIME_OBSERVABILITY_DISCOVERY_REPORT.md`（L0 Discovery，Q1–Q8）
- 产出（本次）：
  1. `P3_DECISION_UPDATE_PROPOSAL.md`（`DECISION.md` append-only 追加提案，**未落地**）
  2. 本报告（Decision Closure）
- 状态：**DECISION CLOSED（提案态）→ 等待用户批准 → 批准后方可进入 P3-1 L3 Spec 阶段**

---

## 0. 本阶段做了什么 / 没做什么

| 项 | 结果 |
|---|---|
| Decision Closure（Q1–Q8 → `D-P3-001…008`） | ✅ 完成（§2/§3） |
| `DECISION.md` 追加提案（含 `git apply --check` 证据） | ✅ 完成，**已落地**（用户批准后 append-only 追加，见 §12） |
| 冻结约束 / 后续实施约束 | ✅ 逐条给出（§4 F1–F19 / §5 I1–I13） |
| 遗留决策衔接（D-Phase2-P2-2-008/-009/-012/-015/-017） | ✅ 显式承接关系（§6） |
| Review 修正项（I-1 … I-7） | ✅ 已按用户裁决并入决策记录（各条标注「I-n 裁决」） |
| `PROJECT_CONTEXT.md` 同步 | ✅ **已落地**（同批；原拟文本见附录 A，落地状态见 §12） |
| Problem Registry 登记 | ⏸ **不登记（N3 裁决）**；另记录 1 项**存量偏差待单独裁决**（§8.2） |
| 代码 / DB / migration / 依赖 / branch | ❌ **全部未触碰**（§9） |

---

## 1. 证据清单（读了什么 / 为什么读）

| 文件 | 为什么读 | 关键结论（对本决策的影响） |
|---|---|---|
| `AGENTS.md` | 行为契约与权限（§1 Authority / §4 禁止行为 / §5 最小修改 / §6 安全 / §10 Git / §11 Problem Capture） | 用户请求 > PROCESS > ARCHITECTURE > 既有实现（§1）；`§4` 禁止无批准新增依赖与改公共契约（约束 `D-P3-006`）；§6 安全边界优先（约束 `D-P3-004`）；§10.2 允许 Decision/Readiness 产物留 main（§7 落地路径 A 的依据）；§10.5 Mixed Working Tree → STOP/report（§9） |
| `PROCESS.md` | 执行状态机、L0–L3 分类、Git 执行、Problem Capture Review 节点 | §11.1 L0「不产 Implementation Plan」→ 本任务定位为文档级决策产物；§11.3 结论指向实现须另行立项（P3-1 为 L3）；§11.4 User Freeze 必须；§13 三问必答；§12.1 L2/L3 必须 feature branch（约束后续实施） |
| `DECISION.md` | 既有决策体例、编号惯例、需承接的遗留项 | 体例 = `## D-xxx — 标题` + Status/Context/Decision/Alternatives/Rejected/Consequences/Constraints Created/Related Problems/Related Architecture；编号序列 `D001–D019`、`D-Phase2-P2-1-001`、`D-Phase2-P2-2-001…017`；**`D-Phase2-P2-2-008` 明文把「结构化 payload 扩展」留给 P2-4** → 由 `D-P3-002` 承接；`-017` 的 reason 展示后续项 → 由 `D-P3-004`/P3-4 承接；`-009` 跨进程 exactly-once 限制 → 由 `D-P3-002` 继承；`-012` `flush_pending` 待定 → 仍是待办（不影响本批）；文件当前 **881 行**，末尾为 `D-Phase2-P2-2-017` |
| `PROJECT_CONTEXT.md` | current-state index（非规范） | **状态漂移确认**：§1 仍写「F9-P0 COMPLETE → Documentation/Presentation；禁止修改 F9 runtime」，与 `main` 实际（P2-1/P2-2 已 FROZEN+MERGED）不符 → `D-P3-008` 要求批准后同步修正 |
| `TESTING.md` | 验证契约（§1 手段 / §2 按范围选验证 / §4 六问 / §5 Security / §9 HARNESS REVIEW） | 纯文档改动 = 「只执行适用的文档一致性验证（§9 HARNESS REVIEW 按需）」（§2 表）；`DECISION.md` 属六核心文件 → 落地 MUST 执行 §9（已在提案 §3 落地清单化）；`D-P3-004` 要求端点的 TESTING §5 负路径测试 |
| `RUNTIME_OBSERVABILITY_AUDIT_REPORT.md` | 同域先前 L0 审计（避免重复/冲突） | Phase-2 方案与本 Phase 一致；本 Decision Closure 是其「后续项」的正式约束化 |
| `P2-1_RUNTIME_POLICY_ENFORCEMENT_SPEC.md` / `P2-1_INTEGRATION_READINESS_REPORT.md` / `P2-1_MERGE_REPORT.md` | 前置批次冻结状态与 Phase-2 路线图定义 | P2-1 FROZEN/MERGED `dd7af9a`；**§3 路线图定义 P2-3/P2-4/P2-5**（`D-P3-001` 映射依据）；P2-1 已保证「生产恒 governed」是本 Phase 的数据地基 |
| `P2-2_SPEC_v2.md`（Rev 2.2）/ `P2-2_IMPLEMENTATION_PLAN.md` / `P2-2_RELEASE_GATE_AUDIT_REPORT.md` | P2-2 冻结契约、Non-Goals、Gate 状态 | §21 分类汇总把 P2-3（durable timeline）、P2-4（admin 端点）、P2-5（title）列为 **pending / 未触碰**；`health_view`/`health_snapshot` 注释「供 P2-4 消费」= P3-2/P3-3 的现成接缝；Non-Goals 明确「本批零 frontend / 不新增端点 / 不新增 TaskStatus / 不引连接池」→ `D-P3-003`/`D-P3-004` 继承其纪律 |
| `P2-2_S3_DEVIATION_REPORT.md` / `P2-2_D15_REVISION_REPORT.md` / `P2-2_PENDING_DECISION_PROPOSAL.md` | 过程纪律先例 | 先例：**Decision 提案先产出、批准后 append-only 追加**（与本提案同形）；越出授权范围时先 STOP 报冲突 |
| `DECISION.md` `D-Phase2-P2-2-001…017`（逐条） | 冻结约束继承 | health 非 lease；stale 派生态；reclaim 只走 funnel；诊断载体 = `error` 字符串；PG 写约束（短事务/节流/无连接池）；frontend 零改动（本 Phase 的 P3-4 属**新批准范围**，需显式解除该边界 → §6 记录） |
| `app/runtime/governance/*`、`app/api/*`、`app/agent/*`、`app/tools/*`、`frontend/src/*` | L0 已完成的实现现状盘点（本报告直接引用其结论） | 见 L0 报告 §1–§3（`events.py` 白名单 lifecycle-only；`callbacks.py` 无 tool_end；monitor 不持久化；无 projection/端点；`health_view` 闲置；前端丢弃 `governance_replay`） |
| `git log` / `git status` / `git branch --show-current` | 基线、工作树、branch 状态 | `main`；工作树含**非本任务**改动（简历文件 + P2-1/P2-2 未跟踪报告）→ §9 遵守 AGENTS §10.5 |

> 未读（按 AGENTS §3 Document Loading Policy 明确排除）：F1–F7 specs、F9 Batch1–8 报告、`docs/interview/*`、`docs/frontend/*` —— 与本 Phase 无依赖关系。

---

## 2. 决策总览（Q1–Q8 → `D-P3-001…008`）

| Q | Decision ID | 标题 | 状态 | 一句话结论 |
|---|---|---|---|---|
| Q1 | **D-P3-001** | P3 编号体系与 P2-3/P2-4 映射 | Proposed | P3-1…P3-5 为**唯一规范编号**；旧 P2-3/P2-4 的 runtime observability 部分 superseded-by-P3；transcript 剔出；P2-5 不受影响 |
| Q2 | **D-P3-002** | Event Contract additive evolution | Proposed | 批准 additive（新增 `RuntimeEvent` 契约 + 词表扩展 + 观察型回调）；**不改** `lifecycle_event` / terminalize / CAS / funnel / enforcement |
| Q3 | **D-P3-003** | 事件粒度分级 + 禁止 tracing backend | Proposed | durable 默认 task/agent/tool 级；step 级可配置默认关；**禁止** Runtime Event 演变为 tracing backend |
| Q4 | **D-P3-004** | Admin API 安全边界 | Proposed | 默认关闭 + 仅 localhost/env 开启 + 只读 + 字段最小化（无 prompt/token/secret/正文） |
| Q5 | **D-P3-005** | LangSmith correlation | Proposed | **沿用既有 `run_id`；不新增 `trace_id`** |
| Q6 | **D-P3-006** | LangSmith integration 独立 P3-5 | Proposed | 独立批次 + 单独 dependency / data-export Decision；默认关闭、fail-open、不进 control path |
| Q7 | **D-P3-007** | Conversation transcript | Proposed | **不纳入 P3**；执行可观测与对话历史解耦 |
| Q8 | **D-P3-008** | PROJECT_CONTEXT 同步 | Proposed | Decision Closure 获批后**同批**更新（含修正当前状态漂移） |

> 8 条决策的**逐字追加文本**见 `P3_DECISION_UPDATE_PROPOSAL.md` §2（已 `git apply --check` 通过）；
> 以下 §3 为同一内容的决策摘要，供 Review 使用（不复制全文，避免双源）。

---

## 3. 逐项 Decision Closure

### D-P3-001 — P3 编号体系与 P2-3/P2-4 映射（Q1）

- **Decision ID**: `D-P3-001`
- **Context**: 用户以 **P3**（P3-1…P3-5）命名 Runtime Observability Phase；Phase-2 路线图以
  **P2-3**（durable 时间线 + transcript + 刷新恢复 UI）、**P2-4**（admin runtime 端点）命名相邻能力，
  且二者**均无 Spec**（pending spec）。不裁决将产生同一工作双编号漂移（AGENTS §1 要求显式裁决）。
- **Options considered**: A 全局采用 P3-1…P3-5（旧 P2-3/P2-4 标 superseded-by-P3，transcript 剔出）；
  B 保留 P2-3/P2-4、P3 仅作别名；C 拆回 P2-3/P2-4。
- **Selected decision**: **A**。冻结映射：
  | P3 批次 | 目标 | 与旧编号关系 |
  |---|---|---|
  | P3-1 | Event Contract | 旧 P2-3「durable 事件时间线」的契约前置 → superseded-by-P3-1 |
  | P3-2 | State Projection | 旧 P2-4 的数据层前置 → superseded-by-P3-2 |
  | P3-3 | Admin API（只读端点族） | **= 旧 P2-4** 端点部分 → superseded-by-P3-3 |
  | P3-4 | Frontend（用户进度 + 管理 Console） | 旧 P2-3「刷新恢复 UI」+ 旧 P2-4 展示层 + `D-Phase2-P2-2-017` 遗留 reason 展示 → superseded-by-P3-4 |
  | P3-5 | LangSmith integration | **新增**（旧路线图无此项） |
  | — | conversation transcript | **不在 P3**（`D-P3-007`） |
  | — | P2-5 session 标题 | **不受影响** |
- **Consequences**: 编号单一权威；历史 `P2-*` 文档不改写，仅以指针标注 superseded；后续 Spec 与
  Project Context 必须显式标注该关系。
- **Frozen constraints**: 批次编号与语义边界不得重排/复用；禁止双编号并存；历史 P2 文档零改动。
- **Future implementation constraints**: 每批独立 branch `feature/p3-N-<slug>`；P3-1 未 Freeze 前
  不得启动 P3-2…P3-5 实现；transcript 立项需用户另定编号。
  **P3-4 frontend 获准（I-7）**：`D-Phase2-P2-2-017` 的「本批零 frontend 改动」只约束 **P2-2 批次**，
  **不约束 P3**；P3-4 修改 `frontend/src/**` 属本 Phase 已批准范围。

### D-P3-002 — Event Contract additive evolution（Q2）

- **Decision ID**: `D-P3-002`
- **Context**: `governance_events` 白名单仅 lifecycle；agent/tool/step 事实全部 live-only；
  live 与 durable 无统一契约；`events.py` 属冻结 observation 模块；`callbacks.py` 是 budget
  control 唯一 canonical producer；`D-Phase2-P2-2-008` 明文把 payload 扩展留给 P2-4（= 本决策承接点）。
- **Options considered**: A additive evolution（新增 `RuntimeEvent` 契约 + 复用既有单写者 +
  观察型回调）；B 新建 bus/表/sequencer；C 仅扩展 `lifecycle_event` payload；D 维持现状（只做 UI）。
- **Selected decision**: **A**，并显式冻结 **IS / IS NOT**：
  - **IS**：新增 `RuntimeEvent` 契约文档；`event_type` additive 扩展；`callbacks.py` 新增
    `on_tool_end`/`on_tool_error` 等**观察型**回调；live/durable 同源（`event_id` 对齐）；
    durable 写失败 fail-open + `durability_gap`。
  - **IS — 采集点授权（I-2）**：**允许**在 `run_deep_agent` astream 循环内、`research/orchestrator`
    **既有 `monitor.*` 调用点旁**新增 observation-only emit；**MUST NOT** 改控制流 / 返回值 /
    异常语义（含 `governance_active` re-raise）/ 工具签名 / prompt / topology（界定 F15 边界）。
  - **IS NOT**：不改 `lifecycle_event` 语义与 payload 构造；不改 `terminalize`/`finalize_with_event`/
    CAS/retry/`pending`；不改 funnel/watchdog/BudgetCounter/异常映射；不改 enforcement 路径
    （`on_llm_start` 分类计数、`on_tool_start` 双槽计数、`GovernanceLimitExceeded` 抛出）；
    不新增 sequencer/bus/事件表；不复制 monitor seq；不让新增回调计数或抛出。
- **Consequences**: 执行事实获得 durable 载体且冻结语义零改动；代价 = 写放大（受 `D-P3-003` 与
  `D-Phase2-P2-2-015` 约束）；需以「计数不变」测试锁定观察路径不影响 enforcement。
- **Frozen constraints**: Controller 唯一 lifecycle 权威；TaskRecord = terminal truth；
  event = observation（不驱动状态）；`(task_id,seq)` 全序/PK 幂等/replay cursor 语义不变；
  三平面不混；monitor `seq` 不进 durable 契约。
- **Future implementation constraints**: P3-1 走 L3（Spec + Readiness + Decision + Plan Review）；
  `event_type` 逐条显式登记，禁止隐式 fallback 产生新类型；新增事件 fail-open / 不进 control path /
  payload 无凭据与正文（继承 `-008`）；「恰好一次」表述须继承 `-009` 的跨进程限制声明；
  **`-008` 遗留项拆分（I-1）**：契约/词表 → P3-1、展示 → P3-3；`task_stale_detected` /
  `task_reclaimed` / `event_durability_gap` 是否加入由 P3-1 Spec 裁决；`error` 字符串载体与脱敏约束不变；
  **migration 边界（I-6）**：P3 默认 zero migration，additive index 需 Spec 列出 + 新 Decision + `0004_*`，
  禁止改 `0001–0003`；
  **Architecture Gate（I-4）**：本决策即 `ARCHITECTURE.md` §8 对「扩展 event / WS frame 类型」要求的
  Decision 记录；P3-1 Spec MUST 引用 §6/§8/§9 并声明非静默扩展。

### D-P3-003 — 事件粒度分级 + 禁止演变为 tracing backend（Q3）

- **Decision ID**: `D-P3-003`
- **Context**: 粒度决定写放大（PG 短连接 + 事件循环内同步写）；同时存在职责滑坡风险（若开始记录
  prompt/正文/token/逐 chunk 即与 LangSmith 平面重叠，违背用户「不替代 LangSmith」目标）。
- **Options considered**: A 分级 + 默认 tool 级（step 可配置且默认关）；B 全量 step/LLM 级；
  C 仅 task 级；D 所有 live 事件原样落库。
- **Selected decision**: **A**。**默认 `tool` 为冻结默认级别**（I-3 裁决）；仅 env 命名 / 值域校验 /
  payload 上限 / 采样率细节由 P3-1 Spec 定稿；**改变默认级别需新 Decision**。step 级默认 off、
  需显式开启 + **确定性**采样（按 `(task_id, step_index)` 稳定哈希，沿用 heartbeat 相位先例）；
  **禁止**记录 prompt 正文 / 输出正文 / 工具参数与结果正文 / token·成本 / 逐 chunk / 全量 messages，
  **禁止**做 span 树 / 采样引擎 / 查询 UI / 保留策略引擎（均为 LangSmith 平面职责）。
- **Consequences**: 默认配置下事件量 ≈ 工具调用次数 × 常数，足以回答「正在做什么」；细粒度诊断需
  显式开启并承担成本；与 LLM 平面职责边界清晰。
- **Frozen constraints**: 事件写 = 单语句短事务 + 节流/抖动；不引连接池/批量 writer/新依赖；
  payload 必须有长度上限（clip 规则，具体值 P3-1 定稿）；观测失败 fail-open 且不得削弱 hard budget。
- **Future implementation constraints**: P3-1 定稿级别默认值/词表/payload schema/上限/采样语义；
  上线前 MUST 提供 PG 写成本实测证据；未来更细粒度 tracing 必须走 LangSmith 或新 Decision。

### D-P3-004 — Admin API 安全边界（Q4）

- **Decision ID**: `D-P3-004`
- **Context**: 系统无认证（`P005` Won't Fix，CORS `*`）；新增管理端点扩大信息暴露面（他人任务
  query/error/状态）。AGENTS §6：安全边界优先于便利。
- **Options considered**: A 默认关闭 + 仅 localhost/env 开启 + 只读 + 字段最小化；B 与既有端点同姿态
  开放；C 先引入鉴权；D 不做 API。
- **Selected decision**: **A**。`RUNTIME_ADMIN_API=disabled` 为缺省（未开启时端点不可达，判定必须
  明确无歧义）；仅接受回环来源，非本地 403 fail-closed；全部 `GET` 只读（无取消/终止/重试/配置写）；
  **MUST NOT** 返回 prompt 正文、模型输出正文、工具参数/结果正文、token/成本、凭据/API key、
  文件绝对路径（既有端点已暴露者除外）；`error` 沿用 ≤800 clip 并继承 `-008`；`limit` 有上限、查询有界。
- **Consequences**: 能力默认不可达，暴露面不扩大；代价 = 本地演示需显式开启，默认配置下管理
  Console 不可用（须按文档开启）。
- **Frozen constraints**: `P005` 状态不变；CORS 配置不得改动；管理端点不得成为执行依赖（关闭时
  零影响）；不得弱化既有安全不变量。
- **Future implementation constraints**: P3-3 必须为**每个**端点提供 TESTING §5 负路径测试
  （未开启不可达 / 非本地拒绝 / 字段最小化 / limit 越界）；任何写或控制能力需新 Decision；
  未来引入鉴权属独立 Decision，叠加而非替代本决策。
  **env 义务（I-5）**：`RUNTIME_ADMIN_API` 等新增 env MUST 更新 `.env.example`（默认值 + 含义注释）
  并在 P3-3 Spec 配置表登记（`ARCHITECTURE.md` §9 红线）。
  **Architecture Gate（I-4）**：本决策即 `ARCHITECTURE.md` §8 对「新增 HTTP endpoint」要求的 Decision
  记录；P3-3 Spec MUST 引用 §6/§8/§9 并声明非静默扩展（完整表述见 D-P3-002，不重复）。

### D-P3-005 — LangSmith correlation 沿用 `run_id`（Q5）

- **Decision ID**: `D-P3-005`
- **Context**: 全仓库无 `trace_id`/LangSmith 引用；F1 已使 `run_id` 跨 governance/execution/
  research/monitor **同源**（同一值）。
- **Options considered**: A 沿用 `run_id`，不新增 `trace_id`；B 新增独立 `trace_id`；
  C 用 `thread_id`；D 不关联。
- **Selected decision**: **A**。LangSmith 自身 run/trace 标识只存在于 LLM trace 平面，Console 仅展示
  引用/链接；**不得**写入 `governance_tasks` / `governance_events` / `research_*` 的业务键位置。
- **Consequences**: 零新身份、零 schema 影响；关联粒度为 run（更细粒度由 LangSmith 承载，不属
  Runtime 平面职责）。
- **Frozen constraints**: 三平面身份模型不变；`run_id` 生成/绑定语义（F1：前绑定、仅 `running` 且
  NULL 才回填、已有值不重生成）不得修改；各 `seq`/`event_id` 语义不得为关联目的改写或互抄。
- **Future implementation constraints**: 确需独立 trace 身份时 MUST 新开 Decision（不预留静默扩展）；
  Console 的 trace 链接不得成为可用性依赖；P3-1…P3-4 不得引入 LangSmith 专用字段。

### D-P3-006 — LangSmith integration 独立 P3-5（Q6）

- **Decision ID**: `D-P3-006`
- **Context**: 接入涉及 ①新增依赖（AGENTS §4 需显式批准）②数据外发（隐私/合规裁决）③成本
  （长任务 trace 量 + 保留策略）——三者均超出「Runtime 可观测」本体。
- **Options considered**: A 独立 P3-5 + 单独 dependency / data-export Decision；B 并入 P3-1；
  C 永久不做。
- **Selected decision**: **A**。默认关闭（未配置时零外发零行为变化）；fail-open（不可达不影响执行）；
  启用状态可观测（进 diagnostics，防「以为有 trace 其实没有」）；不进 control path（不计入
  BudgetCounter、非 DurableEvent 必需项、不写业务表）；**不做替代品**（Console 不展示/代理 token、
  cost、prompt、evaluation，仅跳转引用）。
- **Consequences**: P3-1…P3-4 可独立交付，不背负 SaaS 依赖；代价 = Console 不提供 LLM 成本一站式
  视图（设计意图如此）。
- **Frozen constraints**: 不得成为执行可用性依赖；不得改 budget/timeout/cancellation 语义；
  不得改工具签名或 agent 业务逻辑；凭据仅从 `.env` 读取、不入库；依赖变更经用户批准 +
  ARCHITECTURE §8 Gate。
- **Future implementation constraints**: P3-5 立项需 Readiness + 依赖 Decision + 数据外发 Decision +
  采样/成本上限 + 回滚方案（关闭开关回到零外发）；P3-5 取消时 P3-1…P3-4 必须仍完整可用（单向依赖）。

### D-P3-007 — Conversation transcript 不纳入 P3（Q7）

- **Decision ID**: `D-P3-007`
- **Context**: 旧 P2-3 把「事件时间线」与「transcript + 刷新恢复」打包；二者性质不同：前者是
  execution observability（数据源 = governance/health/runtime event），后者是 conversation history
  （需新表 + role/query/result/files 语义 + 与 checkpoint 历史关系裁决 + 前端对话改造）。打包会
  导致范围膨胀并引入第二套「对话真相」。
- **Options considered**: A 不纳入（解耦，留独立批次）；B 纳入 P3-4；C 提前独立做。
- **Selected decision**: **A**。P3 只回答「任务在做什么 / 是否卡住 / 为何失败」；对话历史的持久化与
  重建**不在任何 P3 批次内**。
- **Consequences**: 边界清晰、无需新表、与冻结面解耦；代价 = 「刷新后恢复完整对话内容」仍不可得
  （仅恢复执行态与时间线）——须在 P3 文档与 UI 文案中**显式声明**，不得让用户误以为对话历史已持久化。
- **Frozen constraints**: P3 任何批次不得新增 conversation/transcript 表或列；不得实现完整对话
  历史重建；P3-4 恢复能力仅限执行态（阶段/agent/tool/时间线/终态原因）。
- **Future implementation constraints**: transcript 立项需独立 Spec + 独立 Decision（表设计 /
  与 checkpoint `messages` 关系 / 隐私与保留策略），编号由用户裁决；P3-4 设计须保留
  「对话渲染层」与「执行态层」分离的接缝。

### D-P3-008 — `PROJECT_CONTEXT.md` 同步更新（Q8）

- **Decision ID**: `D-P3-008`
- **Context**: `PROJECT_CONTEXT.md` 仍停留在 F9-P0 阶段且声明「禁止修改 F9 runtime」，与 `main`
  实际（P2-1/P2-2 FROZEN+MERGED）不符；P3 Decision Closure 亦引入新阶段状态与 Next Action。
- **Options considered**: A 批准后**同批**更新（含修正漂移）；B 推迟到 P3-1 实施后；C 不更新。
- **Selected decision**: **A**。更新范围 = 当前阶段（P3 Runtime Observability，Decision Closed /
  未实现）、Next Action（P3-1 L3 Spec）、§8 已知限制、§9 基线表（补 P2-1/P2-2 与 P3 Decision
  Closure）、§4/§5 指针；拟改文本见**附录 A**。
- **Consequences**: 索引恢复可信，后续 Agent 不再据过期状态决策；代价 = 一次需与 `DECISION.md`
  同批提交的文档改动（Commit Gate 覆盖两个文件）。
- **Frozen constraints**: `PROJECT_CONTEXT.md` 仍是 current-state index、**不是规范来源**，不得复制
  Decision/Spec 正文；冲突以权威文档为准并报告；历史 `P2-*` 与既有 Spec 零改动。
- **Future implementation constraints**: 每批 P3-N 完成/Freeze 时按 §12 更新（状态/基线/Next
  Action/Last updated）；Decision Closure 落地必须与索引同步，禁止只改 DECISION.md 留索引漂移。

---

## 4. 冻结约束汇总（Frozen Constraints；不可改动清单）

| # | 不可改动项 | 来源 |
|---|---|---|
| F1 | F8 Controller 是唯一 lifecycle/budget/deadline/cancel/terminal 权威；不新增第二 Runtime 控制面 | F8 §5.3；`D-Phase2-P2-2-001`；`D-P3-002` |
| F2 | `TaskRecord` = terminal truth；event = observation（可延迟/缺失，**绝不驱动状态**） | F8 Step4 Spec；`D-P3-002` |
| F3 | `terminalize` / `finalize_with_event` / 乐观 CAS（`WHERE status='running' AND version=?`）/ retry 阶梯 / `pending` 语义 | F8 Step2；`D-P3-002` |
| F4 | terminal funnel / watchdog / BudgetCounter（四 hard counter、search ⊆ tool、无 overshoot）/ 异常映射 | F8 Step3；`D-P3-002` |
| F5 | `events.lifecycle_event` 既有语义与 payload 构造（status/terminal_reason/error_kind/error/counters_snapshot/finished_at） | `D-Phase2-P2-2-008`；`D-P3-002` |
| F6 | `governance_events` 单写者 + `(task_id, seq)` 全序 + `UNIQUE` + PK 幂等 + task-scoped replay cursor | F8 Step4；`D-P3-002` |
| F7 | `GovernanceCallbackHandler` enforcement 路径（`on_llm_start` 分类与计数、`on_tool_start` 双槽计数、`GovernanceLimitExceeded` 抛出语义） | F8 Step3；`D-P3-002` |
| F8 | 三平面（checkpoint / TaskRecord+GovernanceEvent / ResearchRun）不共表、不共迁移、不共事务；身份模型（`thread_id`/`task_id`/`run_id`）不变 | PROJECT_CONTEXT §3；`D-P3-005` |
| F9 | 无 `TaskStatus` 新增；`stale` 保持派生态（不写入 `governance_tasks.status`） | `D-Phase2-P2-2-001` |
| F10 | heartbeat 不是 lease/fencing；single-instance 假设不变 | `D-Phase2-P2-2-001/-010` |
| F11 | PG 写约束：单语句短事务 + 节流/抖动；不引连接池、不加依赖、不批量写（除独立批准） | `D-Phase2-P2-2-015`；`D-P3-003` |
| F12 | P3 不新增 conversation/transcript 表或能力；执行可观测与对话历史解耦 | `D-P3-007` |
| F13 | Admin 端点默认关闭/仅本地/只读/字段最小化；`P005` 无鉴权边界不变、CORS 不改 | `D-P3-004` |
| F14 | LangSmith 不进 control path、不作为可用性依赖、不写业务表、不替代 Runtime Console | `D-P3-006` |
| F15 | 工具签名、agent prompt/topology、research plane（F1–F7/F9）**行为契约**零改动。**边界（I-2 裁决）**：**允许**在 `run_deep_agent` astream 循环内与 `research/orchestrator` 既有 `monitor.*` 调用点**旁**新增 observation-only emit；MUST NOT 改控制流 / 返回值 / 异常语义 / 工具签名 / prompt / topology | AGENTS §4/§5；P2-2 Non-Goals；`D-P3-002`（IS — 采集点授权） |
| F16 | 历史 `P2-*` 文档、既有 Spec、`P2-2_DECISION_*` 一律**只追加/只标注**，不改写 | AGENTS §10.4；append-only 先例 |
| F17 | **ARCHITECTURE.md §8 Architecture Change Gate（I-4）**：新增 HTTP endpoint、扩展 event / WS frame 类型 MUST 先有 Decision 记录 → `D-P3-002`（event/WS）与 `D-P3-004`（endpoint）即该记录；P3-1/P3-3 Spec MUST 引用 `ARCHITECTURE.md` §6/§8/§9 并声明**非静默**扩展（§9 红线） | `ARCHITECTURE.md:142-154`、`§9:160`；`D-P3-002`/`D-P3-004` |
| F18 | **P3 默认 zero migration（I-6）**：P3-1/P3-2 不建表、不改 schema；若 P3-2/P3-3 需 additive index → Spec 逐条列出 + **新 Decision** 批准 + 新增 `0004_*.{sqlite,postgres}.sql`；**禁止修改既有 `0001–0003`** | `D-P3-002`（migration 边界） |
| F19 | **env 变量义务（I-5）**：新增 env variable MUST ①更新 `.env.example` ②填默认值 ③填含义注释 ④Spec 配置表登记（`ARCHITECTURE.md` §9 红线：MUST NOT 新增环境变量而不更新 `.env.example`） | `ARCHITECTURE.md:163`；`D-P3-003`/`D-P3-004` |

---

## 5. 后续实施约束汇总（Future Implementation Constraints）

| # | 约束 | 适用批次 |
|---|---|---|
| I1 | 每批独立 feature branch `feature/p3-N-<slug>`；批次边界不得混合（AGENTS §10.1） | P3-1…P3-5 |
| I2 | 批次等级：P3-1 / P3-2 / P3-3 / P3-5 = **L3**（Spec + Readiness + Decision Closure + Plan Review + 对抗/集成验证 + 用户 Freeze）；P3-4 = **L2** | 全部 |
| I3 | 依赖顺序：P3-1（契约）→ P3-2（投影）→ P3-3（API）→ P3-4（UI）；P3-5 独立但单向依赖（取消 P3-5 不影响 P3-1…4） | 全部 |
| I4 | P3-1 未 Freeze 前不得启动 P3-2…P3-5 的实现 | P3-2…5 |
| I5 | `event_type` 词表逐条显式登记；禁止隐式 fallback 生成新类型；新增事件 fail-open 且不进 control path | P3-1 |
| I6 | 新增回调必须以「计数不变」测试锁定（enforcement 零回归）；观察路径异常一律 fail-open | P3-1 |
| I7 | 每个 admin 端点必须有 TESTING §5 负路径测试；默认关闭状态不可达 | P3-3 |
| I8 | 上线前需 PG 写成本实测证据（在途任务数 × 事件率 对 loop 抖动的影响） | P3-1/P3-3 |
| I9 | P3 文档与 UI 必须显式声明「对话历史未持久化（transcript 不在 P3）」的已知限制 | P3-4 |
| I10 | P3-4 设计保留「对话渲染层 / 执行态层」分离接缝，避免 transcript 未来立项返工 | P3-4 |
| I11 | P3-5 需 Readiness + 依赖 Decision + 数据外发 Decision + 采样/成本上限 + 回滚方案 | P3-5 |
| I12 | 每批完成/Freeze 时按 PROJECT_CONTEXT §12 更新索引（状态/基线/Next Action/Last updated） | 全部 |
| I13 | 若实施中发现需改冻结面 → STOP → 报告 Cross-Feature Compatibility Gap → 等用户裁决（AGENTS §10.1） | 全部 |

---

## 6. 与既有 Decision 的关系（承接 / 不覆盖）

| 既有决策 | 关系 | 说明 |
|---|---|---|
| `D-Phase2-P2-1-001`（policy=None ⇒ governed） | **前提依赖** | P3 全部能力建立在「生产恒 governed」基线上（watchdog/terminal event 常开） |
| `D-Phase2-P2-2-001`（health plane；stale 派生态；非 lease） | **继承** | P3-2 的 `liveness` 投影沿用派生态语义；`D-P3-002` 重申不改 TaskStatus |
| `-002`（阈值默认） | 继承 | P3 不调参（Runtime Policy Tuning 仍属独立批次） |
| `-003`（终态映射） | 继承 | P3 不新增终态；reclaim 语义不变 |
| `-008`（诊断载体 = `error` 字符串，payload 扩展留 P2-4） | **承接（本决策即该遗留项）** | `D-P3-002` 明确 IS/IS NOT；结构化 payload 的正式化在 P3-1 Spec 定稿；`error` 的脱敏约束继续适用 |
| `-009`（跨进程重复 terminal event = 已知限制） | **继承 + 扩展** | 新增事件的「恰好一次」表述必须同样声明该限制（`D-P3-002` I 条款） |
| `-010`（`SAME_HOST_PREV` 需 liveness 证据） | 继承 | P3-2 的 stale 展示不得弱化该安全性质 |
| `-011`/`-013`/`-014`（确认次数 / startup 时序 / interval） | 继承 | P3 不改 scanner 判定与时序 |
| `-012`（`flush_pending` 本批不接线） | **仍待办（本批未关闭）** | P3 依赖「pending 降级可见」而非「pending 被 flush」→ `degraded_durability` 必须进投影（P3-2 I 条款） |
| `-015`（PG 写约束） | 继承 | 直接约束 `D-P3-003` 的事件粒度与节流 |
| `-016`（Problem 登记时机） | 同法处理 | 本批 Problem Capture Review 见 §8 |
| `-017`（P2-2 零 frontend 改动；reason 展示记后续项） | **批次边界（I-7 已裁决）** | P3-4 将修改 frontend：`-017` 的「零 frontend」是 **P2-2 的批次边界声明**，只约束 P2-2，**不约束 P3**；`-017` 记入的「reason 展示」后续项由 P3-4 承接。本裁决已写入 `D-P3-001` |
| 未来若做 trace_id | **禁止静默扩展** | `D-P3-005` 要求新开 Decision |
| 未来若做 transcript | **禁止复用 P3 编号** | `D-P3-007` 要求独立 Spec/Decision + 用户裁决编号 |

---

## 7. 落地路径与待批准事项

### 7.1 落地路径（待用户裁决；提案 §6 已列 A/B/C）

- **A（推荐）**：批准后在 `main` 直接提交**纯文档**改动（`DECISION.md` 追加 + `PROJECT_CONTEXT.md`
  同步，同批），提交前执行 `DECISION.md` 提案 §3 的 HARNESS REVIEW 与 AGENTS §10.6 Commit Gate。
  依据：AGENTS §10.2（Decision/Readiness 产物可留 main）+ §10.7（Harness 治理文件低风险文档维护）；
  P3-1 实施仍另开 feature branch。
- **B**：暂不落地，随 P3-1 branch 实现提交一并落地（与 P2-2 先例一致；期间 Decision 未生效）。
- **C**（不推荐）：仅改 `DECISION.md`，`PROJECT_CONTEXT.md` 延后 → 违反 `D-P3-008`。

### 7.2 进入 P3-1 L3 Spec 阶段的入口条件（全部满足方可启动）

```text
[ ] 用户批准 D-P3-001 … D-P3-008（可逐条批准；见提案 §5 裁决栏）
[ ] 用户裁决落地路径（7.1 A/B/C）并完成 DECISION.md / PROJECT_CONTEXT.md 落地
[ ] 用户裁决 P3-1 的 Spec 范围与 Non-Goals（尤其 event_type 词表与默认粒度）
[ ] 确认 P3-1 的冻结面清单（§4 F1–F16）无遗漏
[ ] 确认 §10 待裁决项（本报告新增 3 项）已裁决
[ ] 建立 feature branch（git 写操作，需用户批准）
```

### 7.3 明确不在本阶段的事

- ❌ 不写 P3-1 Spec / Plan（本轮只关闭决策）
- ❌ 不改任何代码 / DB / migration / 依赖
- ❌ 不创建 `P2-3`/`P2-4` 或 `P3-*` 的 Spec 文档
- ❌ 不落地 `DECISION.md` / `PROJECT_CONTEXT.md` 改动
- ❌ 不创建 branch、不 commit / push / merge

---

## 8. PROBLEM CAPTURE REVIEW（PROCESS.md §13；三问必答）

| # | 问题 | 回答 | 依据 |
|---|---|---|---|
| 1 | 本任务是否发现新的长期约束？ | **Yes** | 本次 Decision Closure 本身产生 16 条冻结约束（§4 F1–F16）与 13 条后续实施约束（§5 I1–I13）——均为未来 Agent 必须遵守的长期约束；其中 `D-P3-002` 的 IS/IS NOT 清单与 `D-P3-004` 的安全边界为**不可协商**项 |
| 2 | 是否产生未来 Agent 需要知道的信息？ | **Yes** | ① `D-Phase2-P2-2-008` 的「payload 扩展留 P2-4」遗留项**已由 `D-P3-002` 承接**（否则未来 Agent 会重复讨论）；② `D-P3-001` 的编号映射（P3 ↔ 旧 P2-3/P2-4）避免双编号漂移；③ `PROJECT_CONTEXT.md` 状态漂移（C3）已被识别并纳入 `D-P3-008` |
| 3 | 是否存在重复发生风险？ | **Yes** | 高风险项 = 「重复造已有机制」（第二 event bus / 影子 task 表 / 自建 trace 平面）与「编号双轨」；本批以 `D-P3-002`（IS NOT 清单）、`D-P3-001`（禁止双编号）显式阻断，并在提案 §3 HARNESS REVIEW 中列为比对项 |

### 8.1 处置决策（用户 2026-10 裁决：**暂不登记**）

| 决策 | 内容 | 理由 |
|---|---|---|
| **D1 → 不登记** | 本批产生的 8 条 `D-P3-*` 决策本身 | 约束已进入 `DECISION.md`（权威载体），再登记为 Problem 属重复（PROCESS §13 克制约束） |
| **D1 → 不登记（N3 裁决）** | ①「运行时可观测能力已完成但缺出口（health 派生视图 / durable replay 未被消费）」；②「文档-实现漂移：前端 run_id 注释与 F1 绑定事实不符」 | **用户裁决：暂不登记**（本轮不创建 `docs/problem/candidates/` 文件、不改 `PROBLEM.md`）；后续如需登记另行裁决 |
| **D4 → 既有 candidate 近命中评估** | 无既有 candidate 命中（`docs/problem/candidates/` 未在本批读取范围内发现相关项） | 若用户认为存在近命中，可另行指定 |
| **D1 → 不登记（新发现）** | `PROJECT_CONTEXT.md` 状态漂移 | 已由 `D-P3-008` 作为**已执行**的同步动作处理（见 §12），非长期问题 |

> 本节点结论：**可进入 COMPLETE（L1 文档产物）**；D2（直接建档）本批**零执行**。

### 8.2 存量偏差记录（**不登记为 Problem Candidate**；I-5 裁决）

> 用户裁决：**不要立即登记 Problem Candidate**；先作为**存量偏差记录**，等待单独裁决。

| 项 | 内容 |
|---|---|
| 偏差 | `ARCHITECTURE.md` §9 红线（`ARCHITECTURE.md:163`）要求「新增环境变量 MUST 更新 `.env.example`」；**P2-2 新增 12 个 `RUNTIME_*` 环境变量从未同步**（`RUNTIME_RECLAIM_MODE`、`RUNTIME_HEARTBEAT_INTERVAL`/`_JITTER`、`RUNTIME_STALE_GRACE_PERIOD`/`_CONFIRMATIONS`、`RUNTIME_SCAN_INTERVAL`/`_SCAN_BATCH_LIMIT`、`RUNTIME_STARTUP_SWEEP_BUDGET`、`RUNTIME_RECLAIM_MAX_PER_CYCLE`/`_MIN_AGE`/`_LOCAL_ONLY`、`RUNTIME_WALL_CLOCK_TIMEOUT`） |
| 证据 | `.env.example` 中 `RUNTIME_` 条目数 = **0**；`git log -S "RUNTIME_RECLAIM_MODE" -- .env.example` → 空（从未写入）；commit `884a681` 文件清单不含 `.env.example` |
| 影响 | P2-2 的 runtime 配置对使用者**不可发现**（只能读代码或 Spec）；且后续若按同一模式新增 env 会继续累积偏差 |
| 前向约束 | 已写入 `D-P3-003`/`D-P3-004`（F19）：P3 新增 env MUST 同步 `.env.example`（默认值 + 含义注释）+ Spec 配置表登记 |
| 处置 | **待单独裁决**（候选处置：独立小批修复 / 登记 Problem Candidate / 维持现状并记录为已知限制）；**本批未改动 `.env.example`、未改 `PROBLEM.md`** |

---

## 9. 限制遵守证据（用户禁令逐项核对）

| 禁令 | 状态 | 证据 |
|---|---|---|
| 禁止修改代码 | ✅ 未修改 | `git status --short -- app frontend tests` → 空（见 §9.1 命令输出） |
| 禁止修改数据库 | ✅ 未修改 | `git status --short -- db` → 空 |
| 禁止创建 migration | ✅ 未创建 | `db/governance_migrations/` 仍为 0001–0003；无新文件 |
| 禁止新增依赖 | ✅ 未新增 | `git status --short -- requirements.txt pyproject.toml uv.lock` → 空 |
| 禁止修改 `events.py` / `callbacks.py` | ✅ 未修改 | `git status --short -- app/runtime/governance` → 空 |
| 禁止创建 feature branch | ✅ 未创建 | `git branch --show-current` = `main`；未执行 `git branch`/`checkout` |
| 禁止 git commit / push | ✅ 未执行 | 本会话未执行任何 git 写操作（仅 `status`/`log`/`diff`/`apply --check` 等只读命令） |
| DECISION.md 只输出 proposal | ✅ 未修改 | `DECISION.md` 仍 881 行、与 HEAD 一致；追加内容仅存在于 `P3_DECISION_UPDATE_PROPOSAL.md` |
| PROJECT_CONTEXT.md | ✅ 未修改 | Q8 决策为「批准后同步」；拟改文本见附录 A |

### 9.1 只读验证命令与实际输出（`git apply --check` 为唯一「写意图」操作，实际零写入）

```text
$ git branch --show-current
main

$ (Get-Content -Encoding UTF8 DECISION.md).Count
881                                    # 与基线一致 → 未被修改

$ git status --short -- app db frontend tests requirements.txt pyproject.toml uv.lock \
        DECISION.md PROJECT_CONTEXT.md AGENTS.md PROCESS.md TESTING.md PROBLEM.md ARCHITECTURE.md
（空输出）                              # 代码 / DB / 依赖 / 全部治理文档零改动

$ Get-ChildItem db\governance_migrations
0001_governance.postgres.sql   0001_governance.sqlite.sql
0002_sessions.postgres.sql     0002_sessions.sqlite.sql
0003_runtime_health.postgres.sql  0003_runtime_health.sqlite.sql
                                       # 未创建 migration（仍为 0001–0003）

$ git status --short                    # 全量工作树（含基线既有的非本任务改动）
 M 简历项目经历-深度研搜.md               # 基线既有（非本任务）
?? P2-1_INTEGRATION_READINESS_REPORT.md  # 基线既有（非本任务）
?? P2-1_MERGE_REPORT.md                  # 基线既有（非本任务）
?? P2-2_RELEASE_GATE_AUDIT_REPORT.md     # 基线既有（非本任务）
?? P3_DECISION_CLOSURE_REPORT.md         # ← 本批产出
?? P3_DECISION_UPDATE_PROPOSAL.md        # ← 本批产出
?? P3_RUNTIME_OBSERVABILITY_DISCOVERY_REPORT.md  # ← 上一批产出（L0）
?? docs/images/Snipaste_2026-09-11_02-03-56.png  # 基线既有（非本任务）
?? 简历-AI应用开发-Agent方向.html          # 基线既有（非本任务）

$ git apply --check --verbose <P3_DECISION_UPDATE_PROPOSAL §2 append patch>
Checking patch DECISION.md...
exit = 0        # 追加补丁可干净应用；--check 不写入仓库
```

> 工作树中存在**非本任务**的改动（简历文件、P2-1/P2-2 未跟踪报告、`docs/images/*.png`）。
> 按 AGENTS.md §10.5（Mixed / Unowned Working Tree）本任务 **STOP → report → 不触碰**：
> 未修改、未暂存、未提交上述任何文件；本批仅新增 2 个文档（§7/§9 已列）。

---

## 10. 裁决事项（用户 2026-10 已裁决）

| # | 事项 | 裁决结果 |
|---|---|---|
| N1 | **落地路径** | **A**：`main` docs-only commit；**append-only 新增约束不视为 frozen contract 修改**；精确文件白名单 `git add`；禁止 `git add .` / `-A` / `stash` / `reset` / `discard` |
| N1-b | **落地文件白名单** | 包含：`DECISION.md`、`PROJECT_CONTEXT.md`、`P3_DECISION_CLOSURE_REPORT.md`、`P3_DECISION_UPDATE_PROPOSAL.md`、`P3_RUNTIME_OBSERVABILITY_DISCOVERY_REPORT.md`；排除：`P2-1_INTEGRATION_READINESS_REPORT.md`、`P2-1_MERGE_REPORT.md`、`P2-2_RELEASE_GATE_AUDIT_REPORT.md`、简历相关文件、图片文件 |
| N2 | **`DECISION.md` 头部说明过时**（第 7 行仍写「两类决策：D001–D003 与 D004–D006」） | 接受：**独立文档维护 commit，不混入本批**（本批未改；另见 NOTE-3：`ARCHITECTURE.md:115-116` 重复条目建议同批处理） |
| N3 | **Problem Candidate** | **暂不登记**（§8.1）；P2-2 `RUNTIME_*`/`.env.example` 偏差仅作**存量偏差记录**（§8.2），待单独裁决 |
| N4 | **默认事件粒度** | **`tool`**（已冻结为默认级别，见 `D-P3-003` I-3） |
| N5 | **Admin API 开启方式** | **`disabled` by default + localhost only + env enable**（与 `D-P3-004` 一致；NOTE-1：开启后端点「不存在 vs 404/403」由 P3-3 Spec 关闭） |

---

## 11. Next Action

> **前置链状态：① Decision 批准 ✅ → ② `DECISION.md` 落地 ✅ → ③ `PROJECT_CONTEXT.md` 同步 ✅ →
> ④ 创建 `feature/p3-1-event-contract` branch（未执行，属 git 写操作，需用户批准）。**
> ④ 完成后进入 **P3-1 Event Contract 的 L3 Spec 阶段**（Spec → Readiness → Decision → Plan Review）。
> 未获批准前：不创建 branch、不写 P3-1 Spec、不改代码、不 commit/push。

---

## 12. Landing Note（用户 2026-10 批准后的落地记录）

- **批准**：D-P3-001 … D-P3-008（含 Review 修正项 I-1 … I-7、N1-a/N1-b、N2–N5）。
- **落地内容**：

| 文件 | 动作 | 结果 |
|---|---|---|
| `DECISION.md` | append-only 追加 `D-P3-001 … D-P3-008` | 881 → **1179** 行（+298）；既有条目**零修改**；8 条 Status = `Accepted` |
| `PROJECT_CONTEXT.md` | 阶段 / Next Action / 基线 / 已知限制同步 + 状态漂移修正 | 见附录 A（已应用） |
| `P3_DECISION_CLOSURE_REPORT.md` / `P3_DECISION_UPDATE_PROPOSAL.md` / `P3_RUNTIME_OBSERVABILITY_DISCOVERY_REPORT.md` | 随批入库（决策依据可核验） | — |

- **落地方式（N1-a）**：`main` docs-only commit；精确文件白名单 `git add`（未使用 `git add .` / `-A`）；
  未 `stash` / `reset` / `discard`；**未 push**。
- **Review 修正落点**：I-1/I-6/I-4 → `D-P3-002`（Future Constraints + Related Architecture）；
  I-2 → `D-P3-002`（IS — 采集点授权）；I-3/I-5 → `D-P3-003`；I-5/I-4 → `D-P3-004`；
  I-7 → `D-P3-001`（Future Implementation Constraints）。
- **未做**：未改代码 / DB / migration / 依赖；未建 branch；未写 P3-1 Spec；未改 `PROBLEM.md` /
  `ARCHITECTURE.md` / `AGENTS.md` / `PROCESS.md` / `TESTING.md`。

---

## 附录 A — `PROJECT_CONTEXT.md` 更新（Q8；**已应用**）

> 说明：以下为批准后拟改内容（**当前文件未被修改**）。落地时务必与 `DECISION.md` 同批提交，
> 并执行 TESTING.md §9 HARNESS REVIEW。文本仅为**指针与摘要**，不复制 Decision 正文
> （遵守 PROJECT_CONTEXT 定位与 §12 维护规则）。

| 位置 | 拟改内容（摘要） |
|---|---|
| 头部 Last updated | 更新为 Decision Closure 获批日期 |
| §1 Current Status | 当前阶段 = **P3 Runtime Observability（Decision Closed；未实现）**；当前 Feature = P3（`D-P3-001…008` 已提案/已批准）；允许动作 = P3-1 L3 Spec 准备；Next Action = P3-1 Spec |
| §1 Git 状态 | main = `8d9531d`（P2-2 merged）；工作树说明按实际 |
| §2 System in One Page | 补一句：Runtime Observability 现状 = lifecycle durable + health plane 已落地；agent/tool/step 执行事实**尚未持久化**（P3 范围） |
| §4 Frozen Architecture | 补 P3 冻结指针：`D-P3-002`（Event Contract IS/IS NOT）、`D-P3-004`（Admin 安全边界）、`D-P3-007`（transcript 不在 P3） |
| §7 Roadmap | 新增 P3 段（P3-1…P3-5，标注与 P2-3/P2-4 的 superseded 关系）；P2-3/P2-4 标 superseded（runtime observability 部分）；P2-5 保持 pending |
| §8 Known Limitations | 新增/更新：① conversation transcript 不在 P3（对话历史未持久化）；② 事件粒度默认值；③ Admin API 默认关闭；④ LangSmith 未接入；⑤ `pending_terminal` 无 flush 调用方（`-012` 仍待办）；⑥ 跨进程重复 terminal event（`-009`） |
| §9 Verification Baseline | 新增行：P2-1（FROZEN/MERGED `dd7af9a`）、P2-2（FROZEN/MERGED `884a681`/`8d9531d`）、P3 Decision Closure（本报告 + 提案） |
| §10 Important Files | 新增指针：`P3_DECISION_CLOSURE_REPORT.md`、`P3_DECISION_UPDATE_PROPOSAL.md`、`P3_RUNTIME_OBSERVABILITY_DISCOVERY_REPORT.md` |
| §11 Current Next Action | 替换为 §11（本报告）的 Next Action 文本 |

---

## 附录 B — 附：本批为何不覆盖 `P2-5`（session 标题生成）

`P2-5`（`D-Phase2-P2-5-001`：标题确定性规则优先 + LLM 可选）与 Runtime Observability **无依赖关系**
（数据源 = session + query，与 runtime event/health/projection 无关）。`D-P3-001` 仅声明其**不受本
Phase 影响**，不改变其 pending 状态；若用户希望将其并入 P3（例如作为 P3-6），需显式裁决——本批
默认**不并入**（避免 Phase 范围膨胀）。

---

*附：本报告为 Decision Closure 产物（仅决策，不实现），全部结论基于基线 `main @ 8d9531d` 的实际文档与
代码；`DECISION.md` 追加提案以 `git apply --check` 只读验证（exit 0）并已按用户批准落地（见 §12）。
本批未修改任何代码、数据库、migration、依赖与既有 Spec；未创建 feature branch；`main` 上仅执行
一次 **docs-only commit**（白名单 5 文件，未 push），未进入 P3-1 实现。*
