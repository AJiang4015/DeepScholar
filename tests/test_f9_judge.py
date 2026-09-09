"""F9-P0 Batch 3 — Semantic Gap Judge tests（sqlite/纯函数 + F8 governance seam 集成）。

覆盖 T1–T12：
- parse/validate 纯函数边界（合法/非法 JSON/未知字段/类型/缺字段/长度/priority/未知 gap/
  漏判/重复）；A1 协议（仅 5 字段、无 query/tool 等键、顺序=输入顺序）；
- judge_gaps：T1 valid；T2 no-gap 不调 LLM；T3 malformed；T4 invalid gap 引用（含 cross-run
  引用 = 不在本 gap 集即拒）；T5 provider failure；T6 llm 预算耗尽 → GovernanceLimitExceeded
  （不吞）；T9 callback accounting（+1 llm / +0 agent_step / 无裸调 / degraded diagnostic 存在）；
- F8 seam（Controller.execute 单执行体）：T6c policy budget→budget_exceeded；T7 cancel→cancelled；
  T8 timeout→timed_out；T10 同 task/run/counter 身份；T11 judge failure→同 execute baseline→
  completed（F7 exactly-once 属 Batch 6/7 seam，见报告）；T12 Batch1/2 回归套件另行全绿。

SQLite PASS ≠ PG PASS；Judge 不读 Research DB（PG 测试不适用，理由见 Batch3 report）。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from pathlib import Path

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.f9 import gaps as f9g
from app.f9 import judge as f9j
from app.runtime.governance import migrations as gov_migrations
from app.runtime.governance import store as gov_store
from app.runtime.governance.callbacks import GovernanceCallbackHandler
from app.runtime.governance.context import (
    GovernanceExecution,
    enter_governance_execution,
)
from app.runtime.governance.controller import GovernanceController
from app.runtime.governance.counters import BudgetCounter, GovernanceLimitExceeded
from app.runtime.governance.models import TaskStatus

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST_TMP = _REPO_ROOT / "_testtmp"

DEFAULT_LIMITS = {
    "llm_calls": 120,
    "tool_calls": 300,
    "search_calls": 40,
    "agent_steps": 200,
}


def _budget(limits=None, counts=None):
    lim = dict(DEFAULT_LIMITS)
    lim.update(limits or {})
    cnt = {k: 0 for k in DEFAULT_LIMITS}
    cnt.update(counts or {})
    return {
        "limits": lim,
        "counts": cnt,
        "remaining": {k: lim[k] - cnt[k] for k in DEFAULT_LIMITS},
    }


def _proj(**over):
    p = {
        "run_id": "run-x",
        "round": 0,
        "budget": _budget(),
        "sub_questions": [
            {
                "sub_question_id": "sq-root",
                "parent_id": None,
                "position": 0,
                "question": "root question?",
                "status": "planned",
                "required": True,
                "evidence_count": 0,
                "source_count": 0,
            }
        ],
        "required_uncovered": ["sq-root"],
        "claims": [
            {
                "claim_id": "c-1",
                "sub_question_id": "sq-root",
                "statement": "claim one statement",
                "claim_type": "FACT",
                "status": "drafted",
                "created_at": "2026-09-02T12:00:00+00:00",
                "best_verdict": None,
                "independent_flag": None,
                "fresh": None,
                "conflicts": [],
                "evidence_refs": [],
            }
        ],
        "evidence_summary": {"count": 0, "by_domain": {}},
        "gap_signals": {},
        "open_questions": [],
        "size_limits": {
            "omitted": {
                "sub_questions": 0,
                "claims": 0,
                "evidence_refs": 0,
                "conflicts": 0,
            }
        },
    }
    for k, v in over.items():
        p[k] = v
    return p


def _scenario():
    """真实管道：projection(fixture) → detect_gaps → (projection, gaps)。"""
    projection = _proj()
    gaps = f9g.detect_gaps(projection)
    assert {s["type"] for s in gaps["signals"]} == {
        "required_uncovered",
        "claim_no_evidence",
    }
    return projection, gaps


def _ids(gaps):
    return f9j.signal_gap_ids(gaps)


def _valid_payload(
    gap_ids,
    *,
    important=True,
    priority="medium",
    extra=None,
    skip=None,
    unknown_field=False,
    wrap_missing=False,
    bad_json=False,
    bad_priority=False,
    long_reason=False,
    long_need=False,
    bad_type=None,
    dup=False,
    unknown_gap=None,
):
    rows = []
    for i, gid in enumerate(gap_ids):
        if skip == gid:
            continue
        item = {
            "gap_id": gid,
            "important": important,
            "reason": "r" * (f9j.REASON_MAX + 1) if long_reason else f"reason-{i}",
            "priority": "bogus" if bad_priority else priority,
            "semantic_need": "n" * (f9j.SEMANTIC_NEED_MAX + 1)
            if long_need
            else f"need-{i}",
        }
        if unknown_field:
            item["query"] = "FORBIDDEN"
        if bad_type == gid:
            item["important"] = "yes"
        rows.append(item)
        if dup:
            rows.append(dict(item))
    if unknown_gap is not None:
        rows.append(
            {
                "gap_id": unknown_gap,
                "important": True,
                "reason": "x",
                "priority": "low",
                "semantic_need": "y",
            }
        )
    payload = {"judgments": rows}
    if wrap_missing:
        payload = {"other": rows}
    text = json.dumps(payload)
    if bad_json:
        text = "not json{{{"
    return text


def _make_fake_model(payload=None, raise_error=None, hang=False, payload_fn=None):
    class FakeJudgeModel(BaseChatModel):
        def __init__(self):
            # pydantic v2 冻结 BaseChatModel 实例赋值 → 私有状态一律 object.__setattr__，
            # 可变计数放 dict（原地修改，不触发 __setattr__）。
            super().__init__()
            object.__setattr__(self, "_state", {"calls": 0})
            object.__setattr__(self, "_payload", payload)
            object.__setattr__(self, "_raise_error", raise_error)
            object.__setattr__(self, "_hang", hang)
            object.__setattr__(self, "_payload_fn", payload_fn)

        @property
        def _llm_type(self) -> str:
            return "fake-judge"

        @property
        def calls(self) -> int:
            return self._state["calls"]

        def _bump(self) -> None:
            self._state["calls"] += 1

        def _resolve(self):
            if self._raise_error is not None:
                raise self._raise_error
            if self._payload is not None:
                return self._payload
            if self._payload_fn is not None:
                return self._payload_fn([])
            raise RuntimeError("fake model: no payload")

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self._bump()
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content=self._resolve()))]
            )

        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            self._bump()
            if self._hang:
                await asyncio.Event().wait()  # 挂起直至 F8 cancel/timeout
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content=self._resolve()))]
            )

    return FakeJudgeModel()


def _default_ctx(*, limits=None, run_id="run-j"):
    return GovernanceExecution(
        task_id="task-j", counter=BudgetCounter(limits=limits), run_id=run_id
    )


async def _run_judge(projection, gaps, model, ctx=None):
    ctx = ctx or _default_ctx()
    with enter_governance_execution(ctx):
        result = await f9j.judge_gaps(projection, gaps, model)
    return result, ctx


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# parse / validation 纯函数边界
# ---------------------------------------------------------------------------
class TestParseValidation:
    def test_valid_payload_normalized_in_input_order(self):
        ids = ["a|claim|x", "b|sub_question|y"]
        out = f9j.parse_judgments(_valid_payload(ids), ids)
        assert [o["gap_id"] for o in out] == ids
        item = out[0]
        assert set(item) == {
            "gap_id",
            "important",
            "reason",
            "priority",
            "semantic_need",
        }
        assert item["important"] is True and item["priority"] == "medium"

    def test_bad_json_rejected(self):
        ids = ["a|claim|x"]
        with pytest.raises(f9j.JudgeError):
            f9j.parse_judgments("not json{{{", ids)

    def test_missing_wrapper_rejected(self):
        ids = ["a|claim|x"]
        with pytest.raises(f9j.JudgeError):
            f9j.parse_judgments(_valid_payload(ids, wrap_missing=True), ids)

    def test_unknown_field_rejected(self):
        ids = ["a|claim|x"]
        with pytest.raises(f9j.JudgeError):
            f9j.parse_judgments(_valid_payload(ids, unknown_field=True), ids)

    def test_unknown_gap_rejected(self):
        ids = ["a|claim|x"]
        with pytest.raises(f9j.JudgeError):
            f9j.parse_judgments(_valid_payload(ids, unknown_gap="nope|claim|z"), ids)

    def test_missing_gap_rejected(self):
        ids = ["a|claim|x", "b|claim|y"]
        with pytest.raises(f9j.JudgeError):
            f9j.parse_judgments(_valid_payload(ids, skip="b|claim|y"), ids)

    def test_duplicate_gap_rejected(self):
        ids = ["a|claim|x"]
        with pytest.raises(f9j.JudgeError):
            f9j.parse_judgments(_valid_payload(ids, dup=True), ids)

    def test_bad_priority_and_type_and_length_rejected(self):
        ids = ["a|claim|x"]
        for kwargs in (
            {"bad_priority": True},
            {"bad_type": "a|claim|x"},
            {"long_reason": True},
            {"long_need": True},
            {"important": "yes"},
        ):
            with pytest.raises(f9j.JudgeError):
                f9j.parse_judgments(_valid_payload(ids, **kwargs), ids)

    def test_missing_required_field_rejected(self):
        ids = ["a|claim|x"]
        text = json.dumps(
            {
                "judgments": [
                    {
                        "gap_id": "a|claim|x",
                        "important": True,
                        "reason": "r",
                        "priority": "low",
                    }
                ]
            }
        )
        with pytest.raises(f9j.JudgeError):
            f9j.parse_judgments(text, ids)

    def test_gap_id_of_stable(self):
        s = {
            "type": "budget_near",
            "subject_type": "budget_kind",
            "subject_id": "llm_calls",
        }
        assert f9j.gap_id_of(s) == "budget_near|budget_kind|llm_calls"


# ---------------------------------------------------------------------------
# judge_gaps：T1/T2/T3/T4/T5/T6/T9
# ---------------------------------------------------------------------------
class TestJudgeGaps:
    def test_t1_valid_judge(self):
        projection, gaps = _scenario()
        ids = _ids(gaps)
        model = _make_fake_model(payload=_valid_payload(ids))
        result, ctx = _run(_run_judge(projection, gaps, model))
        assert result["judged"] is True
        assert [j["gap_id"] for j in result["judgments"]] == ids
        assert result["round"] == 0
        # T9 accounting：+1 llm / +0 agent_step；无裸调（handler 路径留下 degraded diagnostic）
        assert ctx.counter.count("llm_calls") == 1
        assert ctx.counter.count("agent_steps") == 0
        assert model.calls == 1
        assert any(
            d.get("event") == "llm_classification_unresolved" for d in ctx.diagnostics
        )

    def test_t2_no_gap_no_llm(self):
        from app.f9 import gaps as _g  # noqa: PLC0415

        p2 = _proj(
            sub_questions=[
                {
                    "sub_question_id": "sq-root",
                    "parent_id": None,
                    "position": 0,
                    "question": "q",
                    "status": "planned",
                    "required": True,
                    "evidence_count": 2,
                    "source_count": 2,
                }
            ],
            required_uncovered=[],
            claims=[],
        )
        assert _g.detect_gaps(p2)["signals"] == []
        model = _make_fake_model(payload="{}")
        result, _ctx = _run(_run_judge(p2, _g.detect_gaps(p2), model))
        assert result["judged"] is False
        assert result["judgments"] == []
        assert model.calls == 0

    def test_t3_malformed_output_judge_failure(self):
        projection, gaps = _scenario()
        model = _make_fake_model(payload="not json{{")
        with pytest.raises(f9j.JudgeError):
            _run(_run_judge(projection, gaps, model))
        # LLM 调用发生过（provider 尝试被计费），partial 被丢弃
        assert model.calls == 1

    def test_t4_invalid_gap_reference_rejected(self):
        projection, gaps = _scenario()
        ids = _ids(gaps)
        model = _make_fake_model(
            payload=_valid_payload(ids, unknown_gap="other|claim|z")
        )
        with pytest.raises(f9j.JudgeError):
            _run(_run_judge(projection, gaps, model))

    def test_t4_cross_run_entity_rejected_by_gap_set(self):
        # 引用其它 run 的 claim：不在本 run gap 集合 → 拒绝（run 隔离由 gap 集合保证）
        projection, gaps = _scenario()
        ids = _ids(gaps)
        model = _make_fake_model(
            payload=_valid_payload(
                ids, unknown_gap="claim_no_evidence|claim|run-b-claim"
            )
        )
        with pytest.raises(f9j.JudgeError):
            _run(_run_judge(projection, gaps, model))

    def test_t5_provider_failure_judge_failure(self):
        projection, gaps = _scenario()
        model = _make_fake_model(raise_error=RuntimeError("provider boom"))
        with pytest.raises(f9j.JudgeError):
            _run(_run_judge(projection, gaps, model))

    def test_t6_llm_budget_exhausted_not_swallowed(self):
        projection, gaps = _scenario()
        ctx = _default_ctx(
            limits={
                "llm_calls": 1,
                "tool_calls": 10,
                "search_calls": 5,
                "agent_steps": 10,
            }
        )
        ctx.counter.acquire_llm_call()  # 已耗尽
        model = _make_fake_model(payload="{}")
        with pytest.raises(GovernanceLimitExceeded):
            _run(_run_judge(projection, gaps, model, ctx=ctx))
        assert model.calls == 0  # 回调在 provider 前拦截

    def test_requires_governance_context(self):
        projection, gaps = _scenario()
        model = _make_fake_model(payload="{}")
        with pytest.raises(f9j.JudgeError):
            asyncio.run(f9j.judge_gaps(projection, gaps, model))


# ---------------------------------------------------------------------------
# F8 governance seam：Controller.execute 单执行体（T6c/T7/T8/T10/T11）
# ---------------------------------------------------------------------------
@pytest.fixture
def gov_tmp():
    d = _TEST_TMP / f"judge-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    try:
        shutil.rmtree(d)
    except OSError:
        pass


def _make_controller(gov_tmp):
    db = str(gov_tmp / f"gov-{uuid.uuid4().hex}.sqlite")
    store = gov_store._GovernanceSqliteStore(db)
    gov_migrations.ensure_schema(store)
    ctl = GovernanceController(
        store, owner_instance="inst-judge", retry_delays=(0.01, 0.02)
    )
    ctl.watchdog_tick = 0.02
    return store, ctl


class TestGovernanceSeam:
    def test_t6c_policy_llm_budget_maps_budget_exceeded(self, gov_tmp):
        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-j6")
            projection, gaps = _scenario()
            model = _make_fake_model(payload="{}")

            async def shim():
                return await f9j.judge_gaps(projection, gaps, model)

            with pytest.raises(GovernanceLimitExceeded):
                await ctl.execute(rec.task_id, shim(), policy={"max_llm_calls": 0})
            row = gov_store.get_task(store, rec.task_id)
            assert row.status == TaskStatus.BUDGET_EXCEEDED.value
            assert model.calls == 0
            store.close()

        asyncio.run(scenario())

    def test_t10_same_execution_identity_and_t9_accounting(self, gov_tmp):
        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-j10", run_id="run-j10")
            projection, gaps = _scenario()
            ids = _ids(gaps)
            model = _make_fake_model(payload=_valid_payload(ids))
            observed = {}

            async def shim():
                from app.runtime.governance.context import get_governance_execution  # noqa: PLC0415,PLC2701

                ex = get_governance_execution()
                observed["task_id"] = ex.task_id
                observed["run_id"] = ex.run_id
                observed["counter"] = ex.counter
                return await f9j.judge_gaps(projection, gaps, model)

            result = await ctl.execute(rec.task_id, shim(), policy={})
            row = gov_store.get_task(store, rec.task_id)
            assert row.status == TaskStatus.COMPLETED.value
            assert row.version == 1
            assert result["judged"] is True
            # 同一身份：task_id / run_id / BudgetCounter（judge 与 execution 共享）
            assert observed["task_id"] == rec.task_id
            assert observed["run_id"] == rec.run_id
            assert observed["counter"] is not None
            assert row.counters_snapshot["llm_calls"] == 1
            assert row.counters_snapshot["agent_steps"] == 0
            assert model.calls == 1
            store.close()

        asyncio.run(scenario())

    def test_t11_judge_failure_fallback_same_execute_completed(self, gov_tmp):
        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-j11")
            projection, gaps = _scenario()
            model = _make_fake_model(raise_error=RuntimeError("judge provider down"))
            baseline_ran = {"n": 0}
            handles_before = len(ctl.active_handle_ids())

            async def f9_shim():
                try:
                    await f9j.judge_gaps(projection, gaps, model)
                except f9j.JudgeError:
                    baseline_ran["n"] += (
                        1  # 同 coroutine 内 baseline 分支（orchestrator 语义）
                    )
                    return "BASELINE"
                raise AssertionError("judge 应失败")

            out = await ctl.execute(rec.task_id, f9_shim(), policy={})
            row = gov_store.get_task(store, rec.task_id)
            assert out == "BASELINE"
            assert baseline_ran["n"] == 1
            assert row.status == TaskStatus.COMPLETED.value
            assert row.version == 1
            assert row.counters_snapshot["llm_calls"] == 1  # provider 尝试被计费
            # 无新 task / controller：active handles 清空且仍为同 store
            assert ctl.active_handle_ids() == []
            assert handles_before == 0
            # F7 exactly-once 属 Batch 6/7 seam（run_deep_agent finalize_run），Batch3 不越界
            store.close()

        asyncio.run(scenario())

    def test_t7_cancel_during_judge_cancelled(self, gov_tmp):
        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-j7")
            projection, gaps = _scenario()
            model = _make_fake_model(hang=True)

            async def shim():
                return await f9j.judge_gaps(projection, gaps, model)

            task = asyncio.create_task(ctl.execute(rec.task_id, shim(), policy={}))
            for _ in range(200):
                if ctl.has_active_handle(rec.task_id):
                    break
                await asyncio.sleep(0.001)
            assert ctl.has_active_handle(rec.task_id)
            res = await ctl.cancel(rec.task_id)
            assert res["winner"] is True
            with pytest.raises(asyncio.CancelledError):
                await task
            row = gov_store.get_task(store, rec.task_id)
            assert row.status == TaskStatus.CANCELLED.value
            assert row.version == 1
            store.close()

        asyncio.run(scenario())

    def test_t8_timeout_during_judge_timed_out(self, gov_tmp):
        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-j8")
            projection, gaps = _scenario()
            model = _make_fake_model(hang=True)

            async def shim():
                return await f9j.judge_gaps(projection, gaps, model)

            task = asyncio.create_task(
                ctl.execute(rec.task_id, shim(), policy={"wall_clock_timeout": 0.25})
            )
            with pytest.raises(asyncio.CancelledError):
                await task
            row = gov_store.get_task(store, rec.task_id)
            assert row.status == TaskStatus.TIMED_OUT.value
            assert row.version == 1
            assert (
                row.counters_snapshot["llm_calls"] == 1
            )  # judge 已开始计费后被 deadline 终止
            store.close()

        asyncio.run(scenario())

    def test_handler_is_single_instance_per_execution(self, gov_tmp):
        async def scenario():
            store, ctl = _make_controller(gov_tmp)
            rec = ctl.create_task("th-h")
            projection, gaps = _scenario()
            ids = _ids(gaps)
            model = _make_fake_model(payload=_valid_payload(ids))
            seen = {}

            async def shim():
                from app.runtime.governance.context import get_governance_execution  # noqa: PLC0415,PLC2701

                ex = get_governance_execution()
                h1 = ex.make_handler()
                h2 = ex.make_handler()
                seen["same"] = h1 is h2
                assert isinstance(h1, GovernanceCallbackHandler)
                return await f9j.judge_gaps(projection, gaps, model)

            await ctl.execute(rec.task_id, shim(), policy={})
            assert seen["same"] is True  # C2 单一 producer
            store.close()

        asyncio.run(scenario())
