import pytest
from types import SimpleNamespace

from yuxi.document_extraction import service as service_module
from yuxi.document_extraction.schemas import (
    category_result_for_classification_label,
    category_result_for_classification_labels,
    category_result_to_mapping,
)
from yuxi.document_extraction.service import (
    BusinessExtractionService,
    classify_incoming_document,
    document_input_token_limit,
)


@pytest.fixture(autouse=True)
def fixed_document_input_limit(monkeypatch):
    monkeypatch.setattr(service_module, "document_input_token_limit", lambda _model_spec: 20_000)


async def test_classify_incoming_document_declares_categories(monkeypatch):
    captured = {}

    class FakeModelJsonLLM:
        def __init__(self, model_spec):
            captured["model_spec"] = model_spec

        async def complete_json(self, prompt, schema):
            captured["prompt"] = prompt
            return {
                "classification": "安全管理类",
                "classification_confidence": 0.8,
                "classification_evidence": "按期完成安全整改",
                "summary": "摘要",
                "structured_result": {"requirements": ["按期整改"]},
            }

    monkeypatch.setattr(service_module, "ModelJsonLLM", FakeModelJsonLLM)

    result = await classify_incoming_document(
        filename="incoming.pdf",
        markdown="请按期完成安全整改。",
        metadata={"title": "安全整改通知"},
        model_spec="model-a",
    )

    assert result["classification"] == "安全管理类"
    prompt = captured["prompt"]
    assert "- 通报类：" in prompt
    assert "- 奖惩处置类：包含奖励、表彰、处罚" in prompt
    assert "- 通用类：" in prompt
    assert "只能填写“分类说明”中每行冒号前的名称" in prompt
    assert "按照来文的主要目的" in prompt
    assert "无法归入上述专业类别时填“通用类”" not in prompt
    assert "默认必须填 []" in prompt
    assert "confidence、evidence" in prompt
    assert "仅有关键词、背景说明、引用文件、顺带提及或判断不确定时不得增加" in prompt
    assert "structured_result" not in prompt
    assert "--- 文件名 ---" in prompt
    assert "--- 外部元数据 ---" in prompt
    assert "--- 来文正文 ---" in prompt
    assert "- 其他：" not in prompt
    assert "chat-iframe" not in prompt


def test_document_input_limit_uses_model_context_window(monkeypatch):
    monkeypatch.setattr(
        service_module.model_cache,
        "get_model_info",
        lambda _model_spec: SimpleNamespace(context_length=64_000),
    )

    assert document_input_token_limit("model-a") == 44_800


async def test_classify_incoming_document_keeps_complete_budgeted_markdown(monkeypatch):
    captured = {}

    class FakeModelJsonLLM:
        def __init__(self, model_spec):
            captured["model_spec"] = model_spec

        async def complete_json(self, prompt, schema):
            captured["prompt"] = prompt
            return {
                "classification": "通用类",
                "classification_confidence": 0.7,
                "classification_evidence": "123456",
                "summary": "摘要",
                "structured_result": {},
            }

    monkeypatch.setattr(service_module, "ModelJsonLLM", FakeModelJsonLLM)

    await classify_incoming_document(filename="incoming.pdf", markdown="123456", metadata={}, model_spec="model-a")

    prompt = captured["prompt"]
    assert "--- 来文正文 ---" in prompt
    assert "123456" in prompt


class FakeLLM:
    def __init__(self):
        self.prompts = []

    async def complete_json(self, prompt, schema):
        self.prompts.append(prompt)
        if "DocumentCategoryResult" in prompt:
            return {
                "risk_management": {"matched": True, "evidence": "存在安全风险"},
                "assessment": {"matched": False, "evidence": None},
                "reward_punishment": {"matched": False, "evidence": None},
                "regulation": {"matched": False, "evidence": None},
                "technical_standard": {"matched": False, "evidence": None},
                "safety_management": {"matched": False, "evidence": None},
                "staged_work": {"matched": False, "evidence": None},
                "long_term_requirement": {"matched": False, "evidence": None},
                "general": {"matched": True, "evidence": "模型误判为通用类"},
            }
        if schema.__name__ != "RiskItem":
            return {"items": []}
        return {
            "items": [
                {
                    "risk_name": "现场作业监护不到位",
                    "department": "运维部",
                    "profession": None,
                    "role": "现场负责人",
                    "period_type": "阶段性",
                    "requirement": "加强现场监护",
                    "source_quote": "现场作业监护不到位，应加强现场监护",
                }
            ]
        }


