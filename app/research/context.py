"""Research 运行期上下文（F1）。stdlib contextvars，与 app/api/context、monitor 独立。

- run_deep_agent 在创建 ResearchRun 后 set（run_id + root sub_question_id）；
- 工具（executor 线程）经 LangChain 提交时复制的上下文读取；
- 显式参数优先（registry 公共函数都以显式 run_id 为主），此处为运行期便利；
- finally 中 reset，防泄漏（与 monitor run context 同纪律）。
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Optional

_research_run_ctx: ContextVar[Optional[str]] = ContextVar(
    "research_run_id", default=None
)
_research_subq_ctx: ContextVar[Optional[str]] = ContextVar(
    "research_sub_question_id", default=None
)


def set_research_context(
    run_id: Optional[str], sub_question_id: Optional[str]
) -> tuple:
    """设置当前研究上下文，返回 reset 用 token 对。"""
    run_token: Token = _research_run_ctx.set(run_id)
    sq_token: Token = _research_subq_ctx.set(sub_question_id)
    return run_token, sq_token


def get_research_context() -> tuple[Optional[str], Optional[str]]:
    """返回 (run_id, sub_question_id)；未设置时为 (None, None)。"""
    return _research_run_ctx.get(), _research_subq_ctx.get()


def reset_research_context(run_token: Token, sq_token: Token) -> None:
    _research_run_ctx.reset(run_token)
    _research_subq_ctx.reset(sq_token)
