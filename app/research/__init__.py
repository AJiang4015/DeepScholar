"""app.research — Research Data Plane（F1）。

ResearchRun / SubQuestion / SearchQuery / Source / Evidence 的持久化与查询。
约束（docs/spec/2026-09-07-research-artifact-foundation.md）：
- 不依赖 app.agent；
- 与 LangGraph checkpoint 逻辑解耦（不共表/不共迁移/不共事务）；
- 运行期写入 fail-open，绝不阻断 Agent 主链路。

子模块请显式导入：``from app.research import registry, provenance, ...``
"""
