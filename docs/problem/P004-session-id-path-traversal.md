# P004 — session_id/thread_id 未净化直接拼入目录路径

## Status

Mitigated（R1 修复：thread_id 安全字符集净化 + 服务端换新/拒绝，见 Resolution）

## Severity

Medium

## Created / Last Updated

2026-09-02 / 2026-09-03

## Path

`app/api/server.py`（`run_task`、`/ws/{thread_id}`）、`app/agent/main_agent.py`（`run_deep_agent`）

## Problem

`thread_id` / `session_id` 来自客户端请求（`POST /api/task` 的 `thread_id` 字段，server.py 第 78、100 行；WebSocket 路径 `/ws/{thread_id}` 第 260 行），未净化直接拼入：

- `output/session_{session_id}`（main_agent.py 第 61 行）
- `updated/session_{session_id}`（server.py 第 160 行、main_agent.py 第 72 行）
- LangGraph `thread_id`（main_agent.py 第 96 行）

含 `..` / 路径分隔符的 thread_id 可造成目录穿越（在 app 下创建/写入任意目录），并影响 WebSocket 事件路由与 InMemorySaver 会话键。

## Impact

- 目录创建 / 文件写入逃逸会话隔离（与 P003 部分重叠，但入口不同）。
- 畸形 thread_id 可能破坏 monitor 事件推送路由，或产生意外会话键碰撞。

## Root Cause

thread_id 被当作"标识符"使用，但未做标识符净化（仅允许安全字符集）。

## Evidence

- `app/agent/main_agent.py:61` `project_root_path / "output" / f"session_{session_id}"`
- `app/agent/main_agent.py:72` `project_root_path / "updated" / f"session_{session_id}"`
- `app/api/server.py:160` `updated_dir / f"session_{thread_id}"`
- `app/api/server.py:100` `thread_id = request.thread_id or str(uuid.uuid4())`（用户可控）

## Scope

### In Scope

- thread_id/session_id 净化：仅允许 `[A-Za-z0-9_-]`，否则拒绝或重新生成。

### Out of Scope

- WebSocket 订阅鉴权（见 P005）。

## Constraints

- 前端 `frontend/src/lib/thread.ts` 生成的 thread_id 必须兼容净化规则。
- 既有会话（InMemorySaver）在同一进程内应可继续使用。

## Known Failure Modes

- 净化规则过严导致前端既有 thread_id 失效。
- 只拒绝不重生成导致合法请求 4xx（前端需适配）。

## Candidate Solutions

1. 服务端强制生成 thread_id（忽略客户端传入，或仅接受安全字符集）——最小修改。
2. 输入校验 + 400 拒绝。
3. 维持现状——**被拒绝**：目录穿越与事件路由污染风险。

## Resolution

已修复（Mitigated，R1 / Spec 2026-09-03-R1-file-security）：

- 新增 `app/utils/session_id.py`（纯函数）：`SAFE_THREAD_ID_RE = ^[A-Za-z0-9_-]{1,128}$`，
  `is_safe_thread_id` / `sanitize_thread_id`（非法抛 InvalidThreadIdError）/
  `safe_thread_id_or_new`（非法/缺失生成 uuid4().hex）。
- server.py 接入点：
  - `POST /api/task`：非法/缺失 thread_id → 服务端生成新 uuid（前端接受响应中的 thread_id）；
  - `POST /api/upload`：thread_id 必须合法，否则 400（上传必须精确匹配后续任务会话）；
  - `WS /ws/{thread_id}`：非法 thread_id 拒绝建连并 close(1008)，不注册连接。
- 前端 `thread.ts` 生成的 uuid4 / manual-* 格式均在安全字符集内，无需改动前端。
- 测试：tests/test_session_id.py（uuid/manual 通过；`/`、`\`、`..`、空、超长、控制字符拒绝；
  safe_thread_id_or_new 非法输入生成合法 id）。

残余风险：monitor 事件推送无鉴权（P005 Won't Fix 边界未变），thread_id 净化不替代鉴权。

## Related Decisions

- D002（会话目录隔离）
- D008（R1 文件安全治理）

## Related Architecture

- ARCHITECTURE.md §5（安全边界）

## Related Tests

- 无（P006）。修复后 MUST 新增恶意 thread_id 的 API 测试：`../../x`、`a/b`、`a\b`、空串、超长串。

## Remaining Risks

- monitor 事件无鉴权推送（P005）与 thread_id 路由耦合；修复顺序需考虑（先净化 ID，再考虑鉴权）。
