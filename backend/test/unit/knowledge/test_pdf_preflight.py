from pathlib import Path
from types import SimpleNamespace

import pytest

import yuxi.knowledge.parser.pdf_preflight as pdf_preflight
import yuxi.knowledge.parser.unified as parser_unified
from yuxi.knowledge.parser.base import DocumentParserException


def test_pdf_preflight_reports_unloadable_page_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded_pages: list[int] = []
    closed = False

    class FakeDocument:
        is_encrypted = False
        needs_pass = False
        page_count = 3

        def load_page(self, page_index: int):
            loaded_pages.append(page_index)
            if page_index == 1:
                raise RuntimeError("cycle in page tree")
            return SimpleNamespace(rect=(0, 0, 100, 100))

        def close(self) -> None:
            nonlocal closed
            closed = True

    monkeypatch.setattr(pdf_preflight.fitz, "open", lambda _path: FakeDocument())

    with pytest.raises(DocumentParserException) as error:
        pdf_preflight.validate_pdf_page_tree_loadable("broken.pdf")

    assert error.value.service_name == "pdf_preflight"
    assert error.value.status_code == "invalid_pdf_page_tree"
    assert "第 2 个页槽" in error.value.message
    assert "cycle in page tree" in error.value.message
    assert loaded_pages == [0, 1, 2]
    assert closed is True


@pytest.mark.asyncio
async def test_pdf_preflight_runs_before_pdf_parser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf_path = tmp_path / "document.pdf"
    pdf_path.write_bytes(b"fake pdf")
    call_order: list[str] = []

    def fake_preflight(file_path: Path) -> None:
        assert file_path == pdf_path
        call_order.append("preflight")

    async def fake_parse_pdf(file_path: str, params=None) -> str:
        assert file_path == str(pdf_path)
        call_order.append("parse")
        return "parsed"

    monkeypatch.setattr(parser_unified, "validate_pdf_page_tree_loadable", fake_preflight)
    monkeypatch.setattr(parser_unified, "parse_pdf_async", fake_parse_pdf)

    markdown, file_ext, artifacts = await parser_unified._process_file_to_markdown_core(str(pdf_path))

    assert markdown == "parsed"
    assert file_ext == ".pdf"
    assert artifacts == {}
    assert call_order == ["preflight", "parse"]


@pytest.mark.asyncio
async def test_pdf_preflight_failure_stops_pdf_parser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf_path = tmp_path / "broken.pdf"
    pdf_path.write_bytes(b"fake pdf")
    parser_called = False

    def fail_preflight(_file_path: Path) -> None:
        raise DocumentParserException("bad page tree", "pdf_preflight", "invalid_pdf_page_tree")

    async def fake_parse_pdf(_file_path: str, params=None) -> str:
        nonlocal parser_called
        parser_called = True
        return "unexpected"

    monkeypatch.setattr(parser_unified, "validate_pdf_page_tree_loadable", fail_preflight)
    monkeypatch.setattr(parser_unified, "parse_pdf_async", fake_parse_pdf)

    with pytest.raises(DocumentParserException, match="bad page tree"):
        await parser_unified._process_file_to_markdown_core(str(pdf_path))

    assert parser_called is False
