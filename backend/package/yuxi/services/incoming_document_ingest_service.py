from __future__ import annotations

from asyncio import gather
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from yuxi.document_extraction import (
    BusinessExtractionService,
    classify_incoming_document,
    summarize_incoming_attachment,
)
from yuxi.document_extraction.evidence import find_source_quote
from yuxi.document_extraction.schemas import (
    AdditionalClassification,
    IncomingDocumentClassificationResult,
    document_category_id,
    document_category_label,
)
from yuxi.document_extraction.service import document_input_token_limit
from yuxi.knowledge.chunking.ragflow_like.dispatcher import chunk_markdown
from yuxi.knowledge.chunking.ragflow_like.nlp import count_tokens
from yuxi.knowledge.parser import Parser, is_supported_file_extension
from yuxi.knowledge.utils import calculate_content_hash, parse_minio_url
from yuxi.repositories.incoming_document_repository import IncomingDocumentRepository
from yuxi.services.knowledge_document_ingest_service import KnowledgeDocumentIngestService
from yuxi.services.task_service import TaskContext, tasker
from yuxi.storage.minio import MinIOClient, aupload_file_to_minio, get_minio_client
from yuxi.utils import hashstr
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.upload_utils import MAX_UPLOAD_SIZE_BYTES

UploadFileFn = Callable[..., Awaitable[dict[str, Any]]]
ParseDocumentFn = Callable[[str, dict[str, Any]], Awaitable[str]]
UploadMarkdownFn = Callable[..., Awaitable[str]]
ClassifyDocumentFn = Callable[..., Awaitable[dict[str, Any]]]
SummarizeAttachmentFn = Callable[..., Awaitable[str]]
INCOMING_DOCUMENT_PROCESS_TASK_TYPE = "incoming_document_process"
MULTI_CLASSIFICATION_CONFIDENCE_THRESHOLD = 0.8


class IncomingKnowledgeImportConflict(ValueError):
    """来文入库任务已经在执行。"""


