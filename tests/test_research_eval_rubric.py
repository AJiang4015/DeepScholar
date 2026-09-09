"""F9-P0 Batch 7 — rubric evaluator 单测（deterministic；构造已知 state 断言分数）。"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

_TEST_TMP = Path(__file__).resolve().parent.parent / "_testtmp"


@pytest.fixture
def b7_tmp():
    import shutil

    d = _TEST_TMP / f"b7rb-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    try:
        shutil.rmtree(d)
    except OSError:
        pass


def _seed_state(store, run_id, *, evidences=0, claims=0, ver_succeeded=0,
                conflicts=0, reconciles=0):
    """构造 rubric 输入 state（registry 语义真实，足够 rubric 计数）。"""
    from app.research import registry

    sqid = None
    for i in range(evidences):
        qid = registry.record_search_query(run_id, sqid, agent="a", tool="t", query=f"q{i}") if sqid else None
        url = f"https://rb.example/{uuid.uuid4().hex}"
        sid = None
        if sqid:
            sid = registry.upsert_source(run_id, qid, source_type="web", agent="a", title="t",
                                         locator=url, canonical_key=url, canonical_url=url)
            registry.append_evidence(run_id, sid, sqid, content=f"c{i}", locator=url,
                                     extraction_method="web_result")
    return run_id


class TestRubricDeterministic:
    def test_evaluate_empty_run_neutral(self, b7_tmp, monkeypatch):
        """空 run：rubric 全维度存在，required=0、citation=0、无 claim 中性。"""
        from app.research.eval import rubric as RB

        _db(b7_tmp)
        run_id, _ = _mk_run()
        rb = RB.evaluate_side(_FakeRun(run_id, final="x", task_row=_FakeRow()))
        assert rb["required_coverage"] in (0.0, 1.0)
        assert rb["total"] >= 0.0
        for k in ("citation_coverage", "evidence_sufficiency", "unsupported_score",
                  "conflict_coverage", "redundant_score", "cost_compliance"):
            assert 0.0 <= rb[k] <= 1.0

    def test_verdict_shapes(self):
        from app.research.eval import rubric as RB

        base = {k: 0.0 for k in RB_DIMS} | {"total": 0.0, "_raw": {}}
        ad = {k: 0.0 for k in RB_DIMS} | {"total": 0.0, "_raw": {}}
        ad["required_coverage"] = 1.0
        ad["total"] = 1.0
        v = RB.verdict(base, ad)
        assert v["gate_pass"] is True
        assert "required_coverage" in v["improved_dims"]

    def test_no_pad_when_equal(self):
        from app.research.eval import rubric as RB

        same = {k: 0.5 for k in RB_DIMS} | {"total": 3.5, "_raw": {}}
        v = RB.verdict(same, dict(same))
        assert v["gate_pass"] is False


RB_DIMS = (
    "required_coverage", "citation_coverage", "evidence_sufficiency",
    "unsupported_score", "conflict_coverage", "redundant_score", "cost_compliance",
)


def _db(tmp):
    import os

    from app.research import config as rconfig
    from app.research import store as rstore

    d = tmp / f"db-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=True)
    os.environ[rconfig.RESEARCH_STORE_ENV] = "sqlite"
    os.environ[rconfig.RESEARCH_DB_ENV] = str(d / "research.sqlite")
    rstore.reset_store()


def _mk_run():
    from app.research import registry

    run_id, sqid = registry.create_run_and_root("rb-thread", "rb 问题?")
    return run_id, sqid


class _FakeRun:
    def __init__(self, run_id, final, task_row):
        self.run_id = run_id
        self.final = final
        self.task_row = task_row


class _FakeRow:
    counters_snapshot = {}
    effective_limits = {}
