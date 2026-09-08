# DeepScholar Frontend Consistency Rules（开发新页面/新功能的强制规则）

> 本文件是「后续所有新前端开发」必须遵守的规则集，由 `DESIGN_SYSTEM.md`（视觉 token）与
> `UI_PATTERNS.md`（布局/交互模式）落地而来。规则全部能以当前代码证伪，证据格式（文件:行）。
> 规则分三级：**禁止（MUST NOT） / 强制（MUST） / 约束性建议（SHOULD）**。

---

## 0. 裁决顺序（最高原则，冲突时必须遵守）

```
CURRENT UI（当前激活页面实际代码）
    > EXTRACTED DESIGN SYSTEM（本目录三份文档）
    > SKILL DEFAULTS（impeccable / design-taste-frontend / ui-ux-pro-max / frontend-design 等）
```

- 任何 skill 建议与「当前 UI 现状」冲突时，一律服从当前 UI，并在实现说明中标注冲突点。
- 与仓库级文档冲突时按仓库 `AGENTS.md §1` 的文档优先级执行（User Request 最优先，本文件属于仓库 docs 范畴，由用户明确指定生成，为其背书；后续如与 `AGENTS.md`/`PROCESS.md` 冲突，按 AGENTS.md §1 上报裁决，禁止静默取舍）。

**当前 UI 的定义（Primary Reference Surface）**：`App.tsx` 渲染的聊天工作台
（`.chat-app-shell` → `.chat-sidebar` + `.chat-main`；消息气泡 + 思考时间线 + 输出文件 +
fixed composer；样式在 `frontend/src/styles.css`，AntD 主题在 `frontend/src/main.tsx`）。
新增任何页面/视图时，视觉一致性以「站在该页面前看是否像同一产品」为准绳。

---

## 1. 禁止项（MUST NOT）

### 1.1 视觉体系红线（对应上级任务禁令，均已对照代码复核）

1. **MUST NOT 重新设计当前页面**、改动现有 class 的视觉输出（`styles.css` 中任何被激活页面引用的规则）。
2. **MUST NOT 引入新颜色体系**。可用的色相池锁定为：cyan `#20d6ff`、green `#5dff9f`、amber `#ffc857`、
   red `#ff5c7a`、violet `#7c8cff`、中性 `#05070b / #eef7ff / #8fa2b7 / 白系 rgba`（`styles.css:3-18`）。
   需要新色阶时只能用上述色相 + alpha 派生（沿既有配方：底 4-12% / 边框 22-45% / 图标 100%），
   **禁止**任意新 hex（例如禁止新增品牌紫渐变以外的第二套主色、禁止纯 `#000/#fff` 大面积平涂）。
3. **MUST NOT 引入新字体体系**。沿用 Sans（`IBM Plex Sans, PingFang SC, Microsoft YaHei, system-ui`）与
   Mono（`JetBrains Mono, SFMono-Regular, Consolas`）双栈（`styles.css:37`、`main.tsx:23-26`）。
   **MUST NOT** 擅自加 web font 加载（当前仓库未加载任何字体文件：无 `@font-face`、无 fonts `<link>`、无 fontsource 依赖，实际渲染回退系统字体，见 `DESIGN_SYSTEM.md §1.7`）。
4. **MUST NOT 引入新 spacing 体系**。间距沿用既有节奏 4/6/8/10/12/14/16/18/20/22/24 与
   46/22 大间距、26-58 高度刻度（值分布见 `DESIGN_SYSTEM.md §2.3`）。
5. **MUST NOT 引入新 radius 体系**。表面统一 8px；行内 code 6px；动态圆点/细条 999px；圆形按钮用
   `shape="circle"`。禁止在非「圆点/指示条」上使用大圆角胶囊或 0 圆角扁平化改造
   （`styles.css:1040+ 各处 8px`、`main.tsx:22`）。
