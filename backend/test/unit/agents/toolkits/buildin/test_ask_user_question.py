"""测试内置 ask_user_question 工具的格式契约。"""

import json

import pytest

from yuxi.agents.toolkits.buildin import tools


def test_ask_user_question_schema_exposes_structured_question_array():
    schema = tools.ask_user_question.args_schema.model_json_schema()
    questions_schema = schema["properties"]["questions"]

    assert questions_schema["type"] == "array"
    assert "anyOf" not in questions_schema
    assert questions_schema["minItems"] == 1
    assert questions_schema["maxItems"] == 5


def test_ask_user_question_interrupt_payload_and_result_format(monkeypatch):
    captured_payloads = []
    expected_answer = {"style": "simple"}

    def fake_interrupt(payload):
        captured_payloads.append(payload)
        return expected_answer

    monkeypatch.setattr(tools, "interrupt", fake_interrupt)

    result = tools.ask_user_question.func(
        questions=[
            {
                "question_id": "style",
                "question": "选择界面风格",
                "options": [
                    {"label": "简洁 (Recommended)", "value": "simple"},
                    {"label": "详细", "value": "detailed"},
                ],
                "multi_select": False,
                "allow_other": False,
            }
        ]
    )

    expected_questions = [
        {
            "question_id": "style",
            "question": "选择界面风格",
            "options": [
                {"label": "简洁 (Recommended)", "value": "simple"},
                {"label": "详细", "value": "detailed"},
            ],
            "multi_select": False,
            "allow_other": False,
        }
    ]

    assert captured_payloads == [{"questions": expected_questions, "source": "ask_user_question"}]
    assert result == {"questions": expected_questions, "answer": expected_answer}


def test_ask_user_question_accepts_json_string_questions(monkeypatch):
    captured_payloads = []

    monkeypatch.setattr(tools, "interrupt", lambda payload: captured_payloads.append(payload) or {"q-1": "A"})

    result = tools.ask_user_question.func(
        questions=json.dumps(
            [
                {
                    "question": "选择一个选项",
                    "options": ["A", "B"],
                    "allow_other": False,
                }
            ],
            ensure_ascii=False,
        )
    )

    assert captured_payloads[0]["source"] == "ask_user_question"
    assert captured_payloads[0]["questions"] == [
        {
            "question_id": "q-1",
            "question": "选择一个选项",
            "options": [{"label": "A", "value": "A"}, {"label": "B", "value": "B"}],
            "multi_select": False,
            "allow_other": False,
        }
    ]
    assert result["answer"] == {"q-1": "A"}


def test_ask_user_question_recovers_concatenated_json_and_label_question(monkeypatch):
    captured_payloads = []
    monkeypatch.setattr(
        tools,
        "interrupt",
        lambda payload: captured_payloads.append(payload) or {"q-1": "中", "q-2": "按责任部门"},
    )
    risk_question = json.dumps(
        [
            {
                "label": "风险等级",
                "options": [
                    {"label": "低", "value": "低"},
                    {"label": "中", "value": "中"},
                    {"label": "高", "value": "高"},
                ],
                "multi_select": False,
                "allow_other": False,
            }
        ],
        ensure_ascii=False,
    )
    dimension_question = json.dumps(
        [
            {
                "label": "整理维度",
                "options": [
                    {"label": "按时间", "value": "按时间"},
                    {"label": "按责任部门", "value": "按责任部门"},
                ],
                "multi_select": False,
                "allow_other": False,
            }
        ],
        ensure_ascii=False,
    )

    result = tools.ask_user_question.invoke({"questions": f"{risk_question}\n{dimension_question}"})

    assert captured_payloads == [
        {
            "source": "ask_user_question",
            "questions": [
                {
                    "question_id": "q-1",
                    "question": "风险等级",
                    "options": [
                        {"label": "低", "value": "低"},
                        {"label": "中", "value": "中"},
                        {"label": "高", "value": "高"},
                    ],
                    "multi_select": False,
                    "allow_other": False,
                },
                {
                    "question_id": "q-2",
                    "question": "整理维度",
                    "options": [
                        {"label": "按时间", "value": "按时间"},
                        {"label": "按责任部门", "value": "按责任部门"},
                    ],
                    "multi_select": False,
                    "allow_other": False,
                },
            ],
        }
    ]
    assert result["answer"] == {"q-1": "中", "q-2": "按责任部门"}


def test_ask_user_question_rejects_empty_questions():
    with pytest.raises(ValueError, match="questions 至少需要包含一个有效问题"):
        tools.ask_user_question.func(questions=[])
