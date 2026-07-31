from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from scripts.calibrate_context_budget import _parse_tool_counts, run_calibration


class _CalibrationModel:
    def __init__(self) -> None:
        self.model_name = "calibration-test"
        self.openai_api_base = "http://model.test/v1"
        self.profile = {"max_input_tokens": 8_192, "min_output_reserve_tokens": 512, "context_safety_tokens": 128}
        self.bound_tool_counts: list[int] = []

    def bind_tools(self, tools):
        self.bound_tool_counts.append(len(tools))
        return self

    def invoke(self, _messages):
        return AIMessage(
            content="ok",
            usage_metadata={"input_tokens": 500, "output_tokens": 10, "total_tokens": 510},
            response_metadata={"finish_reason": "stop"},
        )


@pytest.mark.unit
def test_calibration_report_uses_synthetic_tool_cases_and_bucketed_gap() -> None:
    model = _CalibrationModel()

    report = run_calibration(
        model=model,
        model_spec="openai:calibration-test",
        context_window=32_768,
        tool_counts=[0, 2],
        probe_near_limit=False,
    )

    assert report["context_window"] == 32_768
    assert report["prompt_budget"] == 32_128
    assert [case["tool_count"] for case in report["cases"]] == [0, 2]
    assert model.bound_tool_counts == [2]
    assert all(case["provider_input_tokens"] == 500 for case in report["cases"])
    assert report["max_positive_gap_by_bucket"]
    assert "synthetic-near-limit" not in [case["name"] for case in report["cases"]]


@pytest.mark.unit
def test_calibration_near_limit_probe_is_explicit_and_tool_counts_are_validated() -> None:
    model = _CalibrationModel()

    report = run_calibration(
        model=model,
        model_spec="openai:calibration-test",
        context_window=8_192,
        tool_counts=[0],
        probe_near_limit=True,
    )

    assert report["probe_near_limit"] is True
    assert report["cases"][-1]["name"] == "synthetic-near-limit"
    assert _parse_tool_counts("0, 5,20") == [0, 5, 20]
    with pytest.raises(Exception, match="must not be empty"):
        _parse_tool_counts("")
