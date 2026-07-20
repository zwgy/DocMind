from yuxi.document_extraction.prompts import build_attachment_summary_prompt, build_extraction_prompt
from yuxi.document_extraction.schemas import ManagementRequirementItem


def test_extraction_prompt_defines_item_granularity():
    prompt = build_extraction_prompt(ManagementRequirementItem, "各单位应建立台账，并每月开展检查。")

    assert "关键结论、重要责任、核心动作" in prompt
    assert "不要穷举每句话、每一款或每个执行步骤" in prompt
    assert "同一主题、目标或责任语境下的连续内容应合并" in prompt
    assert "用户需要关注的关键事项" in prompt
    assert "不得新增原文未出现的主体、数字、期限、条件、责任或结论" in prompt
    assert "不要求逐字一致" in prompt
    assert "不要把多个无关位置的信息拼成原文没有表达过的结论" in prompt
    assert "第X章" not in prompt
    assert "第X条" not in prompt


def test_attachment_summary_prompt_does_not_request_classification_or_business_items():
    prompt = build_attachment_summary_prompt(filename="风险清单.xlsx", markdown="列出检查风险和责任单位。")

    assert "来文分类" in prompt
    assert "不要输出管理要求、任务、风险等业务条目" in prompt
    assert "classification" not in prompt
