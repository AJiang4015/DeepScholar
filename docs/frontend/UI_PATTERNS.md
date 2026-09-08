# DeepScholar Frontend UI Patterns（现有页面布局与交互模式）

> 逆向自当前激活页面（`App.tsx` 组装）与全局样式 `frontend/src/styles.css`。
> 每个模式都给出：模式名 / 出现位置（组件、class）/ 行为描述 / 代码证据（文件:行）。
> 本文只描述「现状如何」，规则与禁止项见 `FRONTEND_CONSISTENCY_RULES.md`。

---

## 1. 页面级布局模式（Primary Reference Surface）

### 1.1 Shell：双栏全高应用框架

```
+------------------------------------------+------------------------------+
| .chat-sidebar（292px，sticky 全高，z-index:2）| .chat-main（剩余宽度）          |
|  brand / 新建按钮 / THREAD / 状态列表        |  .chat-topbar（标题+运行指示）    |
|  AGENTS / ENDPOINTS（margin-top:auto 贴底） |  .chat-alert（错误横幅，条件渲染） |
|  背景 blur(18px)，右边框 --line             |  .chat-stream-panel（唯一滚动区）  |
|                                          |  .chat-composer（fixed 贴底浮层） |
+------------------------------------------+------------------------------+
```

- 容器：`.chat-app-shell` = `display:grid; grid-template-columns: 292px minmax(0,1fr); min-height:100dvh`，背景网格+扫光装饰 `::before`。证据 `styles.css:674-694`；JSX `App.tsx:142`。
- 侧栏 `.chat-sidebar`：`position:sticky; top:0; height:100dvh; z-index:2`，`rgba(7,11,18,.9)` + `blur(18px)`，右 `1px --line` 分隔，padding 20px，内容纵向 `flex column gap:16px`。证据 `styles.css:696-708`、JSX `App.tsx:143-207`。
- 主区 `.chat-main`：`grid-template-rows: auto auto minmax(0,1fr); height:100dvh; overflow:hidden; padding:18px 28px 0`，`z-index:1`。证据 `styles.css:840-849`、`App.tsx:209`。
- **整个页面不出现页面级滚动条**：滚动只发生在 `.chat-stream-panel`（消息区）内部。`App.tsx` 用 `ref` + `requestAnimationFrame` + `scrollTo({behavior:"smooth"})` 在每次 turns 变化后滚到底部。证据 `App.tsx:47-81`。

### 1.2 内容列宽：900px 单列「信笺」排版

- `.chat-topbar`、`.chat-alert`、`.chat-stream-panel`、`.chat-composer` 全部 `max-width:900px` 且 `margin: 0 auto`，在主区剩余宽度内居中。证据 `styles.css:851-859`、`885-889`、`891-900`、`1609-1619`。
- 固定输入区与 900px 列精确对齐：`.chat-composer { left: calc(292px + (100vw - 292px)/2); width: min(900px, calc(100vw - 292px - 72px)); transform: translateX(-50%) }`。证据 `styles.css:1609-1619`。

### 1.3 侧栏信息架构（自上而下固定顺序）

1. Brand：`panel-kicker`「DEEPSEARCH」+ H1「深度研搜」+ 副标题「对话式多智能体研究台」（`App.tsx:145-148`；`.sidebar-brand` `styles.css:710-721`）。
2. 「新建研搜」主操作：`Button block`，cyan 边框+浅底（`.new-chat-button` `styles.css:723-728`，`App.tsx:150-152`），功能 = `resetSession` + 清空 turns（`App.tsx:132-137`）。
3. THREAD 块：mono 标签 + 8 位 thread_id 大字（`.sidebar-section/.sidebar-label/.thread-id` `styles.css:730-751`；`App.tsx:154-159`）。
4. 状态列表 `.sidebar-status-list`：4 行「图标井+名称+值」：WebSocket（在线绿/离线琥珀）、助手调度、工具调用、异常（>0 变红）。证据 `styles.css:753-808`、`App.tsx:161-182`。
5. AGENTS 块：`agent-mini-list` 三行图标+名（网络搜索/数据库查询/RAGFlow）。`styles.css:810-825`、`App.tsx:184-200`。
6. ENDPOINTS 块：`margin-top:auto` 贴侧栏底，两行 mono 端地址。`.sidebar-endpoints` `styles.css:827-838`、`App.tsx:202-206`。

