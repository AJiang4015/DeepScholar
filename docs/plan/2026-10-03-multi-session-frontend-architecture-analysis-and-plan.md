# Multi-Session Frontend Adaptation — Architecture Analysis & Implementation Plan（架构分析与实施计划）

> 阶段：**Phase 1（Architecture / Gap / Design-System Integration / Implementation Plan）——只读分析，禁止 Coding。**
> 日期：2026-10-03（仓库 in-world 时间线，与 `feature/multi-session-backend` 交付文档同日；机器时钟仅供参考）
> Branch：`main` @ `5a60f04`（Merge branch 'feature/multi-session-backend'）｜ 本地 = `origin/main`
> Git 状态：working tree 仅含用户个人文件改动（`简历项目经历-深度研搜.md` modified、`简历-AI应用开发-Agent方向.html` untracked）——**未触碰、未 add**；本阶段不 Commit / Push / Merge。
> 分类：**L0（Read-only / Analysis）**——产出本分析文档，不产生 Implementation Plan 之外的代码改动。
> 结论见文末 `§Verdict`。

---

## 0. Executive Summary

Backend Multi-Session Contract 已冻结（`MULTI_SESSION_API_SPEC.md`，8 个 additive 端点，`session_id == thread_id`
1:1 身份，ACTIVE/ARCHIVED 两态，结构隔离 + 归档守卫，零 Breaking Change）。本阶段任务是：在不改 Backend、
不 Redesign Frontend、不违背既有 Frontend Design System 的前提下，把现有**单 thread / 单对话 / 内存态**前端
适配为 **Session Sidebar + 当前 Session + Session-scoped Task/WS/文件** 的 Multi-Session UI。

核心结论（全文证据见各节）：

1. **前端是"轻量单页控制台"，无 router / 无状态库 / 无测试框架**；唯一的会话身份是 localStorage 里的
   单个 `thread_id`，WS、文件轮询、任务提交/取消、错误通道全部围绕这一个 thread 展开 —— 这是最大的
   single-session 假设面（§A、§B）。
2. **Backend 已为前端铺好消费路径**（Spec §8 Consumption Map）；FE 缺口集中在：session 列表/创建/切换/归档 UI
   缺失、提交/取消未走 session-scoped 端点、无 per-session 状态隔离、刷新后无历史呈现（§C、§D）。
3. **Design System 可支持 Session UI 的"最小融入"**：Sidebar 现有 `.sidebar-section`/`.sidebar-label`/status 行词汇
   可以直接扩展；颜色只用既有 5 语义色 + alpha 配方；禁止任何新色相/字体/圆角/间距/组件库（§E–§H）。
4. **推荐状态策略 = 按 session 重挂载 workspace（`key={sessionId}`）**，复用既有 hook 的 WS 生命周期与
   reconnect/cleanup 语义，从根上隔离 A/B session 事件与状态（§J、§L）。
5. **已知硬限制（本阶段不改 Backend，如实记录）**：a) 服务端不持久化"对话消息/最终答案文本"，历史 session
   只能呈现 task 摘要级内容；b) `/api/files` 按绝对路径取值，服务端无 "按 session 列出产物" 端点，历史产物
   列表需 FE 缓存 session 输出路径或作为 Known Limitation（§V）。

> 最终状态：**READY_FOR_FRONTEND_IMPLEMENTATION**（前提见 §Verdict）。

---

# A. Current Frontend Architecture（现状架构）

### A.1 技术栈事实（证据：`frontend/package.json`）

| 项 | 现状 | 证据 |
| --- | --- | --- |
| Framework | React 19（`react@^19.0.0`） | `frontend/package.json:15` |
| Language | TypeScript 5.8+（`strict: true`，`moduleResolution: Bundler`） | `frontend/tsconfig.app.json` |
| Build | Vite 7；`pnpm build` = `tsc -b && vite build`；dev proxy `/api`、`/ws` → `localhost:8000` | `package.json:8`；`vite.config.ts` |
| UI 库 | Ant Design v5（darkAlgorithm + ConfigProvider 主题注入） | `frontend/src/main.tsx:10-38` |
| 图标 | `@ant-design/icons`（唯一图标源） | `package.json:12` |
| Markdown | `react-markdown` + `remark-gfm` | `package.json:17-18` |
| CSS | 单文件 `styles.css`（1819 行，全部设计 token 唯一定义处）+ Tailwind v4 仅 `min-h-dvh` 与 reset | `styles.css:3-18`；`App.tsx:157` |
| Router | **无**（单页，无 react-router / 任何路由依赖） | `package.json` 无 router 依赖；grep 无 useLocation |
| State 管理 | **无外部库**；React `useState` 全在 `App.tsx` + `useDeepAgentSession` | grep 无 zustand/redux/jotai |
| API client | `fetch` 封装 `requestJson<T>`（错误统一抛 `detail`） | `lib/api.ts:14-30` |
| WebSocket | 原生 `WebSocket`（hook 内管理） | `hooks/useDeepAgentSession.ts:167` |
| 持久化 | 仅 `localStorage` 单 key `deepsearch.thread_id` | `lib/thread.ts:1` |
| 测试框架 | **无**（FE 无 test script / vitest / jest / RTL） | `package.json` scripts |
| Lint/Format | FE 无独立脚本；`.pre-commit-config.yaml` 仅 Python（ruff） | `package.json`；仓库根配置 |

### A.2 激活代码结构（Primary Reference Surface）

```text
frontend/src
├── main.tsx                 # ConfigProvider 主题（token 唯一来源）+ <AntApp>
├── App.tsx                  # 单页外壳：sidebar + main；turns 状态机；toast 全集
├── styles.css               # 1819 行全局样式（:root token 3-18；chat 体系 674+）
├── types.ts                 # ConnectionState / MonitorMessage / TaskInfo / 终态集合
├── lib/
│   ├── config.ts            # API_BASE_URL / WS_BASE_URL（env 覆盖）
│   ├── api.ts               # requestJson + startTask/cancelTask/getTask/upload/files/download
│   └── thread.ts            # createThreadId/getStoredThreadId/storeThreadId（localStorage）
├── hooks/useDeepAgentSession.ts  # 单 thread WS 会话唯一实现
└── components/
    ├── ConversationThread.tsx    # 激活：turn/message/ThinkingTimeline/ArtifactShelf/ThinkingLoader/EventIcon
    ├── ChatComposer.tsx          # 激活：fixed composer + upload + send/cancel 圆钮
    ├── MarkdownRenderer.tsx      # 激活：.markdown-body 渲染
    └── 7 个遗留组件（MissionComposer/StatusStrip/AgentTopology/EventStream/UploadPanel/ResultPanel/FileDock）
        # 未被任何文件 import（全仓 grep 仅自身声明），本轮不复活
```

**引用链证据**：`App.tsx:13-18` 只 import `ChatComposer` / `ConversationThread` / `lib/config` / `useDeepAgentSession` / `types`；
`FRONTEND_CONSISTENCY_RULES.md §6` 同结论。

### A.3 数据流与状态

```text
App.tsx
 ├─ state: query / stagedItems / turns: ChatTurn[]（内存，唯一对话）
 ├─ session = useDeepAgentSession()
 │    ├─ state: threadId(源自 thread.ts) / connectionState / events[≤120] / files / sessionPath /
 │    │          result / lastError / isRunning / isCancelling / isUploading / uploadedItems / taskId / taskStatus
 │    ├─ WS: new WebSocket(`${WS_BASE_URL}/ws/${threadId}`)（heartbeat ping 25s；close→2s reconnect）
 │    ├─ onmessage: monitor_event → run_id 锚点过滤 → setEvents(+截断) → task_result/task_cancelled/error 收尾
 │    └─ reconnect 后 doReconcile(): GET /api/tasks/{task_id} 校正 durable 终态
 ├─ useEffect: hook.events/files/isRunning/result/taskStatus → 回填到最后一轮 turn（App.tsx:51-82）
 └─ handlers: handleSubmit→session.submitTask / handleCancel / handleUpload / handleNewSession(resetSession+turns=[])
```

关键行证据：
- `App.tsx:30-41` `createTurn`；`App.tsx:47` `turns` 单数组；`App.tsx:51-82` 仅镜像"最后一轮 turn"（**隐含单对话假设**）。
- `App.tsx:147-152` `handleNewSession` = `resetSession()` + `setTurns([])`（**旧上下文直接丢弃**）。
- `hooks/useDeepAgentSession.ts:53` `threadId` 初值 = `getStoredThreadId()`；`:167` WS URL；`:158-276` connect effect（依赖 `[threadId]`，切换 thread 会整体重建 socket）；`:81-102` `resetSession()` 生成**新** thread_id 并清空全部状态。
- `hooks/useDeepAgentSession.ts:311-346` `submitTask` → `POST /api/task`；`:348-369` `cancelCurrentTask` → `POST /api/task/{threadId}/cancel`。
- `hooks/useDeepAgentSession.ts:293-309` 文件轮询 2.5s(running)/6s(idle)，**依赖 `sessionPath`**（来自 live `session_created` 事件 `data.path`，即服务端绝对目录）。

### A.4 视觉骨架（Evidence）