class IncomingDocumentIngestService:
    """按来文聚合上传、解析、分类和正式业务抽取。"""

    def __init__(
        self,
        *,
        incoming_repo: IncomingDocumentRepository | None = None,
        tasker=tasker,
        upload_file: UploadFileFn | None = None,
        parse_document: ParseDocumentFn | None = None,
        upload_markdown: UploadMarkdownFn | None = None,
        classify_document: ClassifyDocumentFn | None = None,
        summarize_attachment: SummarizeAttachmentFn | None = None,
        business_extraction_service: BusinessExtractionService | None = None,
    ):
        self.incoming_repo = incoming_repo or IncomingDocumentRepository()
        self.tasker = tasker
        self.upload_file = upload_file or _upload_incoming_file
        self.parse_document = parse_document or _parse_incoming_document
        self.upload_markdown = upload_markdown or _upload_incoming_markdown
        self.classify_document = classify_document or classify_incoming_document
        self.summarize_attachment = summarize_attachment or summarize_incoming_attachment
        self.business_extraction_service = business_extraction_service or BusinessExtractionService()

    async def ingest_files(
        self,
        *,
        source_doc_id: str,
        source_function_id: str,
        document_metadata: dict[str, Any],
        source_system: str = "production",
        files: list[dict[str, Any]],
        operator_id: str | None = None,
    ) -> dict[str, Any]:
        source_system = (source_system or "production").strip() or "production"
        source_function_id = (source_function_id or "").strip()
        source_document_id = (source_doc_id or "").strip()
        if not source_function_id:
            raise ValueError("source_function_id is required")
        if not source_document_id:
            raise ValueError("source_doc_id is required")
        if not isinstance(document_metadata, dict):
            raise ValueError("document_metadata must be an object")
        if not files:
            raise ValueError("files is required")

        incoming_id = self._incoming_id(source_system, source_function_id, source_document_id)
        normalized_files = self._validate_files(files)
        existing_document = await self.incoming_repo.get_by_incoming_id(incoming_id)
        if existing_document is not None and existing_document.status in {"parsing", "extracting"}:
            # 处理期间改变附件集合会让运行中的任务产出过期结果，直接要求调用方稍后重试。
            raise ValueError("Incoming document is being processed; retry after it finishes")
        metadata_changed = existing_document is None or existing_document.document_metadata != document_metadata
        existing_files = {item.source_file_id: item for item in await self.incoming_repo.list_files(incoming_id)}
        requested_main = next((item for item in normalized_files if item["is_main_file"] is True), None)
        existing_main = next((item for item in existing_files.values() if item.is_main_file), None)
        target_main_source_id = (
            requested_main["source_file_id"]
            if requested_main is not None
            else existing_main.source_file_id
            if existing_main is not None
            else normalized_files[0]["source_file_id"]
        )
        main_switch_required = existing_main is not None and existing_main.source_file_id != target_main_source_id
        changed = metadata_changed or main_switch_required
        for item in normalized_files:
            item["is_main_file"] = item["source_file_id"] == target_main_source_id
            item["content_hash"] = await calculate_content_hash(item["content"])
            existing = existing_files.get(item["source_file_id"])
            changed = (
                changed
                or existing is None
                or existing.content_hash != item["content_hash"]
                or any(
                    (
                        existing.filename != item["filename"],
                        existing.is_main_file != item["is_main_file"],
                        existing.source_url != item.get("source_url"),
                        existing.mime_type != item.get("mime_type"),
                    )
                )
            )

        if not changed:
            if existing_document.status == "uploaded":
                task = await self._submit_process_task(incoming_id=incoming_id, operator_id=operator_id)
                return {"incomingId": incoming_id, "taskId": task.id, "status": "accepted", "items": []}
            return {
                "incomingId": incoming_id,
                "taskId": None,
                "status": existing_document.status,
                "items": [self._file_payload(file, status="exists") for file in existing_files.values()],
            }
        if existing_document is not None and getattr(existing_document, "knowledge_import_status", None) in {
            "importing",
            "partial",
            "indexed",
        }:
            # 已入知识库的原文件不能静默换新，否则知识库索引与来文结果会指向两份内容。
            raise ValueError("Incoming document already belongs to a knowledge base and cannot be replaced")

        document_data = {
            "source_system": source_system,
            "source_function_id": source_function_id,
            "source_document_id": source_document_id,
            "document_metadata": document_metadata,
            "status": "uploaded",
            "ai_classification": None,
            "classification_confidence": None,
            "classification_evidence": None,
            "additional_classifications": [],
            "confirmed_classification": None,
            "confirmed_by": None,
            "confirmed_at": None,
            "review_status": "draft",
            "summary": None,
            "processing_error": None,
            "linked_kb_id": None,
            "knowledge_import_status": "none",
            "knowledge_import_task_id": None,
            "knowledge_import_error": None,
            "updated_by": operator_id,
        }
        if existing_document is None:
            document_data["created_by"] = operator_id
        document = await self.incoming_repo.upsert_document(incoming_id, document_data)

        items = []
        for item in normalized_files:
            existing = existing_files.get(item["source_file_id"])
            if existing is not None and existing.content_hash == item["content_hash"]:
                file_metadata_changed = any(
                    (
                        existing.filename != item["filename"],
                        existing.is_main_file != item["is_main_file"],
                        existing.source_url != item.get("source_url"),
                        existing.mime_type != item.get("mime_type"),
                    )
                )
                if file_metadata_changed:
                    await self.incoming_repo.update_file(
                        existing.incoming_file_id,
                        {
                            "filename": item["filename"],
                            "is_main_file": item["is_main_file"] and not main_switch_required,
                            "source_url": item.get("source_url"),
                            "mime_type": item.get("mime_type"),
                        },
                    )
                items.append(self._file_payload(existing, status="exists"))
                continue

            incoming_file_id = (
                existing.incoming_file_id
                if existing is not None
                else self._incoming_file_id(incoming_id, item["source_file_id"])
            )
            upload = await self.upload_file(
                source_system=source_system,
                incoming_id=incoming_id,
                incoming_file_id=incoming_file_id,
                filename=item["filename"],
                content=item["content"],
            )
            record = await self.incoming_repo.upsert_file(
                incoming_id,
                incoming_file_id,
                {
                    "source_file_id": item["source_file_id"],
                    "source_url": item.get("source_url"),
                    "filename": item["filename"],
                    "is_main_file": item["is_main_file"] and not main_switch_required,
                    "content_hash": upload["content_hash"],
                    "file_size": upload["size"],
                    "mime_type": item.get("mime_type"),
                    "original_file_url": upload["minio_url"],
                    "markdown_file_url": None,
                    "status": "uploaded",
                    "processing_error": None,
                    "linked_file_id": None,
                    "knowledge_import_status": "none",
                    "knowledge_import_error": None,
                },
            )
            items.append(self._file_payload(record, status="accepted"))

        if main_switch_required:
            await self.incoming_repo.set_main_file(document.incoming_id, target_main_source_id)
            for entry in items:
                entry["isMainFile"] = entry["sourceFileId"] == target_main_source_id

        for file in await self.incoming_repo.list_files(document.incoming_id):
            if getattr(file, "knowledge_import_status", None) != "none" or getattr(file, "linked_file_id", None):
                await self.incoming_repo.update_file(
                    file.incoming_file_id,
                    {
                        "linked_file_id": None,
                        "knowledge_import_status": "none",
                        "knowledge_import_error": None,
                    },
                )
        task = await self._submit_process_task(incoming_id=document.incoming_id, operator_id=operator_id)
        return {"incomingId": document.incoming_id, "taskId": task.id, "status": "accepted", "items": items}

    async def process_incoming_document(
        self,
        incoming_id: str,
        *,
        operator_id: str | None = None,
        context: TaskContext | None = None,
    ) -> dict[str, Any]:
        document = await self.incoming_repo.get_by_incoming_id(incoming_id)
        if document is None:
            raise ValueError(f"Incoming document not found: {incoming_id}")
        files = await self.incoming_repo.list_files(incoming_id)
        if not files:
            raise ValueError("Incoming document has no files")

        try:
            await self.incoming_repo.update_document(
                incoming_id, {"status": "parsing", "processing_error": None, "updated_by": operator_id}
            )

            async def parse_file(file):
                await self.incoming_repo.update_file(
                    file.incoming_file_id, {"status": "parsing", "processing_error": None}
                )
                try:
                    markdown = await self.parse_document(
                        file.original_file_url,
                        {
                            "image_bucket": "public",
                            "image_prefix": f"incoming/{incoming_id}/{file.incoming_file_id}/images",
                        },
                    )
                    if not markdown.strip():
                        raise ValueError(f"Parsed Markdown is empty: {file.filename}")
                    markdown_url = await self.upload_markdown(
                        incoming_id=f"{incoming_id}/{file.incoming_file_id}", markdown=markdown
                    )
                    await self.incoming_repo.update_file(
                        file.incoming_file_id,
                        {"status": "parsed", "markdown_file_url": markdown_url, "processing_error": None},
                    )
                    return {"file": file, "markdown": markdown, "markdown_url": markdown_url}
                except Exception as exc:
                    await self.incoming_repo.update_file(
                        file.incoming_file_id, {"status": "failed", "processing_error": str(exc)}
                    )
                    raise

            parsed_files = list(await gather(*(parse_file(file) for file in files)))
            await _set_progress(context, 50, f"已解析全部 {len(files)} 个附件")

            await self.incoming_repo.update_document(incoming_id, {"status": "extracting", "updated_by": operator_id})
            main_files = [
                parsed for parsed in parsed_files if getattr(parsed["file"], "is_main_file", False)
            ] or parsed_files[:1]
            classification = await self._classify_document_bundle(document, main_files)
            attachment_summaries = await self._summarize_supplementary_files(parsed_files)
            extraction_classifications = _trusted_extraction_classifications(classification)
            await self._run_document_extraction(
                incoming_id=incoming_id,
                parsed_files=parsed_files,
                classifications=extraction_classifications,
                operator_id=operator_id,
                attachment_summaries=attachment_summaries,
            )
            await self.incoming_repo.update_document(
                incoming_id,
                {
                    "status": "ready",
                    "ai_classification": classification.classification,
                    "classification_confidence": classification.classification_confidence,
                    "classification_evidence": classification.classification_evidence,
                    "additional_classifications": [
                        item.model_dump() for item in classification.additional_classifications
                    ],
                    "summary": classification.summary,
                    "processing_error": None,
                    "updated_by": operator_id,
                },
            )
            await _set_progress(context, 100, "来文处理完成")
            return {"incoming_id": incoming_id, "status": "ready"}
        except Exception as exc:
            await self.incoming_repo.update_document(
                incoming_id, {"status": "failed", "processing_error": str(exc), "updated_by": operator_id}
            )
            raise

    async def correct_classification(
        self, incoming_id: str, *, classification: str, operator_id: str | None = None
    ) -> dict[str, Any]:
        classification = document_category_id(classification)
        if classification is None:
            raise ValueError("classification is not configured")
        document = await self.incoming_repo.get_by_incoming_id(incoming_id)
        if document is None:
            raise ValueError(f"Incoming document not found: {incoming_id}")
        if document.status != "ready":
            raise ValueError("Incoming document is not ready")
        if getattr(document, "knowledge_import_status", None) in {"importing", "partial", "indexed"}:
            raise ValueError("Imported incoming document classification cannot be changed")
        files = await self.incoming_repo.list_files(incoming_id)
        if not files or any(not file.markdown_file_url for file in files):
            raise ValueError("Incoming document Markdown is not ready")
        parsed_files = [
            {
                "file": file,
                "markdown": await _download_markdown(file.markdown_file_url),
                "markdown_url": file.markdown_file_url,
            }
            for file in files
        ]
        await self.incoming_repo.update_document(
            incoming_id,
            {
                "review_status": "draft",
                "confirmed_by": None,
                "confirmed_at": None,
                "status": "extracting",
                "additional_classifications": [],
                "processing_error": None,
                "linked_kb_id": None,
                "knowledge_import_status": "none",
                "knowledge_import_task_id": None,
                "knowledge_import_error": None,
                "updated_by": operator_id,
            },
        )
        for file in files:
            if getattr(file, "knowledge_import_status", None) != "none" or getattr(file, "linked_file_id", None):
                await self.incoming_repo.update_file(
                    file.incoming_file_id,
                    {
                        "linked_file_id": None,
                        "knowledge_import_status": "none",
                        "knowledge_import_error": None,
                    },
                )
        try:
            main_files = [
                parsed for parsed in parsed_files if getattr(parsed["file"], "is_main_file", False)
            ] or parsed_files[:1]
            routing = await self._classify_document_bundle(document, main_files)
            attachment_summaries = await self._summarize_supplementary_files(parsed_files)
            routing_additional: list[AdditionalClassification] = []
            if (
                routing.classification != classification
                and routing.classification_confidence is not None
                and routing.classification_confidence >= MULTI_CLASSIFICATION_CONFIDENCE_THRESHOLD
                and routing.classification_evidence
            ):
                routing_additional.append(
                    AdditionalClassification(
                        classification=routing.classification,
                        confidence=routing.classification_confidence,
                        evidence=routing.classification_evidence,
                    )
                )
            routing_additional.extend(routing.additional_classifications)
            routing_additional = _merge_additional_classifications(routing_additional, classification)
            extraction_classifications = _valid_extraction_classifications(
                [classification, *(item.classification for item in routing_additional)], classification
            )
            await self._run_document_extraction(
                incoming_id=incoming_id,
                parsed_files=parsed_files,
                classifications=extraction_classifications,
                operator_id=operator_id,
                attachment_summaries=attachment_summaries,
            )
            await self.incoming_repo.update_document(
                incoming_id,
                {
                    "status": "ready",
                    "confirmed_classification": classification,
                    "additional_classifications": [item.model_dump() for item in routing_additional],
                    "updated_by": operator_id,
                },
            )
        except Exception as exc:
            await self.incoming_repo.update_document(
                incoming_id, {"status": "failed", "processing_error": str(exc), "updated_by": operator_id}
            )
            raise
        return {
            "incomingId": incoming_id,
            "effectiveClassification": classification,
            "effectiveClassificationLabel": document_category_label(classification),
            "status": "ready",
        }

    async def confirm_document(self, incoming_id: str, *, operator_id: str) -> dict[str, Any]:
        document = await self.incoming_repo.get_by_incoming_id(incoming_id)
        if document is None:
            raise ValueError(f"Incoming document not found: {incoming_id}")
        if document.status != "ready":
            raise ValueError("Incoming document is not ready")
        await self.incoming_repo.update_document(
            incoming_id,
            {
                "review_status": "confirmed",
                "confirmed_by": operator_id,
                "confirmed_at": utc_now_naive(),
                "updated_by": operator_id,
            },
        )
        return {"incomingId": incoming_id, "reviewStatus": "confirmed"}

    async def delete_incoming(self, incoming_id: str, *, operator_id: str | None = None) -> dict[str, Any]:
        """管理员清理来文：仓库校验并完成 DB 级联删除，这里负责 MinIO 兜底清理。

        删除前置条件由仓库在事务内校验；对象存储清理放在事务外，DB 是真相源，
        MinIO 部分失败时记录到 ``minioErrors`` 供审计 / 异步任务兜底。
        """

        document, files = await self.incoming_repo.delete_cascade(incoming_id)
        if document is None:
            raise ValueError(f"Incoming document not found: {incoming_id}")

        minio_errors: list[str] = []
        client = get_minio_client()
        for file in files:
            for url in (file.original_file_url, file.markdown_file_url):
                if not url:
                    continue
                try:
                    bucket, object_name = parse_minio_url(url)
                except ValueError as exc:
                    minio_errors.append(f"{url}: {exc}")
                    continue
                try:
                    await client.adelete_file(bucket, object_name)
                except Exception as exc:
                    minio_errors.append(f"{bucket}/{object_name}: {exc}")

        return {
            "incomingId": incoming_id,
            "removedFiles": len(files),
            "minioErrors": minio_errors,
            "operatorId": operator_id,
        }

    async def retry_processing(self, incoming_id: str, *, operator_id: str | None = None) -> dict[str, Any]:
        document = await self.incoming_repo.get_by_incoming_id(incoming_id)
        if document is None:
            raise ValueError(f"Incoming document not found: {incoming_id}")
        if document.status not in {"ready", "failed"}:
            raise ValueError("Incoming document cannot be retried while processing")
        if getattr(document, "knowledge_import_status", None) in {"importing", "partial", "indexed"}:
            raise ValueError("Imported incoming document cannot be reprocessed")
        files = await self.incoming_repo.list_files(incoming_id)
        for file in files:
            await self.incoming_repo.update_file(
                file.incoming_file_id,
                {
                    "status": "uploaded",
                    "markdown_file_url": None,
                    "processing_error": None,
                    "linked_file_id": None,
                    "knowledge_import_status": "none",
                    "knowledge_import_error": None,
                },
            )
        await self.incoming_repo.update_document(
            incoming_id,
            {
                "status": "uploaded",
                "ai_classification": None,
                "classification_confidence": None,
                "classification_evidence": None,
                "additional_classifications": [],
                "confirmed_classification": None,
                "confirmed_by": None,
                "confirmed_at": None,
                "summary": None,
                "processing_error": None,
                "review_status": "draft",
                "updated_by": operator_id,
            },
        )
        task = await self._submit_process_task(incoming_id=incoming_id, operator_id=operator_id)
        return {"incomingId": incoming_id, "taskId": task.id, "status": "accepted"}

    async def import_to_knowledge(
        self,
        incoming_id: str,
        *,
        kb_id: str,
        parent_id: str | None = None,
        source_file_ids: list[str] | None = None,
        params: dict[str, Any] | None = None,
        operator_id: str | None = None,
        document_ingest_service: KnowledgeDocumentIngestService | None = None,
    ) -> dict[str, Any]:
        document = await self.incoming_repo.get_by_incoming_id(incoming_id)
        if document is None:
            raise ValueError(f"Incoming document not found: {incoming_id}")
        if document.status != "ready":
            raise ValueError("Incoming document is not ready")
        if document.knowledge_import_status == "importing":
            raise IncomingKnowledgeImportConflict("Incoming document is already importing to knowledge base")
        files = await self.incoming_repo.list_files(incoming_id)
        if not files:
            raise ValueError("Incoming document has no files")

        if source_file_ids is None:
            selected_files = files
        else:
            selected_ids = list(dict.fromkeys(source_file_id.strip() for source_file_id in source_file_ids))
            if not selected_ids or any(not source_file_id for source_file_id in selected_ids):
                raise ValueError("sourceFileIds must contain at least one valid attachment ID")
            files_by_source_id = {file.source_file_id: file for file in files}
            missing_ids = [
                source_file_id for source_file_id in selected_ids if source_file_id not in files_by_source_id
            ]
            if missing_ids:
                raise ValueError(f"Incoming document files not found: {', '.join(missing_ids)}")
            selected_files = [files_by_source_id[source_file_id] for source_file_id in selected_ids]

        indexed_files = [file for file in files if file.knowledge_import_status == "indexed"]
        if indexed_files and document.linked_kb_id != kb_id:
            raise ValueError("Imported attachments cannot be moved to another knowledge base")
        target_files = [file for file in selected_files if file.knowledge_import_status != "indexed"]
        if not target_files:
            return {
                "incomingId": incoming_id,
                "status": "exists",
                "taskId": document.knowledge_import_task_id,
                "knowledgeImportStatus": document.knowledge_import_status,
                "linkedKbId": document.linked_kb_id,
                "sourceFileIds": [file.source_file_id for file in selected_files],
            }

        ingest_params = dict(params or {}) | {"content_type": "file", "auto_index": True}
        ingest_params["_incoming_document"] = {
            "incoming_id": incoming_id,
            "incoming_file_ids": [file.incoming_file_id for file in target_files],
        }
        if parent_id:
            ingest_params["parent_id"] = parent_id
        sources = [file.original_file_url for file in target_files]
        ingest_params["content_hashes"] = {
            file.original_file_url: file.content_hash for file in target_files if file.content_hash
        }
        ingest_params["file_sizes"] = {
            file.original_file_url: file.file_size for file in target_files if file.file_size is not None
        }
        category_path = _safe_object_segment(_effective_classification(document))
        ingest_params["source_paths"] = {
            file.original_file_url: f"incoming/{category_path}/{file.incoming_file_id}/{file.filename}"
            for file in target_files
        }
        document_ingest = document_ingest_service or KnowledgeDocumentIngestService()
        await document_ingest.ensure_database_supports_documents(kb_id, "来文存入知识库")

        async def update_document_import_result(*, error: str | None = None) -> None:
            current_files = await self.incoming_repo.list_files(incoming_id)
            has_indexed = any(file.knowledge_import_status == "indexed" for file in current_files)
            all_indexed = bool(current_files) and all(
                file.knowledge_import_status == "indexed" for file in current_files
            )
            status = "indexed" if all_indexed else "partial" if has_indexed else "failed" if error else "none"
            await self.incoming_repo.update_document(
                incoming_id,
                {
                    "knowledge_import_status": status,
                    "knowledge_import_error": error,
                    "linked_kb_id": kb_id if has_indexed else None,
                    "updated_by": operator_id,
                },
            )

        async def on_success(result: dict[str, Any]) -> None:
            result_items = result.get("items") or []
            if len(result_items) != len(target_files):
                raise ValueError("Knowledge import result count does not match submitted attachments")
            linked_file_ids = [item.get("file_id") for item in result_items]
            if any(not file_id for file_id in linked_file_ids):
                raise ValueError("Knowledge import result is missing file_id")
            for file, linked_file_id in zip(target_files, linked_file_ids, strict=True):
                await self.incoming_repo.update_file(
                    file.incoming_file_id,
                    {
                        "knowledge_import_status": "indexed",
                        "knowledge_import_error": None,
                        "linked_file_id": linked_file_id,
                    },
                )
            await update_document_import_result()

        async def on_failure(exc: Exception) -> None:
            for file in target_files:
                await self.incoming_repo.update_file(
                    file.incoming_file_id,
                    {"knowledge_import_status": "failed", "knowledge_import_error": str(exc)},
                )
            await update_document_import_result(error=str(exc))

        # 先落状态再入队，避免极快任务回调被随后写入的 importing 状态覆盖。
        await self.incoming_repo.update_document(
            incoming_id,
            {
                "knowledge_import_status": "importing",
                "knowledge_import_task_id": None,
                "knowledge_import_error": None,
                "linked_kb_id": kb_id,
                "updated_by": operator_id,
            },
        )
        for file in target_files:
            await self.incoming_repo.update_file(
                file.incoming_file_id,
                {"knowledge_import_status": "importing", "knowledge_import_error": None},
            )

        try:
            queued = await document_ingest.enqueue_ingest(
                kb_id=kb_id,
                items=sources,
                params=ingest_params,
                operator_id=operator_id,
                task_name=f"来文存入知识库({incoming_id})",
                on_success=on_success,
                on_failure=on_failure,
            )
        except Exception as exc:
            await on_failure(exc)
            raise
        await self.incoming_repo.update_document(
            incoming_id,
            {
                "knowledge_import_task_id": queued["task_id"],
                "updated_by": operator_id,
            },
        )
        return {
            "incomingId": incoming_id,
            "status": "queued",
            "taskId": queued["task_id"],
            "knowledgeImportStatus": "importing",
            "linkedKbId": kb_id,
            "sourceFileIds": [file.source_file_id for file in target_files],
        }

    async def _submit_process_task(self, *, incoming_id: str, operator_id: str | None):
        async def run_process(context: TaskContext):
            return await self.process_incoming_document(incoming_id, operator_id=operator_id, context=context)

        task, _ = await self.tasker.enqueue_unique_by_payload(
            name=f"来文处理 ({incoming_id})",
            task_type=INCOMING_DOCUMENT_PROCESS_TASK_TYPE,
            payload={"incoming_id": incoming_id},
            payload_match={"incoming_id": incoming_id},
            statuses={"pending", "running"},
            coroutine=run_process,
        )
        return task

    async def _classify_document_bundle(
        self, document, parsed_files: list[dict[str, Any]]
    ) -> IncomingDocumentClassificationResult:
        from yuxi.config.app import config

        model_spec = config.default_model
        input_limit = document_input_token_limit(model_spec)
        bundle = _markdown_bundle(parsed_files)
        filename = str((document.document_metadata or {}).get("title") or document.source_document_id)
        if count_tokens(bundle) <= input_limit:
            raw = await self.classify_document(
                filename=filename, markdown=bundle, metadata=document.document_metadata or {}, model_spec=model_spec
            )
            return _validated_classification_result(IncomingDocumentClassificationResult.model_validate(raw), bundle)

        # 超长时先按原文结构分块压缩；正式业务抽取仍回读全部原文分块。
        briefs = []
        observed_classifications: list[AdditionalClassification] = []
        for parsed in parsed_files:
            file = parsed["file"]
            markdown = parsed["markdown"]
            chunks = (
                [{"content": markdown, "chunk_index": 0}]
                if count_tokens(markdown) <= input_limit
                else chunk_markdown(
                    markdown,
                    file.incoming_file_id,
                    file.filename,
                    {
                        "chunk_parser_config": {
                            "chunk_token_num": max(512, int(input_limit / 1.5)),
                            "overlapped_percent": 10,
                        }
                    },
                )
            )
            for chunk in chunks:
                raw = await self.classify_document(
                    filename=file.filename,
                    markdown=chunk["content"],
                    metadata=document.document_metadata or {},
                    model_spec=model_spec,
                )
                result = _validated_classification_result(
                    IncomingDocumentClassificationResult.model_validate(raw), chunk["content"]
                )
                extraction_labels = _trusted_extraction_classifications(result)
                if (
                    result.classification_confidence is not None
                    and result.classification_confidence >= MULTI_CLASSIFICATION_CONFIDENCE_THRESHOLD
                    and result.classification_evidence
                ):
                    observed_classifications.append(
                        AdditionalClassification(
                            classification=result.classification,
                            confidence=result.classification_confidence,
                            evidence=result.classification_evidence,
                        )
                    )
                observed_classifications.extend(result.additional_classifications)
                briefs.append(
                    f"文件：{file.filename}；分块：{chunk['chunk_index']}\n"
                    f"主分类：{result.classification}\n"
                    f"抽取分类：{'、'.join(extraction_labels)}\n摘要：{result.summary}"
                )

        previous_tokens = count_tokens("\n\n".join(briefs))
        reduction_rounds = 0
        while len(briefs) > 1 and previous_tokens > input_limit:
            if reduction_rounds >= 8 or any(count_tokens(item) > input_limit for item in briefs):
                raise RuntimeError("Document classification summaries cannot fit the model context window")
            condensed = []
            for group in _group_by_token_budget(briefs, input_limit):
                group_text = "\n\n".join(group)
                raw = await self.classify_document(
                    filename=filename,
                    markdown=group_text,
                    metadata=document.document_metadata or {},
                    model_spec=model_spec,
                )
                result = _validated_classification_result(
                    IncomingDocumentClassificationResult.model_validate(raw), group_text
                )
                extraction_labels = _trusted_extraction_classifications(result)
                condensed.append(
                    f"主分类：{result.classification}\n抽取分类：{'、'.join(extraction_labels)}\n摘要：{result.summary}"
                )
            current_tokens = count_tokens("\n\n".join(condensed))
            if current_tokens >= previous_tokens:
                raise RuntimeError("Document classification summary reduction did not converge")
            briefs = condensed
            previous_tokens = current_tokens
            reduction_rounds += 1
        if previous_tokens > input_limit:
            raise RuntimeError("Document classification summary exceeds the model context window")
        final_input = "\n\n".join(briefs)
        raw = await self.classify_document(
            filename=filename,
            markdown=final_input,
            metadata=document.document_metadata or {},
            model_spec=model_spec,
        )
        result = _validated_classification_result(IncomingDocumentClassificationResult.model_validate(raw), final_input)
        if not result.classification_evidence:
            primary_evidence = next(
                (
                    item.evidence
                    for item in observed_classifications
                    if item.classification == result.classification and item.evidence in bundle
                ),
                None,
            )
            result.classification_evidence = primary_evidence
        result.additional_classifications = _merge_additional_classifications(
            [
                *[item for item in result.additional_classifications if item.evidence in bundle],
                *observed_classifications,
            ],
            result.classification,
        )
        return result

    async def _run_document_extraction(
        self,
        *,
        incoming_id: str,
        parsed_files: list[dict[str, Any]],
        classifications: list[str],
        operator_id: str | None,
        attachment_summaries: dict[str, str] | None = None,
    ) -> None:
        from yuxi.config.app import config

        await self.business_extraction_service.run_incoming_document_extraction(
            incoming_id=incoming_id,
            files=[
                {
                    "incoming_file_id": parsed["file"].incoming_file_id,
                    "source_file_id": parsed["file"].source_file_id,
                    "filename": parsed["file"].filename,
                    "is_main_file": getattr(parsed["file"], "is_main_file", False),
                    "markdown_file": parsed["markdown_url"],
                    "markdown": parsed["markdown"],
                }
                for parsed in parsed_files
            ],
            classifications=classifications,
            model_spec=config.default_model,
            operator_id=operator_id,
            attachment_summaries=attachment_summaries,
        )

    async def _summarize_supplementary_files(self, parsed_files: list[dict[str, Any]]) -> dict[str, str]:
        """副附件只生成定位摘要，避免混入主来文的分类和业务结构化结果。"""

        main_file = next(
            (parsed for parsed in parsed_files if getattr(parsed["file"], "is_main_file", False)),
            parsed_files[0] if parsed_files else None,
        )
        supplementary_files = [parsed for parsed in parsed_files if parsed is not main_file]
        if not supplementary_files:
            return {}
        results = await gather(
            *(self._summarize_attachment(parsed) for parsed in supplementary_files)
        )
        return {
            parsed["file"].source_file_id: summary
            for parsed, summary in zip(supplementary_files, results, strict=True)
            if summary
        }

    async def _summarize_attachment(self, parsed: dict[str, Any]) -> str:
        from yuxi.config.app import config

        file = parsed["file"]
        markdown = parsed["markdown"]
        input_limit = document_input_token_limit(config.default_model)
        if count_tokens(markdown) <= input_limit:
            return await self.summarize_attachment(
                filename=file.filename,
                markdown=markdown,
                model_spec=config.default_model,
            )

        chunks = chunk_markdown(
            markdown,
            file.incoming_file_id,
            file.filename,
            {
                "chunk_parser_config": {
                    "chunk_token_num": max(512, int(input_limit / 1.5)),
                    "overlapped_percent": 10,
                }
            },
        )
        summaries = await gather(
            *(
                self.summarize_attachment(
                    filename=file.filename,
                    markdown=str(chunk["content"]),
                    model_spec=config.default_model,
                )
                for chunk in chunks
            )
        )
        previous_tokens = count_tokens("\n\n".join(summaries))
        while previous_tokens > input_limit:
            condensed = await gather(
                *(
                    self.summarize_attachment(
                        filename=file.filename,
                        markdown="\n\n".join(group),
                        model_spec=config.default_model,
                    )
                    for group in _group_by_token_budget(summaries, input_limit)
                )
            )
            current_tokens = count_tokens("\n\n".join(condensed))
            if current_tokens >= previous_tokens:
                raise RuntimeError("Attachment summary reduction did not converge")
            summaries = condensed
            previous_tokens = current_tokens
        return await self.summarize_attachment(
            filename=file.filename,
            markdown="\n\n".join(summaries),
            model_spec=config.default_model,
        )

    @staticmethod
    def _incoming_id(source_system: str, source_function_id: str, source_document_id: str) -> str:
        return f"inc_{hashstr(f'{source_system}:{source_function_id}:{source_document_id}', 16)}"

    @staticmethod
    def _incoming_file_id(incoming_id: str, source_file_id: str) -> str:
        return f"incf_{hashstr(f'{incoming_id}:{source_file_id}', 16)}"

    @staticmethod
    def _validate_files(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        source_ids = set()
        main_count = 0
        for item in files:
            source_file_id = str(item.get("source_file_id") or "").strip()
            filename = Path(str(item.get("filename") or "")).name
            content = item.get("content")
            is_main_file = item.get("is_main_file")
            if is_main_file is not None:
                is_main_file = bool(is_main_file)
            if not source_file_id or not filename or not isinstance(content, bytes):
                raise ValueError("every file requires source_file_id, filename and content")
            if source_file_id in source_ids:
                raise ValueError("source_file_id must be unique in one request")
            if not is_supported_file_extension(filename):
                raise ValueError("Unsupported file type")
            if len(content) > MAX_UPLOAD_SIZE_BYTES:
                raise ValueError("文件大小不能超过 100 MB 上限")
            source_ids.add(source_file_id)
            main_count += is_main_file is True
            result.append(
                {**item, "source_file_id": source_file_id, "filename": filename, "is_main_file": is_main_file}
            )
        if main_count > 1:
            raise ValueError("only one main file is allowed")
        return result

    @staticmethod
    def _file_payload(file, *, status: str) -> dict[str, Any]:
        return {
            "incomingFileId": file.incoming_file_id,
            "sourceFileId": file.source_file_id,
            "filename": file.filename,
            "isMainFile": file.is_main_file,
            "status": status,
        }


async def _upload_incoming_file(
    *, source_system: str, incoming_id: str, incoming_file_id: str, filename: str, content: bytes
) -> dict[str, Any]:
    safe_name = Path(filename).name
    suffix = Path(safe_name).suffix.lower()
    object_name = f"incoming/{_safe_object_segment(source_system)}/{incoming_id}/{incoming_file_id}/original{suffix}"
    minio_url = await aupload_file_to_minio(MinIOClient.KB_BUCKETS["documents"], object_name, content)
    return {"minio_url": minio_url, "content_hash": await calculate_content_hash(content), "size": len(content)}


async def _parse_incoming_document(source: str, params: dict[str, Any]) -> str:
    return await Parser.aparse(source=source, params=params)


async def _upload_incoming_markdown(*, incoming_id: str, markdown: str) -> str:
    return await aupload_file_to_minio(
        MinIOClient.KB_BUCKETS["parsed"], f"incoming/{incoming_id}/parsed.md", markdown.encode("utf-8")
    )


async def _download_markdown(markdown_url: str) -> str:
    bucket_name, object_name = parse_minio_url(markdown_url)
    return (await get_minio_client().adownload_file(bucket_name, object_name)).decode("utf-8", errors="replace")


async def _set_progress(context: TaskContext | None, percent: float, message: str) -> None:
    if context is not None:
        await context.set_progress(percent, message)


def _markdown_bundle(parsed_files: list[dict[str, Any]]) -> str:
    return "\n\n".join(f"## 文件：{item['file'].filename}\n\n{item['markdown']}" for item in parsed_files)


def _group_by_token_budget(items: list[str], budget: int) -> list[list[str]]:
    groups: list[list[str]] = []
    current: list[str] = []
    current_tokens = 0
    for item in items:
        item_tokens = count_tokens(item)
        if current and current_tokens + item_tokens > budget:
            groups.append(current)
            current = []
            current_tokens = 0
        current.append(item)
        current_tokens += item_tokens
    if current:
        groups.append(current)
    return groups


def _safe_object_segment(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in (value or "").strip())
    return cleaned or "production"


def _effective_classification(document) -> str:
    return document.confirmed_classification or document.ai_classification or "uncategorized"


def _trusted_extraction_classifications(result: IncomingDocumentClassificationResult) -> list[str]:
    return _valid_extraction_classifications(
        [
            item.classification
            for item in result.additional_classifications
            if item.confidence >= MULTI_CLASSIFICATION_CONFIDENCE_THRESHOLD and item.evidence.strip()
        ],
        result.classification,
    )


def _valid_extraction_classifications(labels: list[str] | None, primary: str | None) -> list[str]:
    classifications: list[str] = []
    for value in [primary, *(labels or [])]:
        classification = document_category_id(value)
        if classification and classification not in classifications:
            classifications.append(classification)
    if any(classification != "general" for classification in classifications):
        classifications = [classification for classification in classifications if classification != "general"]
    return classifications


def _validated_classification_result(
    result: IncomingDocumentClassificationResult, source_text: str
) -> IncomingDocumentClassificationResult:
    classification = document_category_id(result.classification)
    if classification is None:
        raise ValueError(f"Classification is not configured: {result.classification}")
    result.classification = classification
    result.summary = result.summary.strip()
    if not result.summary:
        raise ValueError("Incoming document summary is empty")
    # 主分类和摘要不能因为本地模型把依据轻微改写而整体失败；无法定位时不发布伪原文。
    result.classification_evidence = find_source_quote(result.classification_evidence, source_text)
    result.additional_classifications = _merge_additional_classifications(
        [
            item.model_copy(update={"evidence": evidence})
            for item in result.additional_classifications
            if (evidence := find_source_quote(item.evidence, source_text)) is not None
            and item.confidence >= MULTI_CLASSIFICATION_CONFIDENCE_THRESHOLD
            and _valid_extraction_classifications([item.classification], None)
        ],
        result.classification,
    )
    return result


def _merge_additional_classifications(
    items: list[AdditionalClassification], primary: str
) -> list[AdditionalClassification]:
    merged: dict[str, AdditionalClassification] = {}
    for item in items:
        classification = document_category_id(item.classification)
        evidence = item.evidence.strip()
        if (
            classification is None
            or classification == primary
            or not evidence
            or item.confidence < MULTI_CLASSIFICATION_CONFIDENCE_THRESHOLD
        ):
            continue
        normalized = item.model_copy(update={"classification": classification, "evidence": evidence})
        if classification not in merged or normalized.confidence > merged[classification].confidence:
            merged[classification] = normalized
    valid_labels = set(_valid_extraction_classifications(list(merged), primary))
    return [item for classification, item in merged.items() if classification in valid_labels]
