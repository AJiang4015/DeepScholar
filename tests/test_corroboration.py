"""F5 Independent Evidence Corroboration — SQLite 全量测试（rev2 冻结契约）。

覆盖：0005 schema/迁移/幂等、global clustering（R-URL/R-DOMAIN/R-TITLE/R-SHINGLE、
OR 语义、union-find chaining、deterministic cluster key）、support/contradict 同一聚类投影、
转载跨两侧共享簇、计数（4 sources/3 independent）、cross-side independence、
reviewer 失败→complete+review_failed、deterministic（gate）失败→failed、
fingerprint 幂等与升级、source_profile descriptive 无 score 字段、只读 API。

核心原则：F5 measures independence; it does not score trust.
"""

import json
import os
import sqlite3

from app.research import migrations, registry, verify as f3verify
from app.research.config import RESEARCH_DB_ENV
from app.research.corroboration import (
    DEFAULT_METHOD,
    BaseReviewer,
    CorroborationMethod,
    FakeReviewer,
    ReviewerRuntimeError,
    compute_claim_corroboration,
    compute_run_corroborations,
    get_corroboration,
    jaccard,
    list_corroborations,
    normalize_title,
    _pair_hits_rules,
    _path_prefix,
)
from app.research.normalize import canonical_key_for
from app.research.schemas import ClaimType
from app.research.verify import FakeVerifier as F3Fake
from tests._f2_helpers import make_run


def _claim(run_id, sqid, statement):
    return registry.create_claim(run_id, sqid, statement, ClaimType.FACT)


def _web(run_id, sqid, url, title, content):
    qid = registry.record_search_query(
        run_id, sqid, agent="network_search", tool="internet_search", query="q"
    )
    sid = registry.upsert_source(
        run_id,
        qid,
        source_type="web",
        agent="network_search",
        title=title,
        locator=url,
        canonical_key=canonical_key_for("web", canonical_url=url),
        canonical_url=url,
    )
    return registry.append_evidence(
        run_id, sid, sqid, content=content, locator=url, extraction_method="web_result"
    )


def _bind_verify(run_id, cid, verdict_map):
    """绑定 claim↔evidence 并对 claim 跑 F3 verification（verdict_map 按 evidence_id）。"""
    for eid in verdict_map:
        registry.bind_claim_evidence(run_id, cid, eid)
    f3verify.semantic_verify_claim(
        cid,
        verifier=F3Fake(
            resolver=lambda p: verdict_map.get(p["evidence"]["evidence_id"], "ABSTAIN")
        ),
    )


def _source_dict(**kwargs):
    base = {
        "source_id": "s",
        "canonical_key": "",
        "canonical_url": "",
        "title": "",
        "content": "",
    }
    base.update(kwargs)
    return base


class TestSchemaAndMigration:
    def test_migration_and_table(self, research_sqlite):
        from app.research import store as rstore

        store = rstore.get_store()
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
        conn = sqlite3.connect(os.environ[RESEARCH_DB_ENV])
        cols = {r[1] for r in conn.execute("PRAGMA table_info(corroborations)")}
        conn.close()
        assert {
            "corroboration_id",
            "run_id",
            "claim_id",
            "method_spec",
            "method_fingerprint",
            "status",
            "error",
            "computed_at",
            "metadata",
            "global_clusters",
            "support",
            "contradict",
            "conflicts_independence",
            "source_profile",
        } <= cols

    def test_unique_fingerprint_constraint(self, research_sqlite):
        """同 (run, claim, fingerprint) 只能有一行（identity 约束）。"""
        from app.research import store as rstore

        store = rstore.get_store()
        run_id, sqid = make_run("f5u")
        ev = _web(run_id, sqid, "https://u.example/1", "U", "unique body one")
        cid = _claim(run_id, sqid, "唯一约束 claim")
        _bind_verify(run_id, cid, {ev: "SUPPORTS"})
        compute_claim_corroboration(cid)
        compute_claim_corroboration(cid)
        rows = store.execute(
            "SELECT count(*) AS n FROM corroborations WHERE run_id=%s AND claim_id=%s",
            (run_id, cid),
        )
        assert rows[0]["n"] == 1


