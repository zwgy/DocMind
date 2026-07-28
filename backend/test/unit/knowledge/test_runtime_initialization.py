from types import SimpleNamespace

import pytest

from yuxi.knowledge.manager import KnowledgeBaseManager


@pytest.mark.asyncio
async def test_initialize_awaits_existing_kb_metadata_loading(monkeypatch, tmp_path):
    """初始化返回前必须完成元数据加载，避免首个知识库请求命中空缓存。"""
    manager = KnowledgeBaseManager(str(tmp_path))
    load_calls: list[str] = []

    async def fake_load_metadata(_self):
        load_calls.append("loaded")

    async def fake_get_all(_self):
        return [SimpleNamespace(kb_id="kb-1", kb_type="milvus")]

    instance = SimpleNamespace()
    instance._load_metadata = fake_load_metadata.__get__(instance)

    monkeypatch.setattr(
        "yuxi.repositories.knowledge_base_repository.KnowledgeBaseRepository.get_all", fake_get_all
    )
    monkeypatch.setattr(
        "yuxi.knowledge.manager.KnowledgeBaseFactory.is_type_supported",
        classmethod(lambda cls, kb_type: kb_type == "milvus"),
    )
    monkeypatch.setattr(
        "yuxi.knowledge.manager.KnowledgeBaseFactory.create",
        staticmethod(lambda kb_type, work_dir: instance),
    )

    await manager.initialize()

    assert load_calls == ["loaded"]
    assert manager.kb_instances["milvus"] is instance
