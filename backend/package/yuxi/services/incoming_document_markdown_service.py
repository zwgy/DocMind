from __future__ import annotations

from pathlib import Path

from minio.error import MinioException
from urllib3.exceptions import HTTPError

from yuxi.agents.backends.sandbox import ensure_thread_dirs, sandbox_uploads_dir, virtual_path_for_thread_file
from yuxi.knowledge.utils import parse_minio_url
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.repositories.incoming_document_repository import IncomingDocumentRepository
from yuxi.storage.minio import StorageError, get_minio_client
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils import logger


class IncomingDocumentMarkdownError(RuntimeError):
    """来文 Markdown 下载或落盘失败。"""


class IncomingDocumentMarkdownService:
    """下载来文 Markdown，并按需写入当前对话可读取的 sandbox。"""

    def __init__(self, incoming_repo: IncomingDocumentRepository | None = None):
        self.incoming_repo = incoming_repo or IncomingDocumentRepository()

    @staticmethod
    async def download_text(markdown_file_url: str) -> str:
        try:
            bucket_name, object_name = parse_minio_url(markdown_file_url)
            content = await get_minio_client().adownload_file(bucket_name, object_name)
            return content.decode("utf-8", errors="replace")
        except (StorageError, MinioException, HTTPError, OSError) as exc:
            logger.warning(f"来文 Markdown 下载失败: {exc}")
            raise IncomingDocumentMarkdownError("对象存储中的附件 Markdown 读取失败") from exc

    async def materialize(
        self,
        *,
        incoming_id: str,
        source_file_ids: list[str],
        uid: str,
        thread_id: str,
    ) -> list[dict[str, str]]:
        normalized_file_ids = list(dict.fromkeys(value.strip() for value in source_file_ids if value.strip()))
        if not normalized_file_ids:
            raise ValueError("source_file_ids 不能为空")

        # 原文必须写入当前用户拥有的对话目录，避免跨线程路径被模型借用。
        async with pg_manager.get_async_session_context() as session:
            conversation = await ConversationRepository(session).get_conversation_by_thread_id(thread_id)
            if not conversation or str(conversation.uid) != uid or conversation.status == "deleted":
                raise ValueError("对话线程不存在")

        document = await self.incoming_repo.get_by_incoming_id(incoming_id)
        if document is None:
            raise ValueError(f"来文不存在: {incoming_id}")
        files = await self.incoming_repo.list_files(incoming_id)
        selected = {file.source_file_id: file for file in files if file.source_file_id in normalized_file_ids}
        missing = [source_file_id for source_file_id in normalized_file_ids if source_file_id not in selected]
        if missing:
            raise ValueError(f"来文附件不存在: {', '.join(missing)}")

        ensure_thread_dirs(thread_id, uid)
        target_dir = sandbox_uploads_dir(thread_id) / "incoming-documents" / document.incoming_id
        target_dir.mkdir(parents=True, exist_ok=True)
        result = []
        for source_file_id in normalized_file_ids:
            file = selected[source_file_id]
            if not file.markdown_file_url:
                raise ValueError(f"附件 Markdown 尚未生成: {file.filename}")
            host_path = target_dir / f"{file.incoming_file_id}.md"
            try:
                host_path.write_text(await self.download_text(file.markdown_file_url), encoding="utf-8")
            except OSError as exc:
                logger.warning(f"来文 Markdown 写入会话目录失败: {exc}")
                raise IncomingDocumentMarkdownError("附件 Markdown 写入会话目录失败") from exc
            result.append(
                {
                    "source_file_id": file.source_file_id,
                    "filename": Path(file.filename).name,
                    "markdown_path": virtual_path_for_thread_file(thread_id, host_path, uid=uid),
                }
            )
        return result
