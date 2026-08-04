from langchain.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from yuxi import config as sys_config
from yuxi.models.providers.cache import model_cache
from yuxi.utils import get_docker_safe_url
from yuxi.utils.logging_config import logger


def _normalize_tool_call_chunks(message) -> None:
    """把工具调用续片里空字符串的 name/id 归一化为 None。

    LangGraph v3 流式累积对 tool_call 字段是“后值覆盖”：部分 OpenAI 兼容提供商
    （siliconflow、阿里云百炼等）在续片里把 name/id 下发为空字符串 ""，会覆盖首片
    的真实值（siliconflow 丢 name、百炼丢 id），导致工具结果无法按 tool_call_id
    关联、工具状态停留在“进行中”。OpenAI 官方在续片里发 None 不会触发覆盖，这里
    把空串归一化为 None 对齐该行为。待上游修复 v3 协议后可移除。
    """
    for chunk in message.tool_call_chunks:
        if chunk.get("name") == "":
            chunk["name"] = None
        if chunk.get("id") == "":
            chunk["id"] = None


class _ToolCallChunkFixChatOpenAI(ChatOpenAI):
    """归一化流式 tool_call 续片中的空串 name/id，规避 v3 流式累积缺陷。"""

    async def _astream(self, *args, **kwargs):
        async for chunk in super()._astream(*args, **kwargs):
            _normalize_tool_call_chunks(chunk.message)
            yield chunk

    def _stream(self, *args, **kwargs):
        for chunk in super()._stream(*args, **kwargs):
            _normalize_tool_call_chunks(chunk.message)
            yield chunk


def _copy_ollama_generation_metadata(generation) -> None:
    """将 Ollama 结束块的完成原因同步到消息元数据。

    ``langchain-ollama`` 已从原生响应读取 ``done_reason``，但当前版本只把它
    放在 ``generation_info``。Yuxi 的 TokenUsageMiddleware 按 LangChain 统一
    约定读取 ``AIMessage.response_metadata``，不做这一步就无法区分正常完成和
    ``length`` 截断，进而不能安全触发续写。只复制结束块已经提供的元数据，不猜测
    或重写 Provider 的完成原因。
    """
    generation_info = getattr(generation, "generation_info", None)
    message = getattr(generation, "message", None)
    if not isinstance(generation_info, dict) or message is None:
        return
    response_metadata = getattr(message, "response_metadata", None)
    if isinstance(response_metadata, dict):
        response_metadata.update(generation_info)


def resolve_chat_model_spec(model_spec: str | None, *, fallback: str | None = None) -> str:
    """解析空模型配置，不吞掉已经配置但无效的模型值。

    这里仅处理模型为空时的优先级：请求或配置值、调用方 fallback、系统默认模型；
    具体模型是否存在、是否为聊天模型仍由 model_cache 校验。
    """
    for candidate in (model_spec, fallback, sys_config.default_model):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    raise ValueError("model spec 不能为空")