class TestTextRulesUnit:
    def test_normalize_title_preserves_cjk_and_drops_noise(self):
        # 括号/空白/标点噪声归一（NFKC + 去分隔符），CJK 保留
        assert normalize_title("（苹果）发布 iPhone16 Pro") == normalize_title(
            "苹果 发布 iPhone16 Pro"
        )
        # 日期后缀剥离
        assert normalize_title("报告 2026-09-11 发布") == "报告发布"
        assert "苹果" in normalize_title("苹果发布 iPhone16")
        assert normalize_title("Hello, World!") == "helloworld"
        assert normalize_title(None) == ""
        assert normalize_title("") == ""

    def test_path_prefix(self):
        assert _path_prefix("https://a.example/x/y/z/1")[1] == "x/y/z"
        assert _path_prefix("https://a.example/")[1] == ""
        assert _path_prefix("https://a.example")[1] == ""
        assert _path_prefix("https://b.example/x")[0] == "b.example"

    def test_jaccard_empty(self):
        assert jaccard(set(), set()) == 0.0

    def test_pair_rules_matrix(self):
        m = DEFAULT_METHOD
        # R-URL：canonical_key 相同
        assert _pair_hits_rules(
            _source_dict(canonical_key="k1", canonical_url="https://a.example/x"),
            _source_dict(canonical_key="k1", canonical_url="https://a.example/x?q=1"),
            m,
        )
        # R-DOMAIN：同 domain 且 path 前 3 段相同
        assert _pair_hits_rules(
            _source_dict(
                canonical_key="k-a",
                canonical_url="https://news.example/sec/2026/01/a",
                title="T-A",
                content="aaa",
            ),
            _source_dict(
                canonical_key="k-b",
                canonical_url="https://news.example/sec/2026/01/b",
                title="T-B",
                content="bbb",
            ),
            m,
        )
        # R-DOMAIN：同 domain 且某侧 path 为空 → 同簇
        assert _pair_hits_rules(
            _source_dict(canonical_key="k-root", canonical_url="https://news.example/"),
            _source_dict(
                canonical_key="k-c", canonical_url="https://news.example/sec/2026/01/c"
            ),
            m,
        )
        # R-TITLE：归一化相等（跨域名、正文不同）
        assert _pair_hits_rules(
            _source_dict(
                canonical_key="k1",
                canonical_url="https://a.example/1",
                title="iPhone 16 Pro 评测",
                content="aaaaa",
            ),
            _source_dict(
                canonical_key="k2",
                canonical_url="https://b.example/1",
                title="iPhone16 Pro评测",
                content="bbbbb",
            ),
            m,
        )
        # R-SHINGLE：长正文几乎相同（转载）
        body = "统一转载正文内容 repeated long enough for 5-gram shingles. " * 4
        assert _pair_hits_rules(
            _source_dict(
                canonical_key="k-x",
                canonical_url="https://x.example/1",
                title="标题 X",
                content=body,
            ),
            _source_dict(
                canonical_key="k-y",
                canonical_url="https://y.example/1",
                title="标题 Y",
                content=body,
            ),
            m,
        )
        # 全不命中 → False（域名/标题/正文均无相似）
        assert not _pair_hits_rules(
            _source_dict(
                canonical_key="k-a",
                canonical_url="https://aaa.example/1",
                title="Title Alpha",
                content="aaaaaaaaaa",
            ),
            _source_dict(
                canonical_key="k-b",
                canonical_url="https://bbb.example/2",
                title="Title Beta",
                content="bbbbbbbbbb",
            ),
            m,
        )

    def test_pair_rules_threshold_obeys_method(self):
        loose = CorroborationMethod(title_jaccard=0.4, shingle_jaccard=0.1)
        tight = CorroborationMethod(title_jaccard=1.01, shingle_jaccard=1.01)
        a = _source_dict(
            canonical_key="k-a",
            canonical_url="https://a.example/1",
            title="aaabbbccc",
            content="11111",
        )
        b = _source_dict(
            canonical_key="k-b",
            canonical_url="https://b.example/1",
            title="aaabbbccc ddd",
            content="22222",
        )
        # 字符集 Jaccard({"a","b","c"}, {"a","b","c","d"})=0.75 ≥0.4 且 <1.01
        assert _pair_hits_rules(a, b, loose)
        assert not _pair_hits_rules(a, b, tight)


