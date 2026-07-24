from types import SimpleNamespace

from yuxi.agents.buildin.chatbot.prompt import build_prompt_with_context


def test_chatbot_prompt_uses_general_tool_and_source_rules():
    prompt = build_prompt_with_context(SimpleNamespace(system_prompt=""))

    assert "不要把英文内部计划或逐步自言自语输出为正文" in prompt
    assert "引用来源内容时，明确区分逐字引用、摘要和推断" in prompt
    assert "用户只要求查询、核验或读取内容时" in prompt
    assert "`grep` 是字面搜索" not in prompt
    assert "不得用 `execute`、`glob`、`ls`" not in prompt
    assert "不得把序号自行解释成“第几款”" not in prompt
