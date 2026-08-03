"""模型重试边界。"""

from __future__ import annotations

from langchain.agents.middleware import ModelRetryMiddleware
from langchain_core.exceptions import ContextOverflowError

from yuxi.agents.middlewares.token_usage import ModelOutputIncompleteError


def create_model_retry_middleware(*, max_retries: int = 2) -> ModelRetryMiddleware:
    """创建不会吞掉上下文溢出的模型重试中间件。

    上下文溢出不是瞬态故障，必须上抛给摘要中间件裁剪后再发起新请求；
    若转换成 AIMessage，客户端会把内部失败误显示为模型回答。
    """

    return ModelRetryMiddleware(
        max_retries=max_retries,
        retry_on=lambda error: not isinstance(error, ContextOverflowError | ModelOutputIncompleteError),
        on_failure="error",
    )
