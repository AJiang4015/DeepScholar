"""Registry 测试（F1）：run/root、Web N sources→N evidences、DB、RAGFlow、
Source 幂等、cross-run 隔离、missing run 拒绝、截断。"""

import pytest

from app.research import provenance, registry
from app.research.registry import (
    ResearchRunNotFoundError,
    append_evidence,
    create_run_and_root,
    record_search_query,
    set_run_status,
    upsert_source,
)


def _web_fixture_source(canonical_url="https://a.com/x?utm_source=g"):
    return dict(
        source_type="web",
        agent="network_search",
        title="Title A",
        locator=canonical_url,
        canonical_key=canonical_url,
        canonical_url=canonical_url,
    )


class TestRunLifecycle:
    def test_create_run_and_root(self, research_sqlite):
        run_id, sqid = create_run_and_root("thread-1", "用户问题", run_id="r1")
        assert run_id == "r1"
        run = provenance.get_run("r1")
        assert run is not None
        assert run.thread_id == "thread-1"
        assert run.status == "running"
        assert run.question == "用户问题"
        subs = provenance.list_sub_questions("r1")
        assert len(subs) == 1
        assert subs[0].sub_question_id == sqid
        assert subs[0].position == 0
        assert subs[0].question == "用户问题"

    def test_set_run_status_finished(self, research_sqlite):
        run_id, _ = create_run_and_root("t", "q")
        set_run_status(run_id, "finished")
        assert provenance.get_run(run_id).status == "finished"
        assert provenance.get_run(run_id).finished_at is not None

    def test_missing_run_rejected_on_write(self, research_sqlite):
        with pytest.raises(ResearchRunNotFoundError):
            record_search_query(
                "nope", "nope", agent="database", tool="x", query="select 1"
            )


class TestWebFlow:
    def test_web_registers_query_sources_evidences(self, research_sqlite):
        run_id, sqid = create_run_and_root("t", "q")
        query_id = record_search_query(
            run_id,
            sqid,
            agent="network_search",
            tool="internet_search",
            query="robots",
            topic="general",
        )
        assert query_id
        urls = ["https://a.com/1", "https://a.com/2"]
        for i, url in enumerate(urls):
            sid = upsert_source(
                run_id,
                query_id,
                **_web_fixture_source(canonical_url=url),
                published_at="2026-01-01T00:00:00+00:00",
                metadata={"score": 0.9 - i / 10},
            )
            assert sid
            eid = append_evidence(
                run_id,
                sid,
                sqid,
                content=f"content {i}",
                locator=url,
                extraction_method="web_result",
                metadata={"score": 0.9 - i / 10},
            )
            assert eid
        assert len(provenance.list_sources(run_id, source_type="web")) == 2
        assert len(provenance.list_evidences(run_id)) == 2
        # query 记录在案
        from app.research import store as rstore

        rows = rstore.get_store().execute(
            "SELECT query_id FROM search_queries WHERE run_id=%s", (run_id,)
        )
        assert len(rows) == 1

    def test_source_duplicate_returns_same_id(self, research_sqlite):
        run_id, sqid = create_run_and_root("t", "q")
        query_id = record_search_query(
            run_id, sqid, agent="network_search", tool="s", query="q"
        )
        kwargs = _web_fixture_source()
        first = upsert_source(run_id, query_id, **kwargs)
        second = upsert_source(run_id, query_id, **kwargs)
        assert first == second

    def test_duplicate_url_across_queries_same_run_dedupes(self, research_sqlite):
        run_id, sqid = create_run_and_root("t", "q")
        q1 = record_search_query(
            run_id, sqid, agent="network_search", tool="s", query="q1"
        )
        q2 = record_search_query(
            run_id, sqid, agent="network_search", tool="s", query="q2"
        )
        kwargs = _web_fixture_source(canonical_url="https://a.com/same")
        s1 = upsert_source(run_id, q1, **kwargs)
        s2 = upsert_source(run_id, q2, **kwargs)
        assert s1 == s2  # 同 run + 同 canonical_key → 同 source_id（跨 query 收敛）


class TestDbAndRagFlows:
    def test_db_query_source_evidence(self, research_sqlite):
        run_id, sqid = create_run_and_root("t", "q")
        query_id = record_search_query(
            run_id,
            sqid,
            agent="database",
            tool="execute_sql_query",
            query="SELECT name FROM drugs",
            metadata={"rows": 3, "columns_head": ["name"]},
        )
        from app.research.normalize import canonical_key_for

        sid = upsert_source(
            run_id,
            query_id,
            source_type="db",
            agent="database",
            title="数据库查询结果",
            locator=query_id,
            canonical_key=canonical_key_for("db", query_id=query_id),
            metadata={"sql_sha256": registry.sha256_hex("SELECT name FROM drugs")},
        )
        eid = append_evidence(
            run_id,
            sid,
            sqid,
            content="name\n阿司匹林\n布洛芬",
            locator=f"db:{query_id}",
            extraction_method="db_result",
        )
        assert eid
        evs = provenance.list_evidences(run_id)
        assert len(evs) == 1
        assert evs[0].extraction_method == "db_result"

    def test_ragflow_query_source_evidence(self, research_sqlite):
        run_id, sqid = create_run_and_root("t", "q")
        query_id = record_search_query(
            run_id,
            sqid,
            agent="ragflow",
            tool="create_ask_delete",
            query="2026 趋势?",
            metadata={"chat_name": "电商行业助手"},
        )
        from app.research.normalize import canonical_key_for

        sid = upsert_source(
            run_id,
            query_id,
            source_type="ragflow",
            agent="ragflow",
            title="电商行业助手",
            locator="ragflow:电商行业助手",
            canonical_key=canonical_key_for("ragflow", assistant="电商行业助手"),
            metadata={"assistant_id": "chat-1"},
        )
        append_evidence(
            run_id,
            sid,
            sqid,
            content="回答文本",
            locator="ragflow:chat-1",
            extraction_method="rag_answer",
        )
        sources = provenance.list_sources(run_id, source_type="ragflow")
        assert len(sources) == 1
        assert sources[0].title == "电商行业助手"


class TestCrossRunAndTruncation:
    def test_cross_run_isolation(self, research_sqlite):
        run1, _ = create_run_and_root("t1", "q1")
        run2, _ = create_run_and_root("t2", "q2")
        assert run1 != run2
        qid = record_search_query(
            run1,
            provenance.list_sub_questions(run1)[0].sub_question_id,
            agent="network_search",
            tool="s",
            query="x",
        )
        assert provenance.list_sources(run2) == []
        assert provenance.list_evidences(run2) == []
        assert qid is not None

    def test_evidence_content_truncation(self, research_sqlite):
        run_id, sqid = create_run_and_root("t", "q")
        qid = record_search_query(run_id, sqid, agent="database", tool="s", query="q")
        long = "x" * 10000
        eid = append_evidence(
            run_id,
            "missing-source",  # 先制造失败？不：直接正常路径
            sqid,
            content=long,
            locator="l",
            extraction_method="db_result",
        )
        # source 不存在 → FK 拒绝 → fail-open None
        assert eid is None
        sid = upsert_source(
            run_id,
            qid,
            source_type="db",
            agent="database",
            title="t",
            locator=qid,
            canonical_key=f"db:{qid}",
        )
        assert sid is not None
        append_evidence(
            run_id, sid, sqid, content=long, locator="l", extraction_method="db_result"
        )
        evs = provenance.list_evidences(run_id)
        assert len(evs) == 1
        assert len(evs[0].content) == 8000
        assert evs[0].metadata.get("truncated") is True
