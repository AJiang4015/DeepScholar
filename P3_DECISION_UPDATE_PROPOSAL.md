# P3_DECISION_UPDATE_PROPOSAL — `DECISION.md` append-only 追加提案（**已落地**）

> 本文件原为 **proposal（提案）**；经用户 2026-10 批准后，其内容已按 **append-only** 追加至
> `DECISION.md`（`D-P3-001 … D-P3-008`）。
> `DECISION.md` 落地前为 881 行，落地后为纯追加（**无既有条目被修改、删除或重排**）。
> 本文件同时保留批准的决策原文与逐条裁决记录（提案态文本不再单独存在）。

- 目标文件：`DECISION.md`（Harness 六核心文件之一）
- 基线：`main` @ `8d9531d`（落地前）；落地批次见 `git log`（`docs(p3): land P3 runtime observability decision closure`）
- 操作类型：**append at EOF**（insertion point = 原第 881 行之后）
- 拟追加 Decision：`D-P3-001` … `D-P3-008`（一一对应 L0 Discovery 报告的 Q1–Q8）
- 上游依据：`P3_RUNTIME_OBSERVABILITY_DISCOVERY_REPORT.md`（L0）；本批产出 `P3_DECISION_CLOSURE_REPORT.md`
- 字段约定说明（见 §4）：本提案在既有条目格式（Context / Decision / Alternatives / Rejected
  Alternatives / Consequences / Constraints Created / Related Problems / Related Architecture）之上，
  按用户要求**显式拆分** `Options Considered` / `Selected Decision` / `Frozen Constraints` /
  `Future Implementation Constraints` 四个字段（中英并列，遵守 `D005`：中文正文 + 英文规范关键词）。

---

## 1. 检查清单（落地时状态）

```text
[x] 用户显式批准本提案全部 8 条 D-P3-00N + Review 修正项 I-1…I-7 + N1-a/N1-b（见 §5 裁决栏）
[x] TESTING.md §9 HARNESS REVIEW 逐项执行（结果见 §3）
[x] AGENTS.md §10.6 Commit Gate：仅含白名单内文件（`DECISION.md` + `PROJECT_CONTEXT.md` + 3 份 P3 文档）
[x] AGENTS.md §10.5 工作树核查：非本任务改动保留未暂存、未触碰（精确 `git add` 白名单）
[x] `PROJECT_CONTEXT.md` 同批同步（`D-P3-008`；拟改文本见
    `P3_DECISION_CLOSURE_REPORT.md` 附录 A）
[x] §6 落地路径裁决：采用 A（main docs-only commit）
```

---

## 2. 精确追加内容（paste-ready）

> 从下面 `PATCH-BEGIN` 到 `PATCH-END` 之间的内容**整段追加**至 `DECISION.md` 末尾。
> 追加后 `DECISION.md` 行数 = 881 + 298 = **1179** 行（纯追加，无既有行变更）。

**追加内容规模与验证证据（实测）**：

```text
$ git apply --check <append-only patch>     # 落地前只读 dry-run
exit = 0                                     # 补丁可干净应用（无冲突、无 fuzz）

$ git diff --stat -- DECISION.md             # 落地后复核
（仅新增行；既有 881 行零修改）
```

- 追加内容 = **297** 行正文 + 1 行分隔空行 = **298** 行新增；
- 8 条 `D-P3-*` 条目、8 条 `Status: Accepted`（无遗留 `Proposed`）；
- hunk 头（dry-run 时）：`@@ -878,4 +878,302 @@`（上下文 = 既有第 878–881 行，未修改）；
- 本批同时应用 Review（I-1…I-7）裁决的追加约束，均落在**追加块内**（不改任何既有 `D-*` 条目）。

<!-- PATCH-BEGIN -->

## D-P3-001 — P3 编号体系与 P2-3/P2-4 映射（Runtime Observability Phase；L0 Q1）

- **Status**: Accepted（用户 2026-10 批准 P3 Decision Closure；append-only 追加）
- **Context**: L0 报告确认仓库同时存在两套编号指向同一能力域：用户以 **P3**（P3-1…P3-5）命名
  Runtime Observability Phase；既有 Phase-2 路线图（`P2-1_INTEGRATION_READINESS_REPORT.md` §3、
  `P2-2_SPEC_v2.md` §21）以 **P2-3**（durable 事件时间线 + conversation transcript + 刷新恢复 UI）、
  **P2-4**（admin runtime 诊断端点）命名相邻能力。两者**均无 Spec 文档**（pending spec），
  若不裁决将出现「同一工作两套账本」的编号漂移（AGENTS §1 要求显式裁决，不得静默选择）。
- **Options Considered**:
  - **A（采纳）**：P3 为 Runtime Observability Phase 的**唯一规范编号**（P3-1…P3-5）；旧
    P2-3/P2-4 中属于本能力域的部分**标记为 superseded-by-P3**；P2-3 的 conversation transcript
    子项**明确剔出** P3（见 `D-P3-007`）；P2-5（session 标题）与本 Phase 无关、不受影响。
  - B：保留 P2-3/P2-4 编号，把 P3 仅作为别名 → 两套编号长期并存，检索/引用歧义，拒。
  - C：把 P3-1…P3-5 拆回 P2-3/P2-4 → 与用户已确认的阶段语义不符（P3 覆盖面大于旧 P2-3/P2-4 之和），拒。