6. **MUST NOT 引入新 shadow/glow 体系**。阴影=深黑投影 +（可选的）青色 tint + 1px 内高光 ring 配方
   （`DESIGN_SYSTEM.md §1.10`）；glow 只允许出现在 主按钮 / 运行态 / loader 三处语义。
7. **MUST NOT 引入新 UI framework**。组件基础必须是 AntD v5（经 `ConfigProvider` 统一主题），
   图标必须是 `@ant-design/icons`（`package.json:12`）。禁止引入 shadcn / MUI / Radix 等第二套组件库
   与第二套图标库（图标「井 + 语义色」表达方式不变，见 `DESIGN_SYSTEM.md §1.8`）。
8. **MUST NOT** 为了「更现代/更像 SaaS Landing/Bento/Glassmorphism」而改变现有深色控制台语言；
   禁止新增紫色渐变、AI-SaaS 模板视觉、全屏数字大标题 hero 等与当前页面气质冲突的形态。
9. **MUST NOT 大量增加动画**。新动画必须（a）服务「运行/加载/状态变化」语义，（b）≤ 2.8s 周期或
   一次性短动画，（c）有 `prefers-reduced-motion` 降级（全局兜底 `styles.css:664-672` 仍在）。
   装饰性循环动画（常驻脉冲/流光/marquee/视差）一律禁止。
10. **MUST NOT 重构已有 UI / 顺带清理无关代码**（遵守仓库 `AGENTS.md §4` 最小修改）。
11. **MUST NOT 使用亮色模式**。产品是单深色主题（`color-scheme: dark`，`styles.css:4`）；新增表面必须
    在深色下对比达标，不引入 `prefers-color-scheme: light` 分支，除非产品层面明确立项。

### 1.2 代码组织红线

12. **MUST NOT** 复制粘贴既有 class 的样式到新组件内联样式/新文件后改数值（会造成无声漂移）。
    公共视觉修改只能落在全局 `styles.css`（与现状一致，全站唯一样式表）。
13. **MUST NOT** 用 Tailwind 工具类堆出新的独立视觉层。现状 Tailwind 仅用于极少量工具类
    （`min-h-dvh`，`App.tsx:142`）+ reset；新 UI 沿用全局 class 语言（证据即：激活页全部视觉都由
    `styles.css` 类驱动）。
14. **MUST NOT** 绕过 `ConfigProvider` 直接给 AntD 组件写局部主题，或复制 `main.tsx:10-38` 的 token
    到组件文件里（主题 token 只允许存在于 `main.tsx` 一处）。
15. **MUST NOT** 在组件里用裸 hex 表示语义色（颜色必须来自 `--var` 或既有 rgba 配方）。
16. **MUST NOT** 直接调用 antd `message` 静态方法；沿用 `AntApp.useApp()`（`main.tsx:40-42`、`App.tsx:43`），
    否则会丢掉主题上下文。

---

## 2. 强制项（MUST）

### 2.1 复用既有抽象（按仓库 AGENTS.md §5 的优先序，前端落地为）

1. 已激活组件直接复用：`MarkdownRenderer`（任何「代理最终输出/长文本」必须走它，`.markdown-body`
   自带全套排版与链接行为）、`ConversationThread` 的 turn/消息/思考时间线/产物架语义、
   `ChatComposer` 的 fixed-composer 交互语义、`useDeepAgentSession`（任何 WebSocket 会话功能）。
2. 视觉 token 只扩展 `styles.css:3-18` 的 `:root`（沿色相派生），或直接引用既有变量。
3. 数据呈现文字必须走 Mono 栈 + `--muted`/浅青文本惯例（ID、时间、路径、JSON、计数、时长）。
4. 新按钮/输入/弹层等一律用 AntD 组件 + 既有 class 追加外观，禁止另起炉灶写原生控件。
5. 功能开关、校验、错误提示沿用既有词汇：顶部 `Alert` = 会话级错误；`message.*` = 单次动作反馈
   （成功/失败/信息，文案语气参照 `App.tsx:86-129`）；行内状态色 = 语义状态。

### 2.2 布局与结构（新增视图时逐条对照）

