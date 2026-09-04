"""Agent 执行状态持久化 Checkpointer（R2）。

职责（单一）：把 Agent 执行状态从进程内 InMemorySaver 升级为文件型 SQLite
（langgraph-checkpoint-sqlite 的 SqliteSaver），并管理 DB 文件路径与连接生命周期：

1. 读取环境变量 ``AGENT_CHECKPOINT_DB`` 作为 DB 文件路径（可选覆盖项，非凭据）；
2. 未配置时使用默认路径 ``app/runtime/checkpoints.sqlite``（相对本文件定位，不依赖 CWD）；
3. 自动创建父目录；
4. 创建 SQLite 连接并构造 ``AsyncBridgeSqliteSaver``（SqliteSaver 子类，
   setup 自动建表 checkpoints/writes）；
5. 进程内同一路径复用同一连接/Checkpointer（单例）；
6. 提供 ``get_checkpoint_db_path()`` 供测试/报告读取实际路径；
7. DB 无法写入或建表失败时 fail-fast 抛 ``RuntimeError``，绝不静默退化为内存 Checkpointer。

为什么返回 SqliteSaver 的异步桥接子类（真实环境探测结论，非假设）：
- main_agent 通过 ``astream`` 异步驱动 LangGraph 图，异步 Pregel 需要 checkpointer
  的 a* 方法（aget_tuple/aput/aput_writes/alist）；
- langgraph-checkpoint-sqlite 官方**同步** SqliteSaver 的这些异步方法一律抛
  NotImplementedError（langgraph 1.1.10+checkpoint 4.0.3+sqlite-saver 3.0.3 与
  langgraph 1.2.7+checkpoint 4.1.1+sqlite-saver 3.1.1 两套环境实测一致）；
- InMemorySaver 之所以能配合 astream，是因为它实现了异步方法；
- 因此本模块把异步方法桥接到同步实现（asyncio.to_thread 默认线程池执行）。
  SqliteSaver 内部 threading.Lock 保证连接串行安全（与官方同步用法同契约），
  同步方法原样继承 → sync invoke / 跨进程恢复测试语义不变。

持久化边界（如实声明，勿夸大）：
- 持久化：LangGraph 图状态快照（messages、节点进度、pending writes 等 execution state），
  以 thread_id 为主键之一存于 SQLite checkpoints/writes 表；
- 不持久化/不自动重放：外部副作用（网络搜索 / DB 查询 / RAGFlow 请求 / 文件产物）、
  LLM 连接、ContextVar——这些在服务重启后由上层重建，不在本模块职责内；
- 本模块是 Checkpoint 层，不是 BaseStore / 长期记忆（那是 Store 层，另一机制）。

环境变量约定（与 ARCHITECTURE.md §5/§9 对齐）：AGENT_CHECKPOINT_DB 非凭据，
可选配置、有默认值；相对路径按进程 CWD 解析（生产建议用绝对路径或仓库内相对路径）。
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict

from langgraph.checkpoint.sqlite import SqliteSaver

#: 环境变量名：覆盖默认 checkpoint DB 路径（可选配置，非凭据）
CHECKPOINT_DB_ENV = "AGENT_CHECKPOINT_DB"

#: 默认 DB 路径：app/runtime/checkpoints.sqlite（相对本文件定位，gitignore 已忽略）
DEFAULT_CHECKPOINT_DB_PATH = Path(__file__).resolve().parent / "checkpoints.sqlite"

#: 进程内单例缓存：key = 解析后的 DB 绝对路径，value = SqliteSaver 子类实例
#: （同一路径复用同一连接）。生产环境只会出现一个路径（env 固定或默认），
#: 等价于进程级单例；按路径缓存是为了让测试各自指向临时 DB 时互不污染，
#: 且保证"同一路径复用同一实例"。
_savers: Dict[str, SqliteSaver] = {}

_lock = threading.Lock()


class AsyncBridgeSqliteSaver(SqliteSaver):
    """SqliteSaver 的异步桥接子类（见模块 docstring 的实测结论）。

    LangGraph 异步运行（astream/ainvoke）需要 checkpointer 的 a* 方法；
    官方同步 SqliteSaver 的 a* 方法抛 NotImplementedError。本子类把
    ``aget_tuple`` / ``aput`` / ``aput_writes`` / ``alist`` 桥接到同步实现
    （``asyncio.to_thread`` 默认线程池），同步方法（get_tuple/put/list/...）
    原样继承 → sync invoke 与跨进程恢复语义完全不变。

    并发契约：与官方同步用法一致——SqliteSaver 内部 threading.Lock 串行化
    连接访问；多会话并发时 a* 调用在线程池排队执行，单进程足够（Spec §10）。
    """

    async def aget_tuple(self, config: Any) -> Any:
        return await asyncio.to_thread(super().get_tuple, config)

    async def alist(
        self,
        config: Any,
        *,
        filter: Any = None,
        before: Any = None,
        limit: Any = None,
    ) -> Any:
        items = await asyncio.to_thread(
            lambda: list(
                super().list(config, filter=filter, before=before, limit=limit)
            )
        )
        for item in items:
            yield item

    async def aput(
        self,
        config: Any,
        checkpoint: Any,
        metadata: Any,
        new_versions: Any,
    ) -> Any:
        return await asyncio.to_thread(
            super().put, config, checkpoint, metadata, new_versions
        )

    async def aput_writes(
        self, config: Any, writes: Any, task_id: Any, task_path: str = ""
    ) -> None:
        await asyncio.to_thread(
            super().put_writes, config, writes, task_id, task_path
        )


def resolve_checkpoint_db_path() -> Path:
    """解析实际生效的 DB 路径：env ``AGENT_CHECKPOINT_DB`` 优先，否则默认路径。"""
    raw = os.environ.get(CHECKPOINT_DB_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_CHECKPOINT_DB_PATH
    return Path(raw).expanduser().resolve()


def get_checkpoint_db_path() -> str:
    """返回当前配置生效的 DB 文件绝对路径（供测试/报告断言实际落盘位置）。"""
    return str(resolve_checkpoint_db_path())


def get_sqlite_checkpointer() -> SqliteSaver:
    """构造/复用文件型 SqliteSaver（进程内单例，按解析后路径缓存）。

    返回的实例是 ``AsyncBridgeSqliteSaver``（SqliteSaver 子类）：同步用法
    （invoke / get_state / 跨进程恢复）与异步用法（main_agent 的 astream）
    均可工作，DB 文件与表结构与官方 SqliteSaver 完全一致。

    Fail-fast 语义：父目录创建失败、SQLite 连接失败或 setup 建表失败时抛
    ``RuntimeError``（携带路径与原因），**不允许**静默退化为 InMemorySaver——
    那会破坏 R2 的持久化承诺（服务重启后状态丢失）。
    """
    db_path = resolve_checkpoint_db_path()
    key = str(db_path)
    with _lock:
        cached = _savers.get(key)
        if cached is not None:
            return cached
        saver = _build_sqlite_saver(db_path)
        _savers[key] = saver
        return saver


def _build_sqlite_saver(db_path: Path) -> SqliteSaver:
    """真实创建连接 + AsyncBridgeSqliteSaver + eager setup（幂等建表，可写性探测）。"""
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False：SqliteSaver 内部持锁，跨线程/跨 async 调度共享连接
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        saver = AsyncBridgeSqliteSaver(conn)
        # setup() 幂等：CREATE TABLE IF NOT EXISTS checkpoints/writes。
        # eager 调用即真实写盘一次：路径不可写/磁盘只读在此立刻暴露（fail-fast），
        # 而非等到 LangGraph 首次 checkpoint 时才炸。
        saver.setup()
        return saver
    except (OSError, sqlite3.Error) as exc:
        raise RuntimeError(
            f"无法初始化 SQLite checkpointer（AGENT_CHECKPOINT_DB 或默认路径解析到 "
            f"{db_path}）：{exc}。拒绝静默退化为内存 checkpointer，请检查路径可写性。"
        ) from exc
