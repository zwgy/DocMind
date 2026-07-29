import json
import os
from pathlib import Path
from typing import Annotated

from langchain.tools import InjectedToolCallId
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolRuntime
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field, field_validator, model_validator

from yuxi.agents.toolkits.registry import ToolExtraMetadata, _all_tool_instances, _extra_registry, tool
from yuxi.utils import logger
from yuxi.utils.paths import CONVERSATION_HISTORY_DIR_NAME, LARGE_TOOL_RESULTS_DIR_NAME, VIRTUAL_PATH_OUTPUTS
from yuxi.utils.question_utils import normalize_questions

# Lazy initialization for TavilySearch (only when API key is available)
_tavily_search_instance = None

_PRESENT_ARTIFACTS_INTERNAL_DIR_NAMES = frozenset(
    {CONVERSATION_HISTORY_DIR_NAME, LARGE_TOOL_RESULTS_DIR_NAME, "large_tool_history"}
)


def _create_tavily_search():
    """Create and register TavilySearch tool with metadata."""
    global _tavily_search_instance
    if _tavily_search_instance is None:
        from langchain_tavily import TavilySearch

        _tavily_search_instance = TavilySearch()

    return _tavily_search_instance


# 注册 TavilySearch 工具（延迟初始化）
def _register_tavily_tool():
    """Register TavilySearch tool with extra metadata."""
    tavily_instance = _create_tavily_search()
    # 手动注册到全局注册表
    _extra_registry["tavily_search"] = ToolExtraMetadata(
        category="buildin",
        tags=["搜索"],
        display_name="Tavily 网页搜索",
    )
    # 添加到工具实例列表
    _all_tool_instances.append(tavily_instance)


# 模块加载时注册
if os.getenv("TAVILY_API_KEY"):
    try:
        _register_tavily_tool()
    except Exception as e:
        logger.warning(f"Failed to register TavilySearch tool: {e}")


class PresentArtifactsInput(BaseModel):
    """Expose artifact files to the frontend after the agent finishes."""

    filepaths: list[str] = Field(
        description=f"需要展示给用户的文件绝对路径列表，只允许位于 {VIRTUAL_PATH_OUTPUTS} 下，且不能是内部运行文件"
    )


def _normalize_presented_artifact_path(filepath: str, runtime: ToolRuntime) -> str:
    from yuxi.agents.backends.sandbox.paths import (
        VIRTUAL_PATH_PREFIX,
        ensure_thread_dirs,
        resolve_virtual_path,
        sandbox_outputs_dir,
    )

    outputs_virtual_prefix = f"{VIRTUAL_PATH_PREFIX}/outputs"
    runtime_context = runtime.context
    thread_id = getattr(runtime_context, "file_thread_id", None) or getattr(runtime_context, "thread_id", None)
    if not thread_id:
        raise ValueError("当前运行时缺少 thread_id")
    uid = getattr(runtime_context, "uid", None)
    if not uid:
        raise ValueError("当前运行时缺少 uid")

    ensure_thread_dirs(thread_id, str(uid))
    outputs_dir = sandbox_outputs_dir(thread_id).resolve()
    normalized_input = str(filepath or "").strip()
    if not normalized_input:
        raise ValueError("文件路径不能为空")

    stripped = normalized_input.lstrip("/")
    virtual_prefix = VIRTUAL_PATH_PREFIX.lstrip("/")
    if stripped == virtual_prefix or stripped.startswith(f"{virtual_prefix}/"):
        actual_path = resolve_virtual_path(thread_id, normalized_input, uid=str(uid))
    else:
        actual_path = Path(normalized_input).expanduser().resolve()

    if not actual_path.exists() or not actual_path.is_file():
        raise ValueError(f"文件不存在或不是普通文件: {normalized_input}")

    try:
        relative_path = actual_path.relative_to(outputs_dir)
    except ValueError as exc:
        raise ValueError(f"只允许展示 {outputs_virtual_prefix}/ 下的文件: {normalized_input}") from exc

    if relative_path.parts and relative_path.parts[0] in _PRESENT_ARTIFACTS_INTERNAL_DIR_NAMES:
        raise ValueError(f"不允许展示工具调用阶段文件: {outputs_virtual_prefix}/{relative_path.as_posix()}")

    return f"{outputs_virtual_prefix}/{relative_path.as_posix()}"


PRESENT_ARTIFACTS_DESCRIPTION = f"""
将已经生成好的结果文件展示给用户。

使用场景：
1. 你已经在 `{VIRTUAL_PATH_OUTPUTS}` 下写好了最终结果文件
2. 你希望前端在对话结束后显示这些结果文件卡片
3. 这些文件需要支持下载或预览

注意事项：
1. 只能传入 `{VIRTUAL_PATH_OUTPUTS}` 下的文件
2. 不要传入中间过程文件，只有真正需要给用户看的结果文件才调用
3. 不要传入工具调用阶段文件，例如：
   - `{VIRTUAL_PATH_OUTPUTS}/{LARGE_TOOL_RESULTS_DIR_NAME}`
   - `{VIRTUAL_PATH_OUTPUTS}/{CONVERSATION_HISTORY_DIR_NAME}`
4. 可以一次传多个文件
"""


