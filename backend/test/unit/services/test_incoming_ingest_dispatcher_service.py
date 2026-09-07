from datetime import timedelta

import pytest

from yuxi.repositories.incoming_ingest_repository import DispatchClaim
from yuxi.services.incoming_ingest_dispatcher_service import enqueue_incoming_claim


class FakeQueue:
    def __init__(self):
        self.calls = []

    async def enqueue_job(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return object()


@pytest.mark.asyncio
async def test_enqueue_claim_uses_a_dedicated_queue_and_idempotent_message_id():
    """误投到默认 ARQ 队列或使用随机 ID 会让历史来文干扰 Agent，且无法识别重复消息。"""
    queue = FakeQueue()
    claim = DispatchClaim(job_id="ij_1", delivery_token="token-1")

    await enqueue_incoming_claim(queue, claim)

    assert queue.calls == [
        (
            ("process_incoming_document_job", "ij_1", "token-1"),
            {
                "_queue_name": "arq:incoming",
                "_job_id": "incoming:ij_1:token-1",
                "_expires": timedelta(hours=1),
            },
        )
    ]