- 双栏：`.chat-app-shell { grid-template-columns: 292px minmax(0,1fr) }`（`styles.css:674-684`）。
- Sidebar `.chat-sidebar`：sticky 全高、`rgba(7,11,18,.9)`+blur、右 1px `--line`、padding 20px、flex column gap 16（`styles.css:696-708`）。
- 内容列：topbar/alert/stream/composer 全部 `max-width:900px; margin:0 auto`（`styles.css:851-901, 1609-1619`）。
- 断点：980px（sidebar `display:none`，主区全宽；`styles.css:1731-1758`）/ 560px（`1760-1818`）。
- 滚动只在 `.chat-stream-panel` 内部；`scroll-padding-bottom:210px`（166px @560）给 fixed composer 让位。

---

# B. Current Single-Session Assumptions（现状假设盘点）

以 `thread_id / session / task_id / current task / WebSocket / report / research` 检索后的结论（每条给证据）：

| # | 假设 | 现状证据 | 对 Multi-Session 的影响 |
| --- | --- | --- | --- |
| S1 | **一个用户 = 一个 thread** | `thread.ts` 单 key `deepsearch.thread_id`，首次访问自动生成并永存（`thread.ts:11-20`） | 需升级为"当前 session id"，并可从 `GET /api/sessions` 恢复 |
| S2 | **thread 无需在服务端注册即可使用** | FE 从不调 `POST /api/sessions`；`POST /api/task` 由服务端换新/接受任意合法 thread | 旧 thread 不会出现在 Session Sidebar → 需 boot 注册/协调策略 |
| S3 | **一个 thread = 一次对话** | `turns: ChatTurn[]` 单数组；hook 状态全局单份 | 需 per-session turns 缓存（至少内存级） |
| S4 | **hook 状态是"当前 run"全局镜像** | events/files/result/isRunning/taskId/taskStatus 单份；App 只回填最后一轮 turn | 切换 session 必须整体替换，否则 A 事件污染 B |
| S5 | **单 WebSocket 绑定单 thread** | `/ws/${threadId}`；connect effect 依赖 `[threadId]` | 天然支持切换（换 id 重建 socket），但状态需随挂载重置 |
| S6 | **"新建" = 丢弃旧上下文** | `handleNewSession` = resetSession + turns=[]（`App.tsx:147-152`） | 需改为"新建 Session（POST /api/sessions）+ 切换" |
| S7 | **消息/最终答案只存在于内存** | `result` 来自 live `task_result.data.result`；服务端不持久化对话文本（`BACKEND_FRONTEND_ADAPTATION_REPORT.md §8`） | 刷新/切回历史 session 无法还原消息文本 → 只能给 task 摘要 |
| S8 | **session 输出路径 = live 事件告知** | `sessionPath` 仅由 `session_created.data.path` 填充（`useDeepAgentSession.ts:213-218`），刷新即丢 | 历史产物列表需要路径缓存或接受限制 |
| S9 | **任务身份 = 单一 taskId 引用** | hook 只记"当前 run"的 taskId/runId（`:44-50`），刷新即丢 | 重挂载后需从 session detail / `latest_task` 恢复 |
| S10 | **错误/反馈是单通道** | `lastError`（顶部 Alert）+ `message.*`（动作 toast）全局 | 切换 session 时需清空/重建，避免旧 session 错误残留 |
| S11 | **上传/附件随全局 thread** | `uploadedItems` hook 全局；`POST /api/upload` 带 `threadId`（`lib/api.ts:57-69`） | 应随当前 session（thread_id=session_id）作用域化 |
| S12 | **不消费任何 task 列表 / history API** | FE 只用 `getTask`（单条 reconcile）；从未调 `/api/tasks`、`/api/sessions*`、WS since_seq replay | 历史呈现需要新增对 session/task 列表的消费 |

**隐含关系一句话**：`one user → one thread → one current task → one global research state → one global WebSocket`，
且 `thread_id` 只在浏览器 localStorage 存在、服务端无容器（Session）记录 —— 这就是要适配的核心。

---

# C. Backend Contract Summary（冻结契约摘要）

> 唯一事实来源：仓库根 `MULTI_SESSION_API_SPEC.md`（文档与 `app/api/server.py` + `app/session/*` 一致）。
> 已逐端点核对代码（server.py 行号见下）。

### C.1 Identity

```text
Session.session_id == TaskRecord.thread_id == ResearchRun.thread_id == WS thread_id == checkpoint thread_id
```
- 1:1；`TaskRecord.parent_session` NULL/unused（**不可复用**）；字符集 `[A-Za-z0-9_-]{1,128}`（P004）。
- 归属：Task 属于 Session X ⇔ `task.thread_id == X.session_id`。

### C.2 Session 状态机（两态）

```text
ACTIVE ──archive(DELETE)──▶ ARCHIVED ──unarchive(PATCH {status:"active"})──▶ ACTIVE
```
| 操作 | ACTIVE | ARCHIVED |
| --- | --- | --- |
| 创建 Task | ✅ | ❌ 409 |
| 读 Detail / Task 列表 | ✅ | ✅ |
| Archive（DELETE） | ✅ | ✅ 幂等 `already_archived` |
| Unarchive（PATCH active） | ✅ 幂等 | ✅ |
| 有 running Task 时 Archive | ❌ 409 | — |
| 物理删除 | ❌（永不） | ❌ |

### C.3 端点（代码核对：`app/api/server.py`）

| 端点 | server.py | 备注 |
| --- | --- | --- |
| `POST /api/sessions`（title?/description?/thread_id?） | :364 | thread_id 缺省服务端 uuid；提供则注册既有 thread；重复 409 |
| `GET /api/sessions?include_archived=&limit=&offset=` | :384 | 默认只 active；排序 `updated_at DESC`；返回 `sessions[]+total`，每项含聚合 `task_count/running_tasks/latest_task` |
| `GET /api/sessions/{session_id}` | :403 | detail：session 元数据 + tasks[] + task_count + latest_task |
| `DELETE /api/sessions/{session_id}` | :413 | 归档（有 running → 409） |
| `PATCH /api/sessions/{session_id}` | :423 | v1 仅 `{status:"active"}` unarchive；其他 → 400 |
| `POST /api/sessions/{session_id}/tasks` | :438 | archived → 409；submit 前二次 active 校验；同 thread 单活跃收敛（先取消旧任务）；201 `{status,thread_id,task_id,run_id}` |
| `GET /api/sessions/{session_id}/tasks` | :470 | 只返回 `thread_id==session_id` 的 task（结构隔离）；archived 可读 |
| `POST /api/sessions/{session_id}/tasks/{task_id}/cancel` | :481 | 先 `validate_task_in_session`（归属）；不存在/跨 session → 404；已终态 → `already_terminal` |
| 兼容端点（零改动） | :279/:306/:341/:349/:540/:612/:641 + WS :698 | `POST /api/task`、cancel、`/api/tasks`、`/api/tasks/{id}`、upload、download、files、`WS /ws/{thread_id}`（握手可选 `task_id/since_seq`） |

### C.4 错误模型与隔离保证

- 错误 = `HTTPException + {"detail": <message>}`；语义 404（不存在/跨 session）/ 409（archived 建任务 / running 归档）/ 400 / 503。
- 隔离（Frontend 可依赖）：A 的 list/detail/tasks/cancel 只操作 A；跨 Session cancel/读取 → 404 且不泄露；不同 Session 可同时 running。
- WS schema / monitor 事件 / 心跳协议**全部不变**；`POST /api/upload`、`/api/files`、`/api/download` 以 `session_id` 作为 `thread_id` 使用。

### C.5 兼容与限制

- 遗留客户端无需注册 Session 即可用 `POST /api/task`；需要进 Sidebar 的会话由前端先 `POST /api/sessions {thread_id}` 注册。
- `task.query` 为可选 enrich（research store fail-open），**可能为 null**，FE 不可依赖非空。
- Resume 无产品入口（F9 Batch9 deferred）；durable replay 只含 lifecycle 标记。

---

# D. Frontend ↔ Backend Gap Analysis（差距矩阵）

