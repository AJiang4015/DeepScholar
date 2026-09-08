"""Provenance 反向链测试（F1-C）：evidence → source → search_query → sub_question → run。"""

from app.research import provenance, registry
from app.research.normalize import canonical_key_for


class TestProvenanceChain:
    def test_five_hop_chain(self, research_sqlite):
        run_id, sqid = registry.create_run_and_root("thread-9", "机器人行业趋势?")
        assert run_id and sqid
        query_id = registry.record_search_query(
            run_id,
            sqid,
            agent="network_search",
            tool="internet_search",
            query="robot industry 2026",
            topic="news",
        )
        source_id = registry.upsert_source(
            run_id,
            query_id,
            source_type="web",
            agent="network_search",
            title="Robot report",
            locator="https://example.com/robots",
            canonical_key="https://example.com/robots",
            canonical_url="https://example.com/robots",
        )
        evidence_id = registry.append_evidence(
            run_id,
            source_id,
            sqid,
            content="robots shipments grew",
            locator="https://example.com/robots",
            extraction_method="web_result",
        )

        chain = provenance.get_evidence_chain(evidence_id)
        assert chain is not None
        assert chain["evidence"]["evidence_id"] == evidence_id
        assert chain["source"]["source_id"] == source_id
        assert chain["source"]["canonical_url"] == "https://example.com/robots"
        assert chain["search_query"]["query_id"] == query_id
        assert chain["search_query"]["query"] == "robot industry 2026"
        assert chain["sub_question"]["sub_question_id"] == sqid
        assert chain["run"]["run_id"] == run_id
        assert chain["run"]["question"] == "机器人行业趋势?"

    def test_missing_evidence_returns_none(self, research_sqlite):
        registry.create_run_and_root("t", "q")
        assert provenance.get_evidence_chain("not-exist") is None

    def test_db_chain_from_evidence(self, research_sqlite):
        run_id, sqid = registry.create_run_and_root("t", "药品库存?")
        qid = registry.record_search_query(
            run_id, sqid, agent="database", tool="execute_sql_query", query="SELECT 1"
        )
        sid = registry.upsert_source(
            run_id,
            qid,
            source_type="db",
            agent="database",
            title="数据库查询结果",
            locator=qid,
            canonical_key=canonical_key_for("db", query_id=qid),
        )
        eid = registry.append_evidence(
            run_id,
            sid,
            sqid,
            content="1",
            locator=f"db:{qid}",
            extraction_method="db_result",
        )
        chain = provenance.get_evidence_chain(eid)
        assert chain["source"]["source_type"] == "db"
        assert chain["search_query"]["agent"] == "database"
        assert chain["run"]["thread_id"] == "t"
