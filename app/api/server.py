"""
FastAPI 接口层与项目闭环入口

负责承接前端的任务提交、任务取消、文件上传/下载、输出文件列表查询和
WebSocket 长连接。HTTP 接口只做轻量调度，真正的 DeepAgents 执行放到后台
任务中；执行进度、工具调用和最终结果由 monitor 按 thread_id 推送给前端。
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List

import uvicorn
from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api.monitor import manager
from app.runtime.checkpoint import (
    init_checkpoint_lifespan,
    shutdown_checkpoint_lifespan,
)
from app.runtime.governance import service as gov_service
from app.runtime.governance.controller import (
    GovernanceController,
    get_controller as get_governance_controller,
)
from app.session import service as session_service
from app.utils.session_id import (
    InvalidThreadIdError,
    safe_thread_id_or_new,
    sanitize_thread_id,
)
from app.utils.upload_guard import (
    UploadRejectedError,
    generate_storage_name,
    is_storage_name,
    load_upload_manifest,
    record_upload_meta,
    sanitize_filename,
    save_upload_manifest,
    validate_extension,
    validate_size,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """
    服务生命周期入口。

    1. 绑定当前事件循环到 WebSocket 管理器，确保后台 Agent 任务可以把
       monitor 事件投递回 FastAPI 所在的 loop。
    2. 初始化 checkpoint 后端（postgres：创建 AsyncConnectionPool 并注入；
       sqlite：无操作，由 prewarm 创建）——失败即启动失败（fail-fast）。
    3. 启动期 prewarm：创建后端 checkpointer 并 lazy 组装主智能体（官方
       AsyncSaver 必须在 running loop 内创建），保持 R2 的“启动即可发现 DB
       不可用”语义，并避免首个请求承担初始化延迟。
    """
    loop = asyncio.get_running_loop()
    manager.set_loop(loop)
    print(f"[Server] WebSocket Manager bound to loop: {id(loop)}")

    await init_checkpoint_lifespan()
    # prewarm：get_main_agent 内部 await get_checkpointer() → setup() 建表/迁移
    from app.agent.main_agent import get_main_agent

    await get_main_agent()
    print("[Server] Checkpoint backend ready (main agent assembled lazily)")
    yield
    await shutdown_checkpoint_lifespan()
    print("[Server] Checkpoint resources released")


# 当前文件位于 app/api/server.py，运行时目录统一收敛到 app 目录
current_dir = Path(__file__).resolve().parent
project_root = current_dir.parent

app = FastAPI(title="DeepAgents API", lifespan=lifespan)

# 保存 thread_id -> 后台 Agent 任务，用于同一会话任务替换和主动取消
active_tasks: dict[str, asyncio.Task] = {}
# Step 4 Batch 1：thread_id -> 当前 governed TaskRecord.task_id（server 不直接写 TaskRecord）
_task_registry: dict[str, str] = {}


def _governance_terminal_sink(frame: dict) -> None:
    """governance_terminal live bridge → 现有 per-thread WS manager（observation，fail-open）。"""
    thread_id = frame.get("thread_id")
    if not thread_id:
        return
    try:
        manager.enqueue(frame, thread_id)
    except Exception as exc:  # noqa: BLE001 — bridge 失败不影响 control/lifecycle
        print(
            f"[GovernanceBridge] live sink 失败（thread {thread_id}，fail-open）：{exc}"
        )


def _require_governance() -> GovernanceController:
    """governance controller（store 不可用 → fail-closed 拒绝受理，M-Spec c1）。"""
    ctl = get_governance_controller()
    if ctl is None:
        raise HTTPException(
            status_code=503, detail="governance store 不可用，拒绝受理（fail-closed）"
        )
    if ctl.live_sink is None:  # 最小 DI：复用现有 manager，不新建 event bus
        ctl.live_sink = _governance_terminal_sink
    return ctl


# output 保存每个会话最终工作区，前端只允许从这里浏览和下载生成文件
output_dir = project_root / "output"
output_dir.mkdir(exist_ok=True)

# updated 暂存用户上传文件，run_deep_agent 启动时会复制到对应 output/session_xxx
updated_dir = project_root / "updated"
updated_dir.mkdir(exist_ok=True)

# 教学项目通常前后端分别本地启动，这里放开跨域以便 Vite 页面直接调用 API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TaskRequest(BaseModel):
    """前端启动任务时提交的请求体。"""

    query: str
    thread_id: str = None
    #: Step 4 Batch 1（可选）：治理 policy（max_llm_calls / wall_clock_timeout 等），
    #: 缺省由 governance 服务默认（M-Spec §7.4）。服务端仍可钳制（后续 API Step）。
    policy: dict = None


class SessionCreateRequest(BaseModel):
    """创建 Session 请求体（Decision Closure #4：#1 session_id == thread_id）。

    - thread_id 缺省 → 服务端生成安全 uuid（session_id = thread_id）；
    - thread_id 提供 → 既有历史 Thread 注册（session_id = provided thread_id）；
    - 不得出现 session_id != thread_id。
    """

    title: str = None
    description: str = None
    thread_id: str = None


class SessionRestoreRequest(BaseModel):
    """PATCH /api/sessions/{id} 请求体（v1 仅支持 unarchive：status="active"）。"""

    status: str = "active"


class SessionTaskCreateRequest(BaseModel):
    """session-scoped 研究任务创建请求体（语义同 TaskRequest，thread_id = session_id）。"""

    query: str
    policy: dict = None


def _forget_task(thread_id: str, task: asyncio.Task) -> None:
    """
    清理已结束任务的登记关系。

    done_callback 触发时，active_tasks 中可能已经被新任务替换；只有仍是同一个
    task 时才删除，避免误清理同 thread_id 下刚启动的新任务。
    """
    if active_tasks.get(thread_id) is task:
        active_tasks.pop(thread_id, None)


def _forget_governed_task(thread_id: str, task_id: str, task: asyncio.Task) -> None:
    """清理 governed 登记：只有 registry 仍指向本 task 时才移除（防止旧任务回调误删新任务）。"""
    _forget_task(thread_id, task)
    if _task_registry.get(thread_id) == task_id:
        _task_registry.pop(thread_id, None)


async def _start_governed_task(
    ctl: GovernanceController,
    *,
    thread_id: str,
    query: str,
    policy: dict = None,
    precondition: object = None,
) -> tuple[object, asyncio.Task]:
    """同 thread 单活跃收敛 + governed 提交 + server 登记（POST /api/task 与 session-scoped 共用）。

    语义与既有 run_task 完全一致：同一 thread_id 只保留一个活跃任务（先收敛旧任务——
    治理任务走冻结 cancel funnel；再启动新任务），避免并发写同一会话目录。

    precondition：可调用对象，在收敛旧任务（可能 await、让出事件循环）**之后、真正 submit 之前**
    同步执行 —— 供 session 场景在「归档 / 建任务」并发窗口内二次校验 Session 仍 active
    （Submit 前最后一个同步点，不再让出）。
    """
    old_task = active_tasks.get(thread_id)
    if old_task and not old_task.done():
        old_task.cancel()
    old_task_id = _task_registry.get(thread_id)
    if old_task_id:
        try:
            await ctl.cancel_governed(old_task_id)
        except Exception:  # noqa: BLE001 — 旧任务收敛尽力而为
            pass
    if precondition is not None:
        precondition()
    record, task = gov_service.submit_task(
        ctl, thread_id=thread_id, query=query, policy=policy
    )
    _task_registry[thread_id] = record.task_id
    active_tasks[thread_id] = task
    task.add_done_callback(
        lambda t: _forget_governed_task(thread_id, record.task_id, t)
    )
    return record, task


async def _cancel_governed_task(
    ctl: GovernanceController, thread_id: str, gov_task_id: str
) -> dict:
    """governed cancel（冻结 funnel + lifecycle event）+ server 登记收敛。

    语义与既有 /api/task/{thread_id}/cancel 的 governed 分支一致（共享实现，防止漂移）。
    """
    task = active_tasks.get(thread_id)
    try:
        res = await gov_service.cancel_task(ctl, gov_task_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"governance cancel 失败：{e}"
        ) from e
    if task and not task.done():
        task.cancel()
    if not task or task.done():
        active_tasks.pop(thread_id, None)
    return {
        "status": res.get("status") or "cancelled",
        "thread_id": thread_id,
        "task_id": gov_task_id,
        "already_terminal": res.get("already_terminal", False),
    }


def _map_session_error(exc: Exception) -> HTTPException:
    """session 域错误 → 本项目既有错误形态（HTTPException + detail，Decision Closure #7/#14）。"""
    if isinstance(
        exc,
        (
            session_service.SessionNotFoundError,
            session_service.TaskNotFoundError,
            session_service.TaskNotInSessionError,
        ),
    ):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, session_service.SessionStateError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, session_service.SessionValidationError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=f"session 服务失败：{exc}")


