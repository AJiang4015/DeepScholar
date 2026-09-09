"""F9-P0 Batch 7 — deterministic world：search/evidence 内容与 scripted 决策策略。

设计：每个 scenario 提供一个 World 实例：
- task：benchmark task specification（传给 baseline/adaptive 同一文本）；
- search(query) -> deterministic sources+evidences（经 research registry 写入当前
  research ctx；幂等确定性：同 query 恒产出同 content/locator）；
- 决策策略（judge/plan/verify/F4–F6）由 scenario 以 callables 提供，保证 baseline 与
  adaptive 使用同一 world（同 task / 同 search 结果 / 同 policy）。

Frozen boundary：本模块零修改冻结实现；只调用 research registry 的既有写入 API
（record_search_query/upsert_source/append_evidence 等，与 Batch5/6 fake tool 相同用法）。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# World 原语：确定性 evidence 片段
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Ev:
    """一次确定性搜索结果：一个 source + 一条 evidence（无随机/无 wall-clock）。"""

    query: str
    title: str
    content: str
    url: str = ""  # locator；空则由 world 生成确定性 locator


def _u() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# World
# ---------------------------------------------------------------------------


class World:
    """deterministic world：给定 query → 固定 sources/evidence。

    sub_question 覆盖语义由 registry（current research ctx）承载；world 只负责
    产出确定性的 query→evidence 映射，并可选标记该 evidence 属于哪类“事实”
    （用于 rubric 判定 citation/coverage）。
    """

    def __init__(
        self,
        task: str,
        query_to_evidence: Optional[dict[str, list[Ev]]] = None,
        *,
        seed: str = "batch7-world",
        extra_evidence: Optional[list[Ev]] = None,
    ) -> None:
        self.task = task
        self._q2e: dict[str, list[Ev]] = dict(query_to_evidence or {})
        self._seed = seed
        self._extra = list(extra_evidence or [])
        self.calls: list[str] = []

    # -- deterministic query→evidence --------------------------------------
    def results_for(self, query: str) -> list[Ev]:
        """同 query 恒返回同结果（确定性）；仅显式路由，未注册 query → []。

        用途：round0/baseline 的初始检索（无结果 = 初始证据不足，触发后续 gap）。
        """
        self.calls.append(query)
        return list(self._q2e.get(query, []))

    def results_any(self, query: str) -> list[Ev]:
        """补检索：显式路由优先，否则回退 extra（模拟“定向补搜总能找到补充证据”）。"""
        self.calls.append(query)
        out = list(self._q2e.get(query, []))
        if not out and self._extra:
            out = list(self._extra)
        return out

    def add_route(self, query: str, evs: list[Ev]) -> None:
        self._q2e[query] = list(evs)


def make_locator(seed: str, i: int) -> str:
    """确定性 locator：由 seed+index 推导（无随机）。"""
    return f"https://world.example/{seed}/{i}"


# ---------------------------------------------------------------------------
# Registry 写入 helper（与 Batch5/6 fake tool 相同的既有 API 用法）
# ---------------------------------------------------------------------------


def ingest_search_results(
    run_id: str,
    sub_question_id: str,
    query: str,
    evs: list[Ev],
    *,
    agent: str = "network_search",
    tool: str = "internet_search",
) -> list[str]:
    """把 world 的确定性结果经 research registry 落库，返回新增 evidence_ids。

    幂等语义：query 文本含随机 marker 的由调用方生成唯一 query；同一 run 重复调用
    由 registry.record_search_query/upsert_source 天然幂等（同 run/query 不重复行）。
    """
    from app.research import registry

    added: list[str] = []
    for i, ev in enumerate(evs):
        qid = registry.record_search_query(
            run_id,
            sub_question_id,
            agent=agent,
            tool=tool,
            query=query,
            seq=i,
        )
        url = ev.url or make_locator(ev.title, i)
        sid = registry.upsert_source(
            run_id,
            qid,
            source_type="web",
            agent=agent,
            title=ev.title,
            locator=url,
            canonical_key=url,
            canonical_url=url,
        )
        eid = registry.append_evidence(
            run_id,
            sid,
            sub_question_id,
            content=ev.content,
            locator=url,
            extraction_method="web_result",
        )
        if eid:
            added.append(eid)
    return added


# ---------------------------------------------------------------------------
# Scripted 决策策略签名（供 scenarios 注入；返回与 Batch3/4 模型输出同构）
# ---------------------------------------------------------------------------


@dataclass
class ScriptedPolicies:
    """每个 scenario 的 scripted LLM 决策（deterministic）。

    全部为 callable；None 表示该阶段用默认行为（如 adaptive 侧默认 Fake/现有 seam）。
    """

    judge: Optional[Callable[[list[str]], dict[str, Any]]] = None  # gap_ids → judgments
    plan: Optional[Callable[[list[str]], dict[str, Any]]] = None  # gap_ids → plans
    verifier_verdict: Optional[str] = None  # FakeVerifier verdict（如 SUPPORTS）
    extra: dict[str, Any] = field(default_factory=dict)
