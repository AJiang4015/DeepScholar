"""
R1 P002 修复单元测试：resolve_path 会话目录隔离（fail-closed）

覆盖 P002 逃逸负路径矩阵：
- 相对路径 `../` 逃逸
- 绝对路径逃逸（Windows 盘符路径）
- updated/ 前缀变体
- output/ 前缀与重复 session 名拼接
- 虚拟前缀（/workspace 等）
- Windows 分隔符
- 空串 / None 等边界输入
以及正常路径（文件名、子目录、会话内绝对路径）不被误伤。
"""

import os
import uuid
from pathlib import Path

import pytest

from app.utils.path_utils import PathSafetyError, resolve_path

# 工具层验证（需 langchain_core/fastapi 等依赖；缺失时仅跳过工具用例）
try:
    import app.tools.markdown_tools as markdown_tools_mod
    import app.tools.pdf_tools as pdf_tools_mod
    import app.tools.upload_file_read_tool as read_tool_mod

    FILE_TOOLS_IMPORTABLE = True
except Exception:  # pragma: no cover - 依赖缺失
    FILE_TOOLS_IMPORTABLE = False

needs_file_tools = pytest.mark.skipif(
    not FILE_TOOLS_IMPORTABLE, reason="文件工具依赖（langchain_core 等）不可用"
)

# 本环境沙箱禁止写系统 Temp 目录，测试临时目录统一建在工作区 _testtmp 下
_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST_TMP = _REPO_ROOT / "_testtmp"


def _work_tmp() -> Path:
    d = _TEST_TMP / uuid.uuid4().hex
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def session_dir() -> Path:
    """构造一个真实的会话目录，内含一个可读文件。"""
    sd = _work_tmp() / "session_abc123"
    sd.mkdir(parents=True)
    (sd / "报告.md").write_text("# 测试", encoding="utf-8")
    (sd / "sub").mkdir()
    (sd / "sub" / "nested.md").write_text("# nested", encoding="utf-8")
    return sd


# ---------------------------------------------------------------------------
# 正常路径：不得误伤
# ---------------------------------------------------------------------------
class TestValidPaths:
    def test_plain_filename(self, session_dir):
        assert resolve_path("报告.md", str(session_dir)) == str(
            (session_dir / "报告.md").resolve()
        )

    def test_subdir_relative(self, session_dir):
        assert resolve_path("sub/nested.md", str(session_dir)) == str(
            (session_dir / "sub" / "nested.md").resolve()
        )

    def test_windows_separator(self, session_dir):
        assert resolve_path("sub\\nested.md", str(session_dir)) == str(
            (session_dir / "sub" / "nested.md").resolve()
        )

    def test_session_absolute_path_inside(self, session_dir):
        target = session_dir / "报告.md"
        assert resolve_path(str(target), str(session_dir)) == str(target.resolve())

    def test_output_prefix_cleaned(self, session_dir):
        # 模型把 output/session_x/file 拼进来 -> 收敛到会话目录内
        result = resolve_path(f"output/{session_dir.name}/报告.md", str(session_dir))
        assert result == str((session_dir / "报告.md").resolve())

    def test_session_name_repeated(self, session_dir):
        result = resolve_path(
            f"{session_dir.name}/{session_dir.name}/报告.md", str(session_dir)
        )
        assert result == str((session_dir / "报告.md").resolve())

    def test_virtual_prefix_stripped(self, session_dir):
        result = resolve_path(
            f"/workspace/{session_dir.name}/报告.md", str(session_dir)
        )
        assert result == str((session_dir / "报告.md").resolve())

    def test_updated_prefix_maps_to_session(self, session_dir):
        # updated/session_x/file -> 上传文件已复制进 session_dir，取 basename
        result = resolve_path(f"updated/{session_dir.name}/报告.md", str(session_dir))
        assert result == str((session_dir / "报告.md").resolve())

    def test_nonexistent_file_inside_is_ok(self, session_dir):
        # 生成类工具要先解析不存在的目标文件，不得误伤
        assert resolve_path("new.md", str(session_dir)) == str(
            (session_dir / "new.md").resolve()
        )


