from __future__ import annotations

from typing import Any

from yuxi import config
from yuxi.knowledge.extraction.service import BusinessExtractionService
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.services.task_service import TaskContext, tasker

# 任务类型保留 knowledge 前缀，因为它消费 knowledge_files 的 kb_id/file_id，不处理独立来文。
BUSINESS_EXTRACTION_TASK_TYPE = "knowledge_business_extraction"
ACTIVE_BUSINESS_EXTRACTION_STATUSES = {"pending", "running"}


async def submit_business_extraction_task(
    *,
    kb_id: str,
    file_id: str,
    markdown_file: str,
    model_spec: str | None = None,
    operator_id: str | None = None,
    queue=tasker,
) -> tuple[Any, bool]:
    model = model_spec or config.business_extraction_model or config.default_model

    async def run(context: TaskContext):
        await context.set_progress(5.0, "准备业务结构化抽取")
        record = await KnowledgeFileRepository().get_by_file_id(file_id)
        if record is None or record.kb_id != kb_id:
            raise ValueError(f"File {file_id} not found")
        # 只从知识库文件记录读取 markdown_file，避免和 incoming_documents 的解析产物混用。
        result = await BusinessExtractionService().run_markdown_extraction(
            kb_id=kb_id,
            file_id=file_id,
            markdown_file=markdown_file,
            filename=record.filename or file_id,
            processing_params=record.processing_params or {},
            model_spec=model,
            operator_id=operator_id,
        )
        await context.set_progress(100.0, "业务结构化抽取完成")
        return result

    payload = {"kb_id": kb_id, "file_id": file_id, "markdown_file": markdown_file, "model_spec": model}
    return await queue.enqueue_unique_by_payload(
        name=f"业务结构化抽取 ({file_id})",
        task_type=BUSINESS_EXTRACTION_TASK_TYPE,
        payload=payload,
        payload_match=payload,
        statuses=ACTIVE_BUSINESS_EXTRACTION_STATUSES,
        coroutine=run,
    )
