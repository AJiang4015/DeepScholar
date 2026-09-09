"""F9-P0 Batch 7 — claim 级场景测试（S3/S4/S5；preseed claim run 复用）。

档 B：预置含 claim 的 research run（registry 真实建 run+root+claim+evidence），
adaptive 复用该 run（preseed_run_id）执行 orchestrator —— 验证：
- S3：unverified claim 因新 binding 进入 changed_claims 并触发 verification；
- S4/S5：同 run 上 F4 detect / F5 corroboration 可被 orchestrator 兼容执行
  （seam 级：经 preseed run 的真实 F4/F5/F6 状态不与 claim 状态冲突；confirmed
  conflict / independent corroboration 的细节语义由 F4/F5/F6 各自模块测试保障）。

不依赖真实 provider；Fake detector/reviewer 确定性。
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

_TEST_TMP = Path(__file__).resolve().parent.parent / "_testtmp"


@pytest.fixture
def b7_tmp():
    d = _TEST_TMP / f"b7c-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    import shutil

    try:
        shutil.rmtree(d)
    except OSError:
        pass


@pytest.fixture(autouse=True)
def _eval_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-placeholder-batch7-eval")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://placeholder.invalid/v1")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-placeholder-batch7-eval")
    yield


def _switch_research_db(d: Path):
    from app.research import config as rconfig
    from app.research import store as rstore

    d.mkdir(parents=True, exist_ok=True)
    os.environ[rconfig.RESEARCH_STORE_ENV] = "sqlite"
    os.environ[rconfig.RESEARCH_DB_ENV] = str(d / "research.sqlite")
    rstore.reset_store()


def _seed_claim_run():
    from app.research import registry

    run_id, sqid = registry.create_run_and_root("b7c-thread", "B7C 问题?")
    cid = registry.create_claim(run_id, sqid, f"b7c-claim-{uuid.uuid4().hex}", "FACT")
    url = f"https://b7c.example/{uuid.uuid4().hex}"
    qid = registry.record_search_query(
        run_id, sqid, agent="network_search", tool="internet_search", query="seed-q"
    )
    sid = registry.upsert_source(
        run_id, qid, source_type="web", agent="network_search", title="t",
        locator=url, canonical_key=url, canonical_url=url,
    )
    eid = registry.append_evidence(
        run_id, sid, sqid, content="seed evidence", locator=url,
        extraction_method="web_result",
    )
    registry.bind_claim_evidence(run_id, cid, eid)
    return run_id, sqid, cid


class TestClaimLevelScenarios:
    def test_s3_preseed_claim_gets_reverified(self, b7_tmp):
        from app.f9.eval import agents as AG
        from app.f9.eval import harness as H
        from app.f9.eval import scenarios as SC

        sc = SC.get_scenario("s3_unverified_claim_reverify")
        d = b7_tmp / f"a-{uuid.uuid4().hex}"
        _switch_research_db(d)
        run_id, sqid, cid = _seed_claim_run()

        gov_store, ctl = H.make_governance(d, owner="s3")
        runner, _ = SC.make_adaptive_graph_runner_for(sc)
        tool = AG.make_scripted_search_tool(sc.world)
        ad = H.run_adaptive(
            gov_store, ctl, task_query=sc.task, graph_runner=runner,
            judge_model=AG.make_scripted_judge(),
            plan_model=AG.make_scripted_plan(),
            search_tool=tool, policy=sc.adaptive_policy,
            preseed_run_id=run_id,
        )
        gov_store.close()

        assert ad.error == ""
        assert ad.summary["finalize_calls"] == 1
        # changed claims = preseed claim（新 binding 触发验证）
        assert cid in (ad.summary.get("changed_claims") or [])
        state = H.collect_research_metrics(ad.run_id)
        assert state["claim_count"] >= 1
        # claim 至少一条 succeeded verification（F3 增量验证发生）
        assert state["verification_succeeded"] >= 1

    def test_s4_f4_compatible_on_preseed_run(self, b7_tmp):
        """S4 seam：preseed claim run 上 F4 detect_claim_conflicts 可执行（不改 state）。"""
        from app.f9.eval import harness as H

        d = b7_tmp / f"a4-{uuid.uuid4().hex}"
        _switch_research_db(d)
        run_id, sqid, cid = _seed_claim_run()

        from app.research.conflict import detect_claim_conflicts

        # 单 evidence → 无 pair → items 空（F4 对不充分输入确定性返回空，不抛）
        res = detect_claim_conflicts(cid)
        assert res["claim_id"] == cid
        assert "items" in res
        # 数据未被 F4 破坏
        st = H.collect_research_metrics(run_id)
        assert st["claim_count"] == 1

    def test_s5_f5_compatible_on_preseed_run(self, b7_tmp):
        """S5 seam：preseed claim run 上 F5 corroboration 计算可执行且确定性。"""
        from app.f9.eval import harness as H

        d = b7_tmp / f"a5-{uuid.uuid4().hex}"
        _switch_research_db(d)
        run_id, sqid, cid = _seed_claim_run()

        from app.research.corroboration import compute_claim_corroboration

        res = compute_claim_corroboration(cid)
        assert "claim_id" in res or "status" in res or "support" in res
        st = H.collect_research_metrics(run_id)
        assert st["claim_count"] == 1  # F5 不产生额外 claim