| Domain | 当前实现（证据） | Backend Contract | Gap | Required Adaptation |
| --- | --- | --- | --- | --- |
| Session list | ❌ 无任何 session 概念；只显示本地 thread 8 位短码（`App.tsx:169-174`） | `GET /api/sessions`（含聚合 running_tasks/latest_task） | 全缺：无列表、无 active/archived 区分 | 新增 Session Sidebar 区块，消费 `sessions[]`；默认 active，提供 archived 开关 |
| Create session | ❌ "新建研搜" = 本地换新 thread id（`App.tsx:147-152`；`thread.ts:3-9`） | `POST /api/sessions` | 不创建服务端容器 → 无法进 Sidebar / 无法归档 | "新建"改为 POST → 新 session 成为 current；boot 协调旧 thread 注册 |
| Get / Current session | ❌ 无 current session 概念；`threadId` 单值内存+LS | `GET /api/sessions/{id}`（detail+tasks+latest_task） | 无恢复/详情 | App 增加 `currentSessionId`；打开/切换时取 detail |
| Switch session | ❌ 无 | `session_id` 语义 | 全缺 | 切换 = 重挂载 workspace（换 sessionId），见 §J/§L |
| Archive / Restore | ❌ 无 | `DELETE`（有 running→409）/ `PATCH active` | 全缺 | Sidebar 行内操作 + 确认 + 409 反馈 |
| Task creation | ✅ 走遗留 `POST /api/task`（`useDeepAgentSession.ts:330`） | `POST /api/sessions/{id}/tasks`（archived→409、归属、同 thread 收敛） | 未走 session-scoped：archived session 仍能提交（绕过 409）；归属语义弱 | 当前有 Session 时改走 session-scoped；遗留端点仅作为兼容/迁移路径保留 |
| Task list | ❌ 仅 `getTask` 单条 reconcile（`lib/api.ts:53-55`） | `GET /api/sessions/{id}/tasks` | 无历史/无 per-session 列表 | 新 TaskHistory 呈现（task 摘要级） |
| Cancel | ✅ 遗留 `POST /api/task/{thread}/cancel`（`:356`） | `POST /api/sessions/{id}/tasks/{task_id}/cancel` | 未走归属校验 | 切换为 session-scoped cancel（携带 task_id） |
| WebSocket | ✅ `/ws/{threadId}`；run_id 锚点过滤；reconnect→durable reconcile | `WS /ws/{session_id}`（schema 不变；握手 task_id/since_seq 可选） | 单连接绑定单 thread —— 正好契合"当前 session"，但状态需随 session 重置 | 按 session 重挂载 + session 级事件/状态隔离；replay 帧维持现状（REST 校正） |
| Report / 产物 | ✅ live 文件轮询（依赖 sessionPath）；结果文本仅内存 | `/api/files?path=`（绝对路径）；无 per-session 目录端点 | 历史产物列表：绝对路径不在客户端可知范围 | FE 缓存 session 输出路径 or Known Limitation（§V） |
| Refresh 恢复 | ⚠️ 只恢复 thread_id；turns 丢、sessionPath 丢、taskId 丢 | session 元数据+task 摘要持久化 | 刷新后无法还原消息文本（服务端不存）；但可还原 session 列表与 task 摘要 | 恢复 currentSessionId + 会话列表 + task 摘要视图 |
| Isolation（A∥B） | ❌ 全局单状态，无 A/B 概念 | 服务端隔离完备（Spec §6） | FE 无 per-session 状态 | 重挂载策略从根隔离（§J） |

**结论**：Backend 无缺口（除"历史产物路径解析"与"消息文本不持久化"两项属服务端既有边界，非本阶段可改）；缺口全部在 Frontend 的"单会话假设"与"未消费的新 API"。

---

# E. Existing Frontend Design System Summary（现状 Design System 摘要）

> 权威：`docs/frontend/DESIGN_SYSTEM.md`（自代码逆向）；仅列本阶段直接相关的结论。

- **视觉 DNA**：深空底 `#05070b` + 极细 cyan/green 工程网格 + 1px 发光描边面板（8px 圆角）+ 终端 mono 标签。
- **唯一 token 源**：`styles.css:3-18`（CSS vars）+ `main.tsx:10-38`（AntD token，全部映射同一色相）。
- **语义色（禁止新增）**：cyan `#20d6ff`（主/信息/选中）、green `#5dff9f`（成功/运行/AI 侧）、amber `#ffc857`（工具/警告/文件/取消过程）、red `#ff5c7a`（错误/取消强调）、violet `#7c8cff`（仅预设芯片）、中性 `#05070b/#eef7ff/#8fa2b7`。
- **状态色配方**：图标 100% 语义色；边框 ≈ 24-38% alpha；底 ≈ 7-10% alpha；hover = 边框亮起 + 底色抬升 + `translateY(-1px)`；focus-visible 可见；禁用 `opacity:.55`。
- **字体**：Sans `IBM Plex Sans, PingFang SC, Microsoft YaHei, system-ui` 管标题/正文/按钮；Mono `JetBrains Mono, SFMono-Regular, Consolas` 只管 ID/数字/时间/事件/路径/JSON。**无字体文件加载**，禁止新增字体。
- **字号层级（px，硬编码）**：sidebar brand H1 30 / topbar H2 24 / markdown H1-4 24-16 / 面板标题 18 / kicker 11-12 / 次级 12-13。
- **间距节奏**：4/6/8/10/12/14/16/18/20/22/24 + 大间距 22/46；高度刻度 26-58（面板 padding 12-16；sidebar padding 20）。
- **Radius**：表面一律 8px（`main.tsx:22`）；inline code 6px；圆点/指示条 999px；整圆按钮 `shape="circle"`。
- **Glow 纪律**：glow 只允许 主按钮 / 运行态 / loader 三处；阴影 = 深黑投影 + 可选 cyan tint + 1px 内高光 ring。
- **动画**：只在运行/加载语义；≤2.8s；全局 `prefers-reduced-motion` 兜底（`styles.css:664-672`）。
- **组件事实**：激活页只用 AntD `Button / Alert / Tooltip / Upload / message(AntApp.useApp)`；`Empty` 仅遗留页。所有自定义视觉 = 追加全局 class（不覆盖 AntD 内部样式）。**无 Modal / Drawer / Popconfirm / Tabs / Dropdown 在激活页出现**（新增弹层类组件需评估，见 §O/V）。
- **遗留词汇表（不复活）**：`.console-panel / .metric-tile / .agent-node / .event-row / .file-list` 等（styles.css:46-616），仅作"同语言参考"，不得与激活页混排。

---

# F. DESIGN SYSTEM.md Integration（Session UI 如何融入）

> 原则：**Session Sidebar 是 Sidebar 的扩展，不是新页面**。所有新表面走"既有 class 词汇 + 追加新 class（只加不改）"。

### F.1 Sidebar 扩展（Session 列表区）

- 复用 `.chat-sidebar`（292px 双栏不变）；Session 列表作为新的 `.sidebar-section`（1px `rgba(255,255,255,.08)` 边框、8px、`rgba(255,255,255,.035)` 底、padding 12 —— `styles.css:730-735`）。
- 区块头复用 `.sidebar-label`（mono 11px/700/`--muted`）：`SESSIONS`（对应现状 `THREAD` 块的排版语言，`styles.css:737-743`）。
- 行元素建议新 class（追加进 `styles.css`，禁止改既有规则）：
  - `.session-item`：1px 中性边框 + `rgba(255,255,255,.035)` 底、8px、gap 10-12、min-height 与 `.sidebar-status` 同刻度（~44-54px）。
  - `.session-item--active`（当前 session）：cyan 边框 `rgba(32,214,255,.34)` + 底 `rgba(32,214,255,.08~.1)`（沿用选中配方）；语义"当前"= cyan（同选中态，非新颜色）。
  - `.session-item--archived`：`opacity` 或中性降级表达 + 右侧 mono `ARCHIVED`/「已归档」`--muted`（**不引入新色相**；若需强调可走 amber 系？否——归档非"警告/进行中"，建议中性 `--muted` + 短横线 label，符合"语义色不换色表达"）。
  - 行内徽标：running 数 → green 呼吸点或 mono green 计数（复用 live 语义：`run-indicator--live` 的 green 配方 `styles.css:879-883`）。
- 每行内容结构（顺序固定）：① 图标井（可选，38px 方形，cyan 底 —— 复用 `.example-icon` 配方）或直接标题；② 主文本（sans 14px `--text`，title 单行截断）；③ 次行 meta（mono 11px `--muted`：updated time `HH:mm` / 状态）。— 完全对应 `DESIGN_SYSTEM.md §1.6-1.8` 的文本层级。

### F.2 Current Session Indicator

- 保持现有 Header/Navigation 结构：`THREAD` 块升级为 "当前 Session" 信息（title + mono session_id 8 位短码 + archived/running 状态小标），同时保留 `.thread-id` 风格（`styles.css:745-751`）——即 `THREAD` → `SESSION`（或保留 `THREAD` 单词以免破坏语义？建议：kicker 改 `SESSION`，id 显示当前 session_id，下方 title 用 sans）。
- 顶部 `.chat-topbar` 文案保持（CHAT WORKSPACE / 深度研搜对话），可把 run-indicator 语义扩展到"当前 session 运行态"（已是全局运行态，天然匹配当前 session）。

### F.3 Main Research Area

- **ConversationThread / ChatComposer / MarkdownRenderer 完全不变**；只把数据源从"全局唯一 turns"变为"currentSession 的 turns（内存缓存或空）"。
- turns 为空但 session 有历史 → 渲染新 TaskHistory 摘要视图（非对话流），见 §N。

### F.4 交互与状态映射（颜色↔语义必须一致）

| UI 语义 | 用色 | 依据 |
| --- | --- | --- |
| 当前 Session 高亮 / 主操作 | cyan | 选中态/主色语义（1.4） |
| Session 有 running Task | green（badge/呼吸点） | "运行中"语义（1.5） |
| Task 终态：completed/failed/cancelled/超时/预算等 | 复用 ConversationThread `TERMINAL_STATUS_LABELS`/`endedNoticeText` 已有配色（`ConversationThread.tsx:306-347`） | 不新建文案/配色 |
| 归档（不可再建任务） | 中性降级 + 禁交互 | archived ≠ 错误/警告；语义色不换色 |
| Archive 动作（可逆但需确认） | red 强调（同取消系破坏性动作，`send-button--cancel` 红色语义 `styles.css:1674-1684`） | 2.3.5 |
| 错误（列表加载失败 / 409） | red + message.error / Alert 分工 | 2.1.5 |

---

# G. FRONTEND_CONSISTENCY_RULES.md Compliance（强制规则逐条）

> 规则文件：`docs/frontend/FRONTEND_CONSISTENCY_RULES.md`。以下为本阶段与 Implementation 阶段必须保证的清单：

