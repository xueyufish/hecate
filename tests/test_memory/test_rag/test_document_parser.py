"""Tests for PPTX/XLSX document parsing (catalog 3.1.1).

Office Open XML parsing uses stdlib zip+XML, so fixtures are built hermetically
with ``zipfile`` — no python-pptx / openpyxl dependency.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from hecate_memory.rag.parser import SUPPORTED_EXTENSIONS, document_parser

_DRAWINGML_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_SPREADSHEETML_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _slide_xml(texts: list[str]) -> str:
    body = "".join(f"<a:t>{text}</a:t>" for text in texts)
    return (
        '<p:sld xmlns:p="p" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        f"<p:txBody>{body}</p:txBody></p:sld>"
    )


def _write_pptx(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("ppt/slides/slide1.xml", _slide_xml(["Title Slide", "Intro"]))
        archive.writestr("ppt/slides/slide2.xml", _slide_xml(["Second"]))
        archive.writestr("ppt/slides/slide10.xml", _slide_xml(["Tenth"]))


def _write_xlsx(path: Path) -> None:
    shared_strings = (
        f'<?xml version="1.0"?><sst xmlns="{_SPREADSHEETML_NS}"><si><t>Name</t></si><si><t>City</t></si></sst>'
    )
    sheet = (
        '<?xml version="1.0"?>'
        f'<worksheet xmlns="{_SPREADSHEETML_NS}">'
        '<row r="1"><c t="s" r="A1"><v>0</v></c><c t="s" r="B1"><v>1</v></c></row>'
        '<row r="2"><c t="inlineStr" r="A2"><is><t>Alice</t></is></c>'
        '<c r="B2"><v>42</v></c><c r="C2"><v></v></c></row>'
        "</worksheet>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", shared_strings)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)


class TestPptxParsing:
    async def test_extracts_text_from_all_slides(self, tmp_path: Path) -> None:
        path = tmp_path / "deck.pptx"
        _write_pptx(path)
        text = await document_parser.parse(str(path))
        assert "Title Slide" in text
        assert "Second" in text
        assert "Tenth" in text

    async def test_slides_ordered_numerically(self, tmp_path: Path) -> None:
        """slide10 must come after slide2 — lexicographic order would not."""
        path = tmp_path / "deck.pptx"
        _write_pptx(path)
        text = await document_parser.parse(str(path))
        assert text.index("Second") < text.index("Tenth")

    async def test_blank_runs_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "deck.pptx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "ppt/slides/slide1.xml",
                _slide_xml(["", "real text", "  "]),
            )
        text = await document_parser.parse(str(path))
        assert text == "real text"

    async def test_corrupt_zip_raises_value_error(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.pptx"
        path.write_text("not a zip")
        with pytest.raises(ValueError, match="Invalid PPTX"):
            await document_parser.parse(str(path))


class TestXlsxParsing:
    async def test_shared_strings_and_inline_cells(self, tmp_path: Path) -> None:
        path = tmp_path / "book.xlsx"
        _write_xlsx(path)
        text = await document_parser.parse(str(path))
        assert "Name\tCity" in text
        assert "Alice\t42" in text

    async def test_empty_cells_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "book.xlsx"
        _write_xlsx(path)
        text = await document_parser.parse(str(path))
        # Row 2's empty C cell must not produce a trailing tab artifact line.
        assert "Alice\t42" in text and "\t\t" not in text

    async def test_corrupt_zip_raises_value_error(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.xlsx"
        path.write_text("not a zip")
        with pytest.raises(ValueError, match="Invalid XLSX"):
            await document_parser.parse(str(path))


class TestSupportedExtensions:
    def test_office_formats_declared(self) -> None:
        assert ".pptx" in SUPPORTED_EXTENSIONS
        assert ".xlsx" in SUPPORTED_EXTENSIONS

    async def test_legacy_binary_formats_still_rejected(self, tmp_path: Path) -> None:
        for suffix in (".ppt", ".xls"):
            path = tmp_path / f"legacy{suffix}"
            path.write_text("x")
            with pytest.raises(ValueError, match="Unsupported file format"):
                await document_parser.parse(str(path))
