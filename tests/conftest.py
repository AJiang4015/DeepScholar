"""pytest 共享配置：Windows loop policy + research sqlite 隔离环境（_testtmp 沙箱兼容）。

Windows 注记：psycopg 的 async（AsyncConnectionPool/AsyncPostgresSaver）不能在
ProactorEventLoop 上运行（真实运行验证发现，见 docs/spec/2026-09-03-postgres-
checkpoint-migration.md §11 补充）；测试在 Windows 下统一使用 SelectorEventLoop。
Linux/生产默认即 SelectorEventLoop，不受影响。
"""

import asyncio
import shutil
import sys
import uuid
from pathlib import Path

import pytest

if sys.platform == "win32":  # pragma: no cover - 平台相关
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.research import config as rconfig  # noqa: E402
from app.research import store as rstore  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST_TMP = _REPO_ROOT / "_testtmp"


@pytest.fixture
def research_tmp():
    d = _TEST_TMP / f"rs-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    try:
        shutil.rmtree(d)
    except OSError:
        pass


@pytest.fixture
def research_sqlite(research_tmp, monkeypatch):
    """sqlite research store 隔离环境：独立 DB 文件 + store 单例重置。"""
    monkeypatch.setenv(rconfig.RESEARCH_STORE_ENV, "sqlite")
    monkeypatch.setenv(rconfig.RESEARCH_DB_ENV, str(research_tmp / "research.sqlite"))
    monkeypatch.delenv(rconfig.RESEARCH_DSN_ENV, raising=False)
    rstore.reset_store()
    yield research_tmp
    rstore.reset_store()
