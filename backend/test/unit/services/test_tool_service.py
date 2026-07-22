from __future__ import annotations

from types import SimpleNamespace

from langchain_core.tools import BaseTool

from yuxi.agents.toolkits.incoming_documents.tools import read_incoming_document
from yuxi.agents.toolkits.registry import get_all_tool_instances
from yuxi.agents.toolkits import service as tool_service


def test_get_tool_metadata_includes_config_guide(monkeypatch):
    tool_service._metadata_cache.clear()

    fake_tool = SimpleNamespace(
        name="demo_tool",
        description="demo description",
        metadata={},
        args_schema=None,
        tool_call_schema=None,
    )
    fake_extra = SimpleNamespace(
        category="buildin",
        tags=["demo"],
        display_name="演示工具",
        config_guide="请先配置 DEMO_API_KEY",
    )

    monkeypatch.setattr(
        "yuxi.agents.toolkits.registry.get_all_tool_instances",
        lambda: [fake_tool],
    )
    monkeypatch.setattr(
        "yuxi.agents.toolkits.registry.get_all_extra_metadata",
        lambda: {"demo_tool": fake_extra},
    )

    result = tool_service.get_tool_metadata()

    assert result == [
        {
            "slug": "demo_tool",
            "name": "演示工具",
            "description": "demo description",
            "metadata": {},
            "args": [],
            "category": "buildin",
            "tags": ["demo"],
            "config_guide": "请先配置 DEMO_API_KEY",
        }
    ]

    tool_service._metadata_cache.clear()


def test_extract_tool_info_uses_public_tool_call_schema():
    result = tool_service._extract_tool_info(read_incoming_document)

    assert {item["name"] for item in result["args"]} == {
        "incoming_id",
        "source_file_ids",
        "include_full_text",
    }


def test_extract_tool_info_accepts_json_schema_dict():
    tool = SimpleNamespace(
        name="demo_tool",
        description="demo description",
        metadata={},
        tool_call_schema={
            "properties": {
                "query": {"type": "string", "description": "查询内容"},
            }
        },
    )

    assert tool_service._extract_tool_info(tool)["args"] == [
        {"name": "query", "type": "string", "description": "查询内容"},
    ]


def test_registered_tools_expose_langchain_public_call_schemas():
    assert all(isinstance(tool, BaseTool) and tool.tool_call_schema for tool in get_all_tool_instances())
