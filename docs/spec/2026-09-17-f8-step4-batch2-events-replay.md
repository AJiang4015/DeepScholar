# F8 Step 4 Batch 2 Spec — since_seq / WS Catch-up / Replay API / Event Ordering（Spec-only）

> 状态：SPEC-ONLY（未实现）。依据：Batch 1 Post-Gate Audit **PASS** + F8 Step4 Audit/Spec
> （`2026-09-17-f8-step4-runtime-history-replay.md`）§12.4–§12.5。
> 本批次**只**覆盖事件观测面接线：durable 回放读取 + live/durable 关系 + 顺序/去重。
> **不实现**：sweeper / startup recovery hook / frontend 改造 / multi-instance / Redis/Kafka /
> Event Sourcing / research 功能。

## 1. 问题与目标

Batch 1 已把 lifecycle 事件 durable 写入 `governance_events`（单写者，task-scoped seq、event_id
幂等）。Batch 2 补读取面：

1. **WS reconnect catch-up**：客户端断线/刷新后，`/ws/{thread_id}` 握手带 `since_seq`
   （governance_seq 游标）→ 先回放 durable 事件（升序、含 next cursor）再转 live；
   全新观察者 → 最近 N 条（默认 200）。
2. **Replay API**：`GET /api/threads/{thread_id}/events?since_seq=&limit=`（durable 升序，
   `next_seq` 分页；`event_id` 幂等去重；seq 空洞 → `gap=true` 标记触发 catch-up 语义）。
3. **live 关系**：live 事件维持 R3 monitor envelope（monitor_seq 不动）；durable 侧新增
   governance_seq 字段只出现在 replay/durable API 响应层（**不在 WS live payload 造双 seq**）。
4. **Ordering**：durable ordering source 唯一 = governance events 单写者分配的
   `(task_id, seq)`；客户端以 `event_id` 去重，不用 monitor_seq 判断 durable 顺序。

## 2. 术语与边界（不得混淆）

| 名 | 定义 | 属主 |
|---|---|---|
| monitor_seq | 进程内 live 分配全序（R3 现状） | monitor（只 live） |
| governance_seq | durable replay cursor（task-scoped `(task_id,seq)`） | governance events 单写者 |
| event_id | publish 时生成；live/durable/replay 同一身份（幂等键） | governance events |
| since_seq | WS 握手游标（governance_seq 语义） | 本批 API/WS |

- 禁止：monitor 持久化第二套 seq；replay 用 monitor_seq；WS live payload 混入 governance_seq。

## 3. API / WS 契约（设计）

```
GET /api/threads/{thread_id}/events?since_seq=&limit=&task_id?=
  → {"events":[{event_id, task_id, thread_id, run_id, governance_seq, event_type, payload, durable, created_at}],
     "next_seq": int|None, "gap": bool}
  · durable 升序（thread 内按 (task_id,seq) 或 thread 维全序 —— 采用 thread 维
    ORDER BY (task_id,seq) 的稳定游标；since_seq 为 last-seen governance_seq；
    空洞：下一条 seq > since_seq+1 且非跨 task → gap=true（触发 catch-up 拉取））
  · limit 上限（默认 200，max 1000）；task_id? 可选过滤。
WS 握手 GET /ws/{thread_id}?since_seq=<governance_seq>
  → 先回放（同 API 语义，replay_to_live=true）→ 之后转 live（R3 monitor 原样推送；
    live 信封含 event_id；客户端按 event_id 去重）
```

- `event_id` 幂等：回放/断线重发同 event_id → 客户端丢弃重复；
- seq 空洞跨 task 不视为数据空洞（不同 task 的 seq 独立）：gap 仅在
  `(task_id, seq)` 同 task 内检测（服务端已单写者保证无洞 → 服务端恒 gap=false；
  客户端仍按文档防御）。

## 4. Sequencer / Ordering 结论（承 Batch1 audit）

- 不再引入“独立进程级 Sequencer”：Batch 1 的 events 单写者已在每次 INSERT 内于事务中分配
  `(task_id,seq)`（UNIQUE + 冲突重试），single-instance 下即唯一 ordering source；
  并发发布（同一 task 的 started 与 terminal 来自不同异步点）由 UNIQUE(task_id,seq)+重试保证全序；
- 需要补（Batch 2 实现内）：**回放读取排序与游标**（index (task_id,seq) 已存在）、
  分页 next_seq、WS 握手回放粘合、live 转发时保持 event_id 一致性。
- 判定：不需要独立服务/队列；仍是 governance 内小模块（events.read + ws 接线）。

## 5. Failure / Idempotency / Crash

- durable 读失败 → API 500/WS 握手失败可重试（不伪装 live）；event 写仍 fail-open（Batch1）；
- 重放中后端 crash：客户端 with since_seq 重新握手 → 从持久游标继续（无丢、可重）；
- `durable=false` live 预览事件：不进入 durable 回放（无 governance_seq）；Batch 2 仅文档化，
  预览通道（如需）延后；
- WS 多连接：manager 已按 thread 多实例管理；disconnect 只移除当前实例（现状语义）。

## 6. 测试（Batch 2 实现 Gate 用）

- SQLite 逻辑：replay 分页/游标/next_seq；event_id 去重；gap 判定；since_seq 边界；
- PG Gate：真实 PG 上 thread 内 durable 升序回放与游标稳定性；并发发布 (task_id,seq) 全序；
  since_seq 空洞（人为删一行模拟）→ gap 标记；重连 catch-up 等价于全量（从0）；
- WS 层（无凭据环境以 service/replay 函数级代替，标注）：
  live 信封不含 governance_seq；握手 since_seq 回放后再 live；
- Regression：Step1–4 Batch1 + Audit 全量。

## 7. Non-goals（写死）

sweeper / startup recovery 产品 hook / frontend 实现 / multi-instance / lease / heartbeat /
Redis/Kafka/Event Sourcing / research F3–F7 / monitor payload schema 修改 / token hard budget。

## 8. Gate Criteria

- Step 1–3 + Phase A–F + Batch1 + Audit 全绿（sqlite + PG）；
- 新增 replay/WS 测试：sqlite 逻辑 + 真实 PG（升序/游标/gap/idempotency/catch-up）；
- ruff / format / compileall；scope audit 无越界；
- **不实现 Batch 3 内容**（durability_gap 表/全量 replay UI 等延后）。

## 9. Decision（审计 + 范围）

Batch 1 Post-Gate Audit **PASS**（5 边界均有代码事实与最小测试证据）。
Batch 2 建议范围按 §3–§4 实施（since_seq/WS catch-up/Replay API/order semantics）；
无需独立 Sequencer 组件。等待下一步实现指令。
