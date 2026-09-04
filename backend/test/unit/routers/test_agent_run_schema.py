import pytest
from pydantic import ValidationError

from server.routers.agent_router import AgentEvalRunCreate, AgentRunCreate


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (
            AgentRunCreate,
            {"query": "test", "agent_id": "default", "thread_id": "thread-1"},
        ),
        (
            AgentEvalRunCreate,
            {"query": "test", "agent_slug": "default"},
        ),
    ],
)
def test_agent_run_request_rejects_oversized_request_id(model, payload) -> None:
    with pytest.raises(ValidationError, match="meta.request_id"):
        model(**payload, meta={"request_id": "x" * 65})


def test_agent_run_request_rejects_oversized_resume_request_id() -> None:
    with pytest.raises(ValidationError, match="resume_request_id"):
        AgentRunCreate(
            query="test",
            agent_id="default",
            thread_id="thread-1",
            resume_request_id="x" * 65,
        )
