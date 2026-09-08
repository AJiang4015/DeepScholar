"""normalize 纯函数测试（F1 §5）：canonical URL / tracking 去除 / canonical_key。"""

import pytest

from app.research.normalize import (
    canonical_key_for,
    canonicalize_url,
    normalize_assistant_key,
    strip_tracking_params,
)


class TestCanonicalizeUrl:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # 主机小写 + 默认端口去除（参数名大小写保留）
            ("HTTPS://Example.COM:443/A/B?C=1#frag", "https://example.com/A/B?C=1"),
            # fragment 去除 + query 排序
            ("https://a.com/p?b=2&a=1#sec", "https://a.com/p?a=1&b=2"),
            # tracking 参数去除（utm/fbclid/ref 精确+前缀）
            (
                "https://a.com/x?utm_source=g&fbclid=zz&a=1&ref=r",
                "https://a.com/x?a=1",
            ),
            # 非默认端口保留
            ("http://h.com:8080/p", "http://h.com:8080/p"),
            # 尾部斜杠归一化（根路径保留）
            ("https://a.com/p/", "https://a.com/p"),
            ("https://a.com/", "https://a.com/"),
            # 无 scheme 补 https
            ("www.example.com/a", "https://www.example.com/a"),
            # userinfo 去除
            ("https://user:pass@h.com/p", "https://h.com/p"),
        ],
    )
    def test_cases(self, raw, expected):
        assert canonicalize_url(raw) == expected

    @pytest.mark.parametrize("bad", ["", "   ", None, "not a url://", "http://"])
    def test_invalid_returns_none(self, bad):
        assert canonicalize_url(bad) is None

    def test_strip_tracking_params_exact_and_prefix(self):
        out = strip_tracking_params(
            "https://x.com/?utm_medium=mail&utm_campaign=c&gclid=9&q=1"
        )
        assert "utm_" not in out and "gclid" not in out and "q=1" in out


class TestCanonicalKey:
    def test_web_key_is_canonical_url(self):
        assert canonical_key_for("web", canonical_url="https://x.com/a") == (
            "https://x.com/a"
        )

    def test_db_key(self):
        assert canonical_key_for("db", query_id="q123") == "db:q123"

    def test_ragflow_key_normalizes_assistant(self):
        assert canonical_key_for("ragflow", assistant=" 金融行业助手 ") == (
            "ragflow:金融行业助手"
        )

    def test_normalize_assistant_key(self):
        assert normalize_assistant_key("  A B  C ") == "abc"
        assert normalize_assistant_key("") == ""

    def test_unknown_type_rejected(self):
        with pytest.raises(ValueError):
            canonical_key_for("unknown", canonical_url="https://x.com")