**侧栏通用小节容器** `.sidebar-section`：1px `rgba(255,255,255,.08)` 边框、8px 圆角、`rgba(255,255,255,.035)` 底、padding 12px。证据 `styles.css:730-735`。

### 1.4 顶部栏与运行指示

- `.chat-topbar`：左侧 kicker「CHAT WORKSPACE」+ H2「深度研搜对话」；右侧 `.run-indicator`（40px 高胶囊：空闲 = `CheckCircleOutlined` + 「待命」中性灰；运行 = `BranchesOutlined` + 「研搜中」，绿框绿底）。证据 `styles.css:851-883`、`App.tsx:210-219`。
- 错误横幅 `.chat-alert`（AntD `Alert type="error" showIcon`，居中 900px），仅 `session.lastError` 存在时渲染。证据 `App.tsx:221-228`。

### 1.5 对话流排版（turn / message）

```
.conversation-thread（gap 46px）
└─ .conversation-turn（gap 22px，用户+助手成对）
   ├─ .chat-message--user      （右对齐）
   │    └─ .message-bubble     max-width min(560px, 82%)，cyan 底/描边
   └─ .chat-message--assistant （左对齐，36px 「AI」头像 绿）
        └─ .message-bubble     border:0/透明，宽 min(820px, calc(100% - 48px))
             ├─ .message-meta  （DeepSearch Agents | 状态·用时，mono 12px）
             ├─ details.thinking-block  深度研搜过程 [N]
             │    └─ .thinking-timeline > li.thinking-event--{event}
             ├─ .assistant-answer | .assistant-answer--pending（含 ThinkingLoader）
             └─ details.thinking-block.artifact-block  输出文件 [N]
                  └─ .artifact-shelf > .artifact-card
```

- 证据：CSS `.conversation-thread/.conversation-turn` `styles.css:1015-1023`；`.chat-message--user/--assistant` `styles.css:1025-1038`；`.message-bubble` 三态 `styles.css:1054-1079`；JSX 组装 `frontend/src/components/ConversationThread.tsx:413-438`。
- **助手消息是「无框大面板」**：占满 820px，内嵌小块（thinking / answer / artifacts）；**用户消息是「有色小气泡」**（右对齐、560px 封顶）。这是本页最强的左右身份区分。
- 每条消息带 `.message-meta`：左「你 / DeepSearch Agents」、右时间或状态（如「生成中 · 思考 00:42」「已同步 · 用时 01:03」「已取消 · 用时 …」）。证据 `ConversationThread.tsx:314-327, 419-425`；`.message-meta` `styles.css:1081-1090`。
- 状态时间线（`details` 折叠）默认 `open`（运行中或有事件时）；摘要行左侧标题+图标、右侧 mono 计数徽标（`strong`，cyan）。证据 `ConversationThread.tsx:329-341`；`.thinking-block summary` `styles.css:1107-1134`。

### 1.6 空态与示例入口（首次进入）

- `turns.length===0` 时渲染 `.conversation-empty`（透明、无边框、居中，min-height `clamp(430px, calc(100dvh - 330px), 620px)`）：kicker「TASK EXAMPLES」+ H3 + 说明 + `.example-grid` 2×2（最后一张横跨整行）。
- 每张 `.example-card` 是 **button**：38px 图标井 + 三行文本（mono 工具标签 / 15px 标题 / 2 行截断的提示文案）。点击把示例 prompt 填入输入框（`onUseExample={setQuery}`）。
- 证据：`ConversationThread.tsx:379-411`；CSS `.conversation-empty/.empty-examples/.example-grid/.example-card` `styles.css:903-1013`；`App.tsx:230-235`（`onUseExample={setQuery}`）。

### 1.7 固定输入区（composer overlay 模式）

- `.chat-composer` **fixed 浮层**贴在视口底，左右对齐 900px 内容列；顶部有 34px 渐隐区 + 从底到上渐变的背景，保证消息滚入其下时自然淡出。证据 `styles.css:1609-1619`。
- 消息滚动区给 composer 让位：`.chat-stream-panel { padding: 26px 0 210px; scroll-padding-bottom: 210px }`；560px 以下改为 166px。证据 `styles.css:891-901`、`1778-1780`。
- `.composer-shell`：cyan 1px 描边壳、`rgba(10,15,23,.96)` 底、8px 圆角、12/14 padding；内含：
  - 无框自增高 `textarea`（min 68 / max 180px，可纵向拖大，placeholder「向 DeepSearch Agents 发送任务...」，`resize:vertical`，disabled=运行中）；证据 `ChatComposer.tsx:109-122`、`styles.css:1631-1647`。
  - `.composer-toolbar`：左侧 `composer-left-actions`（新建会话 +、选择附件，均 40px 圆形 icon 按钮带 Tooltip）；右侧发送/取消圆钮（40px，primary cyan glow；运行中变红底「取消」`send-button--cancel`）。证据 `ChatComposer.tsx:124-168`、`styles.css:1649-1684`。
  - 附件 pills 在壳上方 `.attachment-strip`：已上传 = 绿色 pill，待上传 = 琥珀 `--pending` pill，上传中追加「附着中...」mono 文案。证据 `ChatComposer.tsx:86-107`、`styles.css:1686-1723`。