- **Selected Decision**: 采纳 **A**。映射关系（冻结）：
  | P3 批次 | 目标 | 与旧编号关系 |
  |---|---|---|
  | P3-1 | Event Contract（统一 RuntimeEvent 契约；durable 扩展 additive） | 旧 P2-3「durable 事件时间线」的**契约前置**（旧编号未定义契约层）→ superseded-by-P3-1 |
  | P3-2 | State Projection（只读派生，不建表） | 旧 P2-4 的**数据层前置**（旧编号未定义投影模型）→ superseded-by-P3-2 |
  | P3-3 | Admin API（只读端点族） | **= 旧 P2-4**（端点部分）→ superseded-by-P3-3 |
  | P3-4 | Frontend（用户进度 + 管理 Console） | 旧 P2-3「刷新恢复 UI」+ 旧 P2-4 展示层 + `D-Phase2-P2-2-017` 遗留的 reason 展示项 → superseded-by-P3-4 |
  | P3-5 | LangSmith integration（LLM trace 平面关联） | **新增**（旧 Phase-2 路线图无此项） |
  | — | conversation transcript（旧 P2-3 残余） | **不在 P3 范围**；保持 pending（见 `D-P3-007`） |
  | — | P2-5 session 标题生成 | **不受本决策影响**（独立批次） |
- **Consequences**: 单一编号权威，引用无歧义；代价是 `PROJECT_CONTEXT.md` 与后续 Spec **必须**
  显式标注 P2-3/P2-4 的 superseded 关系（历史 `P2-*` 文档**不改写** —— 追加/指针标注，符合
  append-only 与实际交付物不追改的仓库纪律）。
- **Frozen Constraints**: P3-1…P3-5 的编号与语义边界不得重排、不得复用给其它能力域；
  历史 `P2-1/P2-2` 文档一字节不改；禁止 P2-3/P2-4 与 P3-* 双编号并存（同一工作只能有一个规范编号）。
- **Future Implementation Constraints**: 每批 P3-N MUST 使用独立 feature branch
  `feature/p3-N-<slug>`（AGENTS §10.1），批次边界不得混合；P3-1 未 Freeze 之前 MUST NOT 启动
  P3-2…P3-5 的实现（`D-P3-002`/`D-P3-003` 是它们的契约前置）；transcript 若立项，编号由用户另行裁决。
  **P3-4 的 frontend 改动获准（I-7 裁决）**：`D-Phase2-P2-2-017` 的「本批零 frontend 改动」是
  **P2-2 的批次边界声明**，只约束 P2-2 批次，**不约束 P3**；P3-4 修改 `frontend/src/**`
  属本 Phase 的**已批准范围**（并承接 `-017` 记入的「reason 展示」后续项）。
- **Related Problems**: 无新增
- **Related Architecture**: `P3_RUNTIME_OBSERVABILITY_DISCOVERY_REPORT.md` §0.2/§5；
  `P2-1_INTEGRATION_READINESS_REPORT.md` §3；`P2-2_SPEC_v2.md` §21。

## D-P3-002 — Event Contract additive evolution 批准（冻结面 IS/IS NOT；L0 Q2）

- **Status**: Accepted（用户 2026-10 批准 P3 Decision Closure；append-only 追加）
- **Context**: L0 确认执行事实缺口：`governance_events` 的 `event_type` 白名单仅 lifecycle
  （`events.py`），agent/tool/step 事件**全部 live-only 且不持久化**（`monitor`），live 与 durable
  两套信封不共享契约。同时 `events.py` 属 F8 冻结 observation 模块，
  `callbacks.py` 是 budget control 的**唯一 canonical producer**；
  `D-Phase2-P2-2-008` 已明文「**MUST NOT** 修改 `events.lifecycle_event` 的 payload 构造；
  结构化 payload 扩展留 P2-4 另行决策」——本决策即为该遗留项的承接。
- **Options Considered**:
  - **A（采纳）**：**additive evolution** —— 新增独立 `RuntimeEvent` 契约（词表 + 信封 + 关联键 +
    载体规则），durable 侧复用**既有单写者** `governance_events` + `(task_id, seq)` + PK 幂等 +
    replay cursor；`callbacks.py` 仅新增**观察型**回调；enforcement 与 lifecycle 语义**零改动**。
  - B：新建独立 event bus / 事件表 / sequencer → 双账本漂移，违反「三平面不混淆」与
    「不新增第二 Runtime 控制面」，拒。
  - C：仅扩展 `lifecycle_event` 的 payload → 触碰冻结 observation 模块契约，与
    `D-Phase2-P2-2-008` 冲突，拒。
  - D：维持现状（只把 live 事件搬进 UI）→ 不解决持久化，刷新/断线仍不可恢复，拒。