1. 新功能若进入当前页面：内容列宽保持 **900px 居中**、主区 `grid-template-rows: auto auto minmax(0,1fr)`、
   外层禁止出现页面滚动条（滚动只发生在内容滚动容器）。证据 `styles.css:840-901`。
2. 需要整页级新容器时沿用壳模式：`.chat-app-shell` 双栏（侧栏 292px）或全宽主区；断点沿用
   **980 / 560**（聊天页体系），并补齐 560px 折叠态。
3. fixed 输入/悬浮操作区必须在滚动容器下保留底部让位 padding 与 `scroll-padding-bottom`
   （现状 210px / 166px，`styles.css:891-901, 1778-1780`），否则内容会被浮层遮挡。
4. 状态表达必须与既有「颜色↔语义」映射一致（成功绿 / 工具与警告琥珀 / 错误红 / 运行绿 / 主信息青，
   见 `DESIGN_SYSTEM.md §1.5`），不得换色表达同一语义。
5. 事件/日志/过程类数据走「等宽 meta 行 + 图标井 + 时间线」结构（参考 `.thinking-event`/`.event-row`），
   原始 JSON 用 `code`/`pre` 深黑底呈现（`styles.css:1205-1212, 1493-1507`）。

### 2.3 交互与状态

1. 可点卡片/chip 的 hover/focus 反馈 = 边框亮起 + 底色 +4% alpha + `translateY(-1px)`（8px 圆角不变，
   参考 `.preset-chip:hover` `styles.css:281-287`）；focus-visible 必须可见。
2. 运行态语义必须三处呼应（顶栏指示 + pending 面板 + 时间线/计数），禁止只做一个角落提示。
3. 长时间运行禁止全局 spinner，用思考加载器套件结构（pulse + track + dots + steps，
   `UI_PATTERNS.md §3.5`）；短动作用 AntD `loading`。
4. 任务运行中禁用输入/提交（沿用 `ChatComposer.tsx:112,148,160-161` 的禁用链）。
5. 取消类破坏性动作以红色表达（参考 `.send-button--cancel` `styles.css:1674-1684`）。
6. 空态文字必须是「指引/说明状态」祈使句（参照 `ConversationThread.tsx:192-199, 230-238, 348-354`），
   禁止玩梗/装饰文案。
7. 滚动容器加青色细滚动条（`scrollbar-color`，参照 `styles.css:891-901`）；消息类实时追加必须
   RAF 滚底、事件上限截断（现状 120，`useDeepAgentSession.ts:13`）。

### 2.4 可访问性（沿用既有水平）

1. 图标按钮必须有 `aria-label` 或包裹 `Tooltip`（现状全部如此：`ChatComposer.tsx:127-153`、
   `ConversationThread.tsx:251-259`）。
2. 装饰图标一律 `aria-hidden`（沿用 `App.tsx:163` 等）；实时区域加 `aria-live`（加载器
   `ConversationThread.tsx:267-272`）。
3. 运行/加载区域提供非视觉可读状态文本（参考 loader `aria-label="正在生成回复"`）。
4. 颜色对比以现状色值为基准（`--muted #8fa2b7` 仅用于 12-13px 辅助文字与占位，禁止把主信息做成
   `--muted` 级别）。

### 2.5 文案（Copy）

1. UI 文案简体中文、短句祈使（「新建研搜」「发送任务」「取消当前任务」）；英文仅用于 mono kicker 与
   品牌标签（全大写、无空格风格如 `CHAT WORKSPACE` / `THREAD`）。证据 `App.tsx:145-206`、`styles.css:90-98`。
2. 时间/时长/文件大小等格式化沿用既有 helper 语义：`toLocaleTimeString("zh-CN", hour12:false)`、
   B/KB/MB、`mm:ss` / `h:mm:ss`（`ConversationThread.tsx:75-114`），新功能不得另造格式。

---

## 3. 约束性建议（SHOULD）