@app.post("/api/task")
async def run_task(request: TaskRequest):
    """
    启动一次 DeepAgents 后台任务（governed：TaskRecord + Controller.execute(policy)）。

    HTTP 请求只负责创建 TaskRecord、登记后台任务并立即返回；后续执行轨迹、子智能体
    调用和最终结果仍由 monitor 通过 `/ws/{thread_id}` 推送（live），terminal 状态与
    lifecycle event 由 governance（durable）记录。governance store 不可用 → 503 拒绝受理。
    """
    # thread_id 净化：非法/缺失时服务端生成新 uuid（前端接受响应中的 thread_id）
    thread_id = safe_thread_id_or_new(request.thread_id)

    # 同一 thread_id 只保留一个活跃任务：先收敛旧任务（治理任务走冻结 funnel），
    # 再启动新任务，避免并发写同一会话目录。（AC2 正式 superseded 语义留后续 API Step）
    ctl = _require_governance()
    record, task = await _start_governed_task(
        ctl, thread_id=thread_id, query=request.query, policy=request.policy
    )

    return {
        "status": "started",
        "thread_id": thread_id,
        "task_id": record.task_id,
        "run_id": record.run_id,
    }


@app.post("/api/task/{thread_id}/cancel")
async def cancel_task(thread_id: str):
    """
    取消指定 thread_id 对应的后台 Agent 任务（governed：冻结 Controller funnel + lifecycle event）。

    注意：取消先收敛 TaskRecord（cancelled），再向 asyncio.Task 注入 CancelledError。若底层
    第三方工具正在执行不可中断的同步阻塞调用，任务可能需要等该调用返回后才会真正结束
    （TaskRecord terminal ≠ 底层已停；linger 观察字段记录）。
    """
    task = active_tasks.get(thread_id)
    gov_task_id = _task_registry.get(thread_id)

    if gov_task_id is not None:
        ctl = _require_governance()
        return await _cancel_governed_task(ctl, thread_id, gov_task_id)

    # 兼容旧（非 governed）任务
    if not task or task.done():
        active_tasks.pop(thread_id, None)
        raise HTTPException(status_code=404, detail="任务不存在或已结束")
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=1.0)
    except asyncio.CancelledError:
        _forget_task(thread_id, task)
        return {"status": "cancelled", "thread_id": thread_id}
    except asyncio.TimeoutError:
        return {"status": "cancelling", "thread_id": thread_id}
    except Exception as e:
        _forget_task(thread_id, task)
        return {"status": "cancelled", "thread_id": thread_id, "message": str(e)}
    _forget_task(thread_id, task)
    return {"status": "cancelled", "thread_id": thread_id}


