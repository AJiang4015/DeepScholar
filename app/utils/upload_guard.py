"""
上传文件治理（R1 修复 P003 + 上传类型/大小治理）

上传链路安全边界：
1. 文件名净化：basename 化 + 非法字符替换，拒绝空名/`.`/`..`/超长名；
   （保留中文、空格、括号等合法字符）
2. 类型白名单：与 app/tools/upload_file_read_tool.py 可解析格式对齐
   （.md .txt .docx .pdf .xlsx .xls），防"能上传不能读"；
3. 大小上限：流式写入时由调用方累计字节（本模块提供校验函数）；
4. 随机存储名 + manifest 原名映射：落盘名不受客户端控制（防重名覆盖与
   存储名注入），manifest 记录 original -> {storage, size, uploaded_at}。

安全不变量（已批准约束）：manifest 只做"名称映射"，**不是文件访问授权边界**。
无论 manifest 内容如何，Agent 工具对文件的最终访问一律经 path_utils.resolve_path
的 resolve → is_within(session_dir) 校验（P002 fail-closed）。
因此本模块不承担授权职责，只保证上传落盘阶段的路径安全与可还原。

本模块为纯函数 + 轻量 JSON 读写，不依赖 FastAPI/starlette，便于单元测试。
"""

import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# 与 upload_file_read_tool.py 的可解析格式保持一致
ALLOWED_UPLOAD_EXTENSIONS = frozenset({".md", ".txt", ".docx", ".pdf", ".xlsx", ".xls"})

# 默认单文件大小上限（20MB），可用环境变量覆盖
DEFAULT_MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_UPLOAD_BYTES = int(
    __import__("os").getenv("UPLOAD_MAX_BYTES", str(DEFAULT_MAX_UPLOAD_BYTES))
)

# Windows 保留字符与不可见控制字符 -> 统一替换为下划线
_ILLEGAL_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# 存储名：uuid4().hex + 小写安全扩展名
_STORAGE_NAME_RE = re.compile(r"^[0-9a-f]{32}(\.[a-z0-9]{1,10})?$")

MANIFEST_FILENAME = ".manifest.json"


class UploadRejectedError(ValueError):
    """上传校验失败：文件名/类型/大小不满足安全要求（fail-closed 拒绝）。"""


def sanitize_filename(raw_filename: str) -> str:
    """
    净化客户端文件名 → 安全的 basename。

    - 丢弃一切目录部分（/ 与 \\ 之后只取最后一段），防路径穿越；
    - 拒绝空名、`.`、`..`、超长名；
    - 控制字符与 Windows 非法字符替换为 `_`；
    - 保留中文、空格、括号、`._-` 等合法展示字符。

    :param raw_filename: 客户端提交的原始文件名
    :return: 净化后的 basename
    :raises UploadRejectedError: 净化后仍非法
    """
    if not isinstance(raw_filename, str):
        raise UploadRejectedError("文件名必须是字符串")
    # basename 化：兼容 / 与 \
    basename = raw_filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not basename:
        raise UploadRejectedError("文件名不能为空")
    # 清洗非法字符
    cleaned = _ILLEGAL_FILENAME_CHARS.sub("_", basename).strip()
    if cleaned in ("", ".", ".."):
        raise UploadRejectedError("文件名不合法")
    if len(cleaned) > 255:
        raise UploadRejectedError("文件名过长（超过 255 字符）")
    return cleaned


def validate_extension(filename: str, allowed: Optional[set] = None) -> str:
    """
    校验文件扩展名在白名单内。

    :param filename: 净化后的文件名
    :param allowed: 允许的扩展名集合；默认 ALLOWED_UPLOAD_EXTENSIONS
    :return: 小写扩展名（含点）
    :raises UploadRejectedError: 扩展名缺失或不在白名单
    """
    allowed = allowed or ALLOWED_UPLOAD_EXTENSIONS
    suffix = Path(filename).suffix.lower()
    if not suffix:
        raise UploadRejectedError("文件缺少扩展名")
    if suffix not in allowed:
        raise UploadRejectedError(
            f"不支持的文件类型 '{suffix}'，仅支持：{', '.join(sorted(allowed))}"
        )
    return suffix


