from __future__ import annotations

import pytest
from langchain_core.exceptions import ContextOverflowError

from yuxi.agents.middlewares.retry import create_model_retry_middleware


@pytest.mark.unit
def test_model_retry_leaves_context_overflow_for_summary_recovery() -> None:
    middleware = create_model_retry_middleware(max_retries=3)

    assert middleware.max_retries == 3
    assert middleware.on_failure == "error"
    assert callable(middleware.retry_on)
    assert middleware.retry_on(ContextOverflowError("input too long")) is False
    assert middleware.retry_on(ConnectionError("temporary")) is True
