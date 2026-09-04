from types import SimpleNamespace

from yuxi.agents.buildin.chatbot.prompt import TODO_MID_PROMPT, build_prompt_with_context


def test_chatbot_prompt_uses_general_tool_and_source_rules():
    prompt = build_prompt_with_context(SimpleNamespace(system_prompt=""))

    assert "不要把英文内部计划或逐步自言自语输出为正文" in prompt
    assert "引用来源内容时，明确区分逐字引用、摘要和推断" in prompt
    assert "用户只要求查询、核验或读取内容时" in prompt
    assert "优先使用当前上下文中已提供的信息" in prompt
    assert "明确说明成功后会自动交付" in prompt
    assert "不要重复调用 `present_artifacts`" in prompt
    assert "交付失败时不得声称已经完成" in prompt
    assert prompt.endswith("交付失败时不得声称已经完成。")
    assert prompt.count("<| 文件交付:强制 |>") == 1
    assert "<| 文件任务最终检查:强制 |>" not in prompt
    assert "保留该名称原文" in prompt
    assert "禁止翻译、改写或转写" in prompt
    assert "`write_file` 或 `edit_file` 成功只表示文件已写入，不表示已经交付" in prompt
    assert "下一步必须调用 `present_artifacts`" in prompt
    assert prompt.count("不表示已经交付") == 1
    assert prompt.count("下一步必须调用 `present_artifacts`") == 1
    assert "`grep` 是字面搜索" not in prompt
    assert "不得用 `execute`、`glob`、`ls`" not in prompt
    assert "不得把序号自行解释成“第几款”" not in prompt


def test_todo_prompt_distinguishes_complex_and_single_step_tasks():
    assert "多个相互依赖的步骤" in TODO_MID_PROMPT
    assert "根据中间结果调整后续操作" in TODO_MID_PROMPT
    assert "单步问答、单次查询和已有内容的直接导出不要创建待办" in TODO_MID_PROMPT
    assert "task 只用于可独立完成且有明确成果的子任务" in TODO_MID_PROMPT
    assert "或需要生成交付物" not in TODO_MID_PROMPT
    assert "生成交付物前还需" not in TODO_MID_PROMPT
    assert "失败时根据结果调整后续计划" in TODO_MID_PROMPT
    assert "结果经过验证后才能结束" in TODO_MID_PROMPT
