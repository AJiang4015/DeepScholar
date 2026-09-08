# DeepScholar Frontend Design System（从现有代码逆向提取）

> 本文件是对当前仓库前端 **实际存在的视觉设计系统** 的逆向工程结果。
> 所有结论均来自代码证据，禁止凭经验猜测；每条重要结论都附 文件路径 + class / CSS 变量 / 组件名 + 行号。
>
> 证据范围（全部为前端实际代码，无业务逻辑改动）：
> - `frontend/src/styles.css`（1819 行，全局样式 + 全部设计变量的唯一来源）
> - `frontend/src/main.tsx`（Ant Design v5 `ConfigProvider` 主题，是 AntD 组件的 token 唯一来源）
> - `frontend/src/App.tsx`（当前唯一激活页面的组装处）
> - `frontend/src/components/*.tsx`、`frontend/src/hooks/*.ts`、`frontend/src/lib/*.ts`
>
> 文档权威顺序（与用户约束一致）：
> **当前已有 UI（激活页面实际代码） > 本提取的 Design System > 任何 skill 的默认建议**
> skill（impeccable / design-taste-frontend / ui-ux-pro-max / frontend-design）的默认建议若与本文件冲突，一律服从本文件。

---

## 0. 系统概览：一个深色「控制台 / 终端」风格的设计语言

当前项目是一个深色、单主题（`color-scheme: dark`，无亮色模式）的对话式多智能体研究控制台。
视觉 DNA 是 **「深空底色 + 极细发光网格/扫描线 + 青绿色霓虹描边 + 终端等宽字体标签 + 低透明度叠层」**：

- 近黑蓝背景 `#05070b`，其上叠加两种极细的工程网格（cyan / green，1px 线，3.5%~4% alpha，间距 76px / 80px）与一束青色斜向扫光。
- 所有内容放在带 `1px` 半透明发光描边的「面板 / 气泡」里，圆角统一 `8px`。
- 语义色少而明确：青 `#20d6ff`（主色/信息）、绿 `#5dff9f`（成功/在线/助手调用）、琥珀 `#ffc857`（警告/工具开始/文件）、红 `#ff5c7a`（错误/取消强调）、紫 `#7c8cff`（次要信息，仅预设项使用）。
- 层级标签（kicker，如 `CHAT WORKSPACE`、`THREAD`）与所有数字/ID/时间戳用等宽字体，正文用无衬线字体。

**证据（核心）**
| 项目 | 位置 |
| --- | --- |
| `:root` 全部设计变量 | `frontend/src/styles.css:3-18` |
| `body` 背景合成（网格+渐变） | `frontend/src/styles.css:28-38` |
| AntD 主题 token | `frontend/src/main.tsx:10-38` |
| 激活页面外壳 `chat-app-shell` 背景 | `frontend/src/styles.css:674-694` |

> 重要事实：仓库里同时存在两套 UI 代码。
> 1. **激活的当前页面**：`App.tsx` 渲染的聊天工作台（`.chat-app-shell`），是本次的 **Primary Reference Surface**。
> 2. **遗留控制台页面**：`styles.css` 前半部分（`.app-shell` 三栏 workspace）及 7 个未被任何文件 import 的组件（`MissionComposer` / `StatusStrip` / `AgentTopology` / `EventStream` / `UploadPanel` / `ResultPanel` / `FileDock`）。它们与当前页面共享同一套 `:root` token 和 8px 面板语言，但在当前 App 中 **未被引用**（证据：全仓库 grep 仅在自身文件出现；`App.tsx` 只 import `ChatComposer` 与 `ConversationThread`）。
> 因此本文件把激活聊天页作为基准；遗留控制台样式作为「同一设计语言的既有词汇表」记录，供复用判断（见 `FRONTEND_CONSISTENCY_RULES.md` 第 6 节）。

---

## 1. Visual Language 视觉语言

### 1.1 背景 Background

