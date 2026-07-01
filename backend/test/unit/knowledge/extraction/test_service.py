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
