from yuxi.document_extraction.prompts import build_extraction_prompt
from yuxi.document_extraction.schemas import ManagementRequirementItem


def test_extraction_prompt_defines_item_granularity():
    prompt = build_extraction_prompt(ManagementRequirementItem, "各单位应建立台账，并每月开展检查。")

    assert "关键结论、重要责任、核心动作" in prompt
    assert "不要穷举每句话、每一款或每个执行步骤" in prompt
    assert "同一主题、目标或责任语境下的连续内容应合并" in prompt
    assert "用户需要关注的关键事项" in prompt
    assert "主体、数值、日期、义务和结论必须全部由 source_quote 直接支持" in prompt
    assert "不拼接分散信息" in prompt
    assert "第X章" not in prompt
    assert "第X条" not in prompt