- **Selected Decision**: 采纳 **A**，并同时冻结以下 **IS / IS NOT**：
  - **IS（允许且要求）**：新增 `RuntimeEvent` 契约文档（P3-1 Spec 产出）；`event_type` 词表
    **additive** 扩展（task/agent/tool 级，见 `D-P3-003`）；`callbacks.py` 新增
    `on_tool_end` / `on_tool_error` 等**观察型**回调；采集点 live/durable **同源**（`event_id` 对齐，
    沿用既有 `governance_terminal` live bridge 先例）；durable 写失败**fail-open** 并暴露
    `durability_gap`（既有语义）。
  - **IS — 采集点授权（I-2 裁决；界定下方 `F15` 类约束的边界）**：**允许**在
    `app/agent/main_agent.py` 的 `run_deep_agent` astream 循环内、以及
    `app/research/orchestrator.py` **既有 `monitor.*` 调用点旁**，新增 **observation-only emit**；
    **MUST NOT** 修改控制流、返回值、异常语义（含 `governance_active` re-raise 语义）、
    工具签名、prompt、agent topology。
  - **IS NOT（禁止）**：不修改 `events.lifecycle_event` 的既有语义与 payload 构造
    （status/terminal_reason/error_kind/error/counters_snapshot/finished_at 不变）；
    不修改 `terminalize` / `finalize_with_event` / CAS / retry 阶梯 / `pending` 语义；
    不修改 terminal funnel / watchdog / BudgetCounter / 异常映射的任何行为；
    不修改 `GovernanceCallbackHandler` 的 enforcement 路径（`on_llm_start` 分类与计数、
    `on_tool_start` 双槽计数、`GovernanceLimitExceeded` 抛出语义）；
    不新增 sequencer / bus / 事件表；不复制 monitor seq 到 durable；不让 monitor 反向写 DB；
    不让新增回调参与计数或抛出异常。
- **Consequences**: 执行事实获得 durable 载体，且**零冻结语义改动**；代价是事件写放大与
  PG 短连接成本上升（受 `D-Phase2-P2-2-015` 纪律与 `D-P3-003` 粒度约束）；新增回调需以
  「计数不变」测试锁定，防止观察路径影响 enforcement。
- **Frozen Constraints**: F8 Controller 仍是**唯一** lifecycle/budget/deadline/cancel/terminal 权威；
  `TaskRecord` = terminal truth，event = observation（可延迟/可缺失，**绝不驱动状态**）；
  `(task_id, seq)` 全序 + `UNIQUE(task_id, seq)` + PK 幂等 + task-scoped replay cursor 语义不变；
  checkpoint / governance / research 三平面不共表、不共迁移、不共事务；`monitor` 不持久化、
  `seq` 不进入 durable 契约。
- **Future Implementation Constraints**: P3-1 MUST 产出独立 Spec + Readiness Review + 本契约的
  Plan Review（L3）；`event_type` 词表定稿属 P3-1 Spec 范围，新增类型 MUST 逐条显式登记
  （禁止由 `f"task_{status}"` 式 fallback 隐式产生新类型）；所有新增事件 MUST fail-open、
  MUST NOT 进入 control path、payload MUST NOT 含凭据 / prompt 正文 / 工具参数与结果正文
  （继承 `D-Phase2-P2-2-008`）；`D-Phase2-P2-2-009`（跨进程重复 terminal event = 已知限制）
  同样适用于新增事件的「恰好一次」表述，Spec MUST 显式声明该限制，不得宣称全局 exactly-once。
  **`D-Phase2-P2-2-008` 遗留项拆分（I-1 裁决）**：该条「结构化 payload 扩展留 P2-4」在 P3 内拆分为
  **契约/词表 → P3-1**、**展示 → P3-3**；`task_stale_detected` / `task_reclaimed` /
  `event_durability_gap` 等结构化类型**是否加入由 P3-1 Spec 裁决**；无论结论如何，
  **`error` 字符串载体保持不变**，**脱敏约束保持不变**（不得含凭据 / prompt 正文）。
  **migration 边界（I-6 裁决）**：P3 **默认 zero migration**；若 P3-2/P3-3 确需 additive index，
  MUST ①在对应 Spec 中逐条列出 ②经**新 Decision** 批准 ③新增 `0004_*.{sqlite,postgres}.sql`；
  **禁止修改既有 `0001–0003`**。
- **Related Problems**: 无新增
- **Related Architecture**: `P3_RUNTIME_OBSERVABILITY_DISCOVERY_REPORT.md` §4.2/§5.3；
  `events.py`（观察面单写者）；`callbacks.py`（F8 Step3 唯一 producer）；
  `D-Phase2-P2-2-008` / `-009` / `-015`。
  **Architecture Change Gate（I-4 裁决）**：本决策即 `ARCHITECTURE.md` **§8** 对「**扩展 event 类型 /
  WS 帧类型**（公共 API）」所要求的 **Decision 记录**；P3-1 Spec MUST 显式引用 `ARCHITECTURE.md`
  **§6 / §8 / §9**，并声明本次扩展为**非静默**扩展（§9 红线：MUST NOT 静默修改 WS 事件 schema /
  monitor event 类型）。

## D-P3-003 — 事件粒度分级 + 「禁止演变为 tracing backend」（L0 Q3）

- **Status**: Accepted（用户 2026-10 批准 P3 Decision Closure；append-only 追加）
- **Context**: durable 事件粒度直接决定写放大（PG 短连接、事件循环内同步写，见
  `D-Phase2-P2-2-015`）与诊断价值；同时「Runtime Event」存在边界滑坡风险：一旦开始记录
  prompt/正文/token/逐 chunk，就会与 LangSmith 平面职责重叠，违反用户「不构建 LangSmith 替代品」的目标。
