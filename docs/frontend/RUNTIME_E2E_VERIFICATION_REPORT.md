# Runtime E2E Verification Report

> 目的：证明上一轮实现的「任务终态恢复 / run 隔离」Backend→Frontend 状态闭环在真实运行环境中成立。
> 验证方式：真实启动后端(FastAPI/uvicorn :8000，sqlite governance/checkpoint) + 真实 React 页面
> （vite dev :5173 / :5174）+ 真实 Chrome 通过 CDP 驱动页面交互与断连；事件证据来自页面内 WebSocket
> 探针与 CDP Network 帧；durable 状态证据来自 `GET /api/tasks*`。
>
> **阶段约束执行情况**：未新增 UI、未引入新组件/依赖/CSS、未修改 `DESIGN_SYSTEM.md` /
> `UI_PATTERNS.md`；为让被测闭环在真实时序下成立，对 `useDeepAgentSession.ts` 内部做了 3 处收敛性修复
> （见 §7，纯 hook 逻辑、无视觉变化）。后端零改动。

---

## 1. Test Matrix

| # | 场景 | 判定 | 证据要点 |
| --- | --- | --- | --- |
| 1 | 正常任务 submit→task_started→…→task_result→idle→最终答案 | **PASS** | ~2s 完成；答案 `7` 渲染；timeline 3 事件；meta `已同步 · 用时 00:01` |
| 2 | 用户主动取消 running→cancel→cancelling→task_cancelled→idle | **PASS** | meta `已取消 · 用时 00:00`；回到待命 |
| 3 | 断线但任务继续执行→恢复→不重复/不污染→task_result 正常结束 | **PASS** | 断连窗口后重连；最终答案出现一次；无重复文案 |
| 4 | 断线期间任务完成→live task_result 丢失→重连→GET /api/tasks→恢复 completed，不停留在研搜中 | **PASS** | durable completed（断线期间观测）；恢复后 meta `已完成 · 用时 00:00`，提示文案「任务已完成（连接中断期间结束）…」 |
| 5 | 断线期间任务失败→durable failed→UI 停止 running、显示 error Alert、不误标已取消 | **PASS** | durable failed（WinError 183 受控触发）；恢复后 meta `执行失败 · 用时 00:02` + 顶部 error Alert；无「已取消」 |
| 6 | timed_out：durable timed_out→UI 显示「超时」语义，不显示「已取消」 | **PASS**（受控策略注入触发，2/2 收敛） | live 先到 `task_cancelled`；经 durable 校正收敛为 meta `任务超时 · 用时 00:04`、文案「任务超时（连接中断期间结束）。」 |
| 7 | budget_exceeded：durable budget_exceeded→UI 显示「预算超限」，不显示「已取消」 | **PASS**（受控策略注入触发，3/3 通过） | meta `预算超限 · 用时 00:00` + error Alert（真实原因）+ 文案「任务达到执行预算上限…」 |
| 8 | Run isolation：陈旧 run 事件不得污染当前 run | **PASS** | 注入不同 run_id 帧→被过滤（计数/文案不变）；注入匹配锚点帧→被接受（计数+1、可见）；答案不重复 |
| 9 | 重连幂等：多次 reconnect 不重复建任务/终态/文案/Alert | **PASS** | 运行中 2 次断连+完成后 1 次断连；单一答案、计数不变、无重复 Alert |
| 10 | UI 回归：shell/sidebar/topbar/thread/composer/responsive 无回归 | **PASS** | 桌面关键区域齐备；移动端(≤980)侧栏隐藏、composer 可见；5 张示例卡 |

NOT EXECUTED：无。BLOCKED：无。

---

## 2. 实际执行步骤（要点）

1. **环境**：后端 = `uvicorn app.api.server:app`（:8000，仓库既有健康实例；SQLite governance/checkpoint，真实 LLM
   openai-compatible，任务约 1.7–3s 完成）；前端 = `pnpm dev`（:5173 连 8000；:5174 连受控后端 8002）；
   浏览器 = Headless Chrome 152 + CDP(Node 原生 WebSocket) 驱动真实页面：填输入→点发送→读 DOM 状态。
2. **断连手段**：Chrome `Network.emulateNetworkConditions(offline)` 不会断开已建立的 WS（实验证实）；
   改用**页面内对活跃 WebSocket 执行 `close()`**（对后端等价于断网：per-thread 队列事件丢弃、2s 自动重连），
   并在需要时用 1.4s 周期压住自动重连直到 durable 终态出现后再放行。
3. **受控终态触发（#5/#6/#7，全部为真实治理链路）**：
   - failed：在 `output/session_{thread}` 应创建处预置同名文件 → `mkdir(exist_ok=True)` 抛错 → controller 终态 `failed`；
   - timed_out：策略 `wall_clock_timeout=4` + 模型端点指向不可达地址（:8002 专用后端，真实 hang）→ watchdog → `timed_out`；
   - budget_exceeded：策略 `max_llm_calls=0` → 计数前置拒绝 → `budget_exceeded`。
   由于 UI 暂无 policy 选择器，测试在页面加载前注入 `window.fetch` 包装，把 policy 附到真实的 `POST /api/task`
   请求（产品代码未改，属测试装置）。
4. **断言前等待**：对「终态语义」使用轮询直到 meta/文案出现「超时/预算」等语义（因 durable 落库晚于 live 终态，
   见 §7 竞态修复），避免在收敛前的瞬时态误判。

---

## 3. Backend observed state（节选，均来自真实 durable/HTTP）