class TestGlobalClustering:
    def test_same_title_reprint_clusters_across_domains(self, research_sqlite):
        run_id, sqid = make_run("f5t")
        ev_a = _web(
            run_id, sqid, "https://reporter.example/2026/a", "同一篇报道标题", "x" * 40
        )
        ev_b = _web(
            run_id, sqid, "https://mirror.example/2026/b", "同一篇报道标题", "x" * 40
        )
        ev_c = _web(run_id, sqid, "https://other.example/1", "另一篇报道", "y" * 40)
        cid = _claim(run_id, sqid, "标题转载聚类 claim")
        _bind_verify(
            run_id, cid, {ev_a: "SUPPORTS", ev_b: "SUPPORTS", ev_c: "SUPPORTS"}
        )
        res = compute_claim_corroboration(cid)
        assert res["status"] == "complete"
        clusters = res["global_clusters"]
        assert len(clusters) == 2  # {a,b} 转载同簇 + {c}
        two = [c for c in clusters if len(c["member_source_ids"]) == 2]
        assert len(two) == 1
        assert set(two[0]["member_evidence_ids"]) == {ev_a, ev_b}
        assert res["support"]["source_count"] == 3
        assert res["support"]["independent_count"] == 2

    def test_shingle_reprint_clusters(self, research_sqlite):
        run_id, sqid = make_run("f5sh")
        body = (
            "全文转载的新闻报道正文内容用于 shingle 相似度判定。"
            "全文转载的新闻报道正文内容用于 shingle 相似度判定。"
            "全文转载的新闻报道正文内容用于 shingle 相似度判定。"
        )
        ev_a = _web(run_id, sqid, "https://x.example/a", "标题 X", body)
        ev_b = _web(run_id, sqid, "https://y.example/b", "标题 Y", body)
        cid = _claim(run_id, sqid, "正文转载聚类 claim")
        _bind_verify(run_id, cid, {ev_a: "SUPPORTS", ev_b: "SUPPORTS"})
        res = compute_claim_corroboration(cid)
        clusters = res["global_clusters"]
        assert len(clusters) == 1
        assert len(clusters[0]["member_source_ids"]) == 2
        assert len(clusters[0]["member_evidence_ids"]) == 2
        assert res["support"]["source_count"] == 2
        assert res["support"]["independent_count"] == 1

    def test_union_find_chaining_a_b_c(self, research_sqlite):
        """A≈B（R-DOMAIN）、B≈C（R-TITLE）、A≉C → union-find 归同一簇（AC6 chaining）。"""
        run_id, sqid = make_run("f5chain")
        # A、B 同域名且 path 前 3 段相同 → R-DOMAIN 命中（与标题/正文无关）
        ev_a = _web(
            run_id,
            sqid,
            "https://news.example/sec/2026/01/a",
            "标题 Alpha 完全不同",
            "11111111111111",
        )
        ev_b = _web(
            run_id,
            sqid,
            "https://news.example/sec/2026/01/b",
            "转载标题 Gamma",
            "22222222222222",
        )
        # C 与 B 标题相同（不同域名）→ R-TITLE 命中；C 与 A 无任何规则命中
        ev_c = _web(
            run_id,
            sqid,
            "https://mirror.example/2026/c",
            "转载标题 Gamma",
            "33333333333333",
        )
        assert not _pair_hits_rules(
            _source_dict(
                canonical_key="k-a",
                canonical_url="https://news.example/sec/2026/01/a",
                title="标题 Alpha 完全不同",
                content="11111111111111",
            ),
            _source_dict(
                canonical_key="k-c",
                canonical_url="https://mirror.example/2026/c",
                title="转载标题 Gamma",
                content="33333333333333",
            ),
            DEFAULT_METHOD,
        )
        cid = _claim(run_id, sqid, "chain 三源 claim")
        _bind_verify(
            run_id,
            cid,
            {ev_a: "SUPPORTS", ev_b: "SUPPORTS", ev_c: "SUPPORTS"},
        )
        res = compute_claim_corroboration(cid)
        assert res["status"] == "complete"
        clusters = res["global_clusters"]
        assert len(clusters) == 1
        assert len(clusters[0]["member_source_ids"]) == 3
        assert res["support"]["independent_count"] == 1

    def test_cluster_key_deterministic_and_rerun_identical(self, research_sqlite):
        from app.research import store as rstore

        store = rstore.get_store()
        run_id, sqid = make_run("f5det")
        ev_a = _web(run_id, sqid, "https://alpha.example/1", "重复标题", "alpha body")
        ev_b = _web(run_id, sqid, "https://beta.example/1", "重复标题", "beta body")
        cid = _claim(run_id, sqid, "确定性 claim")
        _bind_verify(run_id, cid, {ev_a: "SUPPORTS", ev_b: "CONTRADICTS"})
        first = compute_claim_corroboration(cid)
        # 幂等复用：同一指纹 → 同一 artifact
        again = compute_claim_corroboration(cid)
        assert again["corroboration_id"] == first["corroboration_id"]
        assert again["global_clusters"] == first["global_clusters"]
        # 删除后重算 → cluster 划分/键完全一致（deterministic，与 artifact 无关）
        with store.transaction() as tx:
            tx.execute(
                "DELETE FROM corroborations WHERE run_id=%s AND claim_id=%s",
                (run_id, cid),
            )
        recomputed = compute_claim_corroboration(cid)
        assert recomputed["status"] == "complete"
        assert recomputed["global_clusters"] == first["global_clusters"]
        assert recomputed["support"] == first["support"]
        assert recomputed["contradict"] == first["contradict"]
        assert recomputed["source_profile"] == first["source_profile"]
        # cluster 键符合 min canonical_key + #n 形态
        for c in first["global_clusters"]:
            assert c["cluster_id"] == c["cluster_key"]
            assert c["cluster_id"].endswith("#1") or c["cluster_id"].endswith("#2")