- **Options Considered**:
  - **A（采纳）**：**分级 + 默认 tool 级** —— durable 默认承载 task / agent / tool 级事件；
    **step 级**（决策回合、计数器快照）**可配置且默认关闭**，启用时受采样/节流约束；并显式
    禁止 Runtime Event 演变为 tracing backend。
  - B：全量 step / LLM 级入 durable → 写放大与成本失控，且与 LangSmith 职责重叠，拒。
  - C：仅 task 级 durable（现状 + agent/tool 仅 live）→ 不满足「知道正在做什么」，拒。
  - D：把所有 live 事件原样落库 → 事件体积与噪声不可控，且 `result` 等字段含正文本体，拒。
- **Selected Decision**: 采纳 **A**。
  - **默认级别（I-3 裁决）**：**默认 `tool` 为冻结默认级别**（值域 `lifecycle | agent | tool | step`；
    `lifecycle` = 仅现状）；**仅** env 变量命名 / 值域校验 / payload 上限 / 采样率细节 由
    P3-1 Spec 定稿；**改变默认级别需要新的 Decision**。
  - **step 级**：默认 off；启用需 env 显式 + 采样率配置；采样必须**确定性可复现**（按
    `(task_id, step_index)` 稳定哈希，沿用 heartbeat 相位先例，避免随机不可复现）。
  - **禁止（IS NOT）**：不记录 prompt 正文、模型输出正文、工具参数/结果正文、token/成本、
    逐 chunk delta、消息全量 messages；不做 span 树 / 父子 trace 图 / 采样引擎 / 查询 UI /
    保留策略引擎 —— 上述均为 LangSmith 平面职责（P3-5 关联，见 `D-P3-005`）。
- **Consequences**: 默认配置下事件量可控（量级 ≈ 工具调用次数 × 常数），足以回答「现在在做什么」；
  代价是 step 级细粒度诊断需显式开启并承担成本；`event` 与 LLM 平面职责边界清晰，避免重复投资。
- **Frozen Constraints**: 事件写 MUST 为单语句短事务并受节流/抖动约束（`D-Phase2-P2-2-015` 纪律）；
  MUST NOT 引入连接池 / 批量 writer / 新依赖（除 `D-P3-006` 单独批准外）；
  payload MUST 有长度上限（沿用 `error` ≤800 字符式 clip 规则，具体值由 P3-1 Spec 定稿）；
  观测失败 MUST fail-open 且不得降低 hard budget 的 enforcement 强度。
  **env 变量义务（I-5 裁决）**：新增 env variable MUST ①更新 `.env.example` ②填写默认值
  ③填写含义注释 ④在 Spec 配置表登记（`ARCHITECTURE.md` §9 红线：MUST NOT 新增环境变量而不更新
  `.env.example`）。
- **Future Implementation Constraints**: P3-1 MUST 定稿级别默认值、`event_type` 词表、payload schema
  与上限、采样语义；上线前 MUST 提供 PG 写成本实测证据（在途任务数 × 事件率 对 loop 抖动的影响）；
  若未来需要更细粒度 tracing，MUST 走 LangSmith 平面或新 Decision，**不得**在 Runtime Event 内扩展。
- **Related Problems**: 无新增
- **Related Architecture**: `P3_RUNTIME_OBSERVABILITY_DISCOVERY_REPORT.md` §4.2/§4.6；
  `heartbeat.py`（确定性相位先例）；`D-Phase2-P2-2-015`。
  本决策同属 `ARCHITECTURE.md` §8 Architecture Change Gate 范围（事件类型扩展）——Gate 记录与
  非静默声明见 `D-P3-002` 的同一裁决（I-4），此处不重复表述。

## D-P3-004 — Admin API 安全边界：默认关闭 / 仅本地 / 只读 / 字段最小化（L0 Q4）

- **Status**: Accepted（用户 2026-10 批准 P3 Decision Closure；append-only 追加）
- **Context**: 本系统**无认证/授权边界**（`P005`，状态 Won't Fix，教学边界；CORS `allow_origins=["*"]`）。
  新增管理侧 runtime 端点会**扩大信息暴露面**（可读取他人任务的 query/error/执行状态）。L0 审计
  （G22）已标记该风险并要求 fail-closed 默认。安全边界优先于便利（AGENTS §6）。
- **Options Considered**:
  - **A（采纳）**：默认**关闭**；仅 localhost 或 env 显式开启；**只读**；返回体字段最小化。
  - B：与既有端点同姿态直接开放 → 在无鉴权前提下暴露全量运行态，拒。
  - C：引入鉴权体系后再做 → 与 `D003`（教学边界：未经明确要求不引入认证）冲突，且阻塞 P3，拒
    （若用户要求鉴权，应作为独立 Decision）。
  - D：不做 API（只做日志）→ 不满足「管理员都能看到」的目标，拒。
