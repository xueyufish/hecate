"""Document parser for extracting text from various file formats.

Supports PDF, DOCX, HTML, Markdown, and plain text files.
Uses docling when available, falls back to simpler parsers.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".doc",
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


document_parser = DocumentParser()
