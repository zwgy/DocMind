from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from yuxi.agents.backends.sandbox import paths as sandbox_paths
from yuxi.services import incoming_document_markdown_service as service_module
from yuxi.services.incoming_document_markdown_service import IncomingDocumentMarkdownService


class FakeIncomingRepository:
    async def get_by_incoming_id(self, incoming_id):
        return SimpleNamespace(incoming_id=incoming_id) if incoming_id == "inc-1" else None

    async def list_files(self, incoming_id):
        assert incoming_id == "inc-1"
        return [
            SimpleNamespace(
                incoming_file_id="file-row-1",
                source_file_id="main",
                filename="主文件.docx",
                markdown_file_url="minio://parsed/main.md",
            ),
            SimpleNamespace(
                incoming_file_id="file-row-2",
                source_file_id="attachment",
                filename="附件.xlsx",
                markdown_file_url="minio://parsed/attachment.md",
            ),
        ]


@pytest.mark.asyncio
async def test_materialize_writes_only_selected_markdown_to_owned_thread(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox_paths.conf, "save_dir", str(tmp_path))
    ownership_checks = []

    @asynccontextmanager
    async def fake_session_context():
        yield object()

    class FakeConversationRepository:
        def __init__(self, session):
            del session

        async def get_conversation_by_thread_id(self, thread_id):
            ownership_checks.append((thread_id, "user-1"))
            return SimpleNamespace(uid="user-1", status="active")

    async def fake_download_text(url):
        assert url == "minio://parsed/attachment.md"
        return "# 附件内容"

    monkeypatch.setattr(service_module.pg_manager, "get_async_session_context", fake_session_context)
    monkeypatch.setattr(service_module, "ConversationRepository", FakeConversationRepository)
    monkeypatch.setattr(IncomingDocumentMarkdownService, "download_text", staticmethod(fake_download_text))

    result = await IncomingDocumentMarkdownService(FakeIncomingRepository()).materialize(
        incoming_id="inc-1",
        source_file_ids=["attachment"],
        uid="user-1",
        thread_id="thread-1",
    )

    assert ownership_checks == [("thread-1", "user-1")]
    assert result == [
        {
            "source_file_id": "attachment",
            "filename": "附件.xlsx",
            "markdown_path": "/home/gem/user-data/outputs/incoming-documents/inc-1/file-row-2.md",
        }
    ]
    host_path = (
        tmp_path / "threads" / "thread-1" / "user-data" / "outputs" / "incoming-documents" / "inc-1" / "file-row-2.md"
    )
    assert host_path.read_text(encoding="utf-8") == "# 附件内容"
    assert "minio" not in str(result)
    assert str(tmp_path) not in str(result)


@pytest.mark.asyncio
async def test_materialize_rejects_unknown_source_file_id(monkeypatch):
    @asynccontextmanager
    async def fake_session_context():
        yield object()

    class FakeConversationRepository:
        def __init__(self, session):
            del session

        async def get_conversation_by_thread_id(self, thread_id):
            del thread_id
            return SimpleNamespace(uid="user-1", status="active")

    monkeypatch.setattr(service_module.pg_manager, "get_async_session_context", fake_session_context)
    monkeypatch.setattr(service_module, "ConversationRepository", FakeConversationRepository)

    with pytest.raises(ValueError, match="来文附件不存在: missing"):
        await IncomingDocumentMarkdownService(FakeIncomingRepository()).materialize(
            incoming_id="inc-1",
            source_file_ids=["missing"],
            uid="user-1",
            thread_id="thread-1",
        )