- **Selected Decision**: 采纳 **A**，具体冻结如下：
  - **默认关闭**：`RUNTIME_ADMIN_API=disabled`（缺省值）；未显式开启时端点**不存在或返回 404/403**
    （由 P3-3 Spec 定稿，禁止「开启判定含糊」）。
  - **仅本地**：仅接受回环来源（`127.0.0.1`/`::1`）；非本地请求 **fail-closed 拒绝（403）**；
    不接受任何用户输入驱动的状态变更。
  - **只读**：全部端点为 `GET`；**不提供**取消/终止/重试/改配置等任何写或控制能力
    （取消仍走既有 `cancel` 端点，属控制面，不搬入 Console）。
  - **字段最小化（MUST NOT 返回）**：prompt 正文、模型输出正文、工具参数/结果正文、token/成本、
    凭据与 API key、文件绝对路径（既有端点已暴露者除外）；`error` 字段沿用 ≤800 字符 clip 并继承
    `D-Phase2-P2-2-008`（不得含凭据/prompt 正文）；`limit` MUST 有上限、查询 MUST 有界（防放大）。
- **Consequences**: 能力可用但默认不可达，暴露面不因本 Phase 扩大；代价是本地开发/教学演示需显式
  开启（多一步配置），且管理 Console 在默认配置下不可用（须按文档开启）。
- **Frozen Constraints**: `P005`（无鉴权）**不因本决策改变**；CORS 既有配置不得改动（未获批准）；
  管理端点不得成为任务执行的依赖（关闭时执行链路零影响）；不得因新增端点弱化既有安全不变量
  （路径校验 / SQL 只读校验 / 上传白名单等）。
  **env 变量义务（I-5 裁决）**：`RUNTIME_ADMIN_API` 等新增 env variable MUST 更新 `.env.example`
  （含默认值与含义注释）并在 P3-3 Spec 配置表登记（`ARCHITECTURE.md` §9 红线）。
- **Future Implementation Constraints**: P3-3 MUST 按 TESTING.md §5 Security Test Matrix 为**每个**端点
  提供负路径测试（未开启时不可达 / 非本地拒绝 / 字段最小化断言 / limit 越界拒绝）；
  任何新增写操作或控制能力 MUST 新开 Decision；若未来引入鉴权，属独立 Decision，与本决策叠加而非替代。
- **Related Problems**: 关联既有 `P005`（不改其状态）；无新增登记
- **Related Architecture**: `P3_RUNTIME_OBSERVABILITY_DISCOVERY_REPORT.md` §4.4/§3.4 G22；
  `D003`；TESTING.md §5；`D-Phase2-P2-2-008`。
  **Architecture Change Gate（I-4 裁决）**：本决策即 `ARCHITECTURE.md` **§8** 对「**新增 HTTP
  endpoint**（公共 API）」所要求的 **Decision 记录**；P3-3 Spec MUST 引用 `ARCHITECTURE.md`
  §6 / §8 / §9 并声明**非静默**扩展（Gate 记录与非静默声明的完整表述见 `D-P3-002`，此处不重复）。

## D-P3-005 — LangSmith correlation 沿用 `run_id`；不新增 `trace_id`（L0 Q5）

- **Status**: Accepted（用户 2026-10 批准 P3 Decision Closure；append-only 追加）
- **Context**: L0 确认全仓库无 `trace_id`/LangSmith 引用；而 F1 Run Identity Contract 已使
  `TaskRecord.run_id` == `GovernanceExecution.run_id` == `run_deep_agent` ctx `run_id` ==
  `ResearchRun.run_id` == monitor 信封 `run_id`（**同一值跨三平面**）。关联键选择直接决定是否
  引入第二套业务身份。
- **Options Considered**:
  - **A（采纳）**：沿用既有 `run_id` 作为 Runtime ↔ LangSmith 的关联键；**不新增 `trace_id`**。
  - B：新增独立 `trace_id` → 引入第二业务身份，需在 task/event/research 多处传播，违反身份纪律，
    且 `run_id` 已满足需求，拒。
  - C：用 `thread_id` 关联 → 粒度不足（同一 thread 多次 run），拒。
  - D：不做关联（两个平面各自独立）→ 无法从 Runtime Console 跳转到对应 LLM trace，拒。
- **Selected Decision**: 采纳 **A**。LangSmith 自身的 run/trace 标识仅**记录在 LLM trace 平面内**，
  Console 只展示**引用/链接**；不得将其写入 `governance_tasks` / `governance_events` /
  `research_*` 的任何业务键位置。
- **Consequences**: 零新身份、零 schema 影响；代价是关联粒度 = run（无法在同一 run 内区分单次 LLM
  调用）——该粒度由 LangSmith 平面自身承载，不属 Runtime 平面职责。
- **Frozen Constraints**: 三平面身份模型不变；`run_id` 的生成与绑定语义（F1：governed 前绑定、
  仅 `running` 且 `run_id IS NULL` 才回填、已有值严禁重生成）**不得修改**；
  `monitor.event_id` / monitor `seq` / `governance_seq` 各自语义不得为关联目的改写或互相复制。
- **Future Implementation Constraints**: P3-5 若确需发布独立 trace 身份，MUST 新开 Decision
  （本决策**不预留**静默扩展路径）；Console 展示 trace 链接 MUST NOT 成为任务可用性依赖；
  P3-1…P3-4 MUST NOT 引入任何 LangSmith 专用字段（保持单侧依赖，见 `D-P3-006`）。