| token/值 | 用途 | 证据（class / 位置） |
| --- | --- | --- |
| `--bg: #05070b` | 全局底色（近黑、微蓝） | `styles.css:5`；`html { background }` `styles.css:24-26` |
| 背景合成：`linear-gradient(180deg, rgba(5,7,11,.78), #05070b)` + `repeating-linear-gradient(90deg, rgba(32,214,255,.04) 0 1px, transparent 1px 76px)` + `repeating-linear-gradient(180deg, rgba(93,255,159,.035) ... 76px)` | body 默认背景：深色渐变 + cyan/green 1px 细网格（周期 76px） | `styles.css:28-38` |
| 聊天外壳背景（同套路，周期 80px，透明度更低） | `.chat-app-shell` | `styles.css:674-684` |
| 装饰层 `::before`（fixed，`pointer-events:none`）：一束青色斜扫光 `linear-gradient(115deg/116deg ...)` + 横向扫描细线（每 8/9px 一条 `rgba(255,255,255,.018)`） | `.app-shell::before` / `.chat-app-shell::before` | `styles.css:52-61`、`686-694` |
| 聊天外壳额外：`radial-gradient(circle at 78% 12%, rgba(32,214,255,.11), transparent 28%)` | 右上角青气辉 | `styles.css:691-693` |
| 输入区底部渐隐（透明 → 底色 86% → 98%） | `.chat-composer` 顶部渐隐，让消息在固定输入区下淡出 | `styles.css:1617-1618` |

规则：**背景永远是深色层 + 可选的 1px 工程网格/扫光装饰**。装饰层全部 `pointer-events:none`、`position:fixed`，绝不拦截交互。

### 1.2 Surface 面板表面

| class / 值 | 用途 | 证据 |
| --- | --- | --- |
| `--surface: rgba(10,15,24,.88)` | 标准面板底 | `styles.css:6` |
| `--surface-strong: rgba(15,23,35,.96)` | 更强面板底（代码中 `.console-panel` 未直接用，但 token 已定义；composer 用近似值） | `styles.css:7`；`.composer-shell` `rgba(10,15,23,.96)` `styles.css:1624` |
| `.console-panel` = 面板底渐 `linear-gradient(180deg, rgba(255,255,255,.035), transparent 34%)` + `--surface` | 遗留面板：顶部 1 条 3.5% 白色高光渐隐到透明 | `styles.css:159-165` |
| 表面配方（通用）：低 alpha 底色 + 上边缘极淡白色内高光 + `backdrop-filter: blur(18px)` | 玻璃感但克制 | `.console-panel` `styles.css:160-164`；`.chat-sidebar` `background:rgba(7,11,18,.9)` `blur(18px)` `styles.css:705-708` |
| 消息气泡 `background: rgba(12,18,28,.84)` | `.message-bubble` | `styles.css:1054-1062` |
| 用户气泡 `background: rgba(32,214,255,.1)` | `.chat-message--user .message-bubble` | `styles.css:1064-1070` |
| 内嵌行底色 `rgba(255,255,255,.035)` / `.04` / `.07` | 次级行/事件/文件卡片 | `.agent-node` `styles.css:343-352`；`.thinking-event` `styles.css:1146-1154`；`.artifact-card` `styles.css:1563-1573` |

### 1.3 Border 边框

- 默认描边宽度恒为 **1px**；颜色走「语义色低 alpha」配方，不出现纯白实线、不出现 2px+ 描边。
- `--line: rgba(113,247,255,.18)`、`--line-strong: rgba(113,247,255,.34)`：cyan 系发光线（面板边框/强交互边框）`styles.css:8-9`。
- 通用中性边框：`rgba(255,255,255,.07~.09)`（行级元素、卡片）。
- 语义边框随状态切换 hue（见 1.4/1.5）。
- 内凹高光「1px ring」：`box-shadow: inset 0 0 0 1px rgba(255,255,255,.035)`，让毛玻璃面板边缘更锐利。证据：`.console-panel` `styles.css:162`、`.thread-chip` `styles.css:111`。

### 1.4 Primary Accent 主强调色