### 1.8 断点与响应式

| 断点 | 行为 | 证据 |
| --- | --- | --- |
| 激活聊天页 | | |
| ≤ 980px | `.chat-app-shell` 变单列；`.chat-sidebar` `display:none`；`.chat-main` 全宽（padding 16px 14px 0）；`.chat-composer` 改为 `left:14px; right:14px; width:auto; bottom:12px` 无 transform | `styles.css:1731-1758` |
| ≤ 560px | brand H1 28px；状态列表单列；`.chat-topbar` 纵向；`.chat-stream-panel` 底 padding 166px；空态 top 对齐；example 单列；气泡全宽；头像 32px；`.thinking-event` 单列并隐藏图标井 | `styles.css:1760-1818` |
| 遗留控制台页（当前未渲染，仅供对照） | | |
| ≤ 1280px | workspace 变 2 列，右栏转 3 列横排 | `styles.css:618-627` |
| ≤ 900px | app-shell padding 14px、header 纵向、全部单列、状态 2 列 | `styles.css:629-646` |
| ≤ 560px | 状态 1 列 | `styles.css:648-662` |

> 结论：**断点体系并未统一**（聊天页用 980/560，遗留页用 1280/900/560）。新功能加在当前页面体系下应沿用 980/560；如需触达遗留页断点必须另行说明。

### 1.9 字体与颜色（回顾性结论，细节见 DESIGN_SYSTEM.md）

UI 文案为简体中文；英文只用于 **mono kicker 标签与品牌名**（`DEEPSEARCH`、`CHAT WORKSPACE`、`TASK EXAMPLES`、`THREAD`、`AGENTS`、`ENDPOINTS`、`MAIN`）。状态文本用「·」作分隔符（例：`生成中 · 思考 00:42`，`ConversationThread.tsx:317`）。

---

## 2. 组件清单（现状目录）

### 2.1 激活组件（当前页面在渲染，新功能优先复用）

| 组件 | 文件 | 说明 / 视觉模式 |
| --- | --- | --- |
| `App`（页面骨架） | `frontend/src/App.tsx` | 状态机：turns/session 同步（`App.tsx:50-67`）、自动滚底、toast 文案全集 |
| `ConversationThread` | `components/ConversationThread.tsx` | 空态示例入口 + 成对 turn；内含 `AssistantMessage`、`ThinkingTimeline`、`ArtifactShelf`、`ThinkingLoader`、`EventIcon/FileIcon` |
| `ChatComposer` | `components/ChatComposer.tsx` | 固定输入区；antd `Upload`（`beforeUpload:()=>false` + 自管 staged/上传）、textarea、发送/取消圆钮 |
| `MarkdownRenderer` | `components/MarkdownRenderer.tsx` | `react-markdown` + `remark-gfm`；链接强制 `target=_blank rel=noreferrer`（`MarkdownRenderer.tsx:13-21`）；容器 `.markdown-body` |
| `useDeepAgentSession` | `hooks/useDeepAgentSession.ts` | 连接/重连/心跳/事件流/文件轮询/取消/上传去重/统计；事件上限 `MAX_EVENTS=120`（`useDeepAgentSession.ts:13,116`） |

### 2.2 遗留组件（存在但当前未被任何文件 import，勿当作激活功能）

`MissionComposer`、`StatusStrip`、`AgentTopology`、`EventStream`、`UploadPanel`、`ResultPanel`、`FileDock`（均在 `frontend/src/components/`）。
它们实现旧的三栏控制台页（`.app-shell` / `.console-panel` / `.workspace-grid` 等 class，`styles.css:46-616`），与当前页共用同一 token 语言。**新增功能默认不复活它们**；仅在确需「任务控制台视图」时评估复用（规则见 `FRONTEND_CONSISTENCY_RULES.md` §6）。