class FakeNoCategoryLLM(FakeLLM):
    async def complete_json(self, prompt, schema):
        self.prompts.append(prompt)
        if "DocumentCategoryResult" in prompt:
            raise AssertionError("should not classify when category_result is provided")
        return {
            "items": [
                {
                    "requirement": "执行路用客车检修运用管理办法",
                    "department": "车辆段",
                    "role": None,
                    "period_type": "长期性",
                    "source_quote": "路用客车检修运用管理办法",
                }
            ]
        }


class FakeDuplicateManagementLLM(FakeLLM):
    def __init__(self):
        super().__init__()
        self.extraction_calls = 0

    async def complete_json(self, prompt, schema):
        self.prompts.append(prompt)
        if "DocumentCategoryResult" in prompt:
            raise AssertionError("should not classify when category_result is provided")
        self.extraction_calls += 1
        return {
            "items": [
                {
                    "requirement": "建立问题整改台账",
                    "department": "各单位",
                    "role": None,
                    "period_type": "长期性",
                    "source_quote": f"第 {self.extraction_calls} 段要求建立问题整改台账",
                }
            ]
        }


class FakeGeneralLLM(FakeLLM):
    def __init__(self):
        super().__init__()
        self.extraction_calls = 0

    async def complete_json(self, prompt, schema):
        self.prompts.append(prompt)
        if "DocumentCategoryResult" in prompt:
            return {}
        self.extraction_calls += 1
        return {
            "items": [
                {
                    "content": "供应商申请变更结算账户",
                    "subject": "供应商",
                    "time": None,
                    "source_quote": f"第 {self.extraction_calls} 段：供应商申请变更结算账户",
                }
            ]
        }


class FakeDifferentPeriodManagementLLM(FakeDuplicateManagementLLM):
    async def complete_json(self, prompt, schema):
        self.prompts.append(prompt)
        if "DocumentCategoryResult" in prompt:
            raise AssertionError("should not classify when category_result is provided")
        self.extraction_calls += 1
        return {
            "items": [
                {
                    "requirement": "建立问题整改台账",
                    "department": "各单位",
                    "role": None,
                    "period_type": "阶段性" if self.extraction_calls == 1 else "长期性",
                    "source_quote": f"第 {self.extraction_calls} 段要求建立问题整改台账",
                }
            ]
        }


class FakePartialTaskLLM(FakeLLM):
    def __init__(self):
        super().__init__()
        self.extraction_calls = 0

    async def complete_json(self, prompt, schema):
        self.prompts.append(prompt)
        self.extraction_calls += 1
        return {
            "items": [
                {
                    "task_name": "完成专项检查",
                    "department": "安全部" if self.extraction_calls == 1 else None,
                    "role": None,
                    "deadline": "7月31日" if self.extraction_calls == 2 else None,
                    "period_type": "未明确",
                    "source_quote": f"第 {self.extraction_calls} 段关于完成专项检查的要求",
                }
            ]
        }


class FakeExtractionRepository:
    def __init__(self):
        self.created = []
        self.replaced = None
        self.updated = []

    async def create_run(self, data):
        self.created.append(data)

    async def replace_result(self, **kwargs):
        self.replaced = kwargs

    async def update_run(self, run_id, data):
        self.updated.append((run_id, data))


def test_short_markdown_limit_uses_token_count(monkeypatch):
    monkeypatch.setattr(service_module, "SHORT_MARKDOWN_EXTRACTION_TOKEN_LIMIT", 2)

    def fail_chunk_markdown(*_args, **_kwargs):
        raise AssertionError("两个 token 的短文档不应进入分块")

    monkeypatch.setattr(service_module, "chunk_markdown", fail_chunk_markdown)

    segments = BusinessExtractionService._markdown_segments(
        markdown="alpha beta",
        document_key="doc",
        filename="doc.md",
        processing_params={},
    )

    assert segments == [{"chunk_id": None, "content": "alpha beta", "chunk_index": 0}]


def test_long_markdown_uses_large_overlapping_chunks(monkeypatch):
    captured = {}

    def fake_chunk_markdown(_text, _document_key, _filename, params):
        captured.update(params)
        return [{"chunk_id": "doc_chunk_0", "content": "正文", "chunk_index": 0}]

    monkeypatch.setattr(service_module, "count_tokens", lambda _text: 10_000)
    monkeypatch.setattr(service_module, "chunk_markdown", fake_chunk_markdown)

    segments = BusinessExtractionService._markdown_segments(
        markdown="超长正文",
        document_key="doc",
        filename="doc.md",
        processing_params={},
        token_limit=6_000,
    )

    assert captured["chunk_parser_config"] == {"chunk_token_num": 4_000, "overlapped_percent": 10}
    assert segments[0]["chunk_id"] == "doc_chunk_0"