| token | 值 | 角色 | 证据 |
| --- | --- | --- | --- |
| `--cyan` | `#20d6ff` | **唯一主强调色**（AntD `colorPrimary`），用于：链接/主要按钮/选中态/图标主色/面板标题图标/事件图标默认态/滚动条/装饰线 | `styles.css:12`；`main.tsx:14` |
| AntD `Button.primaryShadow` | `0 0 24px rgba(32,214,255,.26)` | 主按钮青色外发光（克制，不是大面积 glow） | `main.tsx:31` |
| AntD `Input.activeBorderColor` | `#20d6ff`（聚焦）/ `hoverBorderColor: #5dff9f`（悬停为绿） | 输入控件焦点/悬停边框 | `main.tsx:34-35` |

### 1.5 Status colors 状态色（语义与事件类型一一绑定）

| token | 值 | 语义 | 典型出现（class + 行号） |
| --- | --- | --- | --- |
| `--green` | `#5dff9f` | 成功 / 在线 / 运行中 / `assistant_call`（助手调用）/ AI 侧 | `.metric-tile--online/--live` `styles.css:235-240`；`.sidebar-status--online` `styles.css:792-796`；`.event-row--assistant_call` `styles.css:429-433`；`.thinking-event--assistant_call` `styles.css:1167-1171`；`.message-avatar` `styles.css:1040-1052`；`run-indicator--live` `styles.css:879-883` |
| `--amber` | `#ffc857` | 警告 / `tool_start`（工具开始）/ 文件与文档 / 上传中 / 取消 | `.metric-tile--warn` `styles.css:242-246`；`.event-row--tool_start` `styles.css:423-427`；`.file-icon` `styles.css:541-550`；`.artifact-icon` `styles.css:1575-1584`；`.attachment-uploading` `styles.css:1716-1723`；`.thinking-event--tool_start/task_cancelled` `styles.css:1173-1189` |
| `--red` | `#ff5c7a` | 错误 / 异常计数 / 取消动作强调 | `.metric-tile--error` `styles.css:248-252`；`.event-row--error` `styles.css:435-439`；`.sidebar-status--error` `styles.css:804-808`；`.send-button--cancel` `styles.css:1674-1684` |
| `--violet` | `#7c8cff` | 次要信息（AntD `colorInfo`）；唯一使用处：预设任务芯片边框/底 `rgba(124,140,255,.075/.2)`、文本 `#cbd5ff` | `styles.css:16`；`.preset-chip` `styles.css:268-278`；`main.tsx:18` |
| `--cyan` 默认 | 默认事件 `ClockCircle` 等中性态 | `.event-row` / `.thinking-event` / `.sidebar-status` 默认 `styles.css:402-421、1146-1165、758-790` |

**状态色配方（重要、需复刻的规律）**：每个语义色出现时几乎都按同一 alpha 配方展开（以 red 为例，`styles.css:248-252`）：
- 图标文字 = 语义色本体；
- 边框 = 语义色 ~30-38% alpha；
- 底色 = 语义色 ~8-10% alpha。
（cyan 0.22-0.45 边框 / .07-.12 底；green .24-.35 / .08；amber .26-.35 / .08；red .34-.62 / .10-.24）

### 1.6 Text hierarchy 文本层级与颜色

| 文本色 token | 值 | 用途 |
| --- | --- | --- |
| `--text` | `#eef7ff` | 主文本（近白、带蓝味） |
| `--muted` | `#8fa2b7` | 次级文本（灰蓝） |
| 强调白 `#ffffff` / `#f4fbff` / `#f3fbff` | markdown `strong`、md 标题、示例卡片标题 | `styles.css:1437-1440, 1407, 1001` |
| 代码/数据亮青 `#d6f7ff` / `#dffcff` / `#aeefff` / `#9ceeff` / `#cbeaff` | 等宽输出、事件 data、行内 code | `styles.css:456-466, 1205-1212, 1483-1491` |
| 灰蓝次级 `#9fb4c3` / `#9fc1d0` / `#64758a`（placeholder） | 示例 small、loader 辅助文字、textarea placeholder | `styles.css:1006-1013, 1269-1273, 1645-1647` |
| 青味浅色 `#c5f7ff`/`#d9faff`/`#e6fbff`/`#d8f8ff`/`#cbd5ff` | markdown em、thinking summary、表头、端地址、预设文本 | `styles.css:1442-1444, 1113, 1533-1536, 831-838, 274` |

