from __future__ import annotations

from pathlib import Path

import pytest

from yuxi.services.visualization_service import VisualizationError, _reserve_output, _validate_svg


def test_validate_svg_accepts_local_fragment_reference(tmp_path: Path) -> None:
    output = tmp_path / "chart.svg"
    output.write_text('<svg xmlns="http://www.w3.org/2000/svg"><use href="#legend" /></svg>', encoding="utf-8")

    _validate_svg(output)


@pytest.mark.parametrize(
    "content",
    [
        '<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><svg>&xxe;</svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.com/image" /></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><rect onclick="alert(1)" /></svg>',
    ],
)
def test_validate_svg_rejects_active_or_external_content(tmp_path: Path, content: str) -> None:
    output = tmp_path / "unsafe.svg"
    output.write_text(content, encoding="utf-8")

    with pytest.raises(VisualizationError):
        _validate_svg(output)


def test_reserve_output_never_overwrites_existing_artifact(tmp_path: Path) -> None:
    (tmp_path / "report.svg").write_text("previous", encoding="utf-8")

    reserved, virtual_path = _reserve_output(tmp_path, "report")

    assert reserved.name == "report-2.svg"
    assert reserved.read_bytes() == b""
    assert virtual_path.endswith("/report-2.svg")