async def test_extract_chunks_uses_known_risk_category():
    service = BusinessExtractionService(llm=FakeLLM())

    result = await service.extract_chunks(
        document_scope="incoming",
        incoming_id="inc_1",
        kb_id="kb_1",
        file_id="file_1",
        category_result=category_result_for_classification_label("风险管理类"),
        chunks=[
            {
                "chunk_id": "file_1_chunk_0",
                "content": "关于安全风险的通报。现场作业监护不到位，应加强现场监护。",
                "chunk_index": 0,
            }
        ],
    )

    assert result.categories.risk_management.matched is True
    assert result.categories.general.matched is False
    assert result.schema_ids == ["risk_item", "task_item", "management_requirement_item"]
    assert result.items[0].item_type == "risk_item"
    assert result.items[0].chunk_id == "file_1_chunk_0"
    assert result.items[0].data["risk_name"] == "现场作业监护不到位"


async def test_incoming_document_extraction_keeps_attachment_evidence():
    repository = FakeExtractionRepository()
    llm = FakeLLM()
    service = BusinessExtractionService(llm=llm, extraction_repo=repository)

    result = await service.run_incoming_document_extraction(
        incoming_id="inc_1",
        classifications=["风险管理类"],
        model_spec="model-a",
        files=[
            {
                "incoming_file_id": "incf_main",
                "source_file_id": "main",
                "filename": "主文件.pdf",
                "markdown_file": "minio://parsed/main.md",
                "markdown": "现场作业监护不到位，应加强现场监护。",
            },
            {
                "incoming_file_id": "incf_attachment",
                "source_file_id": "attachment",
                "filename": "附件.xlsx",
                "markdown_file": "minio://parsed/attachment.md",
                "markdown": "现场作业监护不到位，应加强现场监护。",
            },
        ],
    )

    assert result["item_count"] == 1
    item = next(item for item in repository.replaced["items"] if item["item_type"] == "risk_item")
    assert {evidence["file_name"] for evidence in item["evidence"]} == {"主文件.pdf", "附件.xlsx"}
    assert all("## 文件：主文件.pdf" in prompt and "## 文件：附件.xlsx" in prompt for prompt in llm.prompts)


async def test_incoming_document_extraction_fails_when_any_schema_chunk_fails():
    class FailingLLM:
        async def complete_json(self, _prompt, _schema):
            raise RuntimeError("model unavailable")

    repository = FakeExtractionRepository()
    service = BusinessExtractionService(llm=FailingLLM(), extraction_repo=repository)

    with pytest.raises(RuntimeError, match="Business extraction incomplete"):
        await service.run_incoming_document_extraction(
            incoming_id="inc_1",
            classifications=["规章制度类"],
            model_spec="model-a",
            files=[
                {
                    "incoming_file_id": "incf_main",
                    "source_file_id": "main",
                    "filename": "主文件.pdf",
                    "markdown_file": "minio://parsed/main.md",
                    "markdown": "各单位应建立问题整改台账。",
                }
            ],
        )

    assert repository.replaced is None
    assert repository.updated[-1][1]["status"] == "failed"


async def test_long_incoming_extraction_keeps_each_chunk_evidence(monkeypatch):
    repository = FakeExtractionRepository()
    service = BusinessExtractionService(llm=FakeDuplicateManagementLLM(), extraction_repo=repository)
    monkeypatch.setattr(service_module, "document_input_token_limit", lambda _model_spec: 4)
    monkeypatch.setattr(service_module, "count_tokens", lambda _text: 10)
    monkeypatch.setattr(
        service_module,
        "chunk_markdown",
        lambda *_args: [
            {"chunk_id": "chunk_1", "content": "第 1 段要求建立问题整改台账", "chunk_index": 0},
            {"chunk_id": "chunk_2", "content": "第 2 段要求建立问题整改台账", "chunk_index": 1},
        ],
    )

    await service.run_incoming_document_extraction(
        incoming_id="inc_1",
        classifications=["规章制度类"],
        model_spec="model-a",
        files=[
            {
                "incoming_file_id": "incf_main",
                "source_file_id": "main",
                "filename": "主文件.pdf",
                "markdown_file": "minio://parsed/main.md",
                "markdown": "第 1 段要求建立问题整改台账\n第 2 段要求建立问题整改台账",
            }
        ],
    )

    item = repository.replaced["items"][0]
    assert {evidence["source_location"] for evidence in item["evidence"]} == {"分块 chunk_1", "分块 chunk_2"}
    assert {evidence["quote"] for evidence in item["evidence"]} == {
        "第 1 段要求建立问题整改台账",
        "第 2 段要求建立问题整改台账",
    }