@tool(
    category="buildin",
    tags=["文件", "交付物"],
    display_name="展示交付物",
    description=PRESENT_ARTIFACTS_DESCRIPTION,
    args_schema=PresentArtifactsInput,
)
def present_artifacts(
    filepaths: list[str],
    runtime: ToolRuntime,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """登记当前线程 outputs 目录下的交付物文件，使前端在对话结束后展示给用户。"""
    try:
        normalized_paths = [_normalize_presented_artifact_path(filepath, runtime) for filepath in filepaths]
    except ValueError as exc:
        return Command(update={"messages": [ToolMessage(content=f"Error: {exc}", tool_call_id=tool_call_id)]})

    return Command(
        update={
            "artifacts": normalized_paths,
            # 历史持久化只信任已成功执行的工具结果，避免模型仅生成调用参数就被误认为完成交付。
            "messages": [
                ToolMessage(
                    content="已将交付物展示给用户",
                    tool_call_id=tool_call_id,
                    additional_kwargs={"presented_artifacts": normalized_paths},
                )
            ],
        }
    )


ASK_USER_QUESTION_DESCRIPTION = """
在执行过程中，当你需要用户做决定或补充需求时，使用这个工具向用户提问。

适用场景：
1. 收集用户偏好或需求（例如风格、范围、优先级）
2. 澄清模糊指令（存在多种合理解释时）
3. 在实现过程中让用户选择方案方向
4. 在有明显权衡时让用户做取舍

使用规范：
1. questions 提供 1-5 个问题，每项包含：question、options、multi_select、allow_other
2. 每个问题的 options 提供 2-5 个有区分度的选项，每项包含 label 和 value
3. 若有推荐选项：把推荐项放在第一位，并在 label 末尾加 "(Recommended)"
4. 若需要多选：将该问题的 multi_select 设为 true
5. allow_other 通常保持 true，用户可通过 Other 输入自定义答案

注意事项：
1. 不要用这个工具询问“是否继续执行”“计划是否准备好”这类流程控制问题
2. 不要在信息已充分、无需用户决策时滥用该工具
3. 先基于现有上下文自行决策，只有关键不确定性时才提问

返回结果：
answer 为 object，格式为 {question_id: answer}。
其中 answer 可能是 string（单选）、list（多选）或 object（Other 文本）。
"""


def _decode_question_items(raw_questions: str) -> list[object]:
    """解析本地模型偶发输出的一个或多个连续 JSON 值。"""
    decoder = json.JSONDecoder()
    cursor = 0
    items: list[object] = []
    while cursor < len(raw_questions):
        while cursor < len(raw_questions) and (raw_questions[cursor].isspace() or raw_questions[cursor] == ","):
            cursor += 1
        if cursor >= len(raw_questions):
            break
        value, cursor = decoder.raw_decode(raw_questions, cursor)
        items.extend(value if isinstance(value, list) else [value])
    return items


class AskUserQuestionOption(BaseModel):
    """反问选项的模型调用契约。"""

    label: str = Field(min_length=1, description="展示给用户的选项文本")
    value: str = Field(min_length=1, description="用户选择该项时返回的值")

    @model_validator(mode="before")
    @classmethod
    def normalize_string_option(cls, value):
        # 对外 Schema 始终要求对象；这里只兼容本地模型偶发输出的简单字符串选项。
        if isinstance(value, str):
            return {"label": value, "value": value}
        return value


class AskUserQuestionItem(BaseModel):
    """单个反问问题的模型调用契约。"""

    question_id: str | None = Field(default=None, description="稳定的问题标识；省略时由工具生成")
    question: str = Field(min_length=1, description="展示给用户的问题")
    options: list[AskUserQuestionOption] = Field(
        min_length=2,
        max_length=5,
        description="仅包含本题允许用户选择的选项",
    )
    multi_select: bool = Field(default=False, description="是否允许多选")
    allow_other: bool = Field(default=True, description="是否允许用户输入给定选项之外的答案")
    operation: str | None = Field(default=None, description="可选的前端操作标识")

    @model_validator(mode="before")
    @classmethod
    def normalize_question_text(cls, value):
        if not isinstance(value, dict) or value.get("question"):
            return value
        # 部分本地模型会把题干写入 label/text；边界处规范化后仍按唯一 question 字段执行。
        question = value.get("label") or value.get("text")
        if not question:
            return value
        return {**value, "question": question}


class AskUserQuestionInput(BaseModel):
    """ask_user_question 的严格公开参数契约。"""

    questions: list[AskUserQuestionItem] = Field(
        min_length=1,
        max_length=5,
        description="需要用户回答的问题列表",
    )

    @field_validator("questions", mode="before")
    @classmethod
    def recover_stringified_questions(cls, value):
        # Schema 不公布字符串分支，避免继续诱导模型拼接 JSON；预校验只承担兼容既有本地模型的职责。
        if isinstance(value, str):
            return _decode_question_items(value)
        if isinstance(value, dict):
            return [value]
        return value


@tool(
    category="buildin",
    tags=["交互"],
    display_name="向用户提问",
    description=ASK_USER_QUESTION_DESCRIPTION,
    args_schema=AskUserQuestionInput,
)
def ask_user_question(
    questions: Annotated[
        list[dict] | str | None,
        "问题列表，每项格式 {question, options, multi_select, allow_other, question_id(optional)}",
    ] = None,
) -> dict:
    """向用户发起问题并等待回答。"""
    if isinstance(questions, str):
        try:
            questions = _decode_question_items(questions)
            logger.debug(f"Parsed string questions to list: {questions}")
        except Exception as e:
            logger.error(f"Failed to parse questions string: {e}, using None")
            questions = None

    normalized_questions = normalize_questions(questions or [])

    if not normalized_questions:
        raise ValueError("questions 至少需要包含一个有效问题")

    interrupt_payload = {
        "questions": normalized_questions,
        "source": "ask_user_question",
    }
    answer = interrupt(interrupt_payload)

    return {
        "questions": normalized_questions,
        "answer": answer,
    }
