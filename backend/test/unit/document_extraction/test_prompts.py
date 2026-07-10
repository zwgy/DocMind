from yuxi.document_extraction.prompts import build_extraction_prompt
from yuxi.document_extraction.schemas import ManagementRequirementItem


def test_extraction_prompt_defines_item_granularity():
    prompt = build_extraction_prompt(ManagementRequirementItem, "各单位应建立台账，并每月开展检查。")

    assert "每个 item 表示一个独立业务事项" in prompt
    assert "同一事项的背景、依据、责任对象和要求应合并到同一个 item" in prompt
    assert "多个并列且可独立执行或确认的事项才拆成多个 items" in prompt
