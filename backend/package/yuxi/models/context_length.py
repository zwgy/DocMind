"""模型上下文长度的统一解析规则。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CONTEXT_LENGTH_SOURCE_MANUAL = "manual"
CONTEXT_LENGTH_SOURCE_MODELS_API = "models_api"
CONTEXT_LENGTH_SOURCE_LANGCHAIN_PROFILE = "langchain_profile"
CONTEXT_LENGTH_SOURCE_DEFAULT = "default"

PERSISTED_CONTEXT_LENGTH_SOURCES = frozenset(
    {
        CONTEXT_LENGTH_SOURCE_MANUAL,
        CONTEXT_LENGTH_SOURCE_MODELS_API,
    }
)


@dataclass(frozen=True)
class ResolvedContextLength:
    """运行时最终采用的上下文长度及其来源。"""

    value: int
    source: str


def positive_int_or_none(value: Any) -> int | None:
    """宽松读取外部元数据中的正整数，不让异常字段阻断后续解析。"""
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def resolve_context_length(
    *,
    configured_value: Any,
    configured_source: str | None,
    profile_value: Any,
    default_value: Any,
) -> ResolvedContextLength:
    """按“已保存配置、LangChain profile、系统默认值”解析最终窗口。"""
    configured = positive_int_or_none(configured_value)
    if configured is not None:
        source = (
            configured_source if configured_source in PERSISTED_CONTEXT_LENGTH_SOURCES else CONTEXT_LENGTH_SOURCE_MANUAL
        )
        return ResolvedContextLength(configured, source)

    profile = positive_int_or_none(profile_value)
    if profile is not None:
        return ResolvedContextLength(profile, CONTEXT_LENGTH_SOURCE_LANGCHAIN_PROFILE)

    default = positive_int_or_none(default_value)
    if default is None:
        raise ValueError("default_context_window 必须是正整数")
    return ResolvedContextLength(default, CONTEXT_LENGTH_SOURCE_DEFAULT)
