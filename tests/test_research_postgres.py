"""Research store（PostgreSQL）集成测试（RESEARCH_DSN_TEST 门控）。

覆盖 F1 PostgreSQL Gate 全链路（Spec §16 要求 + 双后端一致性视角）：
schema/migration（0001+0002 幂等）、五跳 provenance、Web/DB/RAGFlow 注册、
Source 幂等、run isolation、missing run、8k truncation、FK、ON DELETE CASCADE。

环境：RESEARCH_DSN_TEST 只允许指向独立测试库；本测试不使用生产库。
"""

import os
import uuid

import pytest

PG_TEST_DSN_ENV = "RESEARCH_DSN_TEST"

try:
    import psycopg  # noqa: F401

    HAS_PSYCOPG = True
except Exception:  # pragma: no cover
    HAS_PSYCOPG = False

needs_pg = pytest.mark.skipif(
    not (HAS_PSYCOPG and os.getenv(PG_TEST_DSN_ENV)),
    reason=f"需要 psycopg 与 {PG_TEST_DSN_ENV}（独立测试库）",
)

from app.research import migrations, provenance, registry, store as rstore  # noqa: E402
from app.research.normalize import canonical_key_for  # noqa: E402
from app.research.registry import ResearchRunNotFoundError  # noqa: E402


def _dsn() -> str:
    return os.getenv(PG_TEST_DSN_ENV)


