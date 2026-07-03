from yuxi.knowledge.extraction.service import BusinessExtractionService


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


async def test_extract_file_runs_category_first_then_matching_schemas():
    service = BusinessExtractionService(llm=FakeLLM())

    result = await service.extract_chunks(
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


class FakeExtractionRepo:
    def __init__(self, reusable=None):
        self.reusable = reusable
        self.runs = []
        self.results = []
        self.updated = []

    async def get_success_by_file_markdown_model(self, *, file_id, markdown_file, model_spec):
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
        kb_id="kb_1",
        file_id="file_1",
        markdown_file="minio://knowledgebases/kb_1/parsed/file_1.md",
        filename="来文.docx",
        processing_params={},
        model_spec="model-a",
        markdown_reader=lambda _: "# 安全风险\n现场作业监护不到位，应加强现场监护。",
    )

    assert result["item_count"] > 0
    assert repo.runs[0]["run_metadata"]["markdown_file"] == "minio://knowledgebases/kb_1/parsed/file_1.md"
    assert {item["chunk_id"] for item in repo.results[0]["items"]} == {None}


async def test_run_markdown_extraction_reuses_same_markdown_and_model():
    repo = FakeExtractionRepo(reusable={"run_id": "ber_old", "items": []})
    service = BusinessExtractionService(llm=FakeLLM(), extraction_repo=repo)

    result = await service.run_markdown_extraction(
        kb_id="kb_1",
        file_id="file_1",
        markdown_file="minio://knowledgebases/kb_1/parsed/file_1.md",
        model_spec="model-a",
        markdown_reader=lambda _: "# ignored",
    )

    assert result["run_id"] == "ber_old"
    assert result["reused"] is True
    assert repo.runs == []