### 2.3 AntD 组件使用面（现状）

`ConfigProvider` 注入主题（`main.tsx`）；实际使用：`Button`（block/圆钮/`shape="circle"`/`loading`/icon、`href` 下载钮）、`Alert`（全宽错误横幅）、`Tooltip`（图标按钮均包裹）、`Upload`（原生于 AntD 组件树）、`message`（经 `AntApp.useApp()` 调用，**非** `message` 静态方法）、`Empty`（遗留页）。所有自定义视觉都通过追加全局 class 完成（如 `.new-chat-button`），**从不覆盖 AntD 组件内部样式**。

---

## 3. 交互与状态模式

### 3.1 悬停 hover / 聚焦 focus

- 可点卡片/chip 悬停统一动作：**边框亮起（转向 cyan 系 alpha 0.42-0.45）+ 底色抬升（+~4% alpha）+ `translateY(-1px/-2px)` 上浮 + 移除 outline 改由边框表达**。证据：`.preset-chip:hover` `styles.css:281-287`；`.example-card:hover` `styles.css:961-969`。
- 取消按钮 hover：红边加深 0.38→0.62、红底 0.16→0.24。`.send-button--cancel:hover` `styles.css:1680-1684`。
- **focus-visible**：以上两类卡片/按钮显式处理并 `outline:none`（键盘可达）；其余控件依赖 AntD 默认 focus 样式与浏览器焦点环。证据 `styles.css:281-287, 961-969, 1680-1684`。
- 链接 hover：markdown 链接悬停下划线 `.markdown-body a:hover` `styles.css:1514-1516`。
- **没有**自定义 `:active` 按压位移；按压反馈交给 AntD 与浏览器默认。

### 3.2 禁用 disabled

- 全局文案不变、仅语义降级：`.preset-chip:disabled { cursor:not-allowed; opacity:.55 }` `styles.css:289-292`。
- 任务运行中整体冻结输入面：textarea `disabled={isRunning}`（`ChatComposer.tsx:112`）、附件钮 `disabled={isRunning||isUploading}`（`ChatComposer.tsx:148`）、发送钮 `disabled={isRunning ? isCancelling : !canSubmit}`（`ChatComposer.tsx:160-161`）、示例卡片禁用（遗留预设 `MissionComposer.tsx:52`）。

### 3.3 加载 loading

- 圆钮加载：`loading={isCancelling}` 旋转图标（`ChatComposer.tsx:162`）；遗留 `Button loading` 同理（`MissionComposer.tsx:64`、`UploadPanel.tsx:63`）。
- 文字加载：「附着中...」mono 琥珀文案（`.attachment-uploading`，`ChatComposer.tsx:105`）。
- **没有全局 spinner**；长时间任务用「思考加载器套件」（见 3.6）。

### 3.4 运行中 running（核心模式）

运行期 = 页面「绿意」期，三处呼应：
1. 顶栏 `.run-indicator--live` 变绿底绿字 + 图标换成 Branches（`styles.css:879-883`）。
2. 助手消息底部显示 `.assistant-answer--pending`：绿色细边框 + 内部全幅**青色扫描光动画**（`::before` + `loader-scan 2.8s`）；其内部渲染 `ThinkingLoader`。证据 `styles.css:1230-1249, 1346-1354`、`ConversationThread.tsx:343-355`。
3. 思考时间线实时追加事件并自动滚底（`ConversationThread.tsx:181-190`）；顶部 meta 每秒更新「思考 00:xx」时长（`ConversationThread.tsx:300-314`）。

### 3.5 思考加载器套件 ThinkingLoader（可复用 kit）

结构 `.thinking-loader`（`ConversationThread.tsx:266-291`，CSS `styles.css:1251-1344`）：
- `.loader-status`：绿呼吸点 `.loader-pulse`（扩散环动画 1.6s）+「正在研搜」+ mono `.loader-duration`「已思考 mm:ss」+ 三点 `.loader-dots`（cyan，1.1s 阶梯）。
- `.loader-track`：2px 圆角细条，青色-绿色渐变光条往返扫（1.7s）。
- `.loader-steps`：三个 mono chip「理解问题 / 调度工具 / 汇总答案」，cyan 细边。
- 无障碍：容器 `aria-live="polite"` + `aria-label="正在生成回复"`；装饰点 `aria-hidden`。

### 3.6 成功 success / 完成

