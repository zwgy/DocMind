from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel

from yuxi.document_extraction.json_utils import parse_json_object


class JsonLLM(Protocol):
    async def complete_json(self, prompt: str, schema: type[BaseModel]) -> dict[str, Any]:
        ...


class ModelJsonLLM:
    def __init__(self, model_spec: str):
        self.model_spec = model_spec

    async def complete_json(self, prompt: str, schema: type[BaseModel]) -> dict[str, Any]:
        from yuxi.models.chat import select_model

        model = select_model(self.model_spec, temperature=0)
        response = await model.call(
            [
                {"role": "system", "content": "你只输出合法 JSON，不输出解释。"},
                {"role": "user", "content": prompt},
            ],
            stream=False,
        )
        return parse_json_object(response.content)