**MUST NOT（§1.1-1.2）**
1. 不改任何激活页现有 class 的视觉输出（styles.css 中 674+ 被引用规则）。
2. 不引入新颜色/字体/spacing/radius/shadow/glow 体系（仅沿既有 token + alpha 派生）。
3. 不引入第二套 UI framework / 图标库（保持 AntD v5 + @ant-design/icons）。
4. 不做 AI-SaaS/landing/glassmorphism 化；不新增装饰性循环动画。
5. 不复制既有 class 样式到组件内联后改数值（公共视觉只落全局 styles.css，追加）。
6. 不在组件里裸写 hex 语义色；不绕过 ConfigProvider；不用 antd 静态 `message`（走 `AntApp.useApp()`）。
7. Tailwind 仅现有用法（`min-h-dvh` + reset），不用来堆新视觉层。

**MUST（§2）**
1. 复用：`MarkdownRenderer` / `ConversationThread` 消息结构 / `ChatComposer` fixed-composer 语义 / `useDeepAgentSession`（WS 会话唯一实现）——新 session 逻辑**必须叠加在这些抽象之上**。
2. 数据文字（session_id / 时间 / 计数）一律 Mono + `--muted`/浅青。
3. 新按钮/弹层用 AntD + 既有 class 追加外观。
4. 功能错误提示沿用：顶部 `Alert` = 会话级；`message.*` = 动作级（文案语气参照 `App.tsx:86-129`）。
5. 布局：内容列宽 900px、外层无页面滚动条、滚动只在内容容器；断点 980/560；fixed composer 让位 padding 不变。
6. 运行态三处呼应（顶栏指示 + pending 面板 + 时间线/计数）语义不变（当前 session 运行 → 三处同步）。
7. 长任务禁全局 spinner（用 ThinkingLoader 套件）；短动作 AntD `loading`。
8. 取消/归档等破坏性动作红色表达；空态文案为指引性祈使句（中文）。
9. a11y：图标按钮 `aria-label`/`Tooltip`；装饰图标 `aria-hidden`；实时区域 `aria-live`（沿用现状）。
10. Copy：中文短句（「新建研搜」「归档会话」「恢复会话」）；英文仅 mono kicker。

**SHOULD（§3）**
- 新组件命名沿用 BEM `block--modifier`；事件名/状态名直接作修饰符后缀（如 `.session-item--archived`）。
- 侧栏扩展沿用 section 容器 + mono 标签 + 图标井行结构（**不把导航做成"一等公民"**——Session 列表是 sidebar 的一个 section，不是主导航框架）。
- 折叠优先 `details/summary`（历史明细可用），避免弹窗泛滥；若确需确认弹层 → AntD Popconfirm/Drawer 需在 Decision 中立项（见 §V）。

**§6 Reuse 分级落地**
- MUST reuse：`ConfigProvider` 主题 / `MarkdownRenderer` / `useDeepAgentSession` / `ConversationThread` / `ChatComposer` / styles.css `:root`。
- SHOULD reuse：ThinkingLoader、`details/summary` 折叠 + 计数徽标、`.panel-kicker`/`.sidebar-label`、EventIcon/FileIcon 映射、`requestJson`/thread 持久化、Alert/message 分工、空态与 `example-grid`。
- MAY create：Session 专用新组件（SessionSidebar / TaskHistory），但必须遵守 0-5 节全部规则。
- 遗留 7 组件：**默认不复活**。

---

# H. UI_PATTERNS.md Reuse Analysis（交互/布局模式复用方案）

| 模式 | 出处 | Session UI 复用方式 |
| --- | --- | --- |
| 双栏 shell + 292px sidebar | `UI_PATTERNS.md §1.1`；`styles.css:674-708` | 结构不变；Session 列表作为 sidebar 新 section |
| Sidebar 信息架构（自上而下固定顺序） | §1.3 | Brand → **新建研搜**（语义改为建 Session）→ **SESSIONS section（新增）** → THREAD/状态（升级为 current session 信息）→ AGENTS → ENDPOINTS |
| 内容 900px 单列 + fixed composer | §1.2/§1.7 | 不变 |
| 运行态三处呼应 | §3.4 | 当前 session running → 沿用（无需新模式） |
| ThinkingLoader 套件 | §3.5 | 不变（长流程仍走它） |
| 成功/错误/取消/空态 | §3.6-3.8 | 错误/反馈通道沿用；新增 session 级空态/错误文案遵循"指引性祈使句" |
| hover/focus-visible | §3.1 | Session 行 hover/focus 沿用 `.preset-chip`/`.example-card` 配方（边框亮起 + 底 +4% + translateY(-1px)） |
| 滚动 | §3.9 | Session 列表超出时容器内滚动 + cyan 细滚动条（`scrollbar-color` 同款）；页面外壳无滚动条 |
| 响应式断点 | §1.8 | **980px 以下 sidebar 隐藏 → Session 切换入口问题（Gap，见 §O/V）** |
| 实时更新 | §3.10 | WS 生命周期全在 hook；按 session 重挂载即复用 reconnect/心跳/run 过滤 |
| 动画纪律 | §3.11 | 不新增入场/布局动画；reduced-motion 兜底沿用 |

---

# I. External Frontend Skills Applicability（外部 Skill 适用性分析）

> 裁决顺序（本项目内部优先，来自 `FRONTEND_CONSISTENCY_RULES.md §0`）：
> `CURRENT UI > DESIGN SYSTEM 三文档 > SKILL DEFAULTS`。以下是对 4 个 Skill 在**本阶段与实施阶段**的可用性评估。

### I.1 Taste Skill（https://github.com/leonxlnx/taste-skill）
- **可用角色**：视觉密度/层级/打磨审查（visual taste, hierarchy, density, polish, visual consistency）——用于 Multi-Session UI 的"是否像同一产品"的辅助判断（§27 audit 阶段）。
- **应采纳**：对 Session 行层级（title vs meta vs badge）、留白节奏、hover 反馈的批评性检查。
- **不应采纳**：若其默认审美（更现代布局/配色/字体/hero）与当前深色终端控制台语言冲突 —— 一律以 `DESIGN_SYSTEM.md` 为准。它可能倾向"重新设计侧栏"，必须拒绝。

### I.2 UI/UX Pro Max（https://github.com/nextlevelbuilder/ui-ux-pro-max-skill）
- **可用角色**：information architecture、interaction patterns、accessibility、responsive、UX 质量 —— 方法论文档价值高。
- **应采纳**：用于 §O 的 a11y/responsive 检查清单（键盘导航、focus 管理、aria、断点行为、触控）与 §N 的信息架构评审；其 "component audit" 思路可用于 §27 audit。
- **不应采纳**：任何需要新调色板 / 字体配对 / 新组件范式的"产品级建议"（该 skill 自带大量 palettes/fonts 库，**禁止**引入到本项目）。

### I.3 Impeccable（https://github.com/pbakaus/impeccable）
- **可用角色**：UI quality review / consistency / spacing / hierarchy / interaction refinement / a11y critique。
- **应采纳**：实施完成后做一次完整 UI audit（§27）：spacing 对照、颜色对照 token、focus-visible、aria、空/错/载状态完备性、响应式。
- **不应采纳**：其"全面重设计/换肤"倾向。必须逐条给出与 `DESIGN_SYSTEM.md` 的对应证据后再落地。

### I.4 Anthropic Frontend Design Skill（frontend-design）
- **可用角色**：component composition、interaction design、responsive implementation、visual quality、设计意图记录。
- **应采纳**：帮助把 Session UI 组织成"现有页面内的最小组件组合"（Sidebar section 扩展而非新页面），以及撰写设计意图/理由。
- **不应采纳**：其面向"从零设计新界面"的完整流程与推荐风格（如新排版方向、玻璃拟态、渐变），与本项目"Adaptation 不是 Redesign"冲突。

> **使用纪律**：4 个 Skill 全部定位为 *Review / Design Quality Assistant*，不是 *Project Design System Authority*。任何与内部三文档冲突的建议 → 直接拒绝并记录冲突点。实施阶段优先完成内部一致性 audit，再用 Skill 交叉 review；禁止因 skill 演示而 redesign。

---

# J. State Management Adaptation（状态管理设计）

> 约束：不引入状态库 / 数据请求库 / router；以现有 `useState` + hook 结构做最小调整（§16 要求"最小调整、避免全局串线"）。

### J.1 推荐结构（映射到真实代码，不机械照抄理想模型）

```text
App.tsx（Session 层，新增/扩展）
 ├─ sessions: SessionSummary[]           # GET /api/sessions 结果（active；可选 archived）
 ├─ sessionsLoading / sessionsError
 ├─ currentSessionId: string|null        # 持久化（localStorage 复用 deepsearch.thread_id 语义？→ 见 §M）
 ├─ turnsBySession: Map<sessionId, ChatTurn[]>   # 内存级 per-session 对话缓存（back-switch 恢复）
 ├─ selectedSession: SessionDetail|null  # GET /api/sessions/{id}（切换时载入）
 └─ handlers: createSession / switchSession / archiveSession / restoreSession

SessionWorkspace（新抽取，key={currentSessionId}）
 ├─ 挂载 useDeepAgentSession（改为接受/绑定 currentSessionId 而非自造 thread）
 ├─ 复用 ConversationThread + ChatComposer + MarkdownRenderer（零视觉改动）
 └─ turns = turnsBySession.get(currentSessionId)（缺失 → TaskHistory 摘要视图）
```

### J.2 关键决策（给出理由）

