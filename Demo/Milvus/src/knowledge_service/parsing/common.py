"""Shared parser safety checks and text normalization."""

from __future__ import annotations

from pathlib import Path
import re
from zipfile import BadZipFile, ZipFile

from knowledge_service.config import KnowledgeSettings


def normalize_text(text: str) -> str:
    value = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    value = "\n".join(line.rstrip() for line in value.split("\n"))
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def detect_language(text: str) -> str:
    sample = text[:4000]
    chinese = sum("\u4e00" <= char <= "\u9fff" for char in sample)
    alphabetic = sum(char.isalpha() for char in sample)
    if chinese and chinese >= max(4, alphabetic // 5):
        return "chinese"
    if alphabetic:
        return "english"
    return "default"


def validate_office_archive(path: Path, settings: KnowledgeSettings) -> None:
    try:
        with ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > settings.maximum_office_archive_entries:
                raise ValueError("Office archive contains too many entries")
            expanded = sum(item.file_size for item in members)
            if expanded > settings.maximum_office_uncompressed_bytes:
                raise ValueError("Office archive expands beyond the configured limit")
            for item in members:
                member = Path(item.filename)
                if member.is_absolute() or ".." in member.parts:
                    raise ValueError("Office archive contains an unsafe path")
    except BadZipFile as exc:
        raise ValueError("Office document is not a valid archive") from exc
