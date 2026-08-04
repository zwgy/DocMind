"""Agent 内部控制消息的统一标记边界。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

INTERNAL_OUTPUT_CONTINUATION_KEY = "_yuxi_output_continuation"


def is_internal_output_continuation(message: Any) -> bool:
    """识别请求级续写指令；该指令不得成为业务会话事实。"""
    additional_kwargs = getattr(message, "additional_kwargs", None)
    return isinstance(additional_kwargs, Mapping) and additional_kwargs.get(INTERNAL_OUTPUT_CONTINUATION_KEY) is True
