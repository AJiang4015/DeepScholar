# P2-2 S3 Deviation Report（STOP：首拍接入点与冻结契约冲突）

- 阶段：S3 执行授权后、**动手修改前**的 STOP 报告（依据用户本次指令：「如发现生命周期接入点存在 invariant 冲突 → 立即 STOP，输出 deviation report，不自行扩大设计」）
- 基线：branch `feature/runtime-p2-2-heartbeat-stale-reclaim`；HEAD `98e4e5a`（未 commit/push/merge）；S1/S2 产物保持现状未回滚
- 本轮动作：**仅只读核验 + 本报告**；`controller.py` / `server.py` 及任何生产/测试文件**零改动**

---

## DEV-1（阻塞，需裁决）— 首拍时机：`submit/accepted` vs `_execute_governed` 执行起点

**冲突内容**

| 来源 | 表述 |
|---|---|
| **本次 S3 指令** | 「task/run 生命周期增加健康写入点：**task accepted / running 首次进入时触发首拍**」 |
| **Spec Rev 2.2 §4.3(1)（L142）** | 「**`_execute_governed` 执行起点、handle 注册成功后立即写首拍**；**`submit_task` 路径 MUST NOT 写首拍**（理由：heartbeat 语义 = 执行者存活；submit 写会掩盖 `execute()` 早期失败留下的 running 行，并把 stale 基准前移到「尚未开始执行」的时刻）」 |
| **Spec §20（L623/L638）** | Gate **D13** 状态 = ✅ ratified → 决策 `D-Phase2-P2-2-004` |
| **用户此前 Decision Closure 批复原文** | 「首拍时机调整：heartbeat first beat = `_execute_governed` 执行起点；**不允许 submit_task 写首拍**」 |
| **Approved Plan §4.1 / §12（I5）** | 首拍 = `_execute_governed` handle 注册后；submit 路径不写；`_execute_bare` 不写 |
| **Spec §6.1（registry 证据）** | registry 脱节判定依赖「心跳行存在 **或** `started_at` 非空」作为「**曾真正进入执行**」的证据 |

**结论**：「task accepted」（submit/受理时刻）与冻结的 D13（执行起点）**互斥**；二者不能同时满足。

**两种解释与后果**

| 方案 | 含义 | 后果 | 与冻结面关系 |
|---|---|---|---|
| **A（推荐，D13 一致）** | 首拍 = governed 执行起点（`_execute_governed` 内、handle 注册成功后立即写；`force=True`） | 心跳存在 ⇒ 可证明「曾进入执行」；不掩盖 `execute()` 早期失败（store 读失败 → 无心跳行 → registry/legacy 回退路径按 `started_at`/`created_at` 判定）；不前移 stale 基准 | ✅ 与 D13/Spec Rev 2.2/Plan/I5 完全一致，**无需任何契约变更** |
| **B（需契约修订）** | 首拍下沉到 `gov_service.submit_task` / `controller.execute` 入口（受理即写） | ① 弱化 §6.1 registry 证据（「曾进入执行」不再成立）；② 掩盖 `execute()` 早期失败留下的 running 行；③ stale 基准前移至尚未开始执行时刻；④ 需同步修订 `D-Phase2-P2-2-004` + Spec §4.3(1)/§6.1 + Plan §4.1/I5，并重做 S2 语义与后续 S4/S5 判定 | ❌ 超出 S3 授权范围（不得自行修订 Spec/Decision） |

**为什么必须停**：该选择直接决定 S5 的 stale/registry 判定语义与 S2 已有实现的调用契约（`HeartbeatWriter.beat(force=True)` 作为首拍），属 Spec/Decision 级不变量，不属于可自行解释的实现细节。

**需要的裁决（一句话即可）**

- 选项 ①：**按 A 执行**（推荐）——首拍仍在 `_execute_governed` 执行起点，S3 指令中的「accepted」理解为「该 run 被接入执行（running 首次进入执行路径）」；
- 选项 ②：**改为 B** —— 则需先修订 `D-Phase2-P2-2-004` 与 Spec Rev 2.2（另开 Spec 修订/Decision 追加流程），S3 暂停等待该修订完成。

---

## DEV-2（非阻塞，请确认口径）— `forget` 的调用位置

- 本次指令：「**terminal cleanup 路径**调用 `heartbeat.forget(task_id)`」。
- Approved Plan §2.1/§4.1 与 S2 实现口径：`forget` 在 **`_execute_governed.finally`**（执行退出清理）调用，**不**放入冻结的 `terminalize()` funnel。
- 理由：Plan §2.3 明确 frozen funnel（`terminalize`/CAS/pending）**只读引用、不得改写**；且 LA-4 规定 controller/funnel 不触碰 health 表（`forget` 仅释放内存节流状态、不删 DB 行，技术上不违反 LA-4，但写入 frozen 函数仍属未授权修改）。
- 建议口径（推荐）：**执行退出（finally）调用 `forget`** ＝ 满足「terminal cleanup 路径」的清理意图（无论正常完成、异常、取消、超时，finally 都会执行）；`terminalize` 内部保持不变。
- 若你要求「必须在 `terminalize` funnel 内调用」→ 属修改 frozen 函数，需你显式批准该例外（否则按推荐口径执行）。