1. **按 session 重挂载（`key={currentSessionId}`）而非全局 Map 单 hook**：
   - 理由：现有 `useDeepAgentSession` 的 connect effect 已以 `threadId` 为依赖并自带 cleanup（close socket、清 timer、`disposed` 守卫）。重挂载 = 自动断开旧 session WS、自动清空 events/files/result/taskId —— **从根上消除 A 状态残留到 B 的可能**（呼应 §14 "防止旧异步请求覆盖新 Session"）。
   - 代价：back-switch 时 hook 状态重置 → 需要 `turnsBySession` 内存缓存把已完成的 turn 还原；进行中 run 的 live 中间状态在离开期间不保留（live 事件不持久化），靠返回后 durable reconcile（既有 `doReconcile`）收敛终态 —— 与现状断线语义一致，可接受。
2. **提交/取消/上传走 session-scoped 端点**（当前 session 存在时）：
   - 提交 `POST /api/sessions/{currentSessionId}/tasks`；取消 `POST /api/sessions/{currentSessionId}/tasks/{task_id}/cancel`；上传 `POST /api/upload`（thread_id = currentSessionId）。
   - 理由：archived session 必须无法提交（服务端 409 兜底 + UI 禁用双保险）；归属校验语义正确。
   - 兼容：legacy 路径（无 session 的 thread）仍允许 `POST /api/task`（旧客户端语义保留）——FE 迁移期 boot 注册后基本不会走到。
3. **任务身份恢复**：重挂载后若无 live `task_started`，用 `GET /api/sessions/{id}`（`latest_task.status==="running"`）恢复 running 展示与 taskId（供 cancel 与 reconcile）；run_id 锚点仍只由 live `task_started` 建立（沿用现有注释语义 `useDeepAgentSession.ts:199-209`）。
4. **拒绝方案**：不引入 zustand/redux/react-query/单一全局 store —— 本应用状态面小、且项目禁止无必要依赖。

---

# K. WebSocket Adaptation（WebSocket 适配）

> Backend WS schema / 心跳 / replay 均冻结，**不改**。`session_id == thread_id` 直接套用现有 `/ws/{thread_id}`。

### K.1 连接模型（推荐：单连接随当前 session）

```text
进入 Session A → 挂载 workspace(sessionId=A) → WS /ws/A（live 事件只到 A）
切到 Session B   → key 变化 → A workspace unmount（socket 关闭、timer 清理、状态丢弃）
                 → B workspace mount → WS /ws/B
回 Session A     → 重新 WS /ws/A；durable reconcile 收敛 A 离开期间的终态
```

- WS 建立方式 / thread_id 来源：`useDeepAgentSession` 绑定传入的 sessionId；URL 仍为 `/ws/{session_id}`。
- session 切换是否重连：**是**（现有 effect 依赖 threadId 的语义正好复用）。
- subscription 切换：随 socket 重连自动切换（服务端按 thread_id 定向推送）。
- stale events 过滤：三层——① 旧 socket cleanup（effect return）；② 现有 `run_id` 锚点过滤；③（可选加固）onmessage 校验 `payload.thread_id === 当前 sessionId`（信封已有 thread_id 字段，`types.ts:25` 已声明）。三层确保 A event ≠ B UI。
- 页面 refresh 恢复：mount 后先取 session detail / tasks 确定 running 状态，再连 WS（REST 先行，live 续接）——对应现有 reconnect 后 `doReconcile` 语义前移为 mount 时执行一次。
- **archived Session**：UI 只读（不能建任务、不能上传），WS 是否连接无实际事件价值 → 建议 archived 当前视图不建立"运行用途"的 live 期待；可保持连接（无任务即无事件）也可按产品口味断开。倾向：**保持 hook 统一连接**（最小改动），但 composer 禁用 + 明确 archived 态。

### K.2 replay 帧（维持现状）
- FE 当前忽略 `governance_replay / governance_error`（`useDeepAgentSession.ts:194-197`），用 REST 校正终态。Multi-Session 不改变该策略；历史 task 详情通过 `GET /api/tasks/{task_id}` 或 session task 列表获取（durable 面），**不消费 replay 做对话重建**（服务端无消息级持久化）。

---

# L. Session Switching Lifecycle（切换生命周期设计）

目标：Session A → switch → Session B 的确定性状态机，且 `request A` 晚到响应不得污染 B。

```text
1. 用户点击 Sidebar 中 Session B（或"新建研搜" → 先 createSession 拿到 B）
2. [写] 置 currentSessionId = B（state 更新 → 触发 workspace 重挂载 key）
3. [写] 持久化 currentSessionId（localStorage）
4. B workspace mount：
   a. useDeepAgentSession 以 B 绑定 → WS /ws/B 建立（connect effect）
   b. GET /api/sessions/B（metadata + tasks + latest_task）→ running/taskId 恢复
   c. turns = turnsBySession.get(B) ?? []（内存缓存；无则展示 TaskHistory/Empty）
   d. files：若缓存了 B 的输出路径（§M）→ GET /api/files?path=... 预载；否则空
5. 旧 A 的异步尾巴被隔离：
   - WS: A socket 已 close（cleanup）；B socket 是新实例
   - REST: hook 内 `disposed` 守卫（connect effect return）+ doReconcile 定时器随 unmount 清理（需补：reconcile retry timer 的 cleanup，见 §R）
   - React: 卸载组件上的 setState 不再影响新组件
6. UI 更新：Sidebar 高亮移至 B；topbar/THREAD 显示 B；composer 按 B 状态启用/禁用
```

防污染细则（对应 §14/§Q）：
- A 的 `GET /api/sessions/A`（detail）、`getTask(A-task)`、files poll 若在卸载后 resolve：React 18 卸载组件 setState 无副作用警告下自动丢弃；定时器在 effect cleanup 清空 → 不产生跨 session 写。
- 任何"响应处理前再读一次 currentSessionId 引用"的显式守卫（session token）作为加固可选项 —— 若采用"单 hook 常驻 + switchSession 重置"替代方案（不推荐）则**必须**加该守卫；重挂载方案可免。

---

# M. Page Refresh / Persistence（刷新与持久化策略）

> 优先复用既有 localStorage 机制，**不引入新依赖**；不造新持久化系统。

### M.1 currentSessionId 恢复
- 方案：继续使用既有 key `deepsearch.thread_id` 存**当前 session id**（因为 `session_id == thread_id`，语义无损）。改名 key 会丢失既有用户 thread 连续性 → 不推荐另立 `deepsearch.session_id`（除非有意切断旧 thread 关联；默认不切）。
- 刷新序列：
  1. `GET /api/sessions`（active）→ 若存储 id ∈ 列表 → 作为 current；
  2. 若存储 id 不在（如被归档但列表只含 active，或从未注册）→ `GET include_archived=true` 查；archived 也可作为只读 current；
  3. 若仍无 → boot 协调：`POST /api/sessions {thread_id: 存储id}` 注册既有 thread（409 = 已注册，则忽略并当作存在）；若服务端完全无记录且本地也无 → 自动 `POST /api/sessions` 创建默认 session（保持"打开即能用"现状）。
- 边界：迁移期旧 thread（从未注册）自动注册后即可进 Sidebar，历史 task/产物随 `thread_id` 归属自动挂到该 session —— 完美承接。

### M.2 session 输出路径缓存（产物可列性，服务端无 per-session 端点）
- 服务端 `/api/files?path=` 只接受 output 目录内的**绝对路径**；客户端无法由 session_id 推导绝对路径（服务器文件系统位置不可知）。
- 方案（FE-only 缓解）：live `session_created.data.path` 到达时写入 localStorage（如 `deepsearch.session_path.<sessionId>`）；打开/返回该 session 时读缓存 → `GET /api/files?path=...` 预载历史产物；无缓存（例如另一浏览器创建的 session）→ 产物区显示空/说明（Known Limitation，见 §V）。
- 上传附件 `uploadedItems` 本就全局 — 随 workspace 重挂载清空即可（附件应在当前 session 提交前即时上传，符合现状交互：`ChatComposer` staged→upload 后清空 staged）。

### M.3 对话文本
- 服务端不持久化消息/最终答案 → 刷新后无法还原消息级 transcript（现状即如此，`BACKEND_FRONTEND_ADAPTATION_REPORT.md §8`）。Multi-Session 维持该边界：历史 = task 摘要（query/status/times）+ 可下载产物；不伪造"已恢复对话"。

---

# N. UI Information Architecture（最小信息架构）

```text
┌────────────────────────────────────────────────────────────────────┐
│ chat-app-shell（现状双栏）                                            │
│  ┌───────────────────────────┬──────────────────────────────────────┐ │
│  │ chat-sidebar 292px         │ chat-main（900px 内容列）             │ │
│  │ brand (不变)               │ topbar: kicker + H2 + run-indicator  │ │
│  │ 新建研搜 [主操作] (语义:建Session)│   (+ 当前 session title/archived 态) │ │
│  │ ┌ SESSIONS ──────────┐    │ chat-alert（错误横幅，沿用）           │ │
│  │ │ session item A ◀高亮│    │ stream panel:                       │ │
│  │ │ session item B     │    │   turnsBySession[当前] 或            │ │
│  │ │ [已归档 …]         │    │   TaskHistory（历史摘要）或           │ │
│  │ │ + archived 开关    │    │   Empty(示例卡，无历史且空)            │ │
│  │ └────────────────────┘    │ composer（fixed；archived→禁用提交）   │ │
│  │ THREAD/状态(→当前session信息)│                                    │ │
│  │ AGENTS (不变)             │                                    │ │
│  │ ENDPOINTS (不变)          │                                    │ │
│  └───────────────────────────┴──────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────┘
```

