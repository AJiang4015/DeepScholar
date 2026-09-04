"""
文件路径解析工具（R1 修复 P002：会话目录隔离 fail-closed）

负责把模型或工具返回的虚拟路径、上传文件路径和相对路径统一转换为本地绝对路径，
并强制约束：**带 session_dir 时，解析结果必须落在 session_dir 内，越界即拒绝**
（PathSafetyError，fail-closed），不再"保持外部路径原样返回"。

安全不变量（供所有 LLM 文件工具复用）：
    LLM tool call → resolve_path → resolve + normalize → is_within(session_dir)
    → 通过：返回会话内绝对路径；越界：raise PathSafetyError（调用方转中文安全拒绝）

语义保留（与原实现一致）：
- 虚拟沙箱前缀剥离：/workspace、/mnt/data、/home/user
- updated/ 前缀：上传文件执行前已由 run_deep_agent 复制进 session_dir，
  此处统一取 updated/ 之后部分的 basename 映射回 session_dir 内同名文件
- output/ 前缀与重复 session 名拼接清洗、Windows 盘符与分隔符处理
"""

import os
from pathlib import Path
from typing import Optional


class PathSafetyError(ValueError):
    """
    路径安全校验失败：目标不在当前会话目录内（或输入非法）。

    与普通 IO/OSError 区分：调用方工具应单独捕获本异常并返回
    "安全拒绝：..." 中文提示，不得被宽泛 except Exception 吞并。
    """


def is_within_directory(path: Path, base: Path) -> bool:
    """
    判断 path 是否位于 base 之内（含相等）。

    用 os.path.normcase 做规范化比较，保证 Windows 下大小写不敏感、
    POSIX 下大小写敏感；比较基于 resolve 后的词法路径，可防 `..` 逃逸。
    """
    try:
        p = os.path.normcase(str(path))
        b = os.path.normcase(str(base))
        return p == b or p.startswith(b.rstrip(os.sep) + os.sep)
    except Exception:
        return False


def resolve_path(filename: str, session_dir: Optional[str] = None) -> str:
    """
    解析文件路径，并把结果强制约束在当前会话目录中（fail-closed）。

    :param filename: 模型、工具或用户传入的文件名/路径
    :param session_dir: 当前任务的会话目录；为空时无沙箱语义（本地调试/纯调用），
                        仅返回词法解析结果，不作为安全边界
    :return: 解析后的会话内绝对路径
    :raises PathSafetyError: 输入非法或解析结果越出 session_dir
    """
    if not isinstance(filename, str) or not filename.strip():
        raise PathSafetyError("文件路径为空或不是合法文本。")

    path = Path(filename)
    path_str = filename.replace("\\", "/")

    # 大模型常返回 /workspace、/mnt/data 这类沙箱路径，本地项目需要先剥离虚拟前缀
    for prefix in ["/workspace", "/mnt/data", "/home/user"]:
        if path_str.startswith(prefix):
            cleaned = path_str[len(prefix) :].lstrip("/")
            path = Path(cleaned)
            path_str = str(path).replace("\\", "/")
            break

    # 无 session_dir：无沙箱上下文（仅本地调试/纯函数调用），返回词法解析结果
    if not session_dir:
        return str(path.resolve())

    session_path = Path(session_dir).resolve()
    session_name = session_path.name
    is_unix_abs = path_str.startswith("/")

    # updated/ 前缀：上传文件执行前已复制进 session_dir，取其 basename 映射回会话目录。
    # 注意：绝不直接 resolve 到进程 CWD 下的 updated/（原实现会随 CWD 逃逸）。
    if "updated/" in path_str:
        idx = path_str.find("updated/") + len("updated/")
        tail = Path(path_str[idx:])
        if ".." in tail.parts:
            raise PathSafetyError(f"路径包含 '..' 逃逸，已拒绝访问：{filename}")
        candidate = session_path / tail.name
        return _finalize(candidate, session_path, filename)

    if path.is_absolute():
        # 真实绝对路径：交给统一包含性校验，越界即拒绝（修复原"原样返回"逃逸）
        candidate = path
    elif os.name == "nt" and is_unix_abs and not path.drive:
        # Windows 下 "/xxx" 没有盘符，按会话目录内的相对路径处理
        candidate = session_path / path_str.lstrip("/")
    else:
        parts = path.parts
        # 避免模型把 session 名或 output 前缀重复拼到当前会话目录里。
        # 但前缀清洗只允许"冗余目录层"（output/、session_x/），若模型混入 `..`
        # 说明存在逃逸意图，必须显式拒绝而非静默改写。
        if session_name in parts:
            idx = parts.index(session_name)
            _reject_parent_traversal(parts[idx + 1 :], filename)
            candidate = session_path / path.name
        elif parts and parts[0] == "output":
            _reject_parent_traversal(parts[1:], filename)
            candidate = session_path / path.name
        else:
            candidate = session_path / path

    return _finalize(candidate, session_path, filename)


def _reject_parent_traversal(parts, original_input: str) -> None:
    """前缀清洗分支的兜底：清洗后的剩余部分若仍含 '..'，视为逃逸意图并拒绝。"""
    if ".." in parts:
        raise PathSafetyError(f"路径包含 '..' 逃逸，已拒绝访问：{original_input}")


def _finalize(candidate: Path, session_path: Path, original_input: str) -> str:
    """
    统一出口：resolve 词法路径 → 会话目录包含性校验 → 越界抛 PathSafetyError。

    对尚不存在的目标文件（如生成工具要新建的文件），resolve() 只做词法归一，
    不会因文件缺失而失败，因此本校验在写入前即可拦截 `..` 等逃逸。
    """
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise PathSafetyError("路径无法解析，已拒绝访问。") from exc

    if not is_within_directory(resolved, session_path):
        raise PathSafetyError(
            f"路径超出当前会话工作目录范围，已拒绝访问：{original_input}"
        )

    # 兼容模型重复拼接 session 名（session_x/session_x/file.md）的清洗
    return _fix_nested_session_path(resolved, session_path, session_path.name)


def _fix_nested_session_path(
    full_path: Path,
    session_path: Path,
    session_name: str,
) -> str:
    """
    修正 session_xxx/session_xxx/file.md 这类重复嵌套路径
    """
    parts = full_path.parts
    for index in range(len(parts) - 1):
        if parts[index] == session_name and parts[index + 1] == session_name:
            return str(session_path / full_path.name)
    return str(full_path)