### 1.7 Typography 字体

- 声明了 **两套字体栈**，但仓库 **没有加载任何字体文件**（无 `@font-face`、无 fonts link、无 fontsource 依赖；grep 全仓证据：`styles.css:37` 与 `main.tsx:23-26` 之外的 font 相关仅剩类内引用）。因此真实渲染回退到系统字体（PingFang SC / Microsoft YaHei / system-ui；等宽回退 Consolas / monospace）。**新增页面不得引入新字体体系，也不得擅自添加 web font 加载**（如确需，先与当前 UI 基准对齐后再决策）。

| 角色 | font-family 栈 | 证据 |
| --- | --- | --- |
| UI/正文 Sans | `"IBM Plex Sans", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif` | `styles.css:37`（body）、`main.tsx:23-24`（AntD `fontFamily`） |
| 数据/标签 Mono | `"JetBrains Mono", "SFMono-Regular", Consolas, monospace`（AntD 另加 `'Liberation Mono'`） | `styles.css` 共 25 处（95, 119, 127, 231, 258, 387, 446, 464, 524, 589, 595, 740, 749, 789, 835, 995, 1050, 1088, 1132, 1196, 1209, 1271, 1342, 1488, 1721 行，栈完全一致）、`main.tsx:25-26` |
| 全局继承 | `button, input, textarea { font: inherit }` | `styles.css:40-44` |

**Mono 的唯一职责**：kicker 标签、ID/thread_id、数字指标、时间戳、事件名、文件路径、工具调用 JSON、状态计数、时长、端地址。（全为「机器可读数据」。）
**Sans 的唯一职责**：标题、正文、按钮文案、说明。

**字号层级（全部硬编码 px，无 token）**，来自激活页：

| 用途 | 字号/行高/字重 | class + 行号 |
| --- | --- | --- |
| 侧栏品牌 H1 | 30px / 1.05 / 800 | `.sidebar-brand h1` `styles.css:710-715` |
| 顶部 H2（对话工作台） | 24px | `.chat-topbar h2` `styles.css:861-865` |
| thread ID | 22px mono / 800 | `.thread-id` `styles.css:745-751`（display block） |
| markdown H1 / H2 / H3 / H4 | 24 / 21 / 18 / 16，800 字重 | `.markdown-body h1-h4` `styles.css:1403-1431` |
| 面板标题 H2 / 主智能体名 | 18px | `.panel-heading h2` `styles.css:175-180`；`.main-agent-node strong` `styles.css:320-322` |
| markdown 正文 | 16px / 1.88 | `.markdown-body` `styles.css:1388-1393` |
| 消息正文（用户气泡 p） | 默认 / 1.75 | `.message-bubble p` `styles.css:1092-1097` |
| kicker / 微型 mono 标签 | 11-12px mono / 700-800 | `.panel-kicker` `styles.css:90-98`；`.sidebar-label` `styles.css:737-743`；`.example-copy span` `styles.css:993-998` |
| 次级说明 | 12-13px，`--muted` | `.file-copy span` `styles.css:568-572`；`.sidebar-status span` `styles.css:782-785` |

布局级 hero 字号仅存在于遗留页：`.brand-block h1 { font-size: clamp(30px, 4vw, 58px) }` `styles.css:75-80`。

### 1.8 Iconography 图标体系

- 唯一图标库：**`@ant-design/icons`**（outlined 风格），无其他图标依赖。`package.json:12`。
- 图标从不裸放：一律放进 **带语义描边与底色的方形图标井（icon well）**，正方形 30-42px、`border-radius: 8px`：
  - 30px：`.thinking-event-icon` `styles.css:1156-1165`
  - 34px：`.metric-tile > .anticon` `styles.css:210-220`、`.sidebar-status > .anticon` `.agent-mini-list .anticon` `styles.css:770-780`、`.artifact-icon` `styles.css:1575-1584`
  - 36px：`.message-avatar` `styles.css:1040-1052`
  - 38px：`.event-icon` `styles.css:412-421`、`.example-icon` `styles.css:975-985`、`.file-icon` `styles.css:541-550`
  - 42px：`.agent-node-icon` `styles.css:354-363`