1. 新增组件优先做成无框内容块（如助手消息大面板），再考虑加框；不要每块都套卡片（现状大量行级元素只用
   1px 细边框 + 极浅底）。
2. 组件命名沿用 BEM 风格修饰符：`block--modifier`（`.sidebar-status--online`、`.event-row--tool_start`、
   `.assistant-answer--pending`）。事件名直接用作修饰符后缀（`--${event.event}`，
   `ConversationThread.tsx:205`），便于按事件类型染色。
3. 侧栏若扩展，沿用 section 容器 + mono 标签 + 图标井行 的结构顺序，不要把导航做成一等公民（当前产品
   无主导航，靠单页内结构组织）。
4. 需要更多层级时，优先用 `details/summary` 折叠（思考时间线、输出文件均如此），避免弹窗泛滥。
5. 若某个自定义视觉被 3 个以上位置使用，先考虑沉淀为公共 class 再复用，而不是复制粘贴。

---

## 4. 新增设计的入口检查（Pre-Flight，设计阶段自查）

新增「页面 / 大区块 / 新组件」动工前，对照清单（每项给出现状证据位置，找不到 = 停止并回查）：

- [ ] 主色只用 cyan/green/amber/red/violet + 深底中性（未新增色相）
- [ ] 圆角：表面 8px；行内 code 6px；圆点 999px（未出现新半径系统）
- [ ] 字体：Sans/Mono 双栈，无新增字体族、无新增 web font 加载
- [ ] 阴影 = 深黑投影 +（青色 tint 可选）+ 1px 内高光；glow 仅 主按钮/运行/loader
- [ ] 组件基于 AntD（ConfigProvider 主题），图标来自 `@ant-design/icons`，无第二套体系
- [ ] 状态色映射、文案语气、数据 mono 化 与现有页一致
- [ ] 断点（980/560）、内容列宽 900px、滚动与浮层让位规则沿用
- [ ] 空态 / loading / error / disabled / running 五态齐备且用既有模式
- [ ] 动画 ≤ 2.8s 且尊重 reduced-motion；无装饰性循环动画
- [ ] 图标按钮有 aria-label/Tooltip；装饰图标 aria-hidden；实时区 aria-live
- [ ] 未改动激活页既有 class 的任何视觉规则

---

## 5. 本规则的维护

- 本三份文档（DESIGN_SYSTEM / UI_PATTERNS / CONSISTENCY_RULES）是「从代码到代码」的镜像：
  若后续有意变更设计语言（新色相、新圆角、亮色模式等），属于产品级决定，必须先改当前 UI（或经
  Decision 记录）再同步更新本目录文档；禁止先改文档后放任代码漂移。
- 发现本文档与代码不一致时，以代码为准并提交差异（文档同步 = 代码变更的一部分，见仓库
  `AGENTS.md` 最小修改原则的文档同步条款）。

---

## 6. Existing Component Reuse（既有组件复用分级）

> 结论基于全仓引用检查：`App.tsx` 只 import `ChatComposer` 与 `ConversationThread`（`App.tsx:13-14`）；
> 其余 7 个控制台组件无任何 import 方（grep 仅命中自身声明），`styles.css` 前半段（`.app-shell` 体系）
> 在激活页无消费方。

### 6.1 必须复用（MUST reuse，它们是当前页的运行骨架）

| 资产 | 文件 | 理由 |
| --- | --- | --- |
| `ConfigProvider` 主题块 | `frontend/src/main.tsx:10-38` | 唯一主题源；新组件必须从这套 token 取色取形 |
| `MarkdownRenderer` + `.markdown-body` | `components/MarkdownRenderer.tsx`、`styles.css:1388-1551` | 一切代理长文本输出的排版标准 |
| `useDeepAgentSession` | `hooks/useDeepAgentSession.ts` | 连接/事件/任务生命周期唯一实现；任何 WS 功能必须复用它而非新写 |
| `ConversationThread` 的消息结构语义（user/assistant、meta、thinking、answer、artifact） | `components/ConversationThread.tsx` | 对话类新功能直接在其上扩展 turn 类型，禁止再造第二套消息体系 |
| `ChatComposer` 的 fixed-composer + staged 上传 + Enter 提交 + 运行禁用链 | `components/ChatComposer.tsx` | 任务输入与「运行中冻结输入」的唯一交互模式 |
| `styles.css` `:root` 变量 + `.markdown-body`/`.chat-*` 公共规则 | `styles.css:3-18, 674+` | 新视觉的唯一挂载点 |