- **Related Problems**: 无新增
- **Related Architecture**: `P3_RUNTIME_OBSERVABILITY_DISCOVERY_REPORT.md` §1.3/§4.5；
  F1 Run Identity Contract；`monitor.py`（信封字段语义）。

## D-P3-006 — LangSmith integration 作为独立 P3-5；依赖与数据外发需单独裁决（L0 Q6）

- **Status**: Accepted（用户 2026-10 批准 P3 Decision Closure；append-only 追加）
- **Context**: LangSmith 接入涉及三件超出「Runtime 可观测」本体的事：①**新增依赖**（AGENTS §4：
  不得升级/新增与任务无关的依赖，需显式批准）；②**数据外发**（prompt/内容离开本机，属隐私/合规裁决）；
  ③**成本**（长任务 trace 量与保留策略）。因此不能与 P3-1…P3-4 同批打包。
- **Options Considered**:
  - **A（采纳）**：独立批次 **P3-5**，其启动前置 = 依赖 Decision + 数据外发 Decision + 成本/采样
    上限裁决；默认关闭。
  - B：并入 P3-1（把 trace 注入塞进事件契约）→ 混淆两个平面职责且让 runtime 契约依赖外部 SaaS，拒。
  - C：永久不做 → 与用户明确的「LLM trace/Prompt/Token/Cost/Eval 归 LangSmith」目标冲突，拒。
- **Selected Decision**: 采纳 **A**。约束：
  - **默认关闭**：未显式配置时零外发、零行为变化（env 显式开启）。
  - **fail-open**：LangSmith 不可达/未配置 MUST NOT 影响任务执行；启用状态 MUST 可观测
    （进 diagnostics，避免「以为有 trace 其实没有」）。
  - **不进 control path**：不计入 BudgetCounter、不作为 DurableEvent 的必需项、不写业务表。
  - **不做替代品**：Runtime Console 不展示/不代理 token、cost、prompt、evaluation；仅提供跳转引用。
- **Consequences**: P3-1…P3-4 可独立交付且不背负 SaaS 依赖；代价是「Console 一站式查看 LLM 成本」
  不可得（设计意图如此：由 LangSmith 承担）。
- **Frozen Constraints**: LangSmith MUST NOT 成为任务执行的可用性依赖；MUST NOT 修改 budget/timeout/
  cancellation 语义；MUST NOT 改工具签名或 agent 业务逻辑；凭据仅从 `.env` 读取、禁止入库；
  依赖变更 MUST 经用户批准并经 Architecture Change Gate（ARCHITECTURE.md §8）。
- **Future Implementation Constraints**: P3-5 立项时 MUST 产出：Readiness + 依赖 Decision + 数据外发
  Decision + 采样/成本上限 + 回滚方案（关闭开关即回到零外发）；P3-5 若取消，P3-1…P3-4 MUST 仍完整可用
  （单向依赖，禁止反向耦合）。
- **Related Problems**: 无新增
- **Related Architecture**: `P3_RUNTIME_OBSERVABILITY_DISCOVERY_REPORT.md` §4.5；
  AGENTS §4；ARCHITECTURE.md §8；`D-P3-005`。

## D-P3-007 — Conversation transcript 不纳入 P3；执行可观测与对话历史解耦（L0 Q7）

- **Status**: Accepted（用户 2026-10 批准 P3 Decision Closure；append-only 追加）
- **Context**: 旧 P2-3 把「durable 事件时间线」与「conversation transcript + 刷新恢复」打包。
  L0 确认二者性质不同：前者是 **execution observability**（任务在做什么，数据源 = governance/
  health/runtime event）；后者是 **conversation history**（我们聊过什么，需要新表 +
  role/query/result/files 语义 + 与 checkpoint 历史的关系裁决 + 前端对话渲染改造）。
  打包会导致 P3 范围膨胀并引入第二套「对话真相」。
- **Options Considered**:
  - **A（采纳）**：transcript **不纳入 P3**；保持解耦，留作独立批次（pending）。
  - B：纳入 P3-4 一并实现 → 需新增表 + 对话语义裁决 + 与 checkpoint 关系定义，超出「Runtime
    Observability」边界，拒。
  - C：把 transcript 提前独立做 → 与本 Phase 无依赖关系，可另行立项，但不属本决策范围（不阻止
    用户未来单独启动）。
- **Selected Decision**: 采纳 **A**。P3 只回答「任务在做什么、是否卡住、为何失败」；
  「对话历史/transcript」的持久化与重建**明确不在 P3 任何批次内**。
- **Consequences**: P3 边界清晰、无需新表、可与 F8/F9 冻结面完全解耦；代价是「刷新后恢复完整对话
  内容」仍不可得（仅恢复**执行态**与 lifecycle/tool 时间线），属**已声明限制**，需在 P3 文档与
  UI 文案中显式说明，不得让用户误以为对话历史已持久化。
- **Frozen Constraints**: P3 任何批次 MUST NOT 新增 conversation/transcript 相关表或列；
  MUST NOT 实现「完整对话历史重建」；P3-4 的恢复能力**仅限执行态**（阶段 / 当前 agent /
  当前 tool / 时间线 / 终态原因）。