- 井内图标颜色 = 语义色本体；井边框 = 语义色 ~24-34% alpha；井底 = 语义色 ~8% alpha（配方同 1.5）。
- 装饰性图标通常带 `aria-hidden`（如 `App.tsx:163`），纯操作图标按钮必有 `aria-label` 或 Tooltip（见 `UI_PATTERNS.md`）。
- 事件名 → 图标映射规则：`assistant_call→BranchesOutlined`、`tool_start→ToolOutlined`、`session_created→FileSearchOutlined`、`task_result→CheckCircleOutlined`、`task_cancelled→StopOutlined`、`error→CloseCircleOutlined`、其余→`ClockCircleOutlined`。证据 `frontend/src/components/ConversationThread.tsx:146-166`（`EventStream.tsx:25-42` 有同名副本）。
- 文件扩展名 → 图标：`.pdf→FilePdfOutlined`、`.md→FileMarkdownOutlined`、其他→`FileTextOutlined`。证据 `ConversationThread.tsx:168-176`、`FileDock.tsx:36-44`。

### 1.9 Radius 圆角

- **无圆角 token，全部硬编码 `8px`**（含 AntD `borderRadius: 8`，`main.tsx:22`）。8px 是「一切表面」的默认：面板、卡片、气泡、按钮（AntD）、输入、列表行、图标井、chip、empty 框、summary 条。
- 例外（仅 2 处，且都有明确理由）：
  - `6px`：markdown 行内 code 块 `styles.css:1483-1491`（文本级小元素）；
  - `999px`：**只用于「发光圆点/指示条」类动态元素**：loader-pulse 圆点、loader-dots 圆点、loader-track 细条 `styles.css:1275-1323`；AntD `shape="circle"` 按钮（整圆按钮）。
- 特殊形状：`.agent-links span` 只有下三边 + `border-radius: 0 0 8px 8px`（仅底部圆角的连接条）`styles.css:324-336`；markdown `blockquote` `border-radius: 0 8px 8px 0`（左侧 3px cyan 竖条 + 右圆角）`styles.css:1474-1481`。

### 1.10 Shadow / Glow

| 值 | 用途 | class + 行号 |
| --- | --- | --- |
| `--shadow: 0 18px 60px rgba(0,0,0,.42)` | 遗留面板通用投影 | `styles.css:17`、`.console-panel` `styles.css:162` |
| `0 14px 44px rgba(0,0,0,.28)` | 助手侧「透明大面板」下的内容承载（气泡） | `.message-bubble` `styles.css:1060` |
| `0 12px 38px rgba(32,214,255,.08)` | 用户气泡：**带青色 tint 的投影**（主色投影惯例） | `.chat-message--user .message-bubble` `styles.css:1068` |
| `0 18px 60px rgba(0,0,0,.46)` + `inset 0 0 0 1px rgba(32,214,255,.04)` | 固定输入区 Composer 壳 | `.composer-shell` `styles.css:1625-1627` |
| `0 0 24px rgba(32,214,255,.26)` | AntD 主按钮光晕（`primaryShadow`） | `main.tsx:31` |
| `box-shadow: 0 0 18px rgba(32,214,255,.55)` | loader-track 行进光 | `styles.css:1322` |
| loader-pulse 外扩光环 `rgba(93,255,159,.34)`（由 keyframes 动画成 12px 扩散环） | 运行呼吸点 | `styles.css:1275-1282, 1356-1364` |
| `inset 0 0 0 1px rgba(255,255,255,.035)` | 表面内高光 ring | `.thread-chip` `styles.css:111`、`.console-panel` `styles.css:162` |

**Glow 纪律**：光晕只出现在「运行中/主操作/加载」三处语义；不把 glow 当常驻装饰。

### 1.11 Opacity / 透明度配方