class TestCountingAndProjection:
    def test_counting_4_sources_2_shared_cluster(self, research_sqlite):
        """规范示例：A/B 转载同簇 + C、D 独立 → source_count=4、independent_count=3。"""
        run_id, sqid = make_run("f5cnt")
        ev_a = _web(run_id, sqid, "https://pa.example/2026/a", "同标题 T", "body A")
        ev_b = _web(run_id, sqid, "https://pb.example/2026/b", "同标题 T", "body B")
        ev_c = _web(run_id, sqid, "https://pc.example/1", "标题 C 独立", "body C")
        ev_d = _web(run_id, sqid, "https://pd.example/1", "标题 D 独立", "body D")
        cid = _claim(run_id, sqid, "4 源计数 claim")
        _bind_verify(
            run_id,
            cid,
            {
                ev_a: "SUPPORTS",
                ev_b: "SUPPORTS",
                ev_c: "SUPPORTS",
                ev_d: "SUPPORTS",
            },
        )
        res = compute_claim_corroboration(cid)
        sup = res["support"]
        assert sup["source_count"] == 4
        assert sup["independent_count"] == 3
        assert len(sup["cluster_ids"]) == 3
        assert res["contradict"]["source_count"] == 0
        assert res["contradict"]["independent_count"] == 0
        # 成员侧投影与 cluster 侧统计一致
        n_shared = sum(
            1
            for c in res["global_clusters"]
            if len(c["member_source_ids"]) == 2 and c["member_side"] == "support"
        )
        assert n_shared == 1

    def test_global_cluster_cross_side_shared_and_not_winner(self, research_sqlite):
        """转载跨 SUPPORTS/CONTRADICTS：同一 global cluster，两侧 cluster_id 相同（AC3）；
        且仅计量，无 winner/可信解释。"""
        run_id, sqid = make_run("f5cross")
        # 同标题 → 同簇，但一侧 SUPPORTS、一侧 CONTRADICTS
        ev_a = _web(
            run_id,
            sqid,
            "https://pos.example/2026/a",
            "同一转载标题 X",
            "body positive",
        )
        ev_b = _web(
            run_id,
            sqid,
            "https://neg.example/2026/b",
            "同一转载标题 X",
            "body negative",
        )
        cid = _claim(run_id, sqid, "跨侧共享簇 claim")
        _bind_verify(run_id, cid, {ev_a: "SUPPORTS", ev_b: "CONTRADICTS"})
        res = compute_claim_corroboration(cid)
        cluster = res["global_clusters"][0]
        assert cluster["member_side"] == "both"
        cid_key = cluster["cluster_id"]
        assert cid_key in res["support"]["cluster_ids"]
        assert cid_key in res["contradict"]["cluster_ids"]
        assert res["support"]["independent_count"] == 1
        assert res["contradict"]["independent_count"] == 1

    def test_global_clustering_shared_universe_not_split(self, research_sqlite):
        """AC2：SUPPORTS 与 CONTRADICTS 使用同一 global clustering（非分别聚类）——
        cluster_id 在两视图一致；不存在仅属于某一侧的重复簇。"""
        run_id, sqid = make_run("f5ac2")
        ev_s1 = _web(run_id, sqid, "https://s1.example/a", "S1 标题", "s1 body")
        ev_s2 = _web(run_id, sqid, "https://s2.example/b", "S2 标题", "s2 body")
        ev_c1 = _web(run_id, sqid, "https://c1.example/a", "C1 标题", "c1 body")
        cid = _claim(run_id, sqid, "投影一致性 claim")
        _bind_verify(
            run_id,
            cid,
            {ev_s1: "SUPPORTS", ev_s2: "SUPPORTS", ev_c1: "CONTRADICTS"},
        )
        res = compute_claim_corroboration(cid)
        cluster_ids = [c["cluster_id"] for c in res["global_clusters"]]
        assert set(cluster_ids) == set(res["support"]["cluster_ids"]) | set(
            res["contradict"]["cluster_ids"]
        )


