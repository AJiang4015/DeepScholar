# P005 — 无认证/授权边界（教学边界）

## Status

Won't Fix（按设计；见 README「能力边界」）

## Severity

Medium

## Created / Last Updated

2026-09-02 / 2026-09-02

## Path

全系统（`app/api/server.py`、WS 端点）

## Problem

系统没有任何认证 / 授权。任何能访问服务的人都可以：

- 提交任务（`POST /api/task`）
- 订阅任意 thread_id 的 WebSocket 事件（含工具参数、搜索结果、最终报告）
- 上传文件、列出 / 下载 output 目录文件
- CORS 配置 `allow_origins=["*"]` + `allow_credentials=True`（server.py 第 65–71 行；浏览器规范下 `*` 与 credentials 组合无效，实际为宽松跨域）

README.md「能力边界」（第 325–337 行）明确声明本仓库**不覆盖**：用户登录、角色权限、多租户隔离、文件上传安全扫描、任务队列、事件持久化、评测、监控等。

## Impact

- 信息泄露（会话事件 / 报告可被他人按 thread_id 订阅）。
- 资源消耗（任意人可启动无限任务）。
- 这是**已声明的教学边界**，不是缺陷回归；错误地"修复"它会破坏教程章节与分支对照。

## Root Cause

教学优先的项目定位：README 第 325–337 行显式声明。

## Evidence

- `README.md:325-337` 能力边界声明。
- `app/api/server.py:65-71` CORS `*` 配置。
- `app/api/server.py:92-112` 任务接口无鉴权。
- `app/api/server.py:260+` WebSocket 无鉴权。

## Scope

### In Scope

- 保持边界不被"顺手"改动；任何引入认证/授权的变更必须走 Decision 并评估对教学链路的影响。

### Out of Scope

- 本问题不做修复（Won't Fix）。

## Constraints

- 教学用途：教程章节不涉及登录；引入认证会破坏章节与 git 分支（02–14）对照。

## Known Failure Modes

- 未来 Agent 误以为"缺认证是 bug"而擅自加入认证中间件（违反最小修改 + 越界，见 AGENTS.md §4）。
- 修复 P003（上传穿越）时错误地同时"加固"为认证系统。

## Candidate Solutions

1. 维持现状并记录边界（本记录）。
2. 引入认证——**被拒绝（当前阶段）**：改变教学边界，需明确需求与 Decision。

## Resolution

Won't Fix（设计边界）。若用户明确要求，另行立项并走 Architecture Change Gate。

## Related Decisions

- D003（教学边界决策）

## Related Architecture

- ARCHITECTURE.md §5（安全边界）、§9（Forbidden Changes：未经 Decision 不得引入认证）

## Related Tests

- 不适用（本问题不修复）。若未来引入认证，须新增鉴权负路径测试。

## Remaining Risks

- 若部署到公网，必须由外部反向代理 / 网关承担鉴权；部署责任不在本仓库。