- 表面一律用带 alpha 的 rgba 表达层级，**alpha 档位（常见值）**：0.035-0.04（中性内嵌行）、0.07-0.09（卡片边框/行底）、0.16-0.18 透明黑（empty 底）、0.28-0.34（代码黑底）、0.62-0.84（面板主底）、0.88-0.98（强表面/侧栏/输入壳）。证据分布见 1.2-1.5 各 class。
- 禁用态统一 `opacity: .55`（`.preset-chip:disabled` `styles.css:289-292`）。
- `backdrop-filter: blur(18px)` 用于长驻玻璃层：`.console-panel` `styles.css:163`、`.chat-sidebar` `styles.css:707`。

---

## 2. Design Tokens 汇总表（代码中真实存在的 token）

### 2.1 CSS 变量（`styles.css:3-18`，唯一定义处）

| 变量 | 值 | 类别 |
| --- | --- | --- |
| `--bg` | `#05070b` | 颜色/背景 |
| `--surface` | `rgba(10, 15, 24, 0.88)` | 颜色/表面 |
| `--surface-strong` | `rgba(15, 23, 35, 0.96)` | 颜色/表面 |
| `--line` | `rgba(113, 247, 255, 0.18)` | 颜色/边框 |
| `--line-strong` | `rgba(113, 247, 255, 0.34)` | 颜色/边框 |
| `--text` | `#eef7ff` | 颜色/文本 |
| `--muted` | `#8fa2b7` | 颜色/文本 |
| `--cyan` | `#20d6ff` | 颜色/主强调 |
| `--green` | `#5dff9f` | 颜色/状态 |
| `--amber` | `#ffc857` | 颜色/状态 |
| `--red` | `#ff5c7a` | 颜色/状态 |
| `--violet` | `#7c8cff` | 颜色/状态 |
| `--shadow` | `0 18px 60px rgba(0, 0, 0, 0.42)` | 阴影 |
| （隐式）`color-scheme: dark` | dark | 主题 |

### 2.2 AntD `ConfigProvider` token（`main.tsx:10-38`，唯一主题注入点）

| token | 值 | 说明 |
| --- | --- | --- |
| `algorithm` | `theme.darkAlgorithm` | 全库深色算法 |
| `colorPrimary` | `#20d6ff` | = `--cyan` |
| `colorSuccess` | `#5dff9f` | = `--green` |
| `colorWarning` | `#ffc857` | = `--amber` |
| `colorError` | `#ff5c7a` | = `--red` |
| `colorInfo` | `#7c8cff` | = `--violet` |
| `colorBgBase` | `#05070b` | = `--bg` |
| `colorBgContainer` | `rgba(12,18,28,.86)` | 与 `.message-bubble` 底一致 |
| `colorBorder` | `rgba(113,247,255,.18)` | = `--line` |
| `borderRadius` | `8` | 全局圆角 |
| `fontFamily` | IBM Plex Sans 栈 | 见 1.7 |
| `fontFamilyCode` | JetBrains Mono 栈 | 见 1.7 |
| components.Button.controlHeightLG | `46` | 大按钮高 |
| components.Button.primaryShadow | `0 0 24px rgba(32,214,255,.26)` | 主按钮 glow |
| components.Input.activeBorderColor | `#20d6ff` | 输入聚焦 |
| components.Input.hoverBorderColor | `#5dff9f` | 输入悬停 |

> **不存在**的 token（必须视为「约定而非 token」）：spacing 数字、radius 数字（除 AntD borderRadius）、字体尺寸、z-index、转场时长、断点像素值。它们全部是散落在 `styles.css` 里的硬编码常量；新代码要沿用「值」，而不是发明新体系（见 `FRONTEND_CONSISTENCY_RULES.md`）。

### 2.3 约定的 spacing 节奏（硬编码，无变量）

