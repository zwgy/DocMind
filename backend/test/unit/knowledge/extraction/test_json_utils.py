import pytest

from yuxi.knowledge.extraction.json_utils import parse_json_object


def test_parse_json_object_accepts_plain_json():
    assert parse_json_object('{"items": []}') == {"items": []}


def test_parse_json_object_extracts_fenced_json():
    assert parse_json_object('```json\n{"items":[{"name":"x"}]}\n```') == {"items": [{"name": "x"}]}


def test_parse_json_object_rejects_non_object_json():
    with pytest.raises(ValueError):
        parse_json_object("[1, 2, 3]")
