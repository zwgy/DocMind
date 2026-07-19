from yuxi.document_extraction.evidence import find_source_quote


def test_align_source_quote_returns_original_text_after_layout_normalization():
    source = "应建立问题\n整改台账，并按月更新。"

    result = find_source_quote("应建立问题整改台账", source)

    assert result == "应建立问题\n整改台账"


def test_align_source_quote_ignores_pdf_page_number_inside_sentence():
    source = "请认真贯彻执\n\n— 2 —\n行。"

    result = find_source_quote("请认真贯彻执行。", source)

    assert result == source


def test_find_source_quote_rejects_paraphrased_evidence():
    source = "中国铁路上海局集团有限公司关于重新修订客车检修运用管理办法。"

    result = find_source_quote("中国铁路上海局集团有限公司关于重新印发客车检修规程", source)

    assert result is None


def test_find_source_quote_rejects_unrelated_claim():
    source = "各有关单位要建立健全问题整改工作台账。"

    result = find_source_quote("文件要求立即停止全部客运业务", source)

    assert result is None