从激活页提取的间距值集合：**4 / 6 / 8 / 9 / 10 / 12 / 14 / 16 / 18 / 20 / 22 / 24 / 26 / 28** px，另有转场用大间距 46px（turn 之间）。
- 组件内常用 gap：8、10、12（图标井/文本、状态行、事件行）；16（section 内、面板间距）。
- 面板 padding：12-16px；侧栏 padding 20px；聊天主区顶部 padding 18px 28px。
- 大间距：`.conversation-thread` gap 46px `styles.css:1015-1018`；`.conversation-turn` gap 22px `styles.css:1020-1023`。
- 高度刻度（min-height，px）：26（loader steps chip）、30（附件 pill / 微控件）、34-42（图标井）、40（run-indicator / 圆形发送钮）、44（新会话钮 / preset chip / thinking summary 条）、52-58（文件/产物/状态行）。

### 2.4 转场与动画时长（硬编码）

| 时长 | 用途 | 证据 |
| --- | --- | --- |
| `180ms ease` | 悬停/聚焦边框+底色+位移（chip、卡片、取消钮） | `.preset-chip` `styles.css:278`、`.example-card` `styles.css:958` |
| `1.1s ease-in-out` | loader-dots 三点跳动（120ms/240ms 阶梯延迟） | `styles.css:1291-1305` |
| `1.6s ease-out` | loader-pulse 绿点扩散 | `styles.css:1281` |
| `1.7s cubic-bezier(.65,0,.35,1)` | loader-track 光条扫动 | `styles.css:1323` |
| `2.8s ease-in-out` | `assistant-answer--pending` 全幅扫描光 | `styles.css:1246-1249` |
| `smooth` | 自动滚动行为（`scroll-behavior` / `scrollTo`） | `App.tsx:76-79`、keyframes `styles.css:664-672` |
| 心跳 | WS `ping` 每 25s | `useDeepAgentSession.ts:94-98` |
| 文件轮询 | 运行中 2.5s / 空闲 6s | `useDeepAgentSession.ts:186-190` |

---

## 3. 语义对照速查（写代码时照着用）

| 你想表达 | 用色 | AntD token | 典型位置 |
| --- | --- | --- | --- |
| 品牌/主操作/链接/信息/中性事件 | cyan `#20d6ff` | colorPrimary | `.example-card` 边框 hover、主按钮、链接 `.markdown-body a` `styles.css:1509-1516` |
| 成功/在线/正在运行/AI 角色/助手调用 | green `#5dff9f` | colorSuccess | 见 1.5 |
| 工具开始/警告/文件/上传中 | amber `#ffc857` | colorWarning | 见 1.5 |
| 错误/取消确认/异常 | red `#ff5c7a` | colorError | 见 1.5 |
| 预设/次级信息 | violet `#7c8cff` | colorInfo | `.preset-chip` |
| 次级文字 | `--muted #8fa2b7` | - | 全局小字 |
| 数据/标签/时间 | mono 栈 + 上述浅青 `#d6f7ff`-类 | fontFamilyCode | `.message-meta`、`.event-meta` 等 |
| 编码/JSON 原始数据 | mono + 深黑底 `rgba(0,0,0,.28/.34)` | - | `.thinking-event code` `styles.css:1205-1212`、`.markdown-body pre` `styles.css:1493-1500` |

---

## 4. 视觉核验记录（docs/images 截图对照，识图引擎：modlens + 百炼 qwen3.8-max）

本节记录用视觉模型实际读取仓库截图 `docs/images/deepsearch-*.jpg` 后与代码结论的对照结果。
**截图确认为「当前聊天工作台」的三个不同状态，不是遗留控制台页**（此前把截图当作不确定历史素材的
疑问已消除）；截图与第 1-3 节的代码证据**全部吻合，无冲突**。

| 截图文件 | 画面状态 | 与代码证据的对应 |
| --- | --- | --- |
| `docs/images/deepsearch-agent-home.jpg` | 首屏空态：完整侧栏 + 待命 + TASK EXAMPLES 示例卡 | 对应 `App.tsx:141-251` 与 `ConversationThread.tsx:379-411` 的空态分支 |
| `docs/images/deepsearch-network-search-result.jpg` | 网络搜索任务运行完毕的对话：用户气泡 + 助手（思考时间线 7 条 + markdown 答案） | 对应 `.conversation-turn` / `.thinking-block` / `.assistant-answer` 全套 |
| `docs/images/deepsearch-database-report-result.jpg` | 数据库查询+Markdown 生成报告结果：含输出文件卡（826 B）与工具 JSON 轨迹 | 对应 `.artifact-card` / `execute_sql_query` 事件呈现（`ConversationThread.tsx:178-264`） |

