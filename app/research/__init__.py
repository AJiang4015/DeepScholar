"""app.research — Research 领域（Data/Evidence Plane + Research Intelligence / Execution）。

① Research Data / Evidence Plane（F1–F7）：ResearchRun / SubQuestion / SearchQuery /
Source / Evidence / Claim / … 的持久化、查询与 F2–F7 语义算法
（verify/conflict/corroboration/reconciliation/extractor/bridge/…）。
约束（docs/spec/2026-09-07-research-artifact-foundation.md）：
- 数据面核心不依赖 app.agent；
- 与 LangGraph checkpoint 逻辑解耦（不共表/不共迁移/不共事务）；
- 运行期写入 fail-open，绝不阻断 Agent 主链路。

② Research Intelligence / Execution（原 F9-P0 orchestration plane，语义迁入本包）：
projection / gaps / judge / plan / targeted / orchestrator —— evidence-driven adaptive
research loop，在**单个** F8 governed execution 内运行（round 计数是编排计数，不注入
F8 BudgetCounter）；orchestrator 属业务研究编排，不拥有 lifecycle / budget / cancellation /
timeout / terminal authority —— F8 Controller 仍唯一权威；F8 control 信号原样传播。

③ eval/ —— Research Intelligence 行为质量验证与校准基础设施
（world/agents/harness/rubric/scenarios/calibration，确定性 scripted world）。

子模块请显式导入：``from app.research import registry, provenance, ...``
"""