def load_chat_model(fully_specified_name: str | None, **kwargs) -> BaseChatModel:
    fully_specified_name = resolve_chat_model_spec(fully_specified_name)

    info = model_cache.get_model_info(fully_specified_name)
    if not info:
        available_specs = model_cache.get_all_specs("chat")
        available_ids = [item.spec for item in available_specs[:10]]
        raise ValueError(
            f"Unknown model spec: '{fully_specified_name}'. "
            f"Available chat models ({len(available_specs)}): {available_ids}"
        )

    if info.model_type != "chat":
        raise ValueError(f"Model {fully_specified_name} is not a chat model (type={info.model_type})")

    api_key = info.api_key
    base_url = get_docker_safe_url(info.base_url)
    try:
        context_length = int(info.context_length) if info.context_length else None
    except (TypeError, ValueError):
        context_length = None
    try:
        min_output_reserve_tokens = (
            int(info.min_output_reserve_tokens) if info.min_output_reserve_tokens else None
        )
    except (TypeError, ValueError):
        min_output_reserve_tokens = None
    try:
        context_safety_tokens = int(info.context_safety_tokens) if info.context_safety_tokens else None
    except (TypeError, ValueError):
        context_safety_tokens = None
    # 模型级参数用于承载供应商支持的运行选项（如 reasoning_effort），避免按模型名称硬编码。
    # 调用方显式参数优先，便于摘要、测试等特定调用按需覆盖模型默认值。
    model_kwargs = dict(info.extra.get("parameters") or {})
    model_kwargs.update(kwargs)
    logger.debug(f"Loading model {fully_specified_name} with provider_type={info.provider_type}")

    if info.provider_type == "anthropic":
        from langchain_anthropic import ChatAnthropic

        model = ChatAnthropic(
            model=info.model_id,
            api_key=SecretStr(api_key),
            base_url=base_url,
            **model_kwargs,
        )
    elif info.provider_type == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        model = ChatGoogleGenerativeAI(
            model=info.model_id,
            google_api_key=SecretStr(api_key),
            **model_kwargs,
        )
    elif info.provider_type == "ollama":
        from langchain_ollama import ChatOllama

        class _CompletionMetadataChatOllama(ChatOllama):
            """补齐当前 langchain-ollama 未透传的原生完成元数据。"""

            def _chat_params(self, messages, stop=None, **kwargs):
                # ChatOllama 构造参数可直接使用 num_predict，但单次调用会把未知顶层
                # 参数原样传给 ollama.AsyncClient.chat。恢复中间件必须动态提高额度，
                # 因而在这里改写为 Ollama 原生 options，避免污染其他 Provider 的通用
                # model_settings 协议。
                num_predict = kwargs.pop("num_predict", None)
                if num_predict is not None:
                    options = dict(kwargs.pop("options", {}) or {})
                    options["num_predict"] = num_predict
                    kwargs["options"] = options
                return super()._chat_params(messages, stop=stop, **kwargs)

            def _generate(self, *args, **kwargs):
                result = super()._generate(*args, **kwargs)
                for generation in result.generations:
                    _copy_ollama_generation_metadata(generation)
                return result

            async def _agenerate(self, *args, **kwargs):
                result = await super()._agenerate(*args, **kwargs)
                for generation in result.generations:
                    _copy_ollama_generation_metadata(generation)
                return result

            def _stream(self, *args, **kwargs):
                for generation in super()._stream(*args, **kwargs):
                    _copy_ollama_generation_metadata(generation)
                    yield generation

            async def _astream(self, *args, **kwargs):
                async for generation in super()._astream(*args, **kwargs):
                    _copy_ollama_generation_metadata(generation)
                    yield generation

        # Ollama 原生 API 的输出上限字段是 num_predict。只在显式 Ollama Provider
        # 内映射，避免把厂商私有参数泄漏给标准 OpenAI 兼容端点。
        if "max_completion_tokens" in model_kwargs and "num_predict" not in model_kwargs:
            model_kwargs["num_predict"] = model_kwargs.pop("max_completion_tokens")
        if "max_tokens" in model_kwargs and "num_predict" not in model_kwargs:
            model_kwargs["num_predict"] = model_kwargs.pop("max_tokens")
        model = _CompletionMetadataChatOllama(
            model=info.model_id,
            base_url=base_url.removesuffix("/v1"),
            **model_kwargs,
        )
    else:
        model = _ToolCallChunkFixChatOpenAI(
            model=info.model_id,
            api_key=SecretStr(api_key),
            base_url=base_url,
            stream_usage=True,
            **model_kwargs,
        )

    profile = dict(model.profile or {})
    if context_length and context_length > 0:
        # 本地配置描述的是服务实例实际可接收的完整窗口，不能按摘要比例改写为输入窗口。
        profile["max_input_tokens"] = context_length
    # 预留只决定何时收缩输入；普通调用不把它转成模型输出上限，避免复杂任务被截断。
    profile["min_output_reserve_tokens"] = min_output_reserve_tokens or sys_config.min_output_reserve_tokens
    if context_safety_tokens and context_safety_tokens > 0:
        profile["context_safety_tokens"] = context_safety_tokens
    if profile:
        model.profile = profile
    return model
