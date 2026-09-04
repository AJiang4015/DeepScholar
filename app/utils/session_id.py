"""
会话标识（thread_id / session_id）净化工具（R1 修复 P004）

用户可控的 thread_id 会拼入文件系统目录（output/session_{id}、updated/session_{id}）
与 WebSocket 路由。若不做字符集限制，含 `..` / 路径分隔符的 thread_id 可造成目录穿越，
并污染 monitor 事件路由与会话键。

净化规则：仅允许 [A-Za-z0-9_-]，长度 1~128。
前端 frontend/src/lib/thread.ts 生成的格式均兼容：
- crypto.randomUUID() -> 36 字符 UUID（hex + '-'）
- manual-{timestamp}-{random-hex}（fallback）

提供两个入口语义：
- sanitize_thread_id：非法即抛 InvalidThreadIdError（供 upload / WS 等必须精确匹配场景）
- safe_thread_id_or_new：非法/缺失时生成 uuid4().hex（供 /api/task，前端接受返回的新 id）
"""

import re
import uuid
from typing import Optional

# 安全字符集：字母数字 + 下划线 + 连字符
SAFE_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

MAX_THREAD_ID_LENGTH = 128


class InvalidThreadIdError(ValueError):
    """thread_id 不满足安全标识符要求。"""


def is_safe_thread_id(value: Optional[str]) -> bool:
    """判断 thread_id 是否落在安全字符集内。"""
    if not isinstance(value, str):
        return False
    return bool(SAFE_THREAD_ID_RE.match(value))


def sanitize_thread_id(value: Optional[str]) -> str:
    """
    校验 thread_id；非法抛 InvalidThreadIdError。

    :param value: 客户端传入的 thread_id
    :return: 原样返回合法值
    """
    if not isinstance(value, str) or not value.strip():
        raise InvalidThreadIdError("thread_id 不能为空")
    value = value.strip()
    if len(value) > MAX_THREAD_ID_LENGTH:
        raise InvalidThreadIdError(
            f"thread_id 过长（最多 {MAX_THREAD_ID_LENGTH} 字符）"
        )
    if not SAFE_THREAD_ID_RE.match(value):
        raise InvalidThreadIdError("thread_id 只能包含字母、数字、下划线或连字符")
    return value


def safe_thread_id_or_new(value: Optional[str]) -> str:
    """
    非法或缺失时生成新的 uuid4().hex（32 位 hex，安全字符集内）。

    供 /api/task 使用：前端 localStorage 里的 thread_id 若因历史原因不合法，
    服务端自动换新并返回，前端 useDeepAgentSession 会接受响应中的 thread_id。
    """
    if is_safe_thread_id(value):
        return value  # type: ignore[arg-type]
    return uuid.uuid4().hex