- S1：`status=completed / terminal_reason=completed`（submit 后约 1–3s）；S2：cancel → 无事件竞态，回 idle；
- S4：断线期间轮询到 `status=completed`（此时 live 事件已不可达）；S5：
  `{"s":"failed","r":"failed","e":"[WinError 183] 当文件已存在时，无法创建该文件。…output\\session_failtest-…"}`；
- S6：`timed_out / timed_out`（wall_clock_timeout=4 起效）；S7：`budget_exceeded / budget_exceeded`，
  error=`governance hard limit exceeded: llm_calls 0 >= 0`；
- 额外发现（关键）：`POST /api/task` 响应与 TaskRecord 的 `run_id`（治理侧）**与 monitor 事件信封中的
  `run_id`（main_agent 本地生成）不一致**（实证两组值不同）。影响与处理见 §7。

## 4. WebSocket observed events（页面内探针实录序列）

- S1/S4 正常链路：`governance_error:no_active`（握手时无活跃任务，正常）→ `task_started` → `session_created`
  → `task_result`（任务执行完成）；
- S6（超时）：… → `task_cancelled`（“任务已取消”，live 面无法区分，durable 为 timed_out）→ 前端 durable
  校正后展示「任务超时」；
- S7（预算）：… → `error`（真实原因文本）→ durable 校正为「预算超限」；
- 无重复帧、无乱序（per-thread 串行队列）；断连窗口内服务端不发/丢事件（无持久化补发）与代码语义一致。

## 5. Frontend observed state（DOM 实测节选）

- S1 答案区实际渲染 `7`；meta `已同步 · 用时 00:01`；思考折叠计数 3（task_started/session_created/task_result）；
- S4 恢复文案 `任务已完成（连接中断期间结束），最终回复未保留在本会话；本次产物见下方「输出文件」。`；
- S5 meta `执行失败 · 用时 00:02` + 顶部 Alert 原文（WinError 183…）；
- S6 meta `任务超时 · 用时 00:04`；S7 meta `预算超限 · 用时 00:00` + Alert；
- S8 陈旧帧注入后计数 3→3 且文案不可见；匹配帧 3→4 且可见；
- S9 两轮运行中重连 + 完成后重连：答案恒 1 条、计数不变、无新 Alert。

## 6. PASS / FAIL / BLOCKED

全部 **PASS**；无 FAIL/BLOCKED。#6/#7 注明“受控策略注入”前提（无 UI 入口），但 durable + 事件 + UI 语义
三面均为真实后端产生并经真实页面验证，非伪造。

## 7. 验证过程中发现并修复的问题（前端 hook，仅此 3 处）

1. **run 隔离锚点修正（必须，否则所有正常任务卡死）**：先前按 `POST /api/task` 响应 `run_id` 过滤事件，
   但实证 monitor 信封 `run_id ≠ TaskRecord.run_id`（后端两套生成）。正确做法改为**以事件流自身的
   `task_started.run_id` 作为当前 run 锚点**（每次新 run 自愈重建），其余事件按锚点比对丢弃（S8 双向验证）。
2. **终态语义收敛重试**：live 终态（error/task_cancelled）常先于 governance durable 落库发出；若只查一次会看到
   `running` 而错过纠正，UI 停留「已取消/已同步」通用态。改为限次快速重试（250ms×20）直至 durable 终态可读，
   使「超时/预算/失败」语义在约 1–4s 内收敛到 UI（S6/S7 验证）。
3. **既有健壮性微调**（沿用上一轮已交付设计）：重连后与 live 终态后统一走幂等 `doReconcile`
   （taskStatusAppliedRef 去重，S9 验证不重复）。

未发现需 UI/CSS 层修改的问题；视觉/布局/断点/可访问性无回归（S10）。

## 8. Remaining Risks（如实记录）

1. **monitor run_id ≠ TaskRecord.run_id（后端语义）**：治理与 monitor 两套 run 身份。前端已用事件锚点规避，
   但建议后端后续统一（monitor envelope 采用治理 run_id），否则跨进程/审计关联仍靠 task_id。
2. **「连接未断但终态发生在任何 monitor 事件之前」的窗口**（如 run_deep_agent 入口异常，如 S5 的 mkdir 失败）：
   该路径无 live 终态事件，UI 只有在 reconnect（或下一次 doReconcile 触发）时才能收敛；实测若一直保持连接，
   UI 会停在「研搜中」直到重连/新建/取消。需要时可用「运行中低频任务状态轮询」兜底（后端无改动方案），属后续项。
3. **policy 无 UI 入口**：timed_out/budget_exceeded 只能经策略触发；当前前端不提供 policy 选择，产品层面需新功能
   立项（如「执行模式」）才有常规用户入口。本次用请求注入完成验证。
4. **Chrome offline 模拟不切断既有 WS**（工具限制），断连验证用客户端 close 替代，与真实断网的后端视角等价。
5. 各场景使用较简单的无工具问答以稳定控制时长；工具链路（db/ragflow/tavily）事件流不在此次矩阵内
   （后端工具事件在 R3 monitor 面，前端既有渲染未改），如需可另立工具型 E2E。

---

### 附：运行环境快照

- 健康后端 :8000（uvicorn + sqlite + 真实 LLM）；受控后端 :8002（模型端点不可达，用于超时/失败确定性触发）
- 前端 vite :5173/:5174（同一源码，:5174 指向受控后端）
- Chrome 152 headless + CDP；驱动与探针脚本为临时工具，验证后已清理，仓库无残留