class TestConflictIndependence:
    def test_conflict_within_same_cluster_is_not_independent(self, research_sqlite):
        """转载链上两侧：同一 global cluster → independent_between_sides=False（AC5/AC3）。"""
        from app.research.conflict import (
            FakeDetector,
            detect_claim_conflicts,
        )

        cid, _ = self._setup_conflict(run="f5c1", same_cluster=True)
        detect_claim_conflicts(
            cid, detector=FakeDetector(conflict_type="CONTRADICTION")
        )
        res = compute_claim_corroboration(cid)
        assert res["status"] == "complete"
        assert len(res["global_clusters"]) == 1  # 两侧同簇
        assert len(res["conflicts_independence"]) == 1
        item = res["conflicts_independence"][0]
        assert item["independent_between_sides"] is False
        assert item["side_a"]["cluster_ids"] == item["side_b"]["cluster_ids"]

    def test_conflict_between_independent_clusters(self, research_sqlite):
        """真正独立的两个簇 → independent_between_sides=True（仅计量，非 winner/可信）。"""
        from app.research.conflict import (
            FakeDetector,
            detect_claim_conflicts,
        )

        cid, _ = self._setup_conflict(run="f5c2", same_cluster=False)
        detect_claim_conflicts(
            cid, detector=FakeDetector(conflict_type="CONTRADICTION")
        )
        res = compute_claim_corroboration(cid)
        assert len(res["global_clusters"]) == 2
        item = res["conflicts_independence"][0]
        assert item["independent_between_sides"] is True
        assert item["side_a"]["cluster_ids"] != item["side_b"]["cluster_ids"]

    def _setup_conflict(self, run, same_cluster):
        run_id, sqid = make_run(run)
        if same_cluster:
            # 转载：同标题、不同域名（两个独立 source_id，但同 global cluster）
            title_pos = title_neg = f"转载标题 {run}"
        else:
            title_pos = f"正面独立标题 {run}"
            title_neg = f"负面独立标题 {run}"
        ev_pos = _web(
            run_id, sqid, f"https://pos-{run}.example/story", title_pos, "pos body"
        )
        ev_neg = _web(
            run_id, sqid, f"https://neg-{run}.example/story", title_neg, "neg body"
        )
        cid = _claim(run_id, sqid, f"冲突独立性 {run}")
        _bind_verify(run_id, cid, {ev_pos: "SUPPORTS", ev_neg: "CONTRADICTS"})
        return cid, (run_id, ev_pos, ev_neg)


