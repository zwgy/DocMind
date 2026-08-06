from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from yuxi.repositories.incoming_document_repository import (
    IncomingDocumentAuditReferenceError,
    IncomingDocumentRepository,
)
from yuxi.services.incoming_task_candidate_service import IncomingTaskCandidateService
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_knowledge import (
    DocumentBusinessExtractionItem,
    DocumentBusinessExtractionResult,
    DocumentBusinessExtractionRun,
    IncomingDocument,
)
from yuxi.storage.postgres.models_scheduled_jobs import (
    IncomingTaskBatch,
    ScheduledJob,
    ScheduledJobAuditLog,
    ScheduledJobCandidate,
    ScheduledJobRecipient,
)


async def _cleanup(*, incoming_id: str, run_id: str, user_uids: list[str]) -> None:
    """按外键逆序清理本用例数据，避免影响共享 Compose 数据库。"""
    async with pg_manager.get_async_session_context() as session:
        candidate_ids = list(
            (
                await session.scalars(
                    select(ScheduledJobCandidate.id).where(ScheduledJobCandidate.incoming_id == incoming_id)
                )
            ).all()
        )
        job_ids = list(
            (
                await session.scalars(
                    select(ScheduledJob.id).where(ScheduledJob.source_candidate_id.in_(candidate_ids))
                )
            ).all()
        )
        if job_ids:
            await session.execute(
                delete(ScheduledJobRecipient).where(ScheduledJobRecipient.scheduled_job_id.in_(job_ids))
            )
            await session.execute(
                delete(ScheduledJobAuditLog).where(ScheduledJobAuditLog.scheduled_job_id.in_(job_ids))
            )
            await session.execute(delete(ScheduledJob).where(ScheduledJob.id.in_(job_ids)))
        if candidate_ids:
            await session.execute(
                delete(ScheduledJobAuditLog).where(ScheduledJobAuditLog.candidate_id.in_(candidate_ids))
            )
            await session.execute(delete(ScheduledJobCandidate).where(ScheduledJobCandidate.id.in_(candidate_ids)))
        await session.execute(delete(IncomingTaskBatch).where(IncomingTaskBatch.incoming_id == incoming_id))
        result_ids = list(
            (
                await session.scalars(
                    select(DocumentBusinessExtractionResult.id).where(
                        DocumentBusinessExtractionResult.run_id == run_id
                    )
                )
            ).all()
        )
        if result_ids:
            await session.execute(
                delete(DocumentBusinessExtractionItem).where(DocumentBusinessExtractionItem.result_id.in_(result_ids))
            )
        await session.execute(
            delete(DocumentBusinessExtractionResult).where(DocumentBusinessExtractionResult.run_id == run_id)
        )
        await session.execute(
            delete(DocumentBusinessExtractionRun).where(DocumentBusinessExtractionRun.run_id == run_id)
        )
        await session.execute(delete(IncomingDocument).where(IncomingDocument.incoming_id == incoming_id))
        if user_uids:
            await session.execute(delete(User).where(User.uid.in_(user_uids)))


async def _create_extraction(
    *, incoming_id: str, run_id: str, item_data: dict, created_by: str | None
) -> str:
    async with pg_manager.get_async_session_context() as session:
        session.add(
            IncomingDocument(
                incoming_id=incoming_id,
                source_system="integration-test",
                source_function_id="scheduled-jobs",
                source_document_id=run_id,
                document_metadata={"title": "候选任务验收"},
                status="extracting",
                created_by=created_by,
            )
        )
        session.add(
            DocumentBusinessExtractionRun(
                run_id=run_id,
                document_scope="incoming",
                incoming_id=incoming_id,
                status="success",
                model_spec="integration-test",
            )
        )
        # 模型未声明 ORM relationship，先落库运行记录才能满足结果表的外键。
        await session.flush()
        result = DocumentBusinessExtractionResult(
            run_id=run_id,
            document_scope="incoming",
            incoming_id=incoming_id,
            status="ready",
        )
        session.add(result)
        await session.flush()
        item_id = f"item_{uuid4().hex}"
        session.add(
            DocumentBusinessExtractionItem(
                item_id=item_id,
                result_id=result.id,
                document_scope="incoming",
                incoming_id=incoming_id,
                item_type="task_item",
                data=item_data,
                evidence=[{"source_file_id": "file_main", "quote": "请按时完成检查"}],
            )
        )
    return item_id


