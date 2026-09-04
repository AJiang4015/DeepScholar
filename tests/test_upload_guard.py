"""
R1 P003 + 上传治理单元测试：upload_guard

覆盖：
- sanitize_filename：basename 化、穿越清洗、中文/空格/括号保留、控制字符替换、超长拒绝
- validate_extension：类型白名单
- validate_size：大小上限
- generate_storage_name / is_storage_name：随机存储名格式、不含原名、篡改检测
- manifest 读写往返（含损坏容错）
- restore_uploads_to_session：原名还原 + 篡改防御（storage 越界/非存储名跳过）
"""

import re
import uuid
from pathlib import Path

import pytest

from app.utils.upload_guard import (
    ALLOWED_UPLOAD_EXTENSIONS,
    MANIFEST_FILENAME,
    UploadRejectedError,
    generate_storage_name,
    is_storage_name,
    load_upload_manifest,
    record_upload_meta,
    restore_uploads_to_session,
    sanitize_filename,
    save_upload_manifest,
    validate_extension,
    validate_size,
)

# 本环境沙箱禁止写系统 Temp 目录，测试临时目录统一建在工作区 _testtmp 下
_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST_TMP = _REPO_ROOT / "_testtmp"


def _work_tmp() -> Path:
    d = _TEST_TMP / uuid.uuid4().hex
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# sanitize_filename
# ---------------------------------------------------------------------------
class TestSanitizeFilename:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("report.md", "report.md"),  # 普通
            (
                "行业分析报告 2026(最终).md",
                "行业分析报告 2026(最终).md",
            ),  # 中文/空格/括号保留
            ("../../etc/passwd", "passwd"),  # 穿越 -> basename 化（丢弃目录部分）
            ("..\\..\\x.txt", "x.txt"),  # Windows 分隔符
            ("C:\\fakepath\\a.pdf", "a.pdf"),  # 浏览器 fakepath
            ("/abs/path/b.xlsx", "b.xlsx"),  # 绝对路径 -> basename
            ("a<b:c>d|e?.txt", "a_b_c_d_e_.txt"),  # Windows 非法字符替换
        ],
    )
    def test_sanitizes(self, raw, expected):
        assert sanitize_filename(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", ".", "..", None, 123, "a" * 256])
    def test_rejects(self, raw):
        with pytest.raises(UploadRejectedError):
            sanitize_filename(raw)


# ---------------------------------------------------------------------------
# validate_extension / validate_size
# ---------------------------------------------------------------------------
class TestValidateExtensionAndSize:
    @pytest.mark.parametrize("ext", [".md", ".txt", ".docx", ".pdf", ".xlsx", ".xls"])
    def test_allows_known(self, ext):
        validate_extension(f"file{ext}")
        validate_extension(f"FILE{ext.upper()}")  # 大小写不敏感

    @pytest.mark.parametrize("name", ["file.exe", "file.py", "file", "file.bin.md.bak"])
    def test_rejects_unknown(self, name):
        with pytest.raises(UploadRejectedError):
            validate_extension(name)

    def test_allowed_set_matches_read_tool_formats(self):
        # 与 upload_file_read_tool 支持的格式保持一致（防"能上传不能读"）
        assert ALLOWED_UPLOAD_EXTENSIONS == {
            ".md",
            ".txt",
            ".docx",
            ".pdf",
            ".xlsx",
            ".xls",
        }

    def test_size_within_limit_ok(self):
        validate_size(1024)  # 默认 20MB 上限内

    def test_size_over_limit_rejected(self):
        with pytest.raises(UploadRejectedError):
            validate_size(21 * 1024 * 1024)  # > 默认 20MB

    def test_custom_limit(self):
        validate_size(10, max_bytes=100)
        with pytest.raises(UploadRejectedError):
            validate_size(101, max_bytes=100)


# ---------------------------------------------------------------------------
# 随机存储名
# ---------------------------------------------------------------------------
class TestStorageName:
    def test_generated_name_has_uuid_and_suffix(self):
        name = generate_storage_name("报告 Final.md")
        assert re.fullmatch(r"[0-9a-f]{32}\.md", name)
        # 存储名不含原名内容
        assert "报告" not in name and "Final" not in name

    def test_uppercase_suffix_lowercased(self):
        assert generate_storage_name("x.PDF").endswith(".pdf")

    def test_odd_suffix_dropped(self):
        name = generate_storage_name("x.verylongextensionname")
        assert re.fullmatch(r"[0-9a-f]{32}", name)

    def test_is_storage_name(self):
        assert is_storage_name("a" * 32 + ".md")
        assert is_storage_name("a" * 32)
        assert not is_storage_name("../evil.md")
        assert not is_storage_name("报告.md")
        assert not is_storage_name("")
        assert not is_storage_name(None)


# ---------------------------------------------------------------------------
# manifest 读写往返
# ---------------------------------------------------------------------------
class TestManifest:
    def test_roundtrip(self):
        d = _work_tmp()
        meta = record_upload_meta("a.md", "a" * 32 + ".md", 100)
        save_upload_manifest(d, {"a.md": meta})
        loaded = load_upload_manifest(d)
        assert loaded["a.md"]["storage"] == "a" * 32 + ".md"
        assert loaded["a.md"]["size"] == 100

    def test_load_missing_returns_empty(self):
        assert load_upload_manifest(_work_tmp()) == {}

    def test_load_corrupt_returns_empty(self):
        d = _work_tmp()
        (d / MANIFEST_FILENAME).write_text("{not json", encoding="utf-8")
        assert load_upload_manifest(d) == {}


# ---------------------------------------------------------------------------
# restore_uploads_to_session：manifest 还原 + 篡改防御
# ---------------------------------------------------------------------------
class TestRestoreUploads:
    def _seed(self):
        base = _work_tmp()
        upload_dir = base / "updated"
        upload_dir.mkdir()
        session_dir = base / "session"
        session_dir.mkdir()
        storage = "a" * 32 + ".md"
        (upload_dir / storage).write_text("# hello", encoding="utf-8")
        return upload_dir, session_dir, storage

    def test_restores_by_original_name(self):
        upload_dir, session_dir, storage = self._seed()
        save_upload_manifest(
            upload_dir, {"报告.md": record_upload_meta("报告.md", storage, 7)}
        )
        restored = restore_uploads_to_session(upload_dir, session_dir)
        assert restored == ["报告.md"]
        assert (session_dir / "报告.md").read_text(encoding="utf-8") == "# hello"

    def test_tampered_storage_skipped(self):
        upload_dir, session_dir, _ = self._seed()
        # 篡改：storage 指向会话目录外文件 -> 必须跳过，不复制外部内容
        outside = _work_tmp() / "secret.env"
        outside.write_text("SECRET", encoding="utf-8")
        save_upload_manifest(
            upload_dir,
            {"报告.md": record_upload_meta("报告.md", "../secret.env", 6)},
        )
        restored = restore_uploads_to_session(upload_dir, session_dir)
        assert restored == []
        assert not (session_dir / "报告.md").exists()

    def test_tampered_original_path_rejected(self):
        upload_dir, session_dir, storage = self._seed()
        # 篡改：original 含路径 -> sanitize(original) != original -> 跳过
        save_upload_manifest(
            upload_dir, {"../../x.md": record_upload_meta("../../x.md", storage, 7)}
        )
        restored = restore_uploads_to_session(upload_dir, session_dir)
        assert restored == []

    def test_fallback_without_manifest(self):
        upload_dir, session_dir, _ = self._seed()
        # 无 manifest（旧会话）：原样复制非隐藏文件
        (upload_dir / "legacy.md").write_text("# legacy", encoding="utf-8")
        restored = restore_uploads_to_session(upload_dir, session_dir)
        assert "legacy.md" in restored
        assert (session_dir / "legacy.md").exists()
        # 隐藏文件（如 manifest 本身）不被复制
        assert not (session_dir / MANIFEST_FILENAME).exists()
