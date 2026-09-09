"""Multi-Session domain package.

Session = Task 上层的对话容器业务域（不拥有 Runtime lifecycle 权威）：
- 元数据 / 生命周期（ACTIVE/ARCHIVED）持久化在 governance store 的 sessions 表族；
- Session ↔ Task 归属 = thread_id 派生关联（session_id == thread_id）；
- 隔离：一切查询/操作以 thread_id=session_id 为作用域，由 governance/research 既有查询保证。

分层：API → app/session/service.py → app/session/store.py + app/runtime/governance（task 只读/提交）。
"""