@app.get("/api/tasks")
async def list_tasks_api(thread_id: str = None):
    """任务列表（只读；可选 thread 过滤；created_at 降序）。"""
    ctl = _require_governance()
    records = gov_service.list_tasks(ctl, thread_id=thread_id)
    return {"tasks": [gov_service.task_dict(r) for r in records]}


@app.get("/api/tasks/{task_id}")
async def get_task_api(task_id: str):
    """任务详情（terminal reason / counters / policy 等）。"""
    ctl = _require_governance()
    record = gov_service.get_task(ctl, task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return gov_service.task_dict(record)


# ---------------------------------------------------------------------------
# Multi-Session（Decision Closure）：/api/sessions —— additive，既有端点语义不变。
# Session 容器：session_id == thread_id（1:1）；归属 = governance_tasks.thread_id。
# 隔离：所有 Session-scoped 操作先验证 session 存在 → 状态合法 → task 归属，否则拒绝。
# ---------------------------------------------------------------------------
@app.post("/api/sessions", status_code=201)
async def create_session_api(request: SessionCreateRequest):
    """创建 Session（默认 ACTIVE；Decision Closure #4）。

    thread_id 缺省 → 服务端生成安全 uuid；提供 → 注册既有历史 Thread。
    不得出现 session_id != thread_id。
    """
    ctl = _require_governance()
    try:
        sess = session_service.create_session(
            ctl,
            title=request.title,
            description=request.description,
            thread_id=request.thread_id,
        )
    except Exception as exc:  # noqa: BLE001 — 统一映射 session 域错误
        raise _map_session_error(exc) from exc
    return sess.to_dict()


@app.get("/api/sessions")
async def list_sessions_api(
    include_archived: bool = False,
    limit: int = session_service.DEFAULT_LIST_LIMIT,
    offset: int = 0,
):
    """Session 列表（缺省只返回 active；含 task_count/running_tasks/latest_task）。"""
    ctl = _require_governance()
    try:
        return session_service.list_sessions(
            ctl,
            include_archived=include_archived,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_session_error(exc) from exc


@app.get("/api/sessions/{session_id}")
async def get_session_api(session_id: str):
    """Session Detail = 元数据 + Session Tasks + 最新执行信息（archived 仍可读）。"""
    ctl = _require_governance()
    try:
        return session_service.get_session_detail(ctl, session_id)
    except Exception as exc:  # noqa: BLE001
        raise _map_session_error(exc) from exc


@app.delete("/api/sessions/{session_id}")
async def archive_session_api(session_id: str):
    """逻辑归档 ACTIVE → ARCHIVED（幂等；有 running Task → 409；不物理删除任何历史）。"""
    ctl = _require_governance()
    try:
        return session_service.archive_session(ctl, session_id)
    except Exception as exc:  # noqa: BLE001
        raise _map_session_error(exc) from exc


@app.patch("/api/sessions/{session_id}")
async def restore_session_api(session_id: str, request: SessionRestoreRequest):
    """v1 状态转换端点：仅支持 unarchive（status="active"；幂等）。"""
    if request.status != "active":
        raise HTTPException(
            status_code=400,
            detail="v1 仅支持恢复操作：PATCH body.status 必须为 'active'（unarchive）",
        )
    ctl = _require_governance()
    try:
        return session_service.unarchive_session(ctl, session_id)
    except Exception as exc:  # noqa: BLE001
        raise _map_session_error(exc) from exc


@app.post("/api/sessions/{session_id}/tasks", status_code=201)
async def create_session_task_api(session_id: str, request: SessionTaskCreateRequest):
    """在 Session 内创建研究任务（thread_id = session_id；复用既有 governed 运行链路）。

    前置：Session 存在 + ACTIVE（archived → 409）；同 thread 单活跃收敛语义与
    POST /api/task 一致（共享 _start_governed_task）。
    """
    ctl = _require_governance()
    try:
        session_service.assert_session_active(ctl, session_id)

        # 归档守卫并发窗口二次校验：submit 前（收敛旧任务让出事件循环之后）再确认仍 active
        def _recheck_active() -> None:
            session_service.assert_session_active(ctl, session_id)

        record, task = await _start_governed_task(
            ctl,
            thread_id=session_id,
            query=request.query,
            policy=request.policy,
            precondition=_recheck_active,
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_session_error(exc) from exc
    return {
        "status": "started",
        "thread_id": session_id,
        "task_id": record.task_id,
        "run_id": record.run_id,
    }


@app.get("/api/sessions/{session_id}/tasks")
async def list_session_tasks_api(session_id: str):
    """Session 下的 Task 列表（thread 作用域；archived 仍可读；query 可选 enrich）。"""
    ctl = _require_governance()
    try:
        tasks = session_service.list_session_tasks(ctl, session_id)
    except Exception as exc:  # noqa: BLE001
        raise _map_session_error(exc) from exc
    return {"session_id": session_id, "tasks": tasks}


@app.post("/api/sessions/{session_id}/tasks/{task_id}/cancel")
async def cancel_session_task_api(session_id: str, task_id: str):
    """Session-scoped 取消：先验证归属（task.thread_id == session_id），再走冻结 funnel。

    Session A 无法 cancel Session B 的 Task（隔离，Decision Closure #7）。
    """
    ctl = _require_governance()
    try:
        session_service.validate_task_in_session(ctl, session_id, task_id)
    except Exception as exc:  # noqa: BLE001
        raise _map_session_error(exc) from exc
    return await _cancel_governed_task(ctl, session_id, task_id)


# ---------------------------------------------------------------------------
# Batch 2：durable event replay（cursor = (task_id, governance_seq)，只读）
# ---------------------------------------------------------------------------
def _resolve_replay_task(thread_id: str, task_id: str):
    """解析 replay task：显式校验归属；缺省 → 当前 thread 的 active governed task。

    返回 (task_id or None, error_or_None)。
    """
    ctl = _require_governance()
    if task_id:
        rec = gov_service.get_task(ctl, task_id)
        if rec is None:
            return None, ("not_found", "任务不存在")
        if rec.thread_id != thread_id:
            return None, ("not_in_thread", "任务不属于该 thread")
        return rec.task_id, None
    active = _task_registry.get(thread_id)
    if active is None:
        return None, ("no_active", "task_id 未提供且当前 thread 无活跃 governed 任务")
    return active, None


@app.get("/api/threads/{thread_id}/events")
async def replay_events_api(
    thread_id: str, task_id: str = None, since_seq: int = 0, limit: int = 200
):
    """task-scoped durable event replay（governance_seq 升序；event_id 客户端去重）。"""
    resolved, err = _resolve_replay_task(thread_id, task_id)
    if err:
        code, msg = err
        status = 404 if code in ("not_found", "not_in_thread") else 400
        raise HTTPException(status_code=status, detail=msg)
    from app.runtime.governance import events as gov_events

    ctl = _require_governance()
    result = gov_events.replay_events(
        ctl._store,  # noqa: SLF001
        thread_id=thread_id,
        task_id=resolved,
        since_seq=since_seq,
        limit=limit,
    )
    return {"task_id": resolved, **result}


@app.post("/api/upload")
async def upload_files(files: List[UploadFile] = File(...), thread_id: str = Form(...)):
    """
    文件上传接口 (File Upload)。

    安全治理（R1 / P003 + 上传类型/大小）：
    1. thread_id 必须落在安全字符集内（P004），非法返回 400；
    2. 文件名净化：只取 basename 并清洗非法字符，拒绝路径穿越与控制字符；
    3. 类型白名单：仅允许 read_file_content 可解析的格式；
    4. 大小上限：流式读取累计字节，超限中止并清理；
    5. 落盘使用随机存储名（storage），原名仅记入 manifest（名称映射，不作授权边界）。

    Args:
        files (List[UploadFile]): 文件对象列表。
        thread_id (str): 关联的任务会话 ID（安全字符集）。
    """
    # thread_id 净化（P004）：upload 必须精确匹配后续 /api/task 使用的会话，
    # 因此非法直接 400（若静默换新 id，上传文件将无法被任务找到）
    try:
        safe_thread_id = sanitize_thread_id(thread_id)
    except InvalidThreadIdError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 上传文件先按会话隔离保存，避免不同任务读取到彼此的附件
    target_dir = updated_dir / f"session_{safe_thread_id}"
    target_dir.mkdir(parents=True, exist_ok=True)

    manifest = load_upload_manifest(target_dir)
    saved_files = []
    for file in files:
        # 1) 文件名净化（P003）：basename 化 + 非法字符清洗
        try:
            original = sanitize_filename(file.filename or "")
            validate_extension(original)
        except UploadRejectedError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        # 2) 存储名：优先复用已有 storage（同名重复上传 = 覆盖语义），否则随机生成
        existing = manifest.get(original, {})
        storage = existing.get("storage") if isinstance(existing, dict) else None
        if not is_storage_name(storage or ""):
            storage = generate_storage_name(original)

        # 3) 流式写入 + 大小限制（边写边累计，超限即删除已写文件，防磁盘耗尽）
        file_path = target_dir / storage
        size = 0
        try:
            with file_path.open("wb") as buffer:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    try:
                        validate_size(size)
                    except UploadRejectedError as exc:
                        raise HTTPException(status_code=413, detail=str(exc)) from exc
                    buffer.write(chunk)
        except Exception:
            # 任一步失败都清理半写文件，避免残留
            if file_path.exists():
                file_path.unlink(missing_ok=True)
            raise

        manifest[original] = record_upload_meta(original, storage, size)
        saved_files.append(original)

    # 4) 原子写 manifest（仅名称映射，非授权边界）
    save_upload_manifest(target_dir, manifest)
    return {"status": "uploaded", "files": saved_files}


@app.get("/api/download")
async def download_file(path: str):
    """
    文件下载接口 (File Download)。

    目标：
    1. 根据绝对路径下载文件。
    2. 严格的安全检查，防止越权访问。

    Args:
        path (str): 文件的绝对路径 (通常从 list_files 接口获取)。
    """
    try:
        # resolve 后再做 is_relative_to，防止 `../` 之类的路径穿越到 output 之外
        abs_path = Path(path).resolve()
        output_abs = output_dir.resolve()

        if not abs_path.is_relative_to(output_abs):
            return {"error": "拒绝访问: 只能下载输出目录下的文件"}
    except Exception:
        return {"error": "无效的路径参数"}

    if not abs_path.exists():
        return {"error": "文件不存在"}

    # FileResponse 会以流式响应返回文件内容，并让浏览器使用原文件名下载
    return FileResponse(abs_path, filename=abs_path.name)


@app.get("/api/files")
async def list_files(path: str):
    """
    文件列表查询接口 (File Explorer)。

    目标：
    1. 列出指定目录下的所有生成文件。
    2. 提供文件元数据（大小、修改时间、下载所需路径）。
    3. 严格的安全检查，防止路径遍历攻击。

    Args:
        path (str): 目标目录的绝对路径 (必须在 output 目录下)。
    """
    print(f"[DEBUG] 请求文件列表: {path}")

    try:
        # 和下载接口保持同一条安全边界：前端只能查看 output 目录内部内容
        abs_path = Path(path).resolve()
        output_abs = output_dir.resolve()

        if not abs_path.is_relative_to(output_abs):
            print(f"[ERROR] 拒绝访问: {abs_path} 不在 {output_abs} 目录下")
            return {"error": "拒绝访问: 只能访问输出目录下的文件"}

    except Exception as e:
        print(f"[ERROR] 路径解析失败: {e}")
        return {"error": f"路径无效: {e}"}

    if not abs_path.exists():
        return {"error": "目录不存在"}

    files = []
    try:
        # 递归返回文件元数据，前端据此渲染文件列表并发起下载请求
        for file_path in abs_path.rglob("*"):
            if file_path.is_file():
                stat = file_path.stat()
                files.append(
                    {
                        "name": file_path.name,
                        "type": "file",
                        "path": str(file_path),
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                    }
                )

    except Exception as e:
        print(f"[ERROR] 遍历文件失败: {e}")
        return {"error": str(e)}

    # 最新生成的文件排在前面，方便用户优先看到本次任务产物
    files.sort(key=lambda x: x.get("mtime", 0), reverse=True)
    print(f"[DEBUG] 找到 {len(files)} 个文件")
    return {"files": files}


@app.websocket("/ws/{thread_id}")
async def websocket_endpoint(websocket: WebSocket, thread_id: str):
    """
    WebSocket 实时通讯核心接口 (Real-time Communication)。

    连接建立后，ConnectionManager 会用 thread_id 保存 WebSocket。monitor 后续
    发送事件时只需要按 thread_id 查找连接，就能把进度推给对应页面。循环中的
    receive_text 用于接收前端心跳，避免连接空闲断开。

    安全：thread_id 必须落在安全字符集内（P004），非法直接关闭连接，不注册，
    避免畸形 id 污染事件路由。
    """
    # thread_id 净化（P004）：非法 thread_id 拒绝建立连接（1008 = policy violation）
    try:
        safe_thread_id = sanitize_thread_id(thread_id)
    except InvalidThreadIdError:
        print(f"[WebSocket] 拒绝非法 thread_id 连接: {thread_id!r}")
        await websocket.close(code=1008)
        return

    print(f"会话向我们发起了请求，要求建立连接：{safe_thread_id} 对应：{websocket}")

    # 连接建立后立即按 thread_id 注册，monitor 后续才能把事件定向推给当前页面
    await manager.connect(websocket, safe_thread_id)

    # Batch 2：握手可选 since_seq（cursor=(task_id, governance_seq)）→ 先 durable replay 再转 live。
    # live payload 契约不变（R3 envelope/monitor_seq）；durable 帧单独 type=governance_replay。
    qp = websocket.query_params
    query_task_id = qp.get("task_id") or None
    try:
        query_since = int(qp.get("since_seq") or "0")
    except ValueError:
        query_since = 0
    try:
        resolved, err = _resolve_replay_task(safe_thread_id, query_task_id)
        if err:
            code, msg = err
            await websocket.send_json(
                {"type": "governance_error", "code": code, "detail": msg}
            )
            if code in ("not_found", "not_in_thread"):
                await websocket.close(code=1008)
                return
            # no_active：发错误帧后空回放，继续 live
        else:
            from app.runtime.governance import events as gov_events

            ctl = _require_governance()
            result = gov_events.replay_events(
                ctl._store,  # noqa: SLF001
                thread_id=safe_thread_id,
                task_id=resolved,
                since_seq=query_since,
                limit=200,
            )
            for ev in result["events"]:
                await websocket.send_json({"type": "governance_replay", "event": ev})
            await websocket.send_json(
                {
                    "type": "governance_replay_end",
                    "task_id": resolved,
                    "next_seq": result["next_seq"],
                    "gap": result["gap"],
                }
            )
    except Exception as e:  # noqa: BLE001 — replay 失败不阻断 live
        print(f"[WebSocket] 握手 replay 失败（thread {safe_thread_id}）：{e}")

    try:
        while True:
            # 前端通常发送 ping 心跳；服务端回复 pong，顺便维持连接活跃
            data = await websocket.receive_text()
            await websocket.send_json(
                {"type": "pong", "message": f"服务端已收到: {data}"}
            )

    except WebSocketDisconnect:
        # 只移除当前 WebSocket 实例，避免旧连接断开时误删同 thread_id 的新连接
        manager.disconnect(websocket, safe_thread_id)
        print(f"[WebSocket] 客户端已断开: {safe_thread_id}")

    except Exception as e:
        print(f"[WebSocket] 连接异常: {e}")
        manager.disconnect(websocket, safe_thread_id)


if __name__ == "__main__":
    uvicorn.run("api.server:app", host="0.0.0.0", port=8000, reload=True)