class TestFailureSemantics:
    def test_review_failure_keeps_complete_with_flag(self, research_sqlite):
        """AC7：review 失败 → status=complete + metadata.review_failed=true（携带 reason）。"""
        method = CorroborationMethod(review_enabled=True, max_review_pairs=5)

        class _BrokenReviewer(BaseReviewer):
            def respond(self, payload, hint=""):
                raise ReviewerRuntimeError("provider", "simulated review outage")

        run_id, sqid, cid, _a, _b = self._independent_pair(run="f5rv")
        res = compute_claim_corroboration(
            cid, method=method, reviewer=_BrokenReviewer()
        )
        assert res["status"] == "complete"
        assert res["metadata"]["review_enabled"] is True
        assert res["metadata"]["review_failed"] is True
        assert "simulated review outage" in res["metadata"]["review_failed_reason"]
        assert res["support"]["independent_count"] == 2  # deterministic 主结果未变

    def test_review_merge_union_on_same(self, research_sqlite):
        method = CorroborationMethod(review_enabled=True, max_review_pairs=5)
        reviewer = FakeReviewer(same=True)
        run_id, sqid, cid, _a, _b = self._independent_pair(run="f5mv")
        res = compute_claim_corroboration(cid, method=method, reviewer=reviewer)
        assert res["status"] == "complete"
        assert res["metadata"]["review_merged"] >= 1
        assert len(res["global_clusters"]) == 1  # 确定性未归并的跨域对经 review 合并

    def test_gate_error_deterministic_failure_failed_artifact(self, research_sqlite):
        """AC7：deterministic（gate/F2 error）失败 → status=failed + error；修复后可收敛到 complete。"""
        from app.research import store as rstore

        store = rstore.get_store()
        run_a, sq_a = make_run("f5gate-a")
        run_b, sq_b = make_run("f5gate-b")
        ev = _web(run_a, sq_a, "https://g.example/1", "G 标题", "g" * 20)
        cid = _claim(run_a, sq_a, "gate 失败 claim")
        _bind_verify(run_a, cid, {ev: "SUPPORTS"})
        # 人为制造 run_a 的 F2 deterministic error（R6 跨 run binding，与 F3 gate 测试同法）
        e_b = _web(run_b, sq_b, "https://h.example/1", "H 标题", "h" * 20)
        with store.transaction() as tx:
            tx.execute(
                "INSERT INTO claim_evidences (binding_id, run_id, claim_id, evidence_id,"
                " created_at, metadata) VALUES (%s,%s,%s,%s,%s,%s)",
                ("f5-gate-cross", run_a, cid, e_b, "2026-01-01T00:00:00+00:00", "{}"),
            )
        failed = compute_claim_corroboration(cid)
        assert failed["status"] == "failed"
        assert failed["error"]
        assert "gate" in failed["error"]
        # 修复（删除跨 run binding）→ 同一指纹重算 → complete（同指纹单行收敛）
        with store.transaction() as tx:
            tx.execute(
                "DELETE FROM claim_evidences WHERE binding_id=%s", ("f5-gate-cross",)
            )
        recovered = compute_claim_corroboration(cid)
        assert recovered["status"] == "complete"
        assert recovered["corroboration_id"] == failed["corroboration_id"]
        rows = store.execute(
            "SELECT count(*) AS n FROM corroborations WHERE run_id=%s AND claim_id=%s",
            (run_a, cid),
        )
        assert rows[0]["n"] == 1

    def _independent_pair(self, run):
        run_id, sqid = make_run(run)
        ev_a = _web(
            run_id, sqid, f"https://a-{run}.example/1", f"A 独立标题 {run}", "a body"
        )
        ev_b = _web(
            run_id, sqid, f"https://b-{run}.example/2", f"B 独立标题 {run}", "b body"
        )
        cid = _claim(run_id, sqid, f"独立双源 {run}")
        _bind_verify(run_id, cid, {ev_a: "SUPPORTS", ev_b: "SUPPORTS"})
        return run_id, sqid, cid, ev_a, ev_b