- **Session Sidebar**：负责 list / current highlight / New Session / title / updated time / active-archived 切换。归档的 session 不默认显示（服务端 `include_archived=false` 缺省即如此），提供显式开关/筛选（如 Section 内 footer 按钮「查看已归档」）。
- **Current Session Indicator**：sidebar 高亮行 + THREAD 块升级显示 title/状态；不新增全局 header 层级（保持现有 Navigation Pattern —— 项目没有主导航，不做成第一公民）。
- **Main Research Area**：现状 UI 原样保留，数据源 = currentSession（内存 turns / TaskHistory / Empty 三分支）。
- **History / Report**：历史 session 打开 → TaskHistory 摘要（每条 task：mono query（或"未命名任务"fallback）+ 状态标签 + 时间 + running badge）；产物经 §M.2 路径缓存列 FileDock 语义行。历史还原**不做**消息级（§V）。

---

# O. Responsive / Accessibility Analysis

### O.1 Responsive
| 断点 | 现状 | Multi-Session 需处理 |
| --- | --- | --- |
| >980px | 双栏，sidebar 292px | Session 列表在 sidebar；无问题 |
| ≤980px | `.chat-sidebar { display:none }`（`styles.css:1731-1758`） | **Session 切换入口丢失（Gap）** → 方案：主区顶部增加"当前 Session 切换"紧凑控件（AntD `Select` 或 `Dropdown`，仅 ≤980 显示；样式 = AntD dark 主题 + 现有输入框 focus 配方）；新建 Session 保留 composer 左 "+" 圆钮（现状即有） |
| ≤560px | 单列 / 气泡全宽 | 同上控件自适应（Select 宽度受约束）；TaskHistory 行内换行 |

- 约束：980 以下 sidebar 隐藏是既有规则（不改）；移动端 Session 切换控件是**新增最小模式**，属 AntD 组件（Select/Dropdown 均在 AntD 内，无新 framework）；需在实现前给设计确认（本项目激活页未用过 Select/Dropdown，属"新组件形态"，按 §V 风险项评估）。

### O.2 Accessibility
- 键盘：Session 列表行为按钮（`<button>`）支持 Tab/Enter/Space；上下键导航为增强项（若用 AntD Menu/Select 自带）；当前项 `aria-current="true"`。
- Focus 管理：切换 session 后焦点移到主区顶部（topbar 或首条 turn），避免焦点悬空/留在消失项。
- aria：Sidebar 区块沿用 `aria-label`（现状 `App.tsx:158` sidebar "会话信息"）；Session 列表 `aria-label="研搜会话列表"`；archive 图标按钮 `aria-label`（「归档会话 <title>」）；实时 running 徽标给 `aria-live="polite"` 或文本态；折叠（历史详情）用 `details/summary`（自带语义）。
- 颜色对比：title `--text`、meta `--muted`（仅 12-13px 辅助），符合现状基准；archived 状态除颜色外必须有文字（不单靠色）。
- 触控：行高 ≥44px（现状刻度）；hover 效果不遮挡 touch（无 hover-only 依赖，动作另有按钮）。

---

# P. Loading / Error / Empty States（状态完备矩阵）

### Session 层
| 状态 | 呈现 | 依据/说明 |
| --- | --- | --- |
| sessions 加载中 | Sidebar SESSIONS section 内 mono 小字「同步会话…」或 AntD loading（短动作）；非全局 spinner | 沿用 loading 纪律（§G-7） |
| sessions 空 | Empty：指引文案「新建一个研搜会话开始研究」+ 主操作按钮 | 祈使句空态（UI_PATTERNS §3.8） |
| sessions 加载失败 | `message.error`（动作级）或 Sidebar 内 inline red 小字 + 重试按钮 | 与顶部 Alert 分工（会话级 vs 动作级） |
| create session 失败 | `message.error(error.detail)`（409 已注册/503 等） | requestJson 已解 detail |
| switch 失败（404） | `message.error`；currentSessionId 回退或保持 | 列表刷新兜底 |
| archive 失败（409 running） | `message.error('会话存在运行中的任务，请先取消后再归档')`（服务端 detail） | 服务端文案为准 |
| archived（当前视图） | 顶部/顶栏旁只读提示 + composer 禁用 + 「恢复会话」入口 | archived ≠ 错误，中性提示 |

### Task 层（复用现有词汇）
| 状态 | 呈现 |
| --- | --- |
| creating | ChatComposer 提交钮 loading；turn 立即创建（现状语义） |
| running | 顶栏 run-indicator + pending 面板（ThinkingLoader）+ timeline 追加 —— 现状三处呼应 |
| cancelling | 取消钮 loading + message.info（现状） |
| cancelled / completed / failed / timed_out / budget… | 复用 `TERMINAL_STATUS_LABELS` + `endedNoticeText`（ConversationThread.tsx:306-347） |
| task 不在 session | session-scoped cancel 404 → message.error（服务端 detail） |

### WebSocket 层（不变）
connecting / connected / reconnecting / closed —— sidebar status 行现状即覆盖。

### UI Race（并发操作一致性）
| 场景 | 处理 |
| --- | --- |
| create session → 立即 switch | create 完成拿到 session 后统一走 switch；按钮创建期间 loading 防连点 |
| create task 后立即 cancel | 现有 submit/cancel 状态机（isCancelling）已防；session-scoped cancel 需 task_id —— 用响应中的 task_id（`TaskResponse.task_id`） |
| archive 与 running 并发 | 服务端单事务守卫 + 409 兜底；UI 在 running 时禁用 archive（tooltip「先取消运行中任务」） |
| 切换期间 A 的旧响应到达 | workspace 重挂载隔离（§L-5） |
| 两个 session 同时 running | 服务端允许（per-thread）；FE 同一时刻只连当前 session WS（§K）；验证见 §U |

---

# Q. Async / Race Condition Analysis（异步与竞态）

汇总 §L/§P 的可执行守卫（写入 Implementation 阶段的验收标准）：

1. **重挂载隔离**（主防线）：workspace `key={sessionId}` → 旧组件卸载；connect effect cleanup 关闭旧 WS/清 timer；`disposed` 守卫拒收旧 socket 消息（已有）。
2. **reconcile 定时器清理**（需补代码）：`doReconcile` 的 250ms 重试 `window.setTimeout`（`useDeepAgentSession.ts:131-137`）当前**未登记到清理集合** → 卸载后仍会触发（对已卸载实例 setState，React 静默忽略，但仍建议补 cleanup，避免跨实例误写 + 浪费请求）。新增 `reconcileTimerRef` 并入 `clearSocketTimers`。
3. **run_id 锚点**（已有）在 session 重挂载后随实例重置 —— 新 session 首个 `task_started` 重建锚点，天然不串。
4. **文件轮询** effect 依赖 `[sessionPath, isRunning]`；重挂载即重置 —— 只轮询当前 session。
5. **提交防连点**：composer 运行禁用链（已有 `ChatComposer.tsx:112,148,160-161`）。
6. **session 切换中双击**：switchSession 期间 Sidebar 禁用其他项点击（或 loading 态），防 A→B→C 乱序。

---

# R. Exact Files Expected To Change（文件级改动清单）

> 只列真实存在的路径（已逐一验证存在）。改动统一标注：why / what / 复用 / state / api / ws / ui。

## R.1 Must Change（必须改）

### `frontend/src/types.ts`
- **Why**：现有类型无 session 概念（仅有 TaskInfo/MonitorMessage）。
- **What**：
  - 新增 `SessionStatus = "active" | "archived"`、`SessionSummary`（session_id/title/description/status/created_at/updated_at/task_count/running_tasks/latest_task?）、`SessionDetail`（session + tasks[] + task_count/running_tasks/latest_task）、`LatestTaskSummary`（task_id/run_id/status/…）、`CreateSessionResponse`、`ArchiveSessionResponse`（already_archived/already_active）。
  - task 摘要对象与现有 `TaskInfo` 对齐（session detail 的 tasks[] 即 governance task_dict + query 可选字段，`app/session/service.py:276,305-325`）。
- **State/API/WS/UI**：纯类型 —— 供 api.ts / hook / App / 新组件共用。

### `frontend/src/lib/api.ts`
- **Why**：FE 从未消费任何 `/api/sessions*`（gap D）。
- **What**：新增（全部复用 `requestJson`）：
  - `listSessions(includeArchived=false)` → `GET /api/sessions?include_archived=`
  - `createSession(title?, threadId?)` → `POST /api/sessions`
  - `getSession(sessionId)` → `GET /api/sessions/{id}`
  - `archiveSession(sessionId)` → `DELETE /api/sessions/{id}`
  - `restoreSession(sessionId)` → `PATCH /api/sessions/{id}` `{status:"active"}`
  - `createSessionTask(sessionId, query)` → `POST /api/sessions/{id}/tasks`（替代 startTask 于 session 场景）
  - `listSessionTasks(sessionId)` → `GET /api/sessions/{id}/tasks`
  - `cancelSessionTask(sessionId, taskId)` → `POST /api/sessions/{id}/tasks/{taskId}/cancel`
- **Why**：既有 `startTask/cancelTask` 保留（兼容/迁移路径），不改旧签名。