async def test_long_incoming_extraction_rejects_hallucinated_quote(monkeypatch):
    class HallucinatingLLM:
        async def complete_json(self, _prompt, _schema):
            return {
                "items": [
                    {
                        "requirement": "建立问题整改台账",
                        "department": None,
                        "role": None,
                        "period_type": "长期性",
                        "source_quote": "原文中不存在的依据",
                    }
                ]
            }

    repository = FakeExtractionRepository()
    service = BusinessExtractionService(llm=HallucinatingLLM(), extraction_repo=repository)
    monkeypatch.setattr(service_module, "document_input_token_limit", lambda _model_spec: 4)
    monkeypatch.setattr(service_module, "count_tokens", lambda _text: 10)
    monkeypatch.setattr(
        service_module,
        "chunk_markdown",
        lambda *_args: [{"chunk_id": "chunk_1", "content": "各单位应建立问题整改台账", "chunk_index": 0}],
    )

    with pytest.raises(RuntimeError, match="Business extraction incomplete"):
        await service.run_incoming_document_extraction(
            incoming_id="inc_1",
            classifications=["规章制度类"],
            model_spec="model-a",
            files=[
                {
                    "incoming_file_id": "incf_main",
                    "source_file_id": "main",
                    "filename": "主文件.pdf",
                    "markdown_file": "minio://parsed/main.md",
                    "markdown": "各单位应建立问题整改台账",
                }
            ],
        )

    assert repository.replaced is None
    assert repository.updated[-1][1]["status"] == "failed"


def test_multiple_extraction_classifications_keep_each_schema_and_drop_general_fallback():
    result = category_result_for_classification_labels(["安全管理类", "阶段性工作类", "通用类"])

    assert category_result_to_mapping(result)["safety_management"] is True
    assert category_result_to_mapping(result)["staged_work"] is True
    assert category_result_to_mapping(result)["general"] is False


async def test_extract_chunks_uses_known_general_category_and_merges_duplicates():
    service = BusinessExtractionService(llm=FakeGeneralLLM())

    result = await service.extract_chunks(
        document_scope="incoming",
        incoming_id="inc_general",
        category_result=category_result_for_classification_label("通用类"),
        chunks=[
            {"chunk_id": "chunk_1", "content": "供应商申请变更结算账户。", "chunk_index": 0},
            {"chunk_id": "chunk_2", "content": "供应商申请变更结算账户。", "chunk_index": 1},
        ],
    )

    assert result.categories.general.matched is True
    assert result.schema_ids == ["general_item"]
    assert len(result.items) == 1
    assert result.items[0].item_type == "general_item"
    assert "第 1 段" in result.items[0].source_quote
    assert "第 2 段" in result.items[0].source_quote


async def test_extract_chunks_uses_known_category_without_reclassifying():
    service = BusinessExtractionService(llm=FakeNoCategoryLLM())

    result = await service.extract_chunks(
        document_scope="incoming",
        incoming_id="inc_1",
        chunks=[{"chunk_id": None, "content": "路用客车检修运用管理办法", "chunk_index": 0}],
        category_result=category_result_for_classification_label("规章制度类"),
    )

    assert result.schema_ids == ["management_requirement_item"]
    assert result.items[0].item_type == "management_requirement_item"


async def test_extract_chunks_merges_obvious_duplicate_items_from_chunks():
    service = BusinessExtractionService(llm=FakeDuplicateManagementLLM())

    result = await service.extract_chunks(
        document_scope="incoming",
        incoming_id="inc_1",
        chunks=[
            {"chunk_id": "chunk_1", "content": "各单位应建立问题整改台账。", "chunk_index": 0},
            {"chunk_id": "chunk_2", "content": "各单位持续建立问题整改台账。", "chunk_index": 1},
        ],
        category_result=category_result_for_classification_label("规章制度类"),
    )

    assert len(result.items) == 1
    assert result.items[0].data["requirement"] == "建立问题整改台账"
    assert "第 1 段要求建立问题整改台账" in result.items[0].source_quote
    assert "第 2 段要求建立问题整改台账" in result.items[0].source_quote


async def test_extract_chunks_keeps_items_when_any_business_field_differs():
    service = BusinessExtractionService(llm=FakeDifferentPeriodManagementLLM())

    result = await service.extract_chunks(
        document_scope="incoming",
        incoming_id="inc_1",
        chunks=[
            {"chunk_id": "chunk_1", "content": "阶段性建立问题整改台账。", "chunk_index": 0},
            {"chunk_id": "chunk_2", "content": "长期建立问题整改台账。", "chunk_index": 1},
        ],
        category_result=category_result_for_classification_label("规章制度类"),
    )

    assert len(result.items) == 2
    assert {item.data["period_type"] for item in result.items} == {"阶段性", "长期性"}


