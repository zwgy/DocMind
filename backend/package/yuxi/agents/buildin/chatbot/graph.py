from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware import TodoListMiddleware

from yuxi.agents import BaseAgent, load_chat_model, resolve_chat_model_spec
from yuxi.agents.backends import create_agent_filesystem_middleware
from yuxi.agents.context import (
    DEFAULT_YUXI_SUMMARY_PROMPT,
    prepare_agent_runtime_context,
)
from yuxi.agents.middlewares import (
    TokenUsageMiddleware,
    create_model_retry_middleware,
    create_context_compaction_middleware,
    save_attachments_to_fs,
)
from yuxi.agents.middlewares.skills import SkillsMiddleware
from yuxi.agents.middlewares.subagent_task import create_subagent_task_middleware
from yuxi.agents.middlewares.token_usage import resolve_tool_token_limit
from yuxi.agents.toolkits.service import resolve_configured_runtime_tools

from .context import ChatBotContext
from .prompt import TODO_MID_PROMPT, build_prompt_with_context
from .state import ChatBotState


async def _build_middlewares(context, *, model=None):
    """构建中间件列表"""
    summary_prompt = getattr(context, "summary_prompt", None) or DEFAULT_YUXI_SUMMARY_PROMPT
    if model is None:
        model_spec = resolve_chat_model_spec(context.model)
        model = load_chat_model(fully_specified_name=model_spec)
    tool_token_limit = resolve_tool_token_limit(context, model=model)
    context_compaction_middleware = create_context_compaction_middleware(
        model=model,
        summary_prompt=summary_prompt,
    )

    middlewares = [
        create_agent_filesystem_middleware(
            tool_token_limit,
            context=context,
        ),
        save_attachments_to_fs,
        SkillsMiddleware(),
        TodoListMiddleware(system_prompt=TODO_MID_PROMPT),
        PatchToolCallsMiddleware(),
    ]
    subagent_middleware = await create_subagent_task_middleware(context)
    if subagent_middleware:
        middlewares.append(subagent_middleware)
    middlewares.extend(
        [
            context_compaction_middleware,
            TokenUsageMiddleware(),
            create_model_retry_middleware(max_retries=getattr(context, "model_retry_times", 2)),
        ]
    )
    return middlewares


class ChatbotAgent(BaseAgent):
    name = "智能助手"
    description = "基础的对话机器人，可以回答问题，可在配置中启用需要的工具。"
    capabilities = ["file_upload", "files"]  # 支持文件上传功能
    context_schema = ChatBotContext

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    async def get_graph(self, context=None, **kwargs):

        context = await prepare_agent_runtime_context(
            context or self.context_schema(),
            context_schema=self.context_schema,
        )

        # 使用 create_agent 创建智能体
        model_spec = resolve_chat_model_spec(context.model)
        model = load_chat_model(fully_specified_name=model_spec)
        graph = create_agent(
            model=model,
            tools=await resolve_configured_runtime_tools(context),
            system_prompt=build_prompt_with_context(context),
            middleware=await _build_middlewares(context, model=model),
            state_schema=ChatBotState,
            checkpointer=await self._get_checkpointer(),
        )

        return graph


def main():
    pass


if __name__ == "__main__":
    main()
    # asyncio.run(main())
