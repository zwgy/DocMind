from types import SimpleNamespace

import pytest

from yuxi.knowledge.manager import KnowledgeBaseManager
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository


@pytest.mark.asyncio
async def test_search_document_files_returns_database_pagination(monkeypatch, tmp_path):
    records = [
        SimpleNamespace(
            file_id="file-1",
            filename="制度汇编.pdf",
            file_type="file",
            status="indexed",
            created_at=None,
            updated_at=None,
            file_size=1024,
            parent_id="folder-1",
        )
    ]

    async def fake_search_files(self, **kwargs):
        assert kwargs == {
            "kb_id": "kb-1",
            "filename_query": "制度",
            "offset": 2,
            "limit": 100,
            "files_only": True,
        }
        return records, 103

    monkeypatch.setattr(KnowledgeFileRepository, "search_files", fake_search_files)

    result = await KnowledgeBaseManager(str(tmp_path)).search_document_files(
        "kb-1", query=" 制度 ", offset=2, include_parent_id=True
    )

    assert result == {
        "files": [
            {
                "file_id": "file-1",
                "filename": "制度汇编.pdf",
                "file_type": "file",
                "status": "indexed",
                "created_at": None,
                "updated_at": None,
                "file_size": 1024,
                "parent_id": "folder-1",
            }
        ],
        "total": 103,
        "offset": 2,
        "limit": 100,
        "has_more": True,
    }
