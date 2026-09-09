"""F9-P0 Batch 7 — Eval / Behavioral Quality Validation（deterministic scripted world）。

归属：app/research/eval —— Research Intelligence 行为质量验证基础设施（源于 F9-P0
Batch 7 eval；Feature 编号已从目录命名解耦，F9-P0 仅作历史溯源）。本子包仅供
测试/校准/报告引用。

模块布局：
- world.py     deterministic world（search/evidence 内容 + scripted 决策策略）
- agents.py    scripted agent（baseline graph 替身）与 adaptive graph runner
- harness.py   双独立 execute 编排 + metrics 收集
- rubric.py    deterministic / rubric-based evaluator
- scenarios.py 8 个 benchmark scenarios（positive + negative/control）

Frozen boundary：本包不改 F8 / F1–F7 / main_agent.py / orchestrator.py / Batch1–6；
baseline 经真实 run_deep_agent 入口（仅进程内替换 get_main_agent 返回 scripted agent）。
Gate 主判据 = rubric；real provider 不在本包（supplementary，凭据环境外）。
"""
