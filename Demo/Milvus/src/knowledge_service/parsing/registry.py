"""Document parser registry."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from knowledge_service.config import KnowledgeSettings
from knowledge_service.errors import UnsupportedDocumentError
from knowledge_service.models import LoadedDocument
from knowledge_service.parsing.common import (
    detect_language,
    normalize_text,
    validate_office_archive,
)
from knowledge_service.parsing.docx import load_docx
from knowledge_service.parsing.pdf import load_pdf
from knowledge_service.parsing.pptx import load_pptx
from knowledge_service.parsing.text import load_text
from knowledge_service.parsing.xlsx import load_xlsx


SUPPORTED_SUFFIXES = {
    ".txt", ".md", ".markdown", ".html", ".htm", ".json", ".csv",
    ".pdf", ".docx", ".xlsx", ".pptx",
}


class DocumentParser:
    def __init__(self, settings: KnowledgeSettings) -> None:
        self.settings = settings

    def load(self, path: str | Path) -> LoadedDocument:
        source = Path(path).expanduser().resolve(strict=True)
        if not source.is_file():
            raise ValueError(f"Document path is not a file: {source}")
        if source.stat().st_size > self.settings.maximum_source_bytes:
            raise ValueError("Document exceeds the configured source size limit")
        suffix = source.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise UnsupportedDocumentError(f"Unsupported document type: {suffix or '<none>'}")
        if suffix in {".docx", ".xlsx", ".pptx"}:
            validate_office_archive(source, self.settings)
        if suffix == ".pdf":
            text = load_pdf(source, maximum_pages=self.settings.maximum_pdf_pages)
            source_type = "pdf"
        elif suffix == ".docx":
            text = load_docx(source)
            source_type = "docx"
        elif suffix == ".xlsx":
            text = load_xlsx(
                source,
                maximum_rows=self.settings.maximum_xlsx_rows_per_sheet,
                maximum_columns=self.settings.maximum_xlsx_columns,
                maximum_nonempty_cells=self.settings.maximum_xlsx_nonempty_cells,
            )
            source_type = "xlsx"
        elif suffix == ".pptx":
            text = load_pptx(
                source,
                maximum_slides=self.settings.maximum_pptx_slides,
                maximum_shapes=self.settings.maximum_pptx_shapes,
            )
            source_type = "pptx"
        else:
            text = load_text(source, suffix)
            source_type = {
                ".md": "markdown", ".markdown": "markdown",
                ".html": "html", ".htm": "html", ".json": "json", ".csv": "csv",
            }.get(suffix, "text")
        normalized = normalize_text(text)
        if not normalized:
            raise ValueError("Document contains no indexable text")
        if len(normalized) > self.settings.maximum_extracted_characters:
            raise ValueError("Extracted text exceeds the configured character limit")
        return LoadedDocument(
            path=source,
            title=source.stem,
            source_type=source_type,
            mime_type=mimetypes.guess_type(source.name)[0] or "text/plain",
            text=normalized,
            language=detect_language(normalized),
        )