---

## DEV-3（信息性，无需裁决）— S3 包含 Plan 中 S6 的 server 装配部分

- 本次 S3 指令授权 `app/api/server.py` 仅做「创建 `HeartbeatWriter` → 注入 controller」的 lifespan 装配；Approved Plan 将 server lifespan 接线列在 **S6**。
- 影响评估：仅**顺序前移**（S6 的 scanner/sweep/periodic 部分仍留在 S5/S6），不改变任何契约；按本次授权执行即可，S5/S6 相应缩小为余下部分。
- 附带约束：`server.py` 的 `HTTP/WS` 端点与生命周期语义不变；`get_controller()` 返回 None（store 不可用）时跳过注入（fail-safe）。

---

## 推荐裁决后 S3 的精确改动面（待批准后执行）

| 文件 | 改动（additive-only） |
|---|---|
| `app/runtime/governance/controller.py` | ① `__init__` 增 `self.heartbeat_writer: Optional[HeartbeatWriter] = None`（与既有 `live_sink` 同款注入模式）；② 新增私有 `_beat(...)`（writer 为 None → no-op；异常捕获 = fail-open）；③ `_execute_governed`：**handle 注册成功后** `_beat(..., force=True)` 首拍；④ `_watchdog_loop` 每 tick `_beat(...)`（writer 内部节流）；⑤ `_execute_governed.finally` 调 `forget(task_id)`。**不改** `terminalize`/CAS/pending/`_execute_bare`/字段语义 |
| `app/api/server.py` | lifespan 中（checkpoint init 之后）：`ctl = get_controller()`；非空则 `ctl.heartbeat_writer = HeartbeatWriter(ctl._store, config=HeartbeatConfig.from_env())`；异常/None → 跳过并记日志。**不新增 endpoint、不改生命周期语义** |
| `tests/test_runtime_heartbeat.py` | 追加 S3 集成用例：首拍确实写入健康表（且 submit 阶段无行）；watchdog tick 推进后 `beat_count` 增长；异常/正常终态后 `forget` 生效（`tracked_tasks()` 清空、可再次首拍）；health 写入失败 fail-open（任务仍 completed/failed，控制流不受影响） |
| `tests/test_runtime_governance_controller.py`（如需） | 仅当 S3 断言需要同步（预期**不需要**：新增字段为可选、默认 None → 既有语义不变） |

**保持不动**：`reclaim.py`（S4）、scanner/reclaim（S5）、migration 与 9 处断言（S7）、`models.py`/`events.py`/`store.py`/`migrations.py`、P2-1 冻结面、frontend/agent/research、依赖文件、Spec/DECISION.md。

---

## 本轮 Scope 证据（零改动）

```
branch: feature/runtime-p2-2-heartbeat-stale-reclaim
HEAD:   98e4e5a（未 commit/push/merge）

git diff --name-only -- app/runtime/governance/controller.py app/api/server.py   →  （空）
S1/S2 产物保持现状：health_store.py / heartbeat.py / 0003_runtime_health.*.sql /
                    tests/test_runtime_heartbeat.py / docs/problem/P007-*.md（均未回滚）
本轮唯一写入：P2-2_S3_DEVIATION_REPORT.md
```

**状态：STOP**，未进入 S3 编码；等待 DEV-1 裁决（推荐选项 ①）与 DEV-2 口径确认后立即执行 S3。

---

## Resolution（用户裁决后追加；未修改 Spec / DECISION.md / Implementation Plan）

- **DEV-1 裁决 = 选项 ①（D13 / Spec Rev 2.2 优先）**：首拍唯一合法位置 =
  `_execute_governed → handle 注册成功 → heartbeat_writer.beat(..., force=True)`；
  **禁止** `submit_task() → heartbeat`；首拍语义固定为 **execution start / liveness evidence**，
  而非 task accepted telemetry。不修改 D13、Spec Rev 2.2、DECISION.md 或 Implementation Plan。
- **DEV-2 裁决 = finally 清理**：`_execute_governed.finally → heartbeat_writer.forget(task_id)`；
  **不得**把 `forget()` 塞进或改写 frozen lifecycle funnel（terminalize / CAS transition /
  pending terminal / event emission）；`governance_runtime_health` DB 行仍由后续
  scanner/reclaim cleanup 负责，S3 不删除 DB health row。
- **DEV-3 裁决 = 批准**：S3 前移完成 server lifespan 装配（create `HeartbeatWriter` →
  inject into existing controller singleton）；仅 additive wiring，不得新增 HTTP/WS endpoint，
  不得重构 server lifecycle。
- **执行结果**：已按上述口径完成 S3（实现 + 测试 + 静态检查），详见 S3 实施报告。