### `frontend/src/hooks/useDeepAgentSession.ts`
- **Why**：hook 自造 thread（`getStoredThreadId`/`createThreadId`）与全局状态 = single-session 核心假设（S3/S4/S9）。
- **What**：
  - 增加显式 session 绑定：`useDeepAgentSession(sessionId: string)`（或 `attachSession(sessionId)` 变体）——由外部传入当前 session id，替代内部自造 thread；`threadId = sessionId`。
  - 挂载即恢复：若 `sessionId` 无 live 事件，先取 session detail（或 tasks）判断 running 并设定 taskId（供 cancel/reconcile）。
  - 提交/取消改走 session-scoped API（`createSessionTask` / `cancelSessionTask`）；上传保持 thread_id = sessionId。
  - 补 `reconcileTimerRef` 清理（§Q-2）；保留 run_id 过滤/reconnect/heartbeat 全部现状。
- **State/API/WS/UI**：核心改动点 —— state 重挂载隔离的落点；API 换用 session-scoped；WS URL 不变（thread=session）。

### `frontend/src/App.tsx`
- **Why**：App 是所有 session 语义的编排点（list/current/create/switch/archive/turnsBySession）。
- **What**：
  - 新增 `sessions/sessionsLoading/sessionsError/currentSessionId/turnsBySession` 状态 + boot 协调（§M.1）。
  - Sidebar 新增 SESSIONS section（渲染列表 + active/archived 切换 + 行操作 archive/restore）；「新建研搜」语义改为 createSession→switch；THREAD 块升级为当前 session 信息。
  - 抽取 `SessionWorkspace`（见 R.2），主区渲染 `<SessionWorkspace key={currentSessionId} …/>`。
  - handlers：createSession / switchSession / archiveSession / restoreSession + message 反馈；TaskHistory 分支组装。
  - 移动端（≤980）当前 session 切换入口（§O.1）。
- **State/API/WS/UI**：全部涉及。

### `frontend/src/styles.css`（只追加，不改既有规则）
- **Why**：公共视觉唯一挂载点；新 UI class 必须在此追加。
- **What（新增 class，全部沿 token/配方派生）**：
  - `.session-section` / `.session-list`（内滚动容器 + cyan 细滚动条）/ `.session-item` / `.session-item--active` / `.session-item--archived` / `.session-item-meta` / `.session-badge` / `.session-badge--running` / `.session-actions` / `.sidebar-toggle-archived`
  - TaskHistory：`.task-history` / `.task-history-item` / `.task-history-item--{status}`（沿用 event 级联 `--modifier` BEM）
  - ≤980 专用：`.session-switcher`（AntD Select 包装类）
  - archived 只读提示 `.session-readonly-note`
- **禁改**：styles.css 中任何被激活页引用的既有规则；不新增 hex/字体/radius/spacing 体系。

## R.2 Should Change（建议改 / 新增）

### `frontend/src/components/SessionSidebar.tsx`（新组件；或内联于 App —— 推荐独立以控复杂度）
- Why：App.tsx 已 269 行，加入 session 层后会膨胀；按 §G-§6.3 允许新建组件。
- What：接收 `sessions/currentSessionId/loading/error/callbacks`，渲染 SESSIONS section（复用既有 `.sidebar-section/.sidebar-label/.sidebar-status` 词汇 + 追加 class）；空/错/载状态齐备（§P）。
- 复用：无新 UI 范式；State 由 App 提供（受控组件）。

### `frontend/src/components/SessionWorkspace.tsx`（新组件）
- Why：承载"当前 session 的对话/任务"；`key` 重挂载是实现隔离的物理点。
- What：内部 = `useDeepAgentSession(sessionId)` + `ConversationThread`（turns=当前缓存）+ `ChatComposer`（按 archived/running 禁用）+ TaskHistory/Empty 分支；向上暴露事件（turns 变更、submit/cancel/upload 完成）由 App 汇入 `turnsBySession`。
- 与 `App.tsx:51-82` 现有"镜像到最后一轮 turn"逻辑合并到此组件内（原 effect 迁移），避免 App 单 turns 假设残留。

### `frontend/src/components/TaskHistory.tsx`（新组件，可并入 SessionWorkspace）
- Why：历史 session（无内存 turns）需呈现 task 摘要（§N）。
- What：渲染 session detail 的 tasks[]：状态标签（复用 `ConversationThread` 的终态文案映射 → **建议把 `TERMINAL_STATUS_LABELS`/`endedNoticeText` 提取为共享常量/工具**，如 `lib/taskStatus.ts`）+ 时间（复用 `formatTime` 语义）+ 产物（若 §M.2 路径缓存可用则列文件）。

### `frontend/src/components/ConversationThread.tsx`（小幅）
- Why：终态文案/时间格式需被 TaskHistory 复用。
- What：把 `TERMINAL_STATUS_LABELS`、`endedNoticeText`、`formatTime/formatBytes` 提取导出（不动视觉）；可选新增 archived 只读说明渲染分支（否则由 App/SessionWorkspace 层承担）。
- 禁止：改消息结构/视觉。

### `frontend/src/components/ChatComposer.tsx`（小幅）
- Why：archived session 提交禁用；"新建会话"圆钮语义不变但 handler 改为建 Session。
- What：新增可选 `disabled/readOnly`（archived）或由父级传 `isArchived`；placeholder 不变。
- 禁止：改固定 composer 布局/CSS。

### `frontend/README.md`
- Why：文档同步（Backend Contract 列表现缺 `/api/sessions*`）。
- What：补 session 端点清单与说明。属文档，非代码。

## R.3 May Change（可选）

### `frontend/src/lib/thread.ts`
- 可选：把"当前 thread/session 持久化"重命名/注释对齐（保留 `deepsearch.thread_id` key），并新增 `sessionPath` 缓存 helpers（`getSessionPath/rememberSessionPath`，key `deepsearch.session_path.<id>`）。
- 若不做 → 缓存逻辑落 App/SessionWorkspace 内。

### 新共享工具 `frontend/src/lib/taskStatus.ts`（若 ConversationThread 提取）
- 纯常量/函数迁移（status label / endedNotice / time / bytes），无视觉。

## R.4 Do Not Change（明确不动）

- `frontend/src/main.tsx`（ConfigProvider token 唯一来源）
- `frontend/src/components/MarkdownRenderer.tsx`
- 遗留 7 组件（`MissionComposer/StatusStrip/AgentTopology/EventStream/UploadPanel/ResultPanel/FileDock`）
- `frontend/src/lib/config.ts`、`vite.config.ts`、`tsconfig*.json`、`index.html`、`package.json`（不加依赖）
- `styles.css` 中一切既有规则（只追加）
- `app/`、`db/`、`tests/`、`docs/frontend/` 三文档、`MULTI_SESSION_API_SPEC.md`、`ARCHITECTURE.md`、`DECISION.md`、`PROJECT_CONTEXT.md`（除非用户另行立项）
- 用户个人文件（简历 md/pdf/png/html 等）

---

# S. Files Explicitly Out Of Scope（明确范围外）

```text
app/runtime/            app/research/            app/harness/
app/session/            db/                      db/governance_migrations/
app/api/server.py       app/agent/               app/tools/ app/utils/ app/api/monitor.py
tests/                  docs/problem/            docs/spec/（冻结 Feature Spec）
docs/frontend/*         MULTI_SESSION_API_SPEC.md  MULTI_SESSION_IMPLEMENTATION_REPORT.md
ARCHITECTURE.md         DECISION.md              PROJECT_CONTEXT.md  PROCESS.md  AGENTS.md  TESTING.md
前端 legacy 组件与遗留 console 样式    用户个人文件（简历等）       .env*    docker/ examples/ docs/knowledge_base/
```
理由：Backend Contract 已冻结（本阶段禁止改 Backend）；`docs/frontend/*` 是权威设计约束（本阶段只读，不改写）；遗留组件规则=不复活。

---

# T. Implementation Order（实施顺序）

> 顺序按"依赖 + 每步可独立验证"。每步结束跑 §U 的 static gate。此清单供 Implementation 阶段（下一阶段，独立 Feature branch `feature/multi-session-frontend`）执行，本阶段不实施。

```text
Step 0   Branch & Baseline
         - 基于 main（5a60f04）创建 feature/multi-session-frontend（用户批准后）
         - 复跑 pnpm build 基线通过
Step 1   Types + API client（types.ts / lib/api.ts）
         - 纯加法；pnpm build 绿
Step 2   hook session 化（useDeepAgentSession）
         - 接受 sessionId；session-scoped submit/cancel；reconcile timer cleanup
         - 单测：类型/语义走查 + pnpm build
Step 3   lib/thread.ts 持久化对齐 + sessionPath 缓存
Step 4   App session 层 + SessionWorkspace 抽取 + boot 协调
         - 新组件 SessionSidebar / TaskHistory 同步引入
         - 本步后具备：列表/新建/切换/归档/恢复/历史摘要
Step 5   ChatComposer archived 禁用 + ConversationThread 常量提取
Step 6   styles.css 追加 class + ≤980 切换入口 + a11y 补齐
Step 7   全量功能验证（§U）+ 回归（原有研究流）
Step 8   Design Consistency Audit（§27 + Skill 辅助 review）
```

---

# U. Verification Plan（验证计划）

> FE 无测试框架且本阶段不加依赖 → 静态用 build/typecheck；功能用手动 E2E + API 对照。Backend 已有 775 测试覆盖服务端语义，FE 重点验证接线与隔离。