- 顶栏回「待命」（CheckCircle）；任务结果事件 → `task_result` 后 `isRunning=false`（`useDeepAgentSession.ts:125-130`）；气泡 meta 变「已同步 · 用时 …」。
- 事件行 `task_result` 图标 `CheckCircleOutlined`（绿）；最终文本经 `MarkdownRenderer` 渲染进 `.assistant-answer`。
- 即时反馈走 AntD message（toast，右上角默认位）：提交「任务已启动，执行过程会显示在对话中」、上传「已上传 N 个文件」等。证据 `App.tsx:86,96,109,116-119,126-129`。

### 3.7 错误 error

- **会话级错误**：红色 `Alert` 横幅（`.chat-alert`，页顶居中）承载 `lastError`（连接异常/解析失败/轮询失败等）。证据 `App.tsx:221-228`、`useDeepAgentSession.ts:144-151`。
- **动作级错误**：`message.error(...)` toast（提交/取消/上传失败，`App.tsx:109,118,128`）。
- **事件级错误**：`.thinking-event--error` / `.event-row--error`（红图标井），异常计数行 `.sidebar-status--error`。
- 取消流：`cancelTask` → `status:"cancelled"` 立即收尾 / `"cancelling"` 提示「取消请求已发送，正在等待当前调用结束」；事件 `task_cancelled` 出现后助手消息标「已取消」。证据 `useDeepAgentSession.ts:223-242`、`App.tsx:113-120`、`ConversationThread.tsx:315-318`。

### 3.8 空态 empty

| 空态 | 呈现 | 证据 |
| --- | --- | --- |
| 无对话 | 示例任务入口（3.6 上文的 hero empty） | `ConversationThread.tsx:379-411` |
| 思考中无事件 | 「等待后端推送执行事件」（ClockCircle 灰蓝小字） | `ConversationThread.tsx:192-199` |
| 无输出文件 | 「暂无输出文件」 | `ConversationThread.tsx:230-238` |
| 结果未到 | pending 面板文案「任务完成后会在这里显示最终回复。」 | `ConversationThread.tsx:348-354` |
| 遗留页 | AntD `Empty PRESENTED_IMAGE_SIMPLE` + 自定义说明，包在虚线框 `.empty-console/.compact-empty` 内 | `EventStream.tsx:59-65`、`ResultPanel.tsx:32-38`；`.empty-console` `styles.css:600-612` |

空态文字都是「指引下一步 / 说明状态」的祈使句，不玩梗、无装饰。

### 3.9 滚动 scrolling

- 滚动容器自带**青色细滚动条**：`.chat-stream-panel { scrollbar-color: rgba(32,214,255,.36) transparent; scrollbar-width: thin }`；`.event-stream` 同款 `rgba(32,214,255,.45)`（遗留）。证据 `styles.css:391-400, 891-901`。
- 消息流变化 → RAF 平滑滚底（`App.tsx:69-81`）；时间线新增事件 → RAF 即时滚底（`ConversationThread.tsx:181-190`）。
- 列表级溢出：`.event-stream max-height:650px`、`.thinking-timeline max-height:360px`、`pre max-height:180px`（内部滚动）`styles.css:391-399, 1136-1144, 468-474`。
- 页面外壳 `overflow-x:hidden` 防横滚（`.app-shell` `styles.css:48`）。

### 3.10 实时更新 realtime

事件流闭环：WebSocket `/ws/{thread_id}` → `monitor_event` → `setEvents`（截断 120 条）→ `App` 的 effect 把最新 `events/files/isRunning/result` **回填到最后一轮 turn**（`App.tsx:50-67`）→ 渲染层滚动/展开。心跳 `ping` 25s（`useDeepAgentSession.ts:94-98`）；断线 2s 后自动重连并显式走 `reconnecting` 态（`useDeepAgentSession.ts:154-165`）；文件列表轮询 2.5s（运行）/6s（空闲）（`useDeepAgentSession.ts:186-190`）；thread_id 持久化在 `localStorage`（`lib/thread.ts:1-24`）。

### 3.11 动画纪律（现状事实）

- 全部动画集中在「运行/加载」语义：pulse / dots / track / scan 四个 loader 动画 + 卡片上浮 180ms。
- 无入场动画、无滚动视差、无 hover 波纹、无布局动画（layout animation）；页面切换/列表增删无过渡。
- 全站尊重 `prefers-reduced-motion: reduce`：把 transition/animation/scroll-behavior 全部压到 1ms（`styles.css:664-672`）。
- 装饰伪元素 `::before/::after` 一律 `pointer-events:none`。

