"""F9-P0 Batch 3 — Semantic Gap Judge（LLM semantic judgment；in-memory；Batch 3 范围）。

裁决记录（用户 Readiness 裁决 A1–A5，2026-09-24）：
- A1 Judge 只产 Semantic Judgment（每 gap：gap_id/important/reason/priority/semantic_need），
  **禁止** query/queries/objective/tool·source selection/search strategy/dedup_identity/
  targeted task/verification execution —— 属 Batch 4/5。Spec §5 "候选 follow-up" 不得解释为
  Judge 生成 executable query。
- A2 无 deterministic gap（signals==[]）→ **不调用 LLM**，直接 no-gap 路径（judged=False）。
- A3 不实现 Judge 专用 retry；provider error / malformed / schema 失败 / 缺 required /
  invalid gap reference / 语义 validation 失败 → 全部 judge failure（丢弃 partial）→
  baseline fallback（同 F8 governed execution，由上层 orchestrator/调用方处理）。
  **F8 control signals（GovernanceLimitExceeded / asyncio.CancelledError / F8 timeout）不得
  被吞掉** → 继续向 F8 Controller 传播（budget_exceeded / cancelled / timed_out）。
- A4 judge_model 依赖注入（OpenAI-compatible LangChain ChatModel 栈；不硬编码 provider/
  model；temperature 非协议约束）；调用 = `await judge_model.ainvoke(messages,
  config={"callbacks":[handler]})`，handler = ctx.make_handler()；Judge = +1 llm_calls /
  +0 agent_steps（F8 冻结分类：judge 直调 → unclassified → 只 acquire_llm_call）。
- A5 结果本轮**不持久化**（in-memory；无表/migration；无 replay/crash recovery）。

职责边界与失败语义详见 docs/plan/2026-09-24-f9-p0-batch3-readiness-review.md 与本 Batch
report。N1：judge 经 handler 计费会触发 F8 冻结分类的 degraded diagnostic
（llm_classification_unresolved）——F8 FROZEN 不改，如实记录（见 report Limitations）。

确定性：LLM 本身 probabilistic；本模块保证输出契约 deterministic —— parse → schema →
gap 引用 → bounds → normalization 全失败即 judge failure；结果顺序 = 输入 signal 顺序。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.runtime.governance.callbacks import GovernanceCallbackHandler
from app.runtime.governance.context import (
    GovernanceExecution,
    get_governance_execution,
)
from app.runtime.governance.counters import GovernanceLimitExceeded

#: priority 枚举（A1 最小 bounded 结构；Spec 未定义 → 本实现定义并在 report 记录）
JUDGMENT_PRIORITIES: frozenset[str] = frozenset({"high", "medium", "low"})

#: 长度上限（bounded；report 记录为最小可验证结构）
REASON_MAX: int = 600
SEMANTIC_NEED_MAX: int = 1000

#: prompt 上下文总字符上限（超限确定性截断，防无界输入）
CONTEXT_CHAR_CAP: int = 16000

#: 单个 claim statement / sq question 注入上限（避免把 Projection 变 context dump）
SNIPPET_MAX: int = 400


class JudgeError(Exception):
    """judge failure：provider / parse / schema / 引用 / bounds 任一失败。

    调用方（未来 orchestrator）按 R2-1 丢弃 partial → 同一 F8 execution 内 baseline fallback。
    注意：GovernanceLimitExceeded / asyncio.CancelledError 是 F8 control signal，**不**包装成
    JudgeError，必须向 F8 Controller 传播。
    """


def gap_id_of(signal: dict[str, Any]) -> str:
    """Batch 2 signal 的稳定 gap identity（A1 的 gap_id 引用锚点）。

    Batch 2 无独立 id 列 → 由唯一键 (type, subject_type, subject_id) 派生，deterministic。
    """
    return "|".join(
        (
            str(signal.get("type", "")),
            str(signal.get("subject_type", "")),
            str(signal.get("subject_id", "")),
        )
    )


def signal_gap_ids(gap_signals: dict[str, Any]) -> list[str]:
    """按 Batch 2 输出顺序返回 gap_id 列表（输入本身已确定性排序）。"""
    return [gap_id_of(s) for s in (gap_signals.get("signals") or [])]


def _fail(message: str) -> None:
    raise JudgeError(message)


def _check_field(j: dict[str, Any], key: str, expected: type) -> Any:
    if key not in j:
        _fail(f"judgment 缺少字段: {key}")
    value = j[key]
    if not isinstance(value, expected):
        _fail(
            f"judgment.{key} 类型错误: {type(value).__name__}（期望 {expected.__name__}）"
        )
    return value


def parse_judgments(text: str, expected_ids: list[str]) -> list[dict[str, Any]]:
    """LLM 输出 → deterministic validation → 归一化 judgments（顺序 = expected_ids）。

    失败（任一）：非 JSON / wrapper 缺失 / 非 list / 未知字段 / 字段类型错 / 缺字段 /
    长度超限 / priority 非法 / 引用不存在 gap / 漏判或重复 → JudgeError。
    """
    try:
        payload = json.loads(text)
    except (ValueError, TypeError) as exc:
        _fail(f"judge 输出非合法 JSON: {exc}")
    if not isinstance(payload, dict) or "judgments" not in payload:
        _fail('judge 输出缺少 {"judgments": [...]} wrapper')
    rows = payload["judgments"]
    if not isinstance(rows, list):
        _fail("judgments 必须为 list")
    expected_set = set(expected_ids)
    if len(expected_set) != len(expected_ids):
        _fail("expected gap ids 重复（内部错误）")
    seen: dict[str, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            _fail("judgments 元素必须为 object")
        unknown = set(raw) - {
            "gap_id",
            "important",
            "reason",
            "priority",
            "semantic_need",
        }
        if unknown:
            _fail(f"judgment 含未知字段（禁扩展协议）: {sorted(unknown)}")
        gid = _check_field(raw, "gap_id", str)
        if gid not in expected_set:
            _fail(f"judgment 引用不存在的 gap: {gid!r}")
        if gid in seen:
            _fail(f"gap 被重复判定: {gid}")
        important = _check_field(raw, "important", bool)
        reason = _check_field(raw, "reason", str)
        priority = _check_field(raw, "priority", str)
        need = _check_field(raw, "semantic_need", str)
        if len(reason) > REASON_MAX:
            _fail(f"reason 超长 >{REASON_MAX}: gap={gid}")
        if len(need) > SEMANTIC_NEED_MAX:
            _fail(f"semantic_need 超长 >{SEMANTIC_NEED_MAX}: gap={gid}")
        if priority not in JUDGMENT_PRIORITIES:
            _fail(f"priority 非法: {priority!r}（允许 {sorted(JUDGMENT_PRIORITIES)}）")
        seen[gid] = {
            "gap_id": gid,
            "important": bool(important),
            "reason": reason.strip(),
            "priority": str(priority),
            "semantic_need": need.strip(),
        }
    missing = sorted(expected_set - set(seen))
    if missing:
        _fail(f"存在未判定的 gap: {missing}")
    return [seen[gid] for gid in expected_ids]


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]


def build_messages(
    projection: dict[str, Any],
    gap_signals: dict[str, Any],
) -> list[Any]:
    """构造 bounded Judge 输入（Projection facts + Gap Signals；无 history/无正文/无 DB）。"""
    signals = gap_signals.get("signals") or []
    claim_by_id: dict[str, dict[str, Any]] = {
        c["claim_id"]: c for c in (projection.get("claims") or [])
    }
    sq_by_id: dict[str, dict[str, Any]] = {
        s["sub_question_id"]: s for s in (projection.get("sub_questions") or [])
    }
    lines: list[str] = []
    for idx, s in enumerate(signals):
        gid = gap_id_of(s)
        subj_type = str(s.get("subject_type"))
        subj_id = str(s.get("subject_id"))
        line = (
            f"[{idx}] gap_id={gid} type={s.get('type')} rule={s.get('rule')} "
            f"subject={subj_type}:{subj_id} reason={str(s.get('reason'))[:200]}"
        )
        extra: Optional[str] = None
        if subj_type == "claim" and subj_id in claim_by_id:
            c = claim_by_id[subj_id]
            extra = (
                f"claim statement={_truncate(str(c.get('statement') or ''), SNIPPET_MAX)} "
                f"verdict={c.get('best_verdict')} fresh={c.get('fresh')} "
                f"independent={c.get('independent_flag')}"
            )
        elif subj_type == "sub_question" and subj_id in sq_by_id:
            q = sq_by_id[subj_id]
            extra = (
                f"sub_question question={_truncate(str(q.get('question') or ''), SNIPPET_MAX)} "
                f"evidence_count={q.get('evidence_count')} source_count={q.get('source_count')}"
            )
        if extra is not None:
            line += f" | {extra}"
        lines.append(line)
    context = "\n".join(lines)
    if len(context) > CONTEXT_CHAR_CAP:
        context = context[:CONTEXT_CHAR_CAP] + "\n…(truncated)"
    system = (
        "You are a research gap judge. For each gap in the provided list, decide whether it is "
        "worth further research (important), why (reason), its follow-up research priority "
        "(priority ∈ high|medium|low), and what semantic question the follow-up must resolve "
        "(semantic_need). This is semantic judgment ONLY: do NOT emit queries, objectives, tool "
        "or source selections, dedup ids, or any executable plan — those belong to a later stage. "
        "Reply with exactly one JSON object: "
        '{"judgments":[{"gap_id","important","reason","priority","semantic_need"}]} '
        "covering EVERY provided gap_id exactly once and nothing else. "
        "reason ≤ 600 chars, semantic_need ≤ 1000 chars."
    )
    user = (
        "Projection round: {round}\n"
        "required_uncovered ids: {ru}\n"
        "claims judged context is embedded per gap line when subject is a claim.\n\n"
        "GAPS:\n{context}".format(
            round=projection.get("round"),
            ru=",".join(projection.get("required_uncovered") or []),
            context=context,
        )
    )
    return [SystemMessage(content=system), HumanMessage(content=user)]


async def _call_model(
    model: Any,
    messages: list[Any],
    handler: GovernanceCallbackHandler,
) -> str:
    """A4/D2 调用模式：显式 handler，禁裸调。"""
    response = await model.ainvoke(messages, config={"callbacks": [handler]})
    if not isinstance(response, AIMessage):
        _fail("judge model 未返回 AIMessage")
    content = getattr(response, "content", None)
    if not isinstance(content, str) or not content.strip():
        _fail("judge model 返回空 content")
    return content.strip()


async def judge_gaps(
    projection: dict[str, Any],
    gap_signals: dict[str, Any],
    judge_model: Any,
) -> dict[str, Any]:
    """Semantic Gap Judge 主入口（在 F8 governed execution 内调用）。

    - 非 governance-active → JudgeError（Judge 必须共享 F8 GovernanceExecution 计费）；
    - signals 为空 → 不调用 LLM（A2），返回 judged=False；
    - 成功 → 返回 {round, judged, judgments[]}（in-memory，不落库 A5）；
    - 失败 → JudgeError（丢弃 partial）；F8 control 异常原样传播（A3）。
    """
    execution: Optional[GovernanceExecution] = get_governance_execution()
    if execution is None:
        _fail("judge_gaps 必须在 F8 governed execution（GovernanceContext）内调用")

    signal_ids = signal_gap_ids(gap_signals)
    if not signal_ids:
        return {
            "round": int(projection.get("round", 0)),
            "judged": False,
            "judgments": [],
        }

    messages = build_messages(projection, gap_signals)
    handler = execution.make_handler()
    try:
        content = await _call_model(judge_model, messages, handler)
    except GovernanceLimitExceeded:
        raise  # F8 control：budget_exceeded，必须向 Controller 传播
    except asyncio.CancelledError:
        raise  # F8 control：cancel/timeout，必须传播
    except Exception as exc:  # noqa: BLE001 — provider / 框架异常 → judge failure
        raise JudgeError(
            f"judge provider failure: {type(exc).__name__}: {exc}"
        ) from exc

    judgments = parse_judgments(content, signal_ids)
    return {
        "round": int(projection.get("round", 0)),
        "judged": True,
        "judgments": judgments,
    }


__all__ = [
    "JudgeError",
    "JUDGMENT_PRIORITIES",
    "REASON_MAX",
    "SEMANTIC_NEED_MAX",
    "gap_id_of",
    "signal_gap_ids",
    "parse_judgments",
    "build_messages",
    "judge_gaps",
]
