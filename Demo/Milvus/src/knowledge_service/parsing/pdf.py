"""Bounded PDF parsing."""

from pathlib import Path


def load_pdf(path: Path, *, maximum_pages: int) -> str:
    from pypdf import PdfReader

    reader = PdfReader(path)
    if len(reader.pages) > maximum_pages:
        raise ValueError("PDF exceeds the configured page limit")
    parts: list[str] = []
    for number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if text.strip():
            parts.append(f"[Page {number}]\n{text}")
    return "\n\n".join(parts)
