"""
主智能体组装与异步执行模块

负责把模型、主提示词、文件类工具和三个专家子智能体组装成 DeepAgent，
并提供 run_deep_agent 作为后续 API 层调用的统一入口。运行时还会为每个
session_id 创建独立工作目录，并把工具调用、子智能体调用和最终结果推送给前端。
"""

import asyncio
import uuid
from pathlib import Path

from deepagents import create_deep_agent

from app.agent.llm import model
from app.agent.prompts import main_agent_content
from app.agent.subagents.database_query_agent import database_query_agent
from app.agent.subagents.knowledge_base_agent import knowledge_base_agent
from app.agent.subagents.network_search_agent import network_search_agent
from app.api.context import (
    reset_session_context,
    set_session_context,
    set_thread_context,
)
from app.api.monitor import (
    RunTerminalGuard,
    monitor,
    reset_run_context,
    reset_task_context,
    set_run_context,
    set_task_context,
)
from app.research import context as research_ctx
from app.research import registry as research_reg
from app.runtime.checkpoint import get_checkpointer
from app.runtime.governance.context import get_governance_execution

# 文件类工具由主智能体直接掌握，负责读取上传附件和生成最终交付文档
from app.tools.markdown_tools import generate_markdown
from app.tools.pdf_tools import convert_md_to_pdf
from app.tools.upload_file_read_tool import read_file_content
from app.utils.upload_guard import restore_uploads_to_session

# 主智能体是调度中心：
# 1. tools 只放最终交付相关的文件工具
# 2. subagents 放网络、数据库、RAGFlow 三类信息获取助手
# 3. checkpointer 通过 thread_id 保存同一会话中的执行上下文（R2 演进：backend 抽象，
#    sqlite=官方 AsyncSqliteSaver（local/test 缺省）| postgres=官方 AsyncPostgresSaver（生产），
#    见 app/runtime/checkpoint.py 与 docs/spec/2026-09-03-postgres-checkpoint-migration.md）。
#
# 官方 AsyncSaver 构造时必须位于 running event loop（构造时绑定 loop），因此 main_agent
# 不再于模块 import 期创建 checkpointer；改为 loop 内的 lazy/async 组装（get_main_agent），
# run_deep_agent 与 FastAPI lifespan（prewarm）均在 loop 内调用。对外契约不变：
# 不得绕过 run_deep_agent 直接调用 agent.astream（ARCHITECTURE.md §3 红线保持）。
_main_agent = None
_main_agent_lock = None


def _agent_lock() -> asyncio.Lock:
    global _main_agent_lock
    if _main_agent_lock is None:
        _main_agent_lock = asyncio.Lock()
    return _main_agent_lock


async def get_main_agent():
    """loop 内 lazy 组装主智能体（进程级缓存，幂等）。

    - 首次调用创建后端 checkpointer（await get_checkpointer()，绑定当前 loop）并编译 agent；
    - 并发首个请求由 asyncio.Lock 串行化，只创建一次；
    - 创建失败不缓存（下次调用重试），异常冒泡给调用方（fail-fast）。
    """
    global _main_agent
    if _main_agent is None:
        async with _agent_lock():
            if _main_agent is None:
                checkpointer = await get_checkpointer()
                agent = create_deep_agent(
                    model=model,
                    system_prompt=main_agent_content["system_prompt"],
                    tools=[generate_markdown, convert_md_to_pdf, read_file_content],
                    checkpointer=checkpointer,
                    subagents=[
                        database_query_agent,
                        network_search_agent,
                        knowledge_base_agent,
                    ],
                )
                _main_agent = agent
    return _main_agent


# 当前文件位于 app/agent/main_agent.py，parents[1] 即 app 目录
project_root_path = Path(__file__).parents[1].resolve()