class TestIdempotencyAndFingerprint:
    def test_method_upgrade_new_artifact(self, research_sqlite):
        from app.research import store as rstore

        store = rstore.get_store()
        run_id, sqid, cid, _a, _b = self._make_two(run="f5fp")
        default = compute_claim_corroboration(cid)
        upgraded = compute_claim_corroboration(
            cid, method=CorroborationMethod(title_jaccard=0.99)
        )
        assert default["method_fingerprint"] != upgraded["method_fingerprint"]
        assert default["corroboration_id"] != upgraded["corroboration_id"]
        rows = store.execute(
            "SELECT method_fingerprint FROM corroborations WHERE run_id=%s AND claim_id=%s",
            (run_id, cid),
        )
        assert len(rows) == 2
        # 升级方法（更严标题阈值）→ 转载不再合并 → 2 个独立簇
        assert upgraded["support"]["independent_count"] == 2

    def test_read_apis(self, research_sqlite):
        run_id, sqid, cid, _a, _b = self._make_two(run="f5api")
        res = compute_claim_corroboration(cid)
        got = get_corroboration(cid)
        assert got["corroboration_id"] == res["corroboration_id"]
        listed = list_corroborations(run_id, claim_id=cid)
        assert len(listed) == 1
        assert listed[0]["claim_id"] == cid
        assert listed[0]["method_fingerprint"] == DEFAULT_METHOD.fingerprint()
        # provenance：method_spec 可回读
        assert listed[0]["method_spec"]["name"] == "corroboration.v1"
        # 不存在的 claim → None
        assert get_corroboration("nope") is None

    def test_run_level_summary(self, research_sqlite):
        run_id, sqid = make_run("f5run")
        ev_a = _web(run_id, sqid, "https://r1.example/1", "R1 标题", "r1 body")
        cid_a = _claim(run_id, sqid, "run 级 claim A")
        _bind_verify(run_id, cid_a, {ev_a: "SUPPORTS"})
        ev_b = _web(run_id, sqid, "https://r2.example/1", "R2 标题", "r2 body")
        cid_b = _claim(run_id, sqid, "run 级 claim B")
        _bind_verify(run_id, cid_b, {ev_b: "SUPPORTS"})
        summary = compute_run_corroborations(run_id)
        assert summary["complete"] == 2
        assert summary["failed"] == 0
        assert {c["claim_id"] for c in summary["claims"]} == {cid_a, cid_b}

    def _make_two(self, run):
        """标题高度相似（字符集 Jaccard≈0.905 ≥0.85）但不等 → 默认方法合并、更严方法拆分。"""
        run_id, sqid = make_run(run)
        title_a = "abcdefghijklmnopqrst"
        title_b = "abcdefghijklmnopqrsu"
        ev_a = _web(run_id, sqid, f"https://fp-{run}-a.example/1", title_a, "a" * 30)
        ev_b = _web(run_id, sqid, f"https://fp-{run}-b.example/1", title_b, "b" * 30)
        cid = _claim(run_id, sqid, f"幂等双源 {run}")
        _bind_verify(run_id, cid, {ev_a: "SUPPORTS", ev_b: "SUPPORTS"})
        return run_id, sqid, cid, ev_a, ev_b


class TestSourceProfileDescriptive:
    def test_profile_shape_and_no_score_fields(self, research_sqlite):
        """AC8/AC9：source_profile 仅 descriptive；全 artifact 无 authority/reliability/
        confidence/truth/winner/score 字段（不解释为信任评分）。"""
        run_id, sqid, cid, _a, _b = self._make_pair(run="f5prof")
        res = compute_claim_corroboration(cid)
        profile = res["source_profile"]
        assert set(profile) == {
            "source_type_distribution",
            "domain_distribution",
            "publisher_distribution",
            "freshness_distribution",
            "agent_distribution",
        }
        assert profile["source_type_distribution"] == {"web": 2}
        assert profile["domain_distribution"] == {
            "alpha-prof.example": 1,
            "beta-prof.example": 1,
        }
        # 全 artifact 文本扫描禁词（score 字段名/语义）
        blob = json.dumps(res, ensure_ascii=False).lower()
        for token in ("authority", "reliability", "confidence", "truth_probability"):
            assert token not in blob
        assert "winner" not in blob
        assert '"score"' not in blob

    def test_no_domain_authority_claim(self, research_sqlite):
        """domain_distribution 是计数（descriptive），不是 authority score。"""
        run_id, sqid, cid, _a, _b = self._make_pair(run="f5dom")
        res = compute_claim_corroboration(cid)
        dom = res["source_profile"]["domain_distribution"]
        assert isinstance(dom, dict)
        assert all(isinstance(v, int) for v in dom.values())

    def _make_pair(self, run):
        run_id, sqid = make_run(run)
        ev_a = _web(
            run_id,
            sqid,
            "https://alpha-prof.example/1",
            f"标题 Alpha {run}",
            "alpha body text",
        )
        ev_b = _web(
            run_id,
            sqid,
            "https://beta-prof.example/1",
            f"标题 Beta {run}",
            "beta body text",
        )
        cid = _claim(run_id, sqid, f"profile 双源 {run}")
        _bind_verify(run_id, cid, {ev_a: "SUPPORTS", ev_b: "CONTRADICTS"})
        return run_id, sqid, cid, ev_a, ev_b