@needs_pg
class TestResearchPostgres:
    def _bind_store(self):
        old = rstore._instance
        store = rstore._PostgresStore(_dsn())
        migrations.ensure_schema(store)
        rstore._instance = store
        self._old_instance = old
        return store

    def _cleanup(self):
        old = getattr(self, "_old_instance", None)
        if old is not None:
            rstore._instance = old

    def test_schema_and_migration_idempotent(self):
        store = self._bind_store()
        try:
            assert store.dialect == "postgres"
            assert sorted(migrations.applied_migration_versions(store)) == [
                "0001",
                "0002",
                "0003",
                "0004",
                "0005",
                "0006",
            ]
            migrations.ensure_schema(store)  # 幂等
            assert sorted(migrations.applied_migration_versions(store)) == [
                "0001",
                "0002",
                "0003",
                "0004",
                "0005",
                "0006",
            ]
            rows = store.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name IN "
                "('research_runs','sub_questions','search_queries','sources',"
                "'evidences','schema_migrations')"
            )
            names = {r["table_name"] for r in rows}
            assert {
                "research_runs",
                "sub_questions",
                "search_queries",
                "sources",
                "evidences",
                "schema_migrations",
            } <= names
        finally:
            self._cleanup()

    def _web_run(self, prefix="pg"):
        run_id = f"{prefix}-{uuid.uuid4().hex[:12]}"
        run_id, sqid = registry.create_run_and_root(
            "pg-thread", f"{prefix} 问题?", run_id=run_id
        )
        return run_id, sqid

    def test_full_web_db_ragflow_chain(self):
        self._bind_store()
        try:
            run_id, sqid = self._web_run()
            q_web = registry.record_search_query(
                run_id,
                sqid,
                agent="network_search",
                tool="internet_search",
                query="trend",
            )
            web_sids = []
            for i in range(3):
                sid = registry.upsert_source(
                    run_id,
                    q_web,
                    source_type="web",
                    agent="network_search",
                    title=f"pg web {i}",
                    locator=f"https://pg.example/{i}",
                    canonical_key=f"https://pg.example/{i}",
                    canonical_url=f"https://pg.example/{i}",
                    metadata={"score": 0.9 - i / 10},
                )
                web_sids.append(sid)
                registry.append_evidence(
                    run_id,
                    sid,
                    sqid,
                    content=f"web content {i}",
                    locator=f"https://pg.example/{i}",
                    extraction_method="web_result",
                    metadata={"score": 0.9 - i / 10},
                )
            q_db = registry.record_search_query(
                run_id,
                sqid,
                agent="database",
                tool="execute_sql_query",
                query="SELECT 1",
            )
            s_db = registry.upsert_source(
                run_id,
                q_db,
                source_type="db",
                agent="database",
                title="数据库查询结果",
                locator=q_db,
                canonical_key=canonical_key_for("db", query_id=q_db),
            )
            registry.append_evidence(
                run_id,
                s_db,
                sqid,
                content="1",
                locator=f"db:{q_db}",
                extraction_method="db_result",
            )
            q_rag = registry.record_search_query(
                run_id, sqid, agent="ragflow", tool="create_ask_delete", query="q?"
            )
            s_rag = registry.upsert_source(
                run_id,
                q_rag,
                source_type="ragflow",
                agent="ragflow",
                title="行业助手",
                locator="ragflow:行业助手",
                canonical_key=canonical_key_for("ragflow", assistant="行业助手"),
            )
            registry.append_evidence(
                run_id,
                s_rag,
                sqid,
                content="rag answer",
                locator="ragflow:chat-1",
                extraction_method="rag_answer",
            )

            assert len(provenance.list_sources(run_id)) == 5
            assert len(provenance.list_evidences(run_id)) == 5
            assert len(provenance.list_sources(run_id, source_type="web")) == 3
            again = registry.upsert_source(
                run_id,
                q_web,
                source_type="web",
                agent="network_search",
                title="pg web 0 again",
                locator="https://pg.example/0",
                canonical_key="https://pg.example/0",
                canonical_url="https://pg.example/0",
            )
            assert again == web_sids[0]
            with rstore.get_store().transaction() as tx:
                tx.execute("DELETE FROM research_runs WHERE run_id = %s", (run_id,))
        finally:
            self._cleanup()

    def test_truncation_and_metadata_flag(self):
        self._bind_store()
        try:
            run_id, sqid = self._web_run("tr")
            qid = registry.record_search_query(
                run_id, sqid, agent="database", tool="s", query="q"
            )
            sid = registry.upsert_source(
                run_id,
                qid,
                source_type="db",
                agent="database",
                title="t",
                locator=qid,
                canonical_key=f"db:{qid}",
            )
            registry.append_evidence(
                run_id,
                sid,
                sqid,
                content="x" * 10000,
                locator="l",
                extraction_method="db_result",
            )
            evs = provenance.list_evidences(run_id)
            assert len(evs) == 1
            assert len(evs[0].content) == 8000
            assert evs[0].metadata.get("truncated") is True
            with rstore.get_store().transaction() as tx:
                tx.execute("DELETE FROM research_runs WHERE run_id = %s", (run_id,))
        finally:
            self._cleanup()

    def test_missing_run_rejected(self):
        self._bind_store()
        try:
            with pytest.raises(ResearchRunNotFoundError):
                registry.record_search_query(
                    "no-such-run", "x", agent="database", tool="s", query="q"
                )
        finally:
            self._cleanup()

    def test_run_isolation_and_cascade(self):
        store = self._bind_store()
        try:
            run1, sq1 = self._web_run("iso1")
            registry.record_search_query(
                run1, sq1, agent="database", tool="s", query="q1"
            )
            run2, _sq2 = self._web_run("iso2")
            assert provenance.list_sources(run2) == []
            with store.transaction() as tx:
                tx.execute("DELETE FROM research_runs WHERE run_id = %s", (run1,))
            assert provenance.get_run(run1) is None
            assert provenance.list_sources(run1) == []
            assert provenance.list_evidences(run1) == []
            with store.transaction() as tx:
                tx.execute("DELETE FROM research_runs WHERE run_id = %s", (run2,))
        finally:
            self._cleanup()

    def test_fk_enforced(self):
        store = self._bind_store()
        try:
            with pytest.raises(Exception):
                with store.transaction() as tx:
                    tx.execute(
                        "INSERT INTO sources (source_id, run_id, query_id, source_type, title,"
                        " locator, canonical_key, fetched_at, agent, metadata) VALUES"
                        " ('s1','no-run','no-query','web','t','l','k',"
                        " '2026-01-01T00:00:00+00:00','a','{}')"
                    )
        finally:
            self._cleanup()

    def test_registry_and_five_hop_provenance(self):
        self._bind_store()
        try:
            run_id, sqid = self._web_run()
            qid = registry.record_search_query(
                run_id,
                sqid,
                agent="network_search",
                tool="internet_search",
                query="trend 2026",
            )
            sid = registry.upsert_source(
                run_id,
                qid,
                source_type="web",
                agent="network_search",
                title="PG source",
                locator="https://pg.example/a",
                canonical_key="https://pg.example/a",
                canonical_url="https://pg.example/a",
            )
            eid = registry.append_evidence(
                run_id,
                sid,
                sqid,
                content="pg evidence body",
                locator="https://pg.example/a",
                extraction_method="web_result",
            )
            chain = provenance.get_evidence_chain(eid)
            assert chain is not None
            assert chain["source"]["title"] == "PG source"
            assert chain["run"]["run_id"] == run_id
            assert chain["search_query"]["agent"] == "network_search"
            assert chain["sub_question"]["sub_question_id"] == sqid
            with rstore.get_store().transaction() as tx:
                tx.execute("DELETE FROM research_runs WHERE run_id = %s", (run_id,))
        finally:
            self._cleanup()
