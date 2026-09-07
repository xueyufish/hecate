"""Document parser for extracting text from various file formats.

Supports PDF, DOCX, PPTX, XLSX, HTML, Markdown, and plain text files.
Uses docling when available, falls back to simpler parsers.
Office Open XML formats (.pptx/.xlsx) are parsed via zip+XML so text
extraction works without optional dependencies; XML is parsed with
defusedxml when available (uploaded documents are untrusted input).
"""

from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

logger = logging.getLogger(__name__)


def _safe_fromstring(data: bytes) -> ElementTree.Element:
    """Parse XML with defusedxml when installed, else stdlib ElementTree.

    OOXML parts come from uploaded documents — untrusted input — so
    defusedxml's entity-expansion hardening is preferred when present.
    """
    try:
        from defusedxml.ElementTree import fromstring
    except ImportError:
        logger.warning("defusedxml not installed; falling back to stdlib XML parsing.")
        return ElementTree.fromstring(data)  # noqa: S314 — deliberate fallback when defusedxml is absent
    return fromstring(data)


SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".doc",
    ".pptx",
    ".xlsx",
    ".html",
    ".htm",
    ".md",
    ".markdown",
    ".txt",
    ".text",
    ".csv",
    ".json",
    ".xml",
    ".rst",
}

_DRAWINGML_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_SPREADSHEETML_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


class DocumentParser:
    """Parse documents and extract text content.

    Supports multiple file formats with automatic format detection.
    """

    async def parse(self, file_path: str, content_type: str | None = None) -> str:
        """Parse a document and extract text.

        Args:
            file_path: Path to the document file.
            content_type: Optional MIME content type.

        Returns:
            str: Extracted text content.

        Raises:
            ValueError: If file format is not supported.
            FileNotFoundError: If file doesn't exist.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        ext = path.suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported file format: {ext}")

        if ext in {".txt", ".text", ".csv", ".json", ".xml", ".rst"}:
            return self._parse_text(path)
        elif ext in {".md", ".markdown"}:
            return self._parse_markdown(path)
        elif ext in {".html", ".htm"}:
            return self._parse_html(path)
        elif ext in {".pdf"}:
            return await self._parse_pdf(path)
        elif ext in {".docx", ".doc"}:
            return await self._parse_docx(path)
        elif ext in {".pptx"}:
            return self._parse_pptx(path)
        elif ext in {".xlsx"}:
            return self._parse_xlsx(path)
        else:
            return self._parse_text(path)

    def _parse_text(self, path: Path) -> str:
        """Parse plain text files."""
        return path.read_text(encoding="utf-8", errors="ignore")

    def _parse_markdown(self, path: Path) -> str:
        """Parse Markdown files."""
        return path.read_text(encoding="utf-8", errors="ignore")

    def _parse_html(self, path: Path) -> str:
        """Parse HTML files."""
        try:
            from bs4 import BeautifulSoup

            content = path.read_text(encoding="utf-8", errors="ignore")
            soup = BeautifulSoup(content, "html.parser")
            return soup.get_text(separator="\n", strip=True)
        except ImportError:
            logger.warning("beautifulsoup4 not installed. Returning raw HTML.")
            return path.read_text(encoding="utf-8", errors="ignore")

    async def _parse_pdf(self, path: Path) -> str:
        """Parse PDF files."""
        try:
            import pdfplumber

            with pdfplumber.open(path) as pdf:
                text_parts = []
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
                return "\n\n".join(text_parts)
        except ImportError:
            logger.warning("pdfplumber not installed. Trying PyPDF2.")
            try:
                from PyPDF2 import PdfReader

                reader = PdfReader(path)
                text_parts = []
                for page in reader.pages:
                    text = page.extract_text()
                    if text:
                        text_parts.append(text)
                return "\n\n".join(text_parts)
            except ImportError:
                raise ImportError("No PDF parser installed. Install pdfplumber or PyPDF2.") from None

    async def _parse_docx(self, path: Path) -> str:
        """Parse DOCX files."""
        try:
            from docx import Document

            doc = Document(path)
            text_parts = []
            for para in doc.paragraphs:
                if para.text.strip():
                    text_parts.append(para.text)
            return "\n\n".join(text_parts)
        except ImportError:
            raise ImportError("python-docx not installed. Install it with: pip install python-docx") from None

    @staticmethod
    def _parse_pptx(path: Path) -> str:
        """Parse PPTX files via stdlib zip+XML (no python-pptx needed).

        Slides live at ``ppt/slides/slideN.xml``; text runs sit in
        DrawingML ``<a:t>`` elements. Slides are ordered numerically —
        lexicographic sort would put ``slide10`` before ``slide2``.
        """
        slide_pattern = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
        try:
            with zipfile.ZipFile(path) as archive:
                slides = []
                for name in archive.namelist():
                    match = slide_pattern.match(name)
                    if match:
                        slides.append((int(match.group(1)), name))
                slides.sort()
                slide_texts = []
                for _, name in slides:
                    root = _safe_fromstring(archive.read(name))
                    runs = [t.text or "" for t in root.iter(f"{{{_DRAWINGML_NS}}}t")]
                    slide_text = "\n".join(run for run in runs if run.strip())
                    if slide_text:
                        slide_texts.append(slide_text)
        except zipfile.BadZipFile as exc:
            raise ValueError(f"Invalid PPTX file (not a zip archive): {path}") from exc
        return "\n\n".join(slide_texts)

    @staticmethod
    def _parse_xlsx(path: Path) -> str:
        """Parse XLSX files via stdlib zip+XML (no openpyxl needed).

        Cell values live in worksheet XML; shared strings are resolved from
        ``xl/sharedStrings.xml`` (cells with ``t="s"`` hold an index into it),
        inline strings from ``<is><t>`` (cells with ``t="inlineStr"``).
        """
        try:
            with zipfile.ZipFile(path) as archive:
                shared: list[str] = []
                if "xl/sharedStrings.xml" in archive.namelist():
                    root = _safe_fromstring(archive.read("xl/sharedStrings.xml"))
                    for si in root.findall(f"{{{_SPREADSHEETML_NS}}}si"):
                        shared.append("".join(t.text or "" for t in si.iter(f"{{{_SPREADSHEETML_NS}}}t")))

                sheet_names = sorted(
                    name
                    for name in archive.namelist()
                    if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
                )
                sheet_texts = []
                for name in sheet_names:
                    root = _safe_fromstring(archive.read(name))
                    rows = []
                    for row in root.iter(f"{{{_SPREADSHEETML_NS}}}row"):
                        cells = []
                        for cell in row.findall(f"{{{_SPREADSHEETML_NS}}}c"):
                            cell_type = cell.get("t")
                            if cell_type == "inlineStr":
                                inline = "".join(t.text or "" for t in cell.iter(f"{{{_SPREADSHEETML_NS}}}t"))
                                if inline.strip():
                                    cells.append(inline)
                                continue
                            value = cell.find(f"{{{_SPREADSHEETML_NS}}}v")
                            if value is None or value.text is None:
                                continue
                            if cell_type == "s":
                                index = int(value.text)
                                cells.append(shared[index] if index < len(shared) else "")
                            else:
                                cells.append(value.text)
                        if cells:
                            rows.append("\t".join(cells))
                    sheet_text = "\n".join(rows)
                    if sheet_text:
                        sheet_texts.append(sheet_text)
        except zipfile.BadZipFile as exc:
            raise ValueError(f"Invalid XLSX file (not a zip archive): {path}") from exc
        return "\n\n".join(sheet_texts)


document_parser = DocumentParser()