async def run_deep_agent(task_query, session_id):
    """
    异步流式执行主智能体

    API 层会为每次任务传入用户问题和 session_id。本函数负责准备会话目录、
    复制上传文件、写入 ContextVar，并在流式执行过程中把关键事件上报给前端。
    :param task_query: 前端提交的原始任务问题
    :param session_id: 当前任务 ID，同时用于 thread_id、输出目录和 WebSocket 定向推送
    """
    print(f"[MainAgent] 开始执行会话，session_id={session_id}")

    # 每个会话独立使用 output/session_{session_id}，避免不同用户的产物互相覆盖
    session_dir = project_root_path / "output" / f"session_{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)

    # 前端和工具使用绝对路径；提示词里只给模型相对路径，降低模型误用系统绝对路径的概率
    session_dir_str = str(session_dir).replace("\\", "/")
    relative_session_dir_str = str(session_dir.relative_to(project_root_path)).replace(
        "\\", "/"
    )

    # 上传文件先落在 updated/session_{session_id}，执行前复制到本次 output 工作目录
    # 这样读文件工具和生成文件工具都只需要围绕同一个 session_dir 工作
    # R1：upload 端点以随机存储名落盘并写 manifest，这里按 manifest 还原为"原名"复制，
    # 保证模型按原名读取的语义不变；无 manifest（旧会话/直接放置）时回退原样复制
    updated_dir_path = project_root_path / "updated" / f"session_{session_id}"
    updated_info_prompt = ""
    if updated_dir_path.exists():
        files = restore_uploads_to_session(updated_dir_path, session_dir)
        if files:
            # 把上传文件列表注入用户消息，提醒模型先调用 read_file_content 获取附件内容
            updated_info_prompt = (
                "\n    [已上传文件] 已加载到工作目录:\n"
                + "\n".join([f"    - {f}" for f in files])
                + "\n    请优先使用工具（read_file_content）读取并参考这些文件。"
            )

    # ContextVar 让深层工具无需显式传参，也能拿到当前会话目录和 WebSocket thread_id
    session_dir_token = set_session_context(session_dir_str)
    session_id_token = set_thread_context(session_id)
    # R3：一次 run_deep_agent = 一个 run_id（区分同 thread 的多次任务 / R2 续跑），
    # 所有事件在 emit 时快照 thread_id + run_id；finally 中必须 reset 防泄漏。
    # Step 4 Batch 1/3（correlation）：governance 服务接线预生成 run_id 并经 execution ctx 注入时
    # 沿用该值 → TaskRecord.run_id == ResearchRun.run_id == monitor run_id（三方同源）；
    # Batch 3(C)：governed 时 monitor envelope 附 task_id（bare → None）。
    gov_exec = get_governance_execution()
    run_id = (
        gov_exec.run_id
        if gov_exec is not None and gov_exec.run_id
        else uuid.uuid4().hex
    )
    run_token = set_run_context(run_id)
    task_token = set_task_context(gov_exec.task_id if gov_exec is not None else None)

    # F1：创建 ResearchRun + root SubQuestion（旁路、fail-open）。
    # research_runs.run_id 与执行 run_id 同源（唯一关联键），但 ResearchRun 不是
    # checkpoint——两套数据面不共表/不共迁移/不共事务。工具经 research ContextVar
    # 读到 (run_id, root sub_question_id) 完成 SearchQuery/Source/Evidence 注册。
    research_tokens = None
    research_run_ids = research_reg.create_run_and_root(
        session_id, task_query[:2000], run_id=run_id
    )
    if research_run_ids:
        research_tokens = research_ctx.set_research_context(*research_run_ids)

    # checkpointer 依赖 thread_id 区分会话记忆；同一 session_id 会复用同一条执行上下文
    config = {"configurable": {"thread_id": session_id}}

    # F8 S1（additive，Spec §8）：governance-active 时注入 GovernanceCallbackHandler +
    # recursion_limit（framework 安全上限；运行 config 优先于 create_agent 的 with_config）。
    # 非 governance 执行（无 context）→ 与历史行为完全一致。
    governance_active = gov_exec is not None
    if gov_exec is not None:
        if gov_exec.recursion_limit is not None:
            config.setdefault("recursion_limit", gov_exec.recursion_limit)
        _cbs = list(config.get("callbacks") or [])
        _cbs.append(gov_exec.make_handler())
        config["callbacks"] = _cbs

    # 工作环境指令是运行时动态补充的，约束模型只在当前会话目录读写文件
    path_instruction = f"""
    【工作环境指令】
    工作目录: {relative_session_dir_str}
    {updated_info_prompt}

    规则：
    1. 新生成文件必须保存到工作目录：'{relative_session_dir_str}/filename'
    2. 读取已上传的文件时，请直接将文件名（例如：'开篇.txt'）作为 filename 参数传入（read_file_content）读取工具，不要带上任何目录前缀。
    3. 使用相对路径，禁止使用绝对路径
    4. 若存在上传文件，请先分析内容
    """

    # R3：run 级终态一次性（task_result / task_cancelled / error 三选一、至多一次）
    terminal_guard = RunTerminalGuard()
    # 正常收尾时的最终内容候选（不逐 chunk 发 result，run 结束才一次性发）
    final_content = None

    try:
        # R3：task 生命周期起点（先于 session_created），随后是工作目录就绪
        monitor.report_task_started(task_query[:500])
        # 前端拿到工作目录后，可以展示本次任务生成的 Markdown/PDF 等产物
        monitor.report_session_dir(session_dir_str)

        # astream 会持续产出模型节点、工具节点和子智能体节点的状态片段
        agent = await get_main_agent()
        async for chunk in agent.astream(
            {"messages": [{"role": "user", "content": task_query + path_instruction}]},
            config=config,
        ):
            # chunk 形如 {"model": {"messages": [...]}}，这里主要关心模型最新消息
            for node_name, state in chunk.items():
                if not state or "messages" not in state:
                    continue
                messages = state["messages"]
                if messages and isinstance(messages, list):
                    last_msg = messages[-1]
                    if node_name == "model":
                        if last_msg.tool_calls:
                            # DeepAgents 调用子智能体时，本质上会产生名为 task 的工具调用
                            for tool_call in last_msg.tool_calls:
                                if tool_call["name"] == "task":
                                    # 子智能体调用单独上报，前端可以展示“正在调用哪个专家助手”
                                    monitor.report_assistant(
                                        tool_call["args"]["subagent_type"],
                                        {
                                            "description": tool_call["args"][
                                                "description"
                                            ]
                                        },
                                    )
                        elif last_msg.content:
                            # 模型没有继续调用工具时的文本：记录为最终结果候选，
                            # 终态事件在 run 收尾一次性发出（R3：exactly-once）
                            print(
                                f"主智能体执行结果，最终结果：{last_msg.content[:100]}"
                            )
                            final_content = last_msg.content

        # astream 正常结束 → run 级一次性终态（保留 data.result 与前端语义）
        if terminal_guard.may_emit("task_result"):
            # F7 Research State Bridge（Spec §7 唯一接线点）：terminal finalization 阶段物化
            # Candidate Claims 并 orchestrate F2–F6（fail-open，绝不阻断 task_result / 主链路）。
            # 取消/异常路径（CancelledError / Exception 分支）不执行 finalization。
            if research_run_ids is not None:
                try:
                    from app.research import bridge as research_bridge

                    research_bridge.finalize_run(
                        run_id, final_content if final_content is not None else ""
                    )
                except Exception as f7_exc:  # noqa: BLE001 - fail-open
                    print(f"[ResearchBridge] finalize_run failed (fail-open): {f7_exc}")
            research_reg.set_run_status(run_id, "finished")
            monitor.report_task_result(
                final_content if final_content is not None else ""
            )

    except asyncio.CancelledError:
        if terminal_guard.may_emit("task_cancelled"):
            research_reg.set_run_status(run_id, "cancelled")
            monitor.report_task_cancelled()
        raise
    except Exception as e:
        # 异步执行异常也走 monitor，保证前端能收到明确错误事件（公共 report_error）
        if terminal_guard.may_emit("error"):
            research_reg.set_run_status(run_id, "failed")
            monitor.report_error(f"执行主智能发生异常信息：{str(e)}", {"error": str(e)})
        # F8 S1（additive，Spec §8）：governance-active 时异常必须上抛，使上层 Governance
        # Controller 能观察到真实异常（否则 governance abort / agent exception 会被吞掉并
        # 被误判为正常完成 → budget control 失明）。非 governance 执行保持吞掉语义不变。
        if governance_active:
            raise
    finally:
        # 任务结束后恢复 ContextVar，避免后续请求复用到本次会话目录、thread_id 或 run_id
        reset_session_context(session_dir_token, session_id_token)
        reset_run_context(run_token)
        reset_task_context(task_token)
        if research_tokens is not None:
            research_ctx.reset_research_context(*research_tokens)


if __name__ == "__main__":
    import asyncio

    asyncio.run(
        run_deep_agent("从网络查询机器人信息，并生成Markdown文件", "test_session_001")
    )