@pytest.mark.integration
async def test_candidate_enable_is_idempotent_and_freezes_batch():
    pg_manager.initialize()
    await pg_manager.create_tables()
    await pg_manager.ensure_business_schema()

    suffix = uuid4().hex
    owner_uid = f"candidate_owner_{suffix}"
    recipient_uid = f"candidate_recipient_{suffix}"
    incoming_id = f"inc_candidate_{suffix}"
    run_id = f"run_candidate_{suffix}"
    try:
        async with pg_manager.get_async_session_context() as session:
            now = await IncomingTaskCandidateService(session)._database_now()
            session.add_all(
                [
                    User(uid=owner_uid, username=f"owner_{suffix[:12]}", password_hash="not-used", role="superadmin"),
                    User(uid=recipient_uid, username=f"recipient_{suffix[:12]}", password_hash="not-used", role="user"),
                ]
            )
        await _create_extraction(
            incoming_id=incoming_id,
            run_id=run_id,
            created_by=owner_uid,
            item_data={
                "task_name": "专项检查提醒",
                "notification_title": "检查提醒",
                "notification_content": "请在规定时间前完成专项检查。",
                "schedule": {"kind": "at", "run_at": (now + timedelta(hours=1)).isoformat()},
                "timezone": "Asia/Shanghai",
                "recipient_scope": "named",
                "recipient_names": [f"recipient_{suffix[:12]}"],
                "source_quote": "请在规定时间前完成专项检查。",
                "source_file_id": "file_main",
            },
        )
        async with pg_manager.get_async_session_context() as session:
            service = IncomingTaskCandidateService(session)
            batch = await service.build_batch_from_extraction(incoming_id=incoming_id, extraction_run_id=run_id)
            candidate = await session.scalar(
                select(ScheduledJobCandidate).where(ScheduledJobCandidate.batch_id == batch.id)
            )
            assert batch.status == "ready"
            assert candidate is not None and candidate.validation_errors == []
            first_job = await service.enable_candidate(
                candidate_id=candidate.id,
                actor_uid=owner_uid,
                version=candidate.version,
            )
            second_job = await service.enable_candidate(candidate_id=candidate.id, actor_uid=owner_uid)
            assert first_job is not None and second_job is not None and first_job.id == second_job.id
            assert batch.status == "frozen"
            assert candidate.status == "enabled"
            recipients = list(
                (
                    await session.scalars(
                        select(ScheduledJobRecipient).where(ScheduledJobRecipient.scheduled_job_id == first_job.id)
                    )
                ).all()
            )
            assert [recipient.recipient_uid for recipient in recipients] == [recipient_uid]
    finally:
        await _cleanup(
            incoming_id=incoming_id,
            run_id=run_id,
            user_uids=[owner_uid, recipient_uid],
        )
        await pg_manager.close()


@pytest.mark.integration
async def test_missing_incoming_owner_keeps_invalid_candidate_for_review():
    pg_manager.initialize()
    await pg_manager.create_tables()
    await pg_manager.ensure_business_schema()

    suffix = uuid4().hex
    incoming_id = f"inc_owner_missing_{suffix}"
    run_id = f"run_owner_missing_{suffix}"
    try:
        await _create_extraction(
            incoming_id=incoming_id,
            run_id=run_id,
            created_by=None,
            item_data={
                "task_name": "待补全任务",
                "recipient_scope": "unknown",
                "source_quote": "请落实有关工作。",
                "source_file_id": "file_main",
            },
        )
        async with pg_manager.get_async_session_context() as session:
            batch = await IncomingTaskCandidateService(session).build_batch_from_extraction(
                incoming_id=incoming_id,
                extraction_run_id=run_id,
            )
            candidate = await session.scalar(
                select(ScheduledJobCandidate).where(ScheduledJobCandidate.batch_id == batch.id)
            )
            assert batch.status == "ready"
            assert candidate is not None and candidate.owner_uid is None
            assert {error["code"] for error in candidate.validation_errors} >= {"recipient_forbidden", "required"}
    finally:
        await _cleanup(incoming_id=incoming_id, run_id=run_id, user_uids=[])
        await pg_manager.close()