- **Future Implementation Constraints**: transcript 若立项 MUST 独立 Spec + 独立 Decision
  （含表设计、与 checkpoint `messages` 的关系、隐私与保留策略），编号由用户裁决；
  P3-4 设计 MUST 保留「对话渲染层」与「执行态层」分离的接缝，避免未来返工。
- **Related Problems**: 无新增
- **Related Architecture**: `P3_RUNTIME_OBSERVABILITY_DISCOVERY_REPORT.md` §5（P3-4 Out 范围）；
  `P2-1_INTEGRATION_READINESS_REPORT.md` §3（P2-3 定义）；`app/session/**`（会话容器语义）。

## D-P3-008 — Decision Closure 后同步更新 `PROJECT_CONTEXT.md`（含状态漂移修正；L0 Q8）

- **Status**: Accepted（用户 2026-10 批准 P3 Decision Closure；append-only 追加）
- **Context**: `PROJECT_CONTEXT.md`（current-state index）当前仍停留在「F9-P0 COMPLETE →
  Documentation/Presentation 阶段；禁止修改 F9 runtime」，而实际 `main` 已含 P2-1（`dd7af9a`）、
  semantic module layout（`a0585ac`）、multi-session（`2dca2ee`）、P2-2（`884a681`）——
  **索引与实际状态不一致**（L0 报告 §0.2 C3）。同时 P3 Decision Closure 引入新的阶段状态与 Next Action。
- **Options Considered**:
  - **A（采纳）**：Decision Closure 获批后，**同一文档提交批次**同步更新 `PROJECT_CONTEXT.md`
    （阶段/Next Action/基线/已知限制），并一并修正上述状态漂移。
  - B：推迟到 P3-1 实施后再更新 → 期间索引继续误导后续 Agent（已有先例：本条漂移本身），拒。
  - C：不更新 → 违反 `PROJECT_CONTEXT.md` §12 维护规则，拒。
- **Selected Decision**: 采纳 **A**。更新范围（附录 A 给出拟改文本）：当前阶段 = P3 Runtime
  Observability（**Decision Closed / 未实现**）；Next Action = P3-1 L3 Spec；§8 Known Limitations
  更新（transcript 不在 P3、事件粒度默认值、Admin API 默认关闭、LangSmith 未接入等）；
  §9 基线表补 P2-1/P2-2 与 P3 Decision Closure 行；§4/§5 指针补 P3 决策。
- **Consequences**: 索引恢复可信，后续 Agent 不再基于过期状态决策；代价是一次文档改动需与
  DECISION.md 落地同批提交（Commit Gate 需覆盖两个文件）。
- **Frozen Constraints**: `PROJECT_CONTEXT.md` 仍是 **current-state index，不是规范来源**，不得承载
  规范（不复制 Decision/Spec 正文）；与权威文档冲突时以权威文档为准并报告冲突（AGENTS §1）；
  历史 `P2-*` 文档与既有 Spec 一字节不改。
- **Future Implementation Constraints**: 每批 P3-N 完成/Freeze 时 MUST 按 §12 更新本文件
  （状态、基线、Next Action）；Decision Closure 的落地 MUST 与 `PROJECT_CONTEXT.md` 同步，
  禁止只改 DECISION.md 而留索引漂移；每次更新 MUST 同步 Last updated。
- **Related Problems**: 无新增
- **Related Architecture**: `PROJECT_CONTEXT.md` §1/§8/§9/§11/§12；AGENTS §3（Document Loading Policy）。

<!-- PATCH-END -->

---

## 3. HARNESS REVIEW 清单（TESTING.md §9；落地时已逐项执行）

```text
[x] 每个文档只有一个主要职责             —— DECISION.md 仍只记录决策与理由，未承载流程/验证规则
[x] 无重大规则重复                        —— 与 AGENTS/PROCESS/TESTING 无重复条款；8 条 D-P3-* 之间无重叠
[x] 无互相冲突规则                        —— 与 D001–D006、D-Phase2-P2-1-001、D-Phase2-P2-2-001…017 逐条比对：
                                              无覆盖、无矛盾；对 -008 / -009 / -015 为「承接」而非「改写」
[x] PROCESS.md 与 AGENTS.md 一致          —— 本批不修改二者
[x] TESTING.md 与 PROCESS.md 一致         —— 本批不修改二者
[x] PROBLEM.md 是索引而非详细日志          —— 本批未改 PROBLEM.md；未新增 Problem 明细（N3：暂不登记）
[x] docs/problem/ 承载详细记录             —— 本批未涉及 docs/problem/
[x] DECISION.md 记录理由而非流水账         —— 每条含 Context/Options/Rejected/Consequences/Constraints
[x] ARCHITECTURE.md 反映真实仓库           —— 本批未改 ARCHITECTURE.md；已将其 §8 Gate / §9 红线作为约束引用
                                              （I-4；ARCHITECTURE.md:115-116 的重复条目留待 N2 类独立维护）
[x] 完成标准可客观验证                     —— 每条 Frozen / Future Constraints 均为 MUST/MUST NOT 可核验表述
[x] 自审可以拒绝完成                      —— §1 检查清单含「可拒绝」项；Review 阶段已产出 7 项 ISSUE 并要求裁决
[x] 安全敏感改动有更强的验证规则            —— D-P3-004 要求每个端点负路径测试（TESTING §5）
```

