"""
R1 P004 修复单元测试：thread_id / session_id 净化

覆盖：前端 uuid4 / manual-* 格式通过；路径分隔符 / 穿越 / 空 / 超长 / 控制字符拒绝。
"""

import re

import pytest

from app.utils.session_id import (
    InvalidThreadIdError,
    is_safe_thread_id,
    safe_thread_id_or_new,
    sanitize_thread_id,
)


class TestIsSafeThreadId:
    @pytest.mark.parametrize(
        "value",
        [
            "550e8400-e29b-41d4-a716-446655440000",  # uuid4（前端 crypto.randomUUID）
            "manual-1710000000000-1a2b3c4d5e6f7",  # 前端 fallback 格式
            "abc_123-XYZ",  # 下划线 + 连字符
            "a" * 128,  # 最大长度
        ],
    )
    def test_safe_values(self, value):
        assert is_safe_thread_id(value) is True
        assert sanitize_thread_id(value) == value

    @pytest.mark.parametrize(
        "value",
        [
            "../secret",  # 路径穿越
            "a/b",  # 正斜杠
            "a\\b",  # 反斜杠
            "a b",  # 空格
            "a.b",  # 点号
            "",  # 空
            "   ",  # 纯空白
            "a" * 129,  # 超长
            "a\x00b",  # 控制字符
            "a;rm -rf",  # shell 元字符
            None,
        ],
    )
    def test_unsafe_values(self, value):
        assert is_safe_thread_id(value) is False

    def test_sanitize_rejects_unsafe(self):
        for bad in ["../x", "a/b", "a\\b", "", "a" * 129, None]:
            with pytest.raises(InvalidThreadIdError):
                sanitize_thread_id(bad)


class TestSafeThreadIdOrNew:
    def test_returns_safe_value_unchanged(self):
        assert safe_thread_id_or_new("550e8400-e29b-41d4-a716-446655440000") == (
            "550e8400-e29b-41d4-a716-446655440000"
        )

    @pytest.mark.parametrize("bad", [None, "", "../x", "a/b", "a\\b", "a b"])
    def test_generates_new_for_unsafe(self, bad):
        new_id = safe_thread_id_or_new(bad)
        assert is_safe_thread_id(new_id)
        assert len(new_id) == 32  # uuid4().hex
        assert re.fullmatch(r"[0-9a-f]{32}", new_id)