async def test_extract_chunks_merges_non_conflicting_partial_items():
    service = BusinessExtractionService(llm=FakePartialTaskLLM())

    result = await service.extract_chunks(
        document_scope="incoming",
        incoming_id="inc_1",
        chunks=[
            {"chunk_id": "chunk_1", "content": "安全部负责完成专项检查。", "chunk_index": 0},
            {"chunk_id": "chunk_2", "content": "完成专项检查的截止日期为7月31日。", "chunk_index": 1},
        ],
        category_result=category_result_for_classification_label("阶段性工作类"),
    )

    assert len(result.items) == 1
    assert result.items[0].data["department"] == "安全部"
    assert result.items[0].data["deadline"] == "7月31日"


class FakeExtractionRepo:
    def __init__(self, reusable=None):
        self.reusable = reusable
        self.runs = []
        self.results = []
        self.updated = []

    async def get_success_by_document_markdown_model(
        self, *, document_scope, incoming_id, file_id, markdown_file, model_spec
    ):
        return self.reusable

    async def create_run(self, data):
        self.runs.append(data)

    async def replace_result(self, *, run_id, result_data, items):
        self.results.append({"run_id": run_id, "result_data": result_data, "items": items})

    async def update_run(self, run_id, data):
        self.updated.append({"run_id": run_id, "data": data})


async def test_run_markdown_extraction_writes_items_without_chunk_id():
    repo = FakeExtractionRepo()
    service = BusinessExtractionService(llm=FakeLLM(), extraction_repo=repo)

    result = await service.run_markdown_extraction(
        document_scope="incoming",
        incoming_id="inc_1",
        kb_id="kb_1",
        file_id="file_1",
        markdown_file="minio://knowledgebases/kb_1/parsed/file_1.md",
        filename="来文.docx",
        processing_params={"classification": "风险管理类"},
        model_spec="model-a",
        markdown_reader=lambda _: "# 安全风险\n现场作业监护不到位，应加强现场监护。",
    )

    assert result["item_count"] > 0
    assert repo.runs[0]["document_scope"] == "incoming"
    assert repo.runs[0]["incoming_id"] == "inc_1"
    assert repo.runs[0]["run_metadata"]["markdown_file"] == "minio://knowledgebases/kb_1/parsed/file_1.md"
    assert {item["chunk_id"] for item in repo.results[0]["items"]} == {None}


async def test_run_markdown_extraction_reuses_same_markdown_and_model():
    repo = FakeExtractionRepo(
        reusable={
            "run_id": "ber_old",
            "schema_ids": ["risk_item", "task_item", "management_requirement_item"],
            "items": [],
        }
    )
    service = BusinessExtractionService(llm=FakeLLM(), extraction_repo=repo)

    result = await service.run_markdown_extraction(
        document_scope="incoming",
        incoming_id="inc_1",
        kb_id="kb_1",
        file_id="file_1",
        markdown_file="minio://knowledgebases/kb_1/parsed/file_1.md",
        model_spec="model-a",
        processing_params={"classification": "风险管理类"},
        markdown_reader=lambda _: "# ignored",
    )

    assert result["run_id"] == "ber_old"
    assert result["reused"] is True
    assert repo.runs == []


async def test_run_markdown_extraction_does_not_reuse_empty_result_when_classification_has_schema():
    repo = FakeExtractionRepo(reusable={"run_id": "ber_old", "schema_ids": [], "items": []})
    service = BusinessExtractionService(llm=FakeNoCategoryLLM(), extraction_repo=repo)

    result = await service.run_markdown_extraction(
        document_scope="incoming",
        incoming_id="inc_1",
        markdown_file="minio://knowledgebases/incoming/inc_1/parsed.md",
        model_spec="model-a",
        processing_params={"classification": "规章制度类"},
        markdown_reader=lambda _: "# 路用客车检修运用管理办法",
    )

    assert result["reused"] is False
    assert repo.runs
    assert repo.results[0]["result_data"]["schema_ids"] == ["management_requirement_item"]


async def test_run_markdown_extraction_requires_known_classification():
    service = BusinessExtractionService(llm=FakeLLM())

    with pytest.raises(ValueError, match="classification"):
        await service.run_markdown_extraction(
            document_scope="incoming",
            incoming_id="inc_1",
            markdown_file="minio://knowledgebases/incoming/inc_1/parsed.md",
            model_spec="model-a",
        )