# ---------------------------------------------------------------------------
# 逃逸负路径：必须 fail-closed 抛 PathSafetyError
# ---------------------------------------------------------------------------
class TestEscapePaths:
    @pytest.mark.parametrize(
        "attack",
        [
            "../secret.env",
            "../../etc/passwd",
            "sub/../../secret.env",
        ],
    )
    def test_relative_traversal_rejected(self, session_dir, attack):
        with pytest.raises(PathSafetyError):
            resolve_path(attack, str(session_dir))

    def test_absolute_escape_rejected(self, session_dir):
        # 会话目录之外的绝对路径（Windows: C:\...；POSIX: /tmp/...）
        outside = _work_tmp() / "secret.env"
        outside.write_text("x", encoding="utf-8")
        with pytest.raises(PathSafetyError):
            resolve_path(str(outside), str(session_dir))

    def test_output_prefix_escape_rejected(self, session_dir):
        # output/session_x/../../secret -> resolve 后越界
        with pytest.raises(PathSafetyError):
            resolve_path(
                f"output/{session_dir.name}/../../secret.env", str(session_dir)
            )

    def test_updated_prefix_dotdot_rejected(self, session_dir):
        with pytest.raises(PathSafetyError):
            resolve_path(
                f"updated/{session_dir.name}/../../secret.env", str(session_dir)
            )

    def test_virtual_prefix_escape_rejected(self, session_dir):
        with pytest.raises(PathSafetyError):
            resolve_path("/workspace/../../secret.env", str(session_dir))

    @pytest.mark.parametrize("bad", ["", "   ", None])
    def test_invalid_input_rejected(self, session_dir, bad):
        with pytest.raises(PathSafetyError):
            resolve_path(bad, str(session_dir))

    def test_windows_drive_escape_rejected(self, session_dir):
        if os.name == "nt":
            with pytest.raises(PathSafetyError):
                resolve_path("C:/Windows/System32/drivers/etc/hosts", str(session_dir))


# ---------------------------------------------------------------------------
# 无 session_dir 时的纯解析（本地调试语义，非安全边界）
# ---------------------------------------------------------------------------
class TestNoSessionDir:
    def test_returns_resolved_path(self):
        f = _work_tmp() / "a.md"
        f.write_text("x", encoding="utf-8")
        assert resolve_path(str(f), None) == str(f.resolve())


# ---------------------------------------------------------------------------
# 工具层：越界路径必须返回"安全拒绝"中文提示（而非抛异常/普通 IO 错误）
# ---------------------------------------------------------------------------
class TestToolSafetyRejection:
    @needs_file_tools
    def test_read_file_content_escape_rejected(self, session_dir, monkeypatch):
        monkeypatch.setattr(
            read_tool_mod, "get_session_context", lambda: str(session_dir)
        )
        result = read_tool_mod.read_file_content.invoke({"filename": "../secret.env"})
        assert result.startswith("安全拒绝：")
        assert "超出" in result

    @needs_file_tools
    def test_generate_markdown_escape_rejected(self, session_dir, monkeypatch):
        monkeypatch.setattr(
            markdown_tools_mod, "get_session_context", lambda: str(session_dir)
        )
        result = markdown_tools_mod.generate_markdown.invoke(
            {"content": "x", "filename": "a.md", "path": "../escape"}
        )
        assert result.startswith("安全拒绝：")

    @needs_file_tools
    def test_convert_md_to_pdf_escape_rejected(self, session_dir, monkeypatch):
        monkeypatch.setattr(
            pdf_tools_mod, "get_session_context", lambda: str(session_dir)
        )
        result = pdf_tools_mod.convert_md_to_pdf.invoke({"md_filename": "../escape"})
        assert result.startswith("安全拒绝：")

    @needs_file_tools
    def test_read_file_content_normal_path_works(self, session_dir, monkeypatch):
        monkeypatch.setattr(
            read_tool_mod, "get_session_context", lambda: str(session_dir)
        )
        # 会话内文件正常读取（回归：合法路径不被误伤）
        result = read_tool_mod.read_file_content.invoke({"filename": "报告.md"})
        assert result == "# 测试"
