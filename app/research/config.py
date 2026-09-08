"""Research Artifact Store 配置解析（F1）。纯函数，可单测。

env 契约：
- RESEARCH_STORE: "sqlite"(缺省) | "postgres" | "disabled"
- RESEARCH_DB:    sqlite 文件路径（缺省 app/runtime/research.sqlite）
- RESEARCH_DSN:   postgres 连接串（backend=postgres 时必填）

配置非法 → ResearchConfigError（首次访问时记录清晰错误，并按 F1 fail-open
contract 处理，不改变 Agent 执行语义）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

RESEARCH_STORE_ENV = "RESEARCH_STORE"
RESEARCH_DB_ENV = "RESEARCH_DB"
RESEARCH_DSN_ENV = "RESEARCH_DSN"
RESEARCH_DSN_TEST_ENV = "RESEARCH_DSN_TEST"

DEFAULT_STORE = "sqlite"
DEFAULT_RESEARCH_DB_PATH = (
    Path(__file__).resolve().parent.parent / "runtime" / "research.sqlite"
)
ALLOWED_STORES = ("sqlite", "postgres", "disabled")


class ResearchConfigError(Exception):
    """Research store 配置非法。"""


@dataclass(frozen=True)
class ResearchConfig:
    store: str  # sqlite | postgres | disabled
    db_path: Optional[Path] = None
    dsn: Optional[str] = None


def parse_research_config(
    environ: Optional[Mapping[str, str]] = None,
) -> ResearchConfig:
    env = dict(os.environ if environ is None else environ)
    raw = (env.get(RESEARCH_STORE_ENV) or DEFAULT_STORE).strip().lower()
    if raw not in ALLOWED_STORES:
        raise ResearchConfigError(
            f"RESEARCH_STORE 取值非法：{raw!r}；允许值：sqlite / postgres / disabled"
        )
    if raw == "postgres":
        dsn = (env.get(RESEARCH_DSN_ENV) or "").strip()
        if not dsn:
            raise ResearchConfigError(
                f"RESEARCH_STORE=postgres 时必须设置 {RESEARCH_DSN_ENV}"
            )
        return ResearchConfig(store="postgres", dsn=dsn)
    if raw == "disabled":
        return ResearchConfig(store="disabled")
    raw_db = env.get(RESEARCH_DB_ENV)
    db_path = (
        DEFAULT_RESEARCH_DB_PATH
        if raw_db is None or not raw_db.strip()
        else Path(raw_db).expanduser().resolve()
    )
    return ResearchConfig(store="sqlite", db_path=db_path)


def get_research_db_path(environ: Optional[Mapping[str, str]] = None) -> str:
    cfg = parse_research_config(environ)
    if cfg.store != "sqlite" or cfg.db_path is None:
        raise ResearchConfigError("get_research_db_path 仅适用于 sqlite store")
    return str(cfg.db_path)
