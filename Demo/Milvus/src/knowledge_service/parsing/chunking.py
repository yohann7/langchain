"""Deterministic heading-aware character chunking."""

from hashlib import sha256
import re

from knowledge_service.models import TextChunk


def split_document(
    text: str,
    *,
    source_type: str,
    chunk_size_chars: int,
    overlap_chars: int,
) -> list[TextChunk]:
    del source_type
    lines = text.splitlines()
    chunks: list[TextChunk] = []
    heading = ""
    buffer: list[str] = []
    start_line = 1

    def flush(end_line: int) -> None:
        nonlocal buffer, start_line
        value = "\n".join(buffer).strip()
        if not value:
            buffer = []
            return
        chunks.append(
            TextChunk(
                index=len(chunks),
                text=value,
                heading_path=heading,
                line_from=start_line,
                line_to=end_line,
                content_hash=sha256(value.encode("utf-8")).hexdigest(),
            )
        )
        overlap = value[-overlap_chars:] if overlap_chars else ""
        buffer = [overlap] if overlap.strip() else []
        start_line = end_line

    for number, line in enumerate(lines, start=1):
        match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if match:
            if buffer:
                flush(max(start_line, number - 1))
                buffer = []
            heading = match.group(1).strip()
        projected = len("\n".join((*buffer, line)))
        if buffer and projected > chunk_size_chars:
            flush(max(start_line, number - 1))
        if not buffer:
            start_line = number
        buffer.append(line)
        while len("\n".join(buffer)) > chunk_size_chars:
            value = "\n".join(buffer)
            head = value[:chunk_size_chars]
            tail = value[max(0, chunk_size_chars - overlap_chars):]
            buffer = [head]
            flush(number)
            buffer = [tail]
            start_line = number
    if buffer:
        flush(len(lines) or 1)
    return chunks