### 6.2 推荐复用（SHOULD reuse，语义完整、随时可提）

| 资产 | 出处 | 场景 |
| --- | --- | --- |
| ThinkingLoader 套件（pulse/dots/track/steps/scan） | `ConversationThread.tsx:266-291` + `styles.css:1230-1386` | 任何长流程「运行中」占位 |
| `details/summary` 折叠块 + 计数徽标 | `ConversationThread.tsx:329-341, 357-369` | 可折叠明细（思考过程/产物） |
| `.panel-kicker` / `.sidebar-label` mono 标签惯例 | `styles.css:90-98, 737-743` | 一切区块英文 kicker |
| 图标井 34/38px 语义配色 + EventIcon/FileIcon 映射 | `DESIGN_SYSTEM.md §1.8`、`ConversationThread.tsx:146-176` | 事件/文件图标 |
| `getDownloadUrl` / `requestJson` / thread 持久化 | `lib/api.ts`、`lib/thread.ts` | 下载、fetch、会话 ID |
| `Alert`（会话级错误横幅）/ `message` toast 分工 | `App.tsx:221-228` 与 `App.tsx:83-130` | 错误与反馈 |
| 空态文案与 `example-grid` 入口形态 | `ConversationThread.tsx:379-411` | 首屏空态 |

### 6.3 可以新建（MAY create new，但必须遵守 0-5 节全部规则）

1. **新页面/新视图的专用容器组件**：允许新 class，但颜色/圆角/字体/间距必须取自 0-5 节约定的值。
2. **面向未来领域的新组件**（例如研究计划视图、引用/校验面板等目前不存在的形态）：允许新建文件，
   但必须复用 6.1 的样式挂载点、语义色映射与交互模式；组件文件仍引用全局 class，不私有化配色。
3. **遗留控制台组件（`MissionComposer`/`StatusStrip`/`AgentTopology`/`EventStream`/`UploadPanel`/
   `ResultPanel`/`FileDock`）**：默认 **不复活、不迁移、不改写**。若产品确需任务控制台视图，应先在
   Decision 中立项，再决定「复活旧组件」还是「按 6.1 语义重做」；二者都属于新功能开发，不在本阶段
   范围内。它们的 class（`.console-panel`/`.metric-tile`/`.agent-node`/`.event-row` 等）仍可当作
   「同语言词汇表」参考（`styles.css:46-616`），但不能与激活页混排出新页面。

---

## 7. Reference Page（基准页）

- **Primary Reference Surface**：`App.tsx` 聊天工作台（`.chat-app-shell`），实跑方式
  `frontend/README.md`（`pnpm install && pnpm dev`，默认 `localhost:5173`，代理后端 8000）。
- 该页面 + `styles.css` + `main.tsx` = 一切视觉裁决的最终裁判。仓库存档截图
  `docs/images/deepsearch-*.jpg` 已用视觉模型核验，**确认均为当前聊天页的三个真实状态**：
  `deepsearch-agent-home.jpg`（首屏空态）、`deepsearch-network-search-result.jpg`（网络搜索运行结果）、
  `deepsearch-database-report-result.jpg`（数据库查询+报告结果）。三者可作新页面/改动的视觉基准与
  回归对照物；视觉细节核对方法见 `DESIGN_SYSTEM.md §4`（精确色值仍以代码 token 为准）。
- 遇到本文档未覆盖的视觉问题：先打开当前页面截图/实跑观察，再以代码为准给出结论；**禁止用 skill
  默认值补位**。