### U.1 Static
| 项 | 命令 | 期望 |
| --- | --- | --- |
| TypeScript + Build | `cd frontend && pnpm build`（=`tsc -b && vite build`） | 0 error（记录输出尾部） |
| Format/Lint | FE 无脚本；沿用仓库 Python ruff 仅约束后端。FE 一致性靠 §27 audit | — |
| `git diff --check` | 提交前 | 无 whitespace errors |

### U.2 Functional（手动 E2E，需后端 `uvicorn app.api.server:app` 运行 + 默认 sqlite）
| 场景 | 步骤 | 期望 |
| --- | --- | --- |
| list sessions | 打开首页（已有注册 session） | Sidebar 显示列表；current 高亮 |
| create session | 新建研搜 | POST /api/sessions → 新 session 成为 current；服务端 sessions 表 +1 |
| switch session | 点 B | WS 断开 A 连 B；B 的 turns/历史显示；A 事件不出现于 B |
| task under session | 在 B 提交 | `POST /api/sessions/B/tasks`；run indicator + timeline 更新 |
| task status update | 观察 running→terminal | 三处呼应状态同步（顶栏/消息/计数） |
| cancel task | 取消当前 | session-scoped cancel；cancelled 文案 |
| archive session | 归档（无 running） | 列表消失（默认视图）；archived 可恢复 |
| archive while running | 运行中归档 | UI 禁用 + 服务端 409 detail 展示 |
| archived behavior | 打开 archived session | 只读：不能建任务/上传；提供恢复入口 |
| report access | 历史 session 产物 | §M.2 路径缓存可用 → 产物列表；无缓存 → 空态说明 |
| browser refresh | F5 | currentSessionId 恢复；sessions 列表恢复；对话文本按限制显示历史摘要 |

### U.3 Isolation（A ∥ B 双 Session 并发）
```text
Session A running
切到 Session B，B running
（A 仍在服务端运行）
回 A：cancel A
A → cancelled（durable reconcile）
再回 B：B 仍 running / 已完成 —— 未被 A 事件污染
断言：A event ≠ B UI；B event ≠ A UI（重挂载隔离 + run 锚点 + WS 单连）
```

### U.4 Existing Flow Regression
- 原有"新会话 → 提交 → 思考时间线 → 结果 → 产物下载"全链路必须仍可用（经由 session-scoped 路径，语义一致）。
- 兼容检查：`POST /api/task` 旧路径（无 session 的 thread）仍可提交（Backend 保证；FE 至少不破坏 requestJson）。

---

# V. Risks / Blockers（风险与阻塞）

### V.1 硬限制（Backend 冻结；本阶段不改 —— 若实施中确认不足，按用户 §21 记录 Blocker 即停）
1. **消息/最终答案文本不持久化**：历史 session 无法还原对话级 transcript（刷新即只余 task 摘要）。这**不是**本阶段可修的 Backend 缺口，是既有产品边界（`BACKEND_FRONTEND_ADAPTATION_REPORT.md §8` 已声明）。若产品要求"历史报告原文阅读"，需后续立项（新端点 / 存储），本阶段以 TaskHistory 摘要 + 产物为限。
2. **历史产物列表依赖绝对路径**：`/api/files?path=` 语义；服务端无 per-session 文件端点 → FE 用 localStorage 路径缓存缓解；跨浏览器/无缓存场景产物区为空（如实呈现，不伪造）。彻底方案（`GET /api/sessions/{id}/files` 之类）需要 Backend additive 端点 → 超出本阶段（§21）。
3. **标题不可改**：PATCH v1 仅 unarchive，无改名端点 → 新 session 均显示"未命名会话"（默认 title），Sidebar 辨识靠时间/query。若需改名能力 → Backend 后续 additive（本阶段不做）；FE 可在创建时用首条 query 派生 title（可选打磨）。

### V.2 设计/实现风险
4. **≤980px Session 切换入口**：现状 sidebar 隐藏；需在移动端引入 AntD Select/Dropdown 形态 —— 项目激活页未用过该形态，属"新组件形态"，实现前需用户确认（符合规则 §3.4 / 不引入第二套体系的前提成立，AntD Select 属现有库）。
5. **重挂载 = 丢中间态**：离开中的 session 在离开期间无 live 事件（服务端 monitor 不持久化）；返回靠 durable reconcile 收敛"终态"而非中间细节 —— 与现状断线窗口行为一致，可接受；文档明确即可。
6. **遗留 thread 注册的副作用**：boot 自动 `POST /api/sessions {thread_id}` 会给"从未想进 Sidebar"的旧 thread 建 session（幂等注册规则）。属产品决策（默认做，保证连续性）；若用户不希望自动注册 → 改为"仅当服务端已有 session 时恢复，否则新建"。
7. **不引入测试框架**：FE 无自动化测试基础设施（P006 同类缺口）。本阶段不加依赖；验证靠手动 E2E + build。若用户要求 FE 自动化，需单独立项（新依赖/框架）。
8. **PROJECT_CONTEXT.md 过时**：其"当前状态"未反映 multi-session backend 合并（Last updated 2026-10-01 < 2026-10-03 后端交付）——本阶段只读不改；完成阶段建议用户批准后同步。

---

# §27-verification（Design Consistency Verification —— 实施完成后执行）

实现完成后逐项对照 `DESIGN_SYSTEM.md / FRONTEND_CONSISTENCY_RULES.md / UI_PATTERNS.md` 并回答：

| 维度 | 检查点 |
| --- | --- |
| typography | Session title/meta/time 只用 Sans/Mono 双栈与既有字号；无新字体加载 |
| spacing | 行距/内距落在既有节奏（4/6/8/10/12/14/16…）；无新间距体系 |
| colors | 所有新增视觉来自 `:root` 5 色 + alpha 配方；无新 hex（grep 校验） |
| buttons | AntD Button + 既有 class；circle 主操作/取消红语义沿用 |
| dialogs | 若引入确认 → AntD（Popconfirm/Drawer）且经用户确认；优先无弹层方案 |
| cards/badges | 复用既有行/徽标词汇或追加一致 class |
| sidebar | Session 列表是 `.sidebar-section` 扩展；未把导航做成"一等公民" |
| empty/loading/error | 五态齐备且用既有模式（§P） |
| responsive | 980/560 断点行为符合；≤980 有 session 切换入口 |
| accessibility | 键盘可达、focus 管理、aria-current/aria-label、live 区域、对比度 |

最终判定问题：**Multi-Session UI 是"融入现有产品"，还是"另起了一套设计体系"？**
→ 预期与验收答案：**融入现有产品**（以最终 audit 报告 + 截图/实跑为证）。

---

# 29-point Final Checklist（对照用户 §29 最终报告要求）

1. **当前 branch**：`main`（本地 = `origin/main` @ `5a60f04`）—— 本阶段只读，不建 branch。
2. **Git status**：仅用户个人文件（`简历项目经历-深度研搜.md` M / `简历-AI应用开发-Agent方向.html` ??）；本分析文档未提交。
3. **Frontend architecture**：见 §A（React19+Vite7+AntD5+TS strict+单页+单 WS hook；无 router/状态库/测试）。
4. **Single-session assumptions**：见 §B（S1–S12，含证据）。
5. **Backend ↔ Frontend gaps**：见 §D（矩阵）；Backend 无新缺口，FE 全量缺口已列。
6. **Design System 分析**：见 §E。
7. **Consistency Rules 分析**：见 §G。
8. **UI Patterns 复用方案**：见 §H。
9. **4 个外部 Skill 适用性**：见 §I（全部 = Review/QA 角色；禁止覆盖内部三文档）。
10. **WebSocket adaptation**：见 §K（单连随 session；重挂载隔离；schema 零改动）。
11. **State management design**：见 §J（App session 层 + 按 session 重挂载 + turnsBySession 内存缓存）。
12. **Session switching lifecycle**：见 §L（8 步状态机 + 防污染细则）。
13. **Persistence strategy**：见 §M（复用 `deepsearch.thread_id` key = current session；sessionPath 缓存；不引入新依赖）。
14. **Exact files to change**：见 §R（Must/Should/May/Do Not，全部真实路径）。
15. **Out-of-scope files**：见 §S。
16. **Implementation plan**：见 §T（Step 0–8）+ §R 文件级。
17. **Verification plan**：见 §U（static + functional + isolation + regression）。
18. **Risks / blockers**：见 §V（3 硬限制 + 5 风险）。

---

# §Verdict（阶段结论）

依据 §A–§V 分析：

- Backend Contract 完整、可消费、无阻断缺口（除已如实声明的两项服务端既有边界：对话文本不持久化、产物路径无 per-session 端点 —— 均有 FE 侧缓解或显式降级方案，非本阶段 blocker）。
- Frontend 现有代码结构（单 hook WS 生命周期、双栏 shell、styles token、激活组件集）足以承载"最小 Multi-Session Adaptation"，无需 redesign / 新库 / 改 Design System / 改 Backend。
- 设计约束全部映射完成（§E–§H），Skill 使用边界已锁定（§I）。

**结论：`READY_FOR_FRONTEND_IMPLEMENTATION`**

> 前提（进入 Implementation 前需用户确认，不影响 READY 判定本身）：
> 1) 创建 `feature/multi-session-frontend` branch（基于 main @ 5a60f04）；
> 2) 确认两个产品决策：a) 遗留 thread boot 自动注册为 Session（默认是）；b) ≤980 移动端 Session 切换采用 AntD Select/Dropdown 形态（默认建议）；
> 3) 确认历史呈现范围 = TaskHistory 摘要 + 可下载产物（不还原对话文本）。