---

## 4. 遗留三栏控制台页模式（对照参考，当前未渲染）

旧 `styles.css:46-616` 定义了一套完整的三栏任务控制台（`.workspace-grid`：左 = MissionComposer + UploadPanel，中 = StatusStrip + AgentTopology + EventStream，右 = ResultPanel + FileDock）。与当前页共享：
- 面板统一 `.console-panel`（1px 发光线、8px、blur、上高光、`--shadow`）。
- 面板头统一 `.panel-heading`：左 kicker + H2，右操作/图标（`.panel-heading-icon` cyan 22px 或圆钮 `.icon-button`）。
- 状态指标 `.status-strip > .metric-tile`（6 宫格：WebSocket/任务态/工具/助手/产物/异常）。
- 路由拓扑 `.agent-hub`（MAIN 节点 → 三条 `.agent-links` 连线 → 3 个 `.agent-node`）。
- 任务输入 `.mission-textarea`（mono）+ `.preset-chip` 预设 + primary 启动钮。
- 事件流 `.event-stream`、结果 `<pre class="result-console">`、上传 Dragger、文件列表 `.file-list`。

若未来需要把这些面板类功能重做进当前页面，视觉层应复刻 2.1/2.2 的组件语义而非直接搬 DOM 结构。

---

## 5. 运行态视觉实录（docs/images 截图核验）

> 用视觉模型（modlens + 百炼 qwen3.8-max）实读 `docs/images/deepsearch-network-search-result.jpg`
> 与 `docs/images/deepsearch-database-report-result.jpg`。**两张截图都是当前聊天页的「任务运行完毕」
> 状态**（非遗留控制台页），用于佐证第 1/3 节的布局与交互模式在真实运行态下的呈现。

### 5.1 网络搜索任务结果（network-search-result）

视觉顺序（自上而下）与代码结构一一对应：
1. 侧栏（同空态）：THREAD `b5814811`、WebSocket 已连接、助手调度 1、工具调用 4、异常 0
   （= 运行结束后 stats 保留，`useDeepAgentSession.ts:286-297`）。
2. 顶栏「深度研搜对话」+ 右侧「待命」胶囊（已回空闲，`run-indicator` 非 live）。
3. 用户消息（右对齐小气泡）：meta「你 21:03」+ 提问正文。
4. 助手消息块：meta「DeepSearch Agents 已同步 · 用时 00:51」；「深度研搜过程 7」折叠块展开，
   内部时间线条目：`tool_start`（含完整 JSON args）→ `task_result`（任务执行完成）；随后是 markdown
   答案正文（教程特点/适用人群/用户评价等小节）。这与 `AssistantMessage` 的结构
   （details.thinking-block → .assistant-answer，`ConversationThread.tsx:329-355`）完全一致。
5. 底部悬浮 composer（placeholder 可见，遮住了答案尾部 → 印证 §1.7 的浮层与 scroll-padding 让位设计）。

要点：**思考时间线计数（7）与实际可视条目（约 4）不一致**，因为块内 `max-height:360px` 内部滚动
（`styles.css:1136-1144`）；这是设计预期（长轨迹收纳），不是 bug。

### 5.2 数据库查询 + 报告生成结果（database-report-result）

视觉顺序：
1. 更早一轮的助手消息尾部：附「输出文件 1」卡（594 B，即上一轮产物）→ 证明每轮 turn 的产物块
   独立收纳在各自 assistant 消息内（`ConversationThread.tsx:357-369`）。
2. 用户消息（右对齐）：「你 21:58」+ 药品库存查询指令。
3. 助手消息：meta「已同步 · 用时 01:28」；「深度研搜过程 9」时间线内可见
   `execute_sql_query`（工具 JSON args 内是完整 SELECT 语句）与「Markdown 文档生成工具」
   （args 内是整篇 markdown 内容）→ **工具 payload 以等宽代码块完整呈现**，是当前页对「可观测执行轨迹」
   的核心呈现方式（`thinking-event code` `styles.css:1205-1212`）。
4. `task_result`「任务执行完成」→ 答案正文（含 .md 文件说明）→ 「输出文件 1」卡（826 B，
   `库存大于100的药品查询结果.md`）→ 悬浮 composer。

要点：两张截图共同验证的 **meta 文案格式**：`已同步 · 用时 mm:ss`（已完成态）；头像侧「AI」方形绿标。
截图与代码的对应关系无任何冲突项。