> 结论：**PASS**（本批仅追加 `DECISION.md` 内容 + 同步 `PROJECT_CONTEXT.md`，不改任何代码/测试/
> 冻结契约；7 项 Review ISSUE 已由用户裁决并落入追加块）。

---

## 4. 字段与既有条目格式的对照（避免「格式漂移」误判）

| 本提案字段 | 既有条目字段（D-Phase2-* 等） | 说明 |
|---|---|---|
| Context | Context | 同义 |
| Options Considered | Alternatives | 同义；本提案保留「采纳/拒绝」标记 |
| Rejected Options | Rejected Alternatives | 同义；部分既有条目未单列，本提案统一单列 |
| Selected Decision | Decision | 同义 |
| Consequences | Consequences | 同义 |
| Frozen Constraints | Constraints Created（拆分） | 拆为「冻结（不可改动）」与「后续实施（必须怎么做）」两类 |
| Future Implementation Constraints | Constraints Created（拆分） | 同上 |
| Status / Related Problems / Related Architecture | 同名字段 | 保持一致 |

**理由**：用户本轮显式要求六个必备字段（Decision ID / Context / Options considered / Selected
decision / Consequences / Frozen constraints / Future implementation constraints）；
本提案通过**中英并列标签 + 字段拆分**同时满足用户要求与仓库既有可读性，不引入第二套格式语义。

---

## 5. 逐条裁决栏（用户已批准）

```text
[x] D-P3-001  P3 编号体系与 P2-3/P2-4 映射
[x] D-P3-002  Event Contract additive evolution（冻结面 IS / IS NOT）
[x] D-P3-003  事件粒度分级 + 禁止演变为 tracing backend（默认 tool = 冻结默认级别）
[x] D-P3-004  Admin API 安全边界（默认关闭 / 仅本地 / 只读 / 字段最小化）
[x] D-P3-005  LangSmith correlation = run_id（不新增 trace_id）
[x] D-P3-006  LangSmith integration 独立 P3-5（依赖 + 数据外发单独裁决）
[x] D-P3-007  Conversation transcript 不纳入 P3
[x] D-P3-008  Decision Closure 后同步更新 PROJECT_CONTEXT.md
[x] I-1 … I-7  Review 修正项（已并入上列条目，见各条「I-n 裁决」标注）
[x] N1-a / N1-b 落地路径与文件白名单裁决
```

---

## 6. 落地路径（已裁决：A）

| 方案 | 内容 | 评价 |
|---|---|---|
| **A（已采纳）** | 用户批准后在 `main` 直接提交**纯文档**改动（`DECISION.md` 追加 + `PROJECT_CONTEXT.md` 同步，同批），提交前执行 §3 HARNESS REVIEW 与 AGENTS §10.6 Commit Gate | 符合 AGENTS §10.2（Decision/Readiness 产物可留 main）与 §10.7（Harness 治理文件低风险文档维护）；P3-1 实施仍另开 feature branch |
| B | 暂不落地，等 P3-1 feature branch 建立后随实现提交一并落地 | 未采纳（Decision 生效延迟、索引漂移继续存在） |
| C | 拆分：`DECISION.md` 走 A，`PROJECT_CONTEXT` 延后 | 未采纳（违反 `D-P3-008`） |

**N1-a / N1-b 裁决（用户 2026-10）**：

- 本批为 **docs-only commit**；**append-only 新增冻结约束不视为「frozen contract 修改」**（AGENTS §10.7 前置条件）；
- 使用**精确文件白名单 `git add`**；**禁止** `git add .` / `git add -A` / `stash` / `reset` / `discard`；
- 落地 commit 文件白名单 = `DECISION.md`、`PROJECT_CONTEXT.md`、`P3_DECISION_CLOSURE_REPORT.md`、
  `P3_DECISION_UPDATE_PROPOSAL.md`、`P3_RUNTIME_OBSERVABILITY_DISCOVERY_REPORT.md`；
- **排除**（归属未确认/非本任务）：`P2-1_INTEGRATION_READINESS_REPORT.md`、`P2-1_MERGE_REPORT.md`、
  `P2-2_RELEASE_GATE_AUDIT_REPORT.md`、简历相关文件、图片文件。

---

## 7. 本批落地记录与未做的事

**已做（用户批准后）**：

- ✅ `DECISION.md`：append-only 追加 `D-P3-001 … D-P3-008`（298 行，既有 881 行零修改）；
- ✅ `PROJECT_CONTEXT.md`：阶段/Next Action/基线/已知限制同步（含状态漂移修正）；
- ✅ 本批按 N1-a 白名单精确 `git add`（未使用 `git add .` / `-A`）。

**未做**：

- ❌ 未修改任何代码；未触碰 `events.py` / `callbacks.py` / `controller.py` / `server.py`
- ❌ 未修改数据库、未创建 migration、未新增依赖
- ❌ 未创建 feature branch、未 push / merge（未执行 P3-1 实现，未编写 P3-1 Spec）
- ❌ 未登记 Problem Candidate（N3：暂不登记）；未修改 `PROBLEM.md` / `ARCHITECTURE.md`
