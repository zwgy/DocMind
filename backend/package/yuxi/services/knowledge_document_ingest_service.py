from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from yuxi import knowledge_base
from yuxi.knowledge.factory import KnowledgeBaseFactory
from yuxi.services.task_service import TaskContext, tasker
from yuxi.utils import logger

TaskCallback = Callable[[dict[str, Any]], Awaitable[Any] | Any]
TaskFailureCallback = Callable[[BaseException], Awaitable[Any] | Any]


class KnowledgeDocumentIngestService:
    """知识库文档入库编排；只处理知识库文件记录、解析和索引。"""

    def __init__(
        self,
        *,
        knowledge=knowledge_base,
        tasker=tasker,
    ):
        self.knowledge = knowledge
        self.tasker = tasker

    async def ensure_database_supports_documents(self, kb_id: str, operation: str) -> dict[str, Any]:
        database = await self._get_database_info(kb_id)
        if not database:
            raise ValueError(f"知识库 {kb_id} 不存在")
        kb_type = (database.get("kb_type") or "").lower()
        if kb_type:
            # 只读连接器不能接收上传文件，入口处直接拒绝比任务中失败更清楚。
            kb_class = KnowledgeBaseFactory.get_kb_class(kb_type)
            if not kb_class.supports_documents:
                raise ValueError(f"{database.get('name') or kb_type} 只支持检索，不支持{operation}")
        return database

    async def enqueue_ingest(
        self,
        *,
        kb_id: str,
        items: list[str],
        params: dict[str, Any],
        operator_id: str | None,
        task_name: str | None = None,
        on_success: TaskCallback | None = None,
        on_failure: TaskFailureCallback | None = None,
    ) -> dict[str, Any]:
        database = await self._get_database_info(kb_id) or {"name": kb_id}

        async def run_ingest(context: TaskContext):
            try:
                # 任务里串行完成添加记录、解析、可选索引，保持知识库文件状态流转一致。
                result = await self.run_ingest(
                    kb_id=kb_id,
                    items=items,
                    params=params,
                    operator_id=operator_id,
                    context=context,
                )
                if on_success is not None:
                    await _maybe_await(on_success(result))
                return result
            except (Exception, asyncio.CancelledError) as exc:
                if on_failure is not None:
                    await _maybe_await(on_failure(exc))
                raise

        task = await self.tasker.enqueue(
            name=task_name or f"知识库文档处理 ({database.get('name') or kb_id})",
            task_type="knowledge_ingest",
            payload={
                "kb_id": kb_id,
                "items": items,
                "params": params,
                "content_type": params.get("content_type", "file"),
            },
            coroutine=run_ingest,
        )
        return {"message": "任务已提交，请在任务中心查看进度", "status": "queued", "task_id": task.id}

    async def run_ingest(
        self,
        *,
        kb_id: str,
        items: list[str],
        params: dict[str, Any],
        operator_id: str | None,
        context: TaskContext,
    ) -> dict[str, Any]:
        await context.set_message("任务初始化")
        await context.set_progress(5.0, "准备处理文档")

        total = len(items)
        auto_index = bool(params.get("auto_index", False))
        indexing_params = _indexing_params(params)
        processed_items: list[dict[str, Any] | None] = [None] * total
        added_files: list[dict[str, Any]] = []

        try:
            await context.set_message("第一阶段：添加文件记录")
            for idx, item in enumerate(items, 1):
                await context.raise_if_cancelled()
                await context.set_progress(5.0 + (idx / total) * 25.0, f"[1/3] 添加记录 {idx}/{total}")

                try:
                    file_meta = await self.knowledge.add_file_record(
                        kb_id,
                        item,
                        params=_params_for_item(item, params),
                        operator_id=operator_id,
                    )
                    added_files.append(
                        {
                            "index": idx - 1,
                            "item": item,
                            "file_id": file_meta["file_id"],
                            "file_meta": file_meta,
                        }
                    )
                except Exception as add_error:
                    logger.error(f"添加文件记录失败 {item}: {add_error}")
                    processed_items[idx - 1] = {
                        "item": item,
                        "status": "failed",
                        "error": f"添加记录失败: {str(add_error)}",
                        "error_type": "timeout" if isinstance(add_error, TimeoutError) else "add_failed",
                    }

            await context.set_message("第二阶段：解析文件")
            parse_end = 60.0 if auto_index else 95.0
            parse_total = len(added_files)
            for idx, record in enumerate(added_files, 1):
                await context.raise_if_cancelled()
                await context.set_progress(
                    30.0 + (idx / parse_total) * (parse_end - 30.0),
                    f"[2/3] 解析文件 {idx}/{parse_total}",
                )

                item = record["item"]
                file_id = record["file_id"]
                try:
                    file_meta = await self.knowledge.parse_file(kb_id, file_id, operator_id=operator_id)
                    record["file_meta"] = file_meta
                    if not auto_index or file_meta.get("status") != "parsed":
                        processed_items[record["index"]] = file_meta
                except Exception as parse_error:
                    logger.error(f"解析文件失败 {item} (file_id={file_id}): {parse_error}")
                    processed_items[record["index"]] = {
                        "item": item,
                        "status": "failed",
                        "error": f"解析失败: {str(parse_error)}",
                        "error_type": "timeout" if isinstance(parse_error, TimeoutError) else "parse_failed",
                    }

            if auto_index:
                await context.set_message("第三阶段：自动入库")
                parsed_files = [record for record in added_files if record["file_meta"].get("status") == "parsed"]
                total_parsed = len(parsed_files)

                for idx, record in enumerate(parsed_files, 1):
                    await context.raise_if_cancelled()
                    await context.set_progress(
                        60.0 + (idx / total_parsed) * 35.0, f"[3/3] 入库文件 {idx}/{total_parsed}"
                    )

                    item = record["item"]
                    file_id = record["file_id"]
                    try:
                        await self.knowledge.update_file_params(
                            kb_id, file_id, indexing_params, operator_id=operator_id
                        )
                        result = await self.knowledge.index_file(
                            kb_id,
                            file_id,
                            operator_id=operator_id,
                            params=indexing_params,
                        )
                        processed_items[record["index"]] = result
                    except Exception as index_error:
                        logger.error(f"自动入库失败 {item} (file_id={file_id}): {index_error}")
                        processed_items[record["index"]] = {
                            "item": item,
                            "status": "failed",
                            "error": f"入库失败: {str(index_error)}",
                            "error_type": "index_failed",
                        }
        except asyncio.CancelledError:
            current_task = asyncio.current_task()
            if current_task is not None and current_task.cancelling():
                current_task.uncancel()
            message = "任务已取消" if context.is_cancel_requested() else "任务执行超时"
            await context.set_progress(100.0, message)
            raise
        except Exception as task_error:
            logger.exception(f"Task processing failed: {task_error}")
            await context.set_progress(100.0, f"任务处理失败: {str(task_error)}")
            raise

        final_items = [
            item
            if item is not None
            else {
                "item": items[index],
                "status": "failed",
                "error": "文件未处理",
                "error_type": "not_processed",
            }
            for index, item in enumerate(processed_items)
        ]
        failed_count = len([item for item in final_items if _is_failed_item(item)])

        summary = {
            "kb_id": kb_id,
            "item_type": "文件",
            "submitted": total,
            "failed": failed_count,
        }
        message = f"文件处理完成，失败 {failed_count} 个" if failed_count else "文件处理完成"
        result_payload = summary | {"items": final_items}
        await context.set_result(result_payload)
        await context.set_progress(100.0, message)

        if failed_count:
            raise RuntimeError(message)
        return result_payload

    async def _get_database_info(self, kb_id: str) -> dict[str, Any] | None:
        if hasattr(self.knowledge, "get_database_info"):
            return await self.knowledge.get_database_info(kb_id)
        return {"name": kb_id}


def _indexing_params(params: dict[str, Any]) -> dict[str, Any]:
    indexing_params: dict[str, Any] = {}
    chunk_preset_id = params.get("chunk_preset_id")
    if chunk_preset_id:
        indexing_params["chunk_preset_id"] = chunk_preset_id

    chunk_parser_config = params.get("chunk_parser_config")
    if isinstance(chunk_parser_config, dict):
        indexing_params["chunk_parser_config"] = chunk_parser_config
    return indexing_params


def _params_for_item(item: str, params: dict[str, Any]) -> dict[str, Any]:
    source_paths = params.get("source_paths")
    item_params = dict(params)
    item_params.pop("source_paths", None)
    # 来文任务恢复标记只用于 Tasker 启动对账，不能写入知识库文件参数。
    item_params.pop("_incoming_document", None)
    if isinstance(source_paths, dict) and source_paths.get(item):
        # source_paths 是批量参数，进入单文件 add_file_record 时要转换成当前文件的 source_path。
        item_params["source_path"] = source_paths[item]
    return item_params


def _is_failed_item(item: dict[str, Any]) -> bool:
    return item.get("status") == "failed" or bool(item.get("error"))


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value