### 4.1 截图中可见并被代码证实的视觉事实

1. **整体**：近黑底 + 青色(teal)/绿色强调 + mono 小标签与代码文本（OCR 多次辨认到 `THREAD`/`AGENTS`/
   `ENDPOINTS`/`CHAT WORKSPACE`/`TASK EXAMPLES` 等全大写 mono 标签）。对应 `styles.css:3-38` 与
   `DESIGN_SYSTEM.md §1.1/§1.6`。
2. **右上装饰**：主区上部有一道青色斜向装饰亮线（识图模型标注为装饰、非控件）。对应
   `.chat-app-shell::before` 的 116deg 青色扫光 `styles.css:686-694`。
3. **顶栏状态胶囊**：右上角「待命」胶囊（空闲态）。对应 `.run-indicator` `styles.css:867-877`。
4. **消息身份结构**：用户消息右对齐、无头像，meta 为「你 hh:mm」；助手消息带方形「AI」标记（绿、mono），
   meta 左为「DeepSearch Agents」、右为状态文案如「已同步 · 用时 00:51 / 01:28」。
   对应 `.message-avatar` `styles.css:1040-1052`、`.message-meta` `styles.css:1081-1090`、
   `ConversationThread.tsx:419-434, 314-327`。
5. **思考过程折叠块**：标题「深度研搜过程」+ 右侧 mono 计数徽标（7 / 9）；块内时间线可内部滚动
   （标题显示 7 但视口内仅见 4 条 → 印证 `.thinking-timeline { max-height:360px; overflow-y:auto }`
   `styles.css:1136-1144`）。每条为「图标井 + mono 事件名与时间 + 事件文案 + JSON 数据块」。
   事件 JSON（如 `{"tool_name":"网络搜索工具","args":{...}}` 与完整 SQL/Markdown payload）以等宽小字显示。
   对应 `.thinking-event` 系列 `styles.css:1146-1222` 与 `ConversationThread.tsx:178-228`。
6. **工具/结果事件色**：工具执行条目带工具图标、任务完成条目带完成图标；与
   `EventIcon` 映射（tool→Tool / task_result→CheckCircle）及 1.5 语义色一致。
7. **最终答案**：markdown 排版正文（标题、要点列表、段落）位于时间线下方，符合 `.markdown-body`
   `styles.css:1388-1551`。
8. **输出文件卡**：「输出文件」折叠块 + 每行 文件图标井 + mono 文件名 + 大小（594 B / 826 B）+ 下载钮。
   对应 `.artifact-card/.artifact-icon/.artifact-download` `styles.css:1553-1607`。
9. **输入区**：底部悬浮 composer，placeholder「向 DeepSearch Agents 发送任务...」，左侧附件/新建圆钮、
   右侧发送圆钮（图像中 send 图标隐约可见）。对应 `.chat-composer` `styles.css:1609-1619`。
10. **侧栏运行态数据联动**：运行后侧栏计数变化（工具调用 4、助手调度 1、异常 0）与顶栏回到「待命」，
    印证 `useDeepAgentSession.ts:286-297` 的 stats 与事件闭环。

### 4.2 识图不确定项（截图核对时的已知噪声）

- 截图颜色为视觉模型近似观察（hue/深浅级别），**精确 hex 一律以 `styles.css:3-18` 与 `main.tsx:14-21`
  为准**；
- 视口底部内容会被悬浮输入区遮挡（OCR 对「适用人群/用户评价」等被遮标题标为不确定）；
- 低分辨率小图标只能按形状识别，图标语义以 `ConversationThread.tsx:146-176` 的映射为准。

> 结论：截图是三份文档之外最直观的「当前页面 = Primary Reference Surface」佐证；未来新增视觉回归或
> 与代码不符时，可用同样方式（modlens 识图或实跑截图）交叉核验。
