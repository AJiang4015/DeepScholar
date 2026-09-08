"""Source normalization（F1）：canonical URL / canonical_key。全部 deterministic，无 LLM。

规则（Spec §5）：
- web：canonicalize_url() 归一化 raw url；
- db：canonical_key = "db:{query_id}"；
- ragflow：canonical_key = "ragflow:{assistant_key}"（助手名归一化：小写 + 去空白）；
- upload：canonical_key = "upload:{file_key}"（文件名归一化，预留）。

canonical URL 规则：
scheme 小写；host 小写、去 userinfo 与默认端口；去 fragment；
去 tracking 参数白名单（utm_* / fbclid / gclid / ref / spm / _ga…）；
query 排序后重拼；path 去尾部 '/'（根路径保留）。
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

#: 常见 tracking 参数名/前缀（精确名或前缀匹配）
_TRACKING_PARAMS_EXACT = {"ref", "spm", "share_token", "from", "source", "scm"}
_TRACKING_PARAMS_PREFIX = ("utm_", "fbclid", "gclid", "_ga", "icp", "spm")

_DEFAULT_PORTS = {"http": 80, "https": 443}


def strip_tracking_params(url: str) -> str:
    """去掉常见 tracking 参数（deterministic，测试矩阵覆盖）。"""
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    keep = []
    for name, value in query:
        low = name.lower()
        if low in _TRACKING_PARAMS_EXACT or low.startswith(_TRACKING_PARAMS_PREFIX):
            continue
        keep.append((name, value))
    return urlunsplit(parts._replace(query=urlencode(keep)))


def canonicalize_url(raw: str) -> Optional[str]:
    """归一化 web URL（失败/空返回 None，由调用方拒绝入库）。"""
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw:
        return None
    # 允许常见无 scheme 形式（www.example.com/path → https://）
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(strip_tracking_params(raw))
    except ValueError:
        return None
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower()
    if not host:
        return None
    try:
        port = parts.port
    except ValueError:
        port = None
    netloc = host
    if port and port != _DEFAULT_PORTS.get(scheme):
        netloc = f"{host}:{port}"
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return urlunsplit((scheme, netloc, path, query, ""))


def normalize_assistant_key(name: str) -> str:
    """RAGFlow 助手名 → canonical_key 片段（小写、去空白）。"""
    return re.sub(r"\s+", "", (name or "").lower())


def canonical_key_for(site_type: str, **parts: str) -> str:
    """按 source_type 构造 canonical_key（registry 调用方使用）。"""
    if site_type == "web":
        url = parts.get("canonical_url") or parts.get("url")
        if not url:
            raise ValueError("web source 需要 canonical_url")
        return url
    if site_type == "db":
        return f"db:{parts.get('query_id') or parts.get('locator')}"
    if site_type == "ragflow":
        return f"ragflow:{normalize_assistant_key(parts.get('assistant', ''))}"
    if site_type == "upload":
        return f"upload:{parts.get('file_key') or parts.get('locator')}"
    raise ValueError(f"不支持的 source_type: {site_type!r}")
