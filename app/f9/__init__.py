"""F9 — Evidence-Driven Adaptive Research Loop（orchestration plane）。

F9-P0 编排层包（docs/spec/2026-09-19-f9-p0-evidence-driven-research-loop.md Rev2、
docs/plan/2026-09-20-f9-p0-implementation-plan.md Rev2）。Batch 1 = Research State
Projection（只读/deterministic/bounded，见 projection.py）。后续 Batch 依次加入
gap detection / judge / follow-up plan / targeted research / round orchestrator。
不新增第二 Runtime 控制面：F8 Controller 仍是唯一 lifecycle/budget/terminal 权威。
"""