def validate_size(size_bytes: int, max_bytes: Optional[int] = None) -> None:
    """
    校验文件大小是否超过上限。

    :param size_bytes: 实际字节数
    :param max_bytes: 上限；默认 MAX_UPLOAD_BYTES（可用 UPLOAD_MAX_BYTES 环境变量覆盖）
    :raises UploadRejectedError: 超过上限
    """
    max_bytes = max_bytes or MAX_UPLOAD_BYTES
    if size_bytes > max_bytes:
        raise UploadRejectedError(
            f"文件超过大小上限（{max_bytes / 1024 / 1024:.0f}MB）"
        )


def generate_storage_name(original_filename: str) -> str:
    """
    生成随机存储名：uuid4().hex + 小写扩展名。

    存储名不含任何原始文件名内容，杜绝重名覆盖、存储名注入与文件名猜测；
    原名与存储名的映射写入 manifest（仅作还原展示，不作授权）。
    """
    suffix = Path(original_filename).suffix.lower()
    safe_suffix = suffix if re.fullmatch(r"\.[a-z0-9]{1,10}", suffix) else ""
    return uuid.uuid4().hex + safe_suffix


def is_storage_name(value: str) -> bool:
    """校验字符串是否符合存储名格式（防 manifest 被篡改后指向任意路径）。"""
    return bool(isinstance(value, str) and _STORAGE_NAME_RE.match(value))


# ---------------------------------------------------------------------------
# manifest：original 文件名 -> {storage, size, uploaded_at}
# manifest 仅用于"上传文件名还原展示"，绝不作为文件访问授权边界
# ---------------------------------------------------------------------------
def load_upload_manifest(upload_dir: Path) -> dict:
    """
    读取 upload_dir 下的 manifest；不存在/损坏时返回空 dict（调用方按无 manifest 处理）。
    """
    manifest_path = Path(upload_dir) / MANIFEST_FILENAME
    if not manifest_path.exists():
        return {}
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        return {}
    return {}


def save_upload_manifest(upload_dir: Path, manifest: dict) -> None:
    """原子写 manifest（先写临时文件再 rename，避免半写状态）。"""
    manifest_path = Path(upload_dir) / MANIFEST_FILENAME
    tmp_path = manifest_path.with_name(manifest_path.name + ".tmp")
    tmp_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp_path.replace(manifest_path)


def record_upload_meta(original: str, storage: str, size: int) -> dict:
    """构造 manifest 单条记录。"""
    return {
        "storage": storage,
        "size": size,
        "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def restore_uploads_to_session(upload_dir: Path, session_dir: Path) -> list:
    """
    把 updated/{session} 下的上传文件按 manifest 还原为**原名**复制到会话目录。

    安全要点（manifest 只作名称映射，不作授权边界）：
    - 只接受 storage 字段为合法存储名格式（uuid.hex[.ext]）的记录；
    - original 字段必须能通过 sanitize_filename 还原为同值（防篡改注入路径）；
    - 复制目标固定为 session_dir / original，不读取任何 manifest 指向外部的内容。

    :param upload_dir: updated/session_{id} 上传暂存目录
    :param session_dir: 会话输出工作目录（复制目的地）
    :return: 成功还原的原始文件名列表（供提示词注入）
    """
    upload_dir = Path(upload_dir)
    session_dir = Path(session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)

    manifest = load_upload_manifest(upload_dir)
    restored: list = []
    if manifest:
        # manifest 模式：按 original -> storage 还原（storage 必须先校验为合法格式）
        for original, meta in manifest.items():
            if not isinstance(meta, dict):
                continue
            storage = meta.get("storage")
            if not is_storage_name(storage or ""):
                # 篡改防御：storage 不是合法存储名则跳过，绝不按任意路径复制
                continue
            # original 必须是"已净化且等于自身"的合法文件名：任何含路径/分隔符的
            # 篡改（如 ../../x.md）都无法通过，防止复制目标逃出 session_dir
            try:
                if sanitize_filename(original) != original:
                    continue
            except UploadRejectedError:
                continue
            src = upload_dir / storage
            if not src.is_file():
                continue
            shutil.copy2(src, session_dir / original)
            restored.append(original)
        return restored

    # 无 manifest（旧版本/直接放置文件）：回退旧行为——原样复制所有非隐藏文件
    for entry in upload_dir.iterdir():
        if entry.is_file() and not entry.name.startswith("."):
            shutil.copy2(entry, session_dir / entry.name)
            restored.append(entry.name)
    return restored
