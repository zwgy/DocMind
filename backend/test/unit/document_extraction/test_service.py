from yuxi.document_extraction.schemas import category_result_for_classification_label
from yuxi.document_extraction.service import BusinessExtractionService


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
            }
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


async def test_extract_file_runs_category_first_then_matching_schemas():
    service = BusinessExtractionService(llm=FakeLLM())

    result = await service.extract_chunks(
        document_scope="incoming",
        incoming_id="inc_1",
        kb_id="kb_1",
        file_id="file_1",
        chunks=[
            {
                "chunk_id": "file_1_chunk_0",
                "content": "关于安全风险的通报。现场作业监护不到位，应加强现场监护。",
                "chunk_index": 0,
            }
        ],
    )

    assert result.categories.risk_management.matched is True
    assert result.schema_ids == ["risk_item", "task_item", "management_requirement_item"]
    assert result.items[0].item_type == "risk_item"
    assert result.items[0].chunk_id == "file_1_chunk_0"
    assert result.items[0].data["risk_name"] == "现场作业监护不到位"


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


class FakeExtractionRepo:
    def __init__(self, reusable=None):
        self.reusable = reusable
        self.runs = []
        self.results = []
        self.updated = []

    async def get_success_by_document_markdown_model(self, *, document_scope, incoming_id, file_id, markdown_file, model_spec):
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
        processing_params={},
        model_spec="model-a",
        markdown_reader=lambda _: "# 安全风险\n现场作业监护不到位，应加强现场监护。",
    )

    assert result["item_count"] > 0
    assert repo.runs[0]["document_scope"] == "incoming"
    assert repo.runs[0]["incoming_id"] == "inc_1"
    assert repo.runs[0]["run_metadata"]["markdown_file"] == "minio://knowledgebases/kb_1/parsed/file_1.md"
    assert {item["chunk_id"] for item in repo.results[0]["items"]} == {None}


async def test_run_markdown_extraction_reuses_same_markdown_and_model():
    repo = FakeExtractionRepo(reusable={"run_id": "ber_old", "items": []})
    service = BusinessExtractionService(llm=FakeLLM(), extraction_repo=repo)

    result = await service.run_markdown_extraction(
        document_scope="incoming",
        incoming_id="inc_1",
        kb_id="kb_1",
        file_id="file_1",
        markdown_file="minio://knowledgebases/kb_1/parsed/file_1.md",
        model_spec="model-a",
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