@pytest.mark.integration
async def test_draft_incoming_can_be_deleted_and_archived_incoming_is_hidden_from_management_list():
    pg_manager.initialize()
    await pg_manager.create_tables()
    await pg_manager.ensure_business_schema()

    suffix = uuid4().hex
    incoming_id = f"inc_lifecycle_{suffix}"
    run_id = f"run_lifecycle_{suffix}"
    try:
        await _create_extraction(
            incoming_id=incoming_id,
            run_id=run_id,
            created_by=None,
            item_data={
                "task_name": "待确认任务",
                "recipient_scope": "unknown",
                "source_quote": "请落实有关工作。",
                "source_file_id": "file_main",
            },
        )
        async with pg_manager.get_async_session_context() as session:
            await IncomingTaskCandidateService(session).build_batch_from_extraction(
                incoming_id=incoming_id,
                extraction_run_id=run_id,
            )

        repository = IncomingDocumentRepository()
        archived = await repository.archive_document(incoming_id, archived_by="integration-admin")
        assert archived is not None and archived.archived_by == "integration-admin"
        items, total = await repository.list_for_management(page=1, page_size=100)
        assert incoming_id not in {item.incoming_id for item in items}
        assert total >= 0

        deleted, _files = await repository.delete_cascade(incoming_id)
        assert deleted is not None
        async with pg_manager.get_async_session_context() as session:
            assert await session.scalar(
                select(ScheduledJobCandidate.id).where(ScheduledJobCandidate.incoming_id == incoming_id)
            ) is None
            assert await session.scalar(select(IncomingTaskBatch.id).where(IncomingTaskBatch.incoming_id == incoming_id)) is None
    finally:
        await _cleanup(incoming_id=incoming_id, run_id=run_id, user_uids=[])
        await pg_manager.close()


@pytest.mark.integration
async def test_incoming_with_candidate_audit_reference_cannot_be_deleted():
    pg_manager.initialize()
    await pg_manager.create_tables()
    await pg_manager.ensure_business_schema()

    suffix = uuid4().hex
    owner_uid = f"candidate_audit_owner_{suffix}"
    incoming_id = f"inc_audit_{suffix}"
    run_id = f"run_audit_{suffix}"
    try:
        async with pg_manager.get_async_session_context() as session:
            session.add(User(uid=owner_uid, username=f"audit_owner_{suffix[:12]}", password_hash="not-used", role="superadmin"))
        await _create_extraction(
            incoming_id=incoming_id,
            run_id=run_id,
            created_by=owner_uid,
            item_data={
                "task_name": "待确认任务",
                "recipient_scope": "unknown",
                "source_quote": "请落实有关工作。",
                "source_file_id": "file_main",
            },
        )
        async with pg_manager.get_async_session_context() as session:
            batch = await IncomingTaskCandidateService(session).build_batch_from_extraction(
                incoming_id=incoming_id,
                extraction_run_id=run_id,
            )
            candidate = await session.scalar(
                select(ScheduledJobCandidate).where(ScheduledJobCandidate.batch_id == batch.id)
            )
            assert candidate is not None
            session.add(
                ScheduledJobAuditLog(
                    id=f"sja_{uuid4().hex}",
                    candidate_id=candidate.id,
                    actor_uid=owner_uid,
                    action="candidate_reviewed",
                )
            )

        with pytest.raises(IncomingDocumentAuditReferenceError) as exc_info:
            await IncomingDocumentRepository().delete_cascade(incoming_id)
        assert exc_info.value.code == "incoming_has_audit_reference"
    finally:
        await _cleanup(incoming_id=incoming_id, run_id=run_id, user_uids=[owner_uid])
        await pg_manager.close()
