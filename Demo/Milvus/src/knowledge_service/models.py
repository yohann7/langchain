"""Typed records shared by application and persistence layers."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class KnowledgeBaseRecord:
    kb_id: str
    owner_id: str
    name: str
    description: str
    scope: str
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class DocumentRecord:
    doc_id: str
    kb_id: str
    display_name: str
    canonical_path: str
    managed_path: str | None
    source_type: str
    mime_type: str
    current_version_id: str | None
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class VersionRecord:
    version_id: str
    doc_id: str
    content_hash: str
    parser_version: str
    chunker_version: str
    embedding_model: str
    embedding_revision: str
    embedding_dimension: int
    chunk_count: int
    status: str
    created_at: str
    activated_at: str | None


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    version_id: str
    status: str
    request_id: str


@dataclass(frozen=True)
class LoadedDocument:
    path: Any
    title: str
    source_type: str
    mime_type: str
    text: str
    language: str


@dataclass(frozen=True)
class TextChunk:
    index: int
    text: str
    heading_path: str
    line_from: int
    line_to: int
    content_hash: str


@dataclass(frozen=True)
class SearchHit:
    chunk_id: str
    score: float
    kb_id: str
    doc_id: str
    version_id: str
    source_label: str
    source_type: str
    title: str
    heading_path: str
    chunk_index: int
    line_from: int
    line_to: int
    content: str

    @classmethod
    def from_row(cls, row: dict[str, object], *, score: float) -> "SearchHit":
        return cls(
            chunk_id=str(row["chunk_id"]),
            score=float(score),
            kb_id=str(row["kb_id"]),
            doc_id=str(row["doc_id"]),
            version_id=str(row["version_id"]),
            source_label=str(row["source_label"]),
            source_type=str(row["source_type"]),
            title=str(row["title"]),
            heading_path=str(row.get("heading_path", "")),
            chunk_index=int(row["chunk_index"]),
            line_from=int(row.get("line_from", 0)),
            line_to=int(row.get("line_to", 0)),
            content=str(row["content"]),
        )


@dataclass(frozen=True)
class SearchResult:
    query: str
    kb_ids: list[str] = field(default_factory=list)
    hits: list[SearchHit] = field(default_factory=list)
    backends: tuple[str, ...] = ()


@dataclass(frozen=True)
class IngestResult:
    status: str
    kb_id: str | None = None
    doc_id: str | None = None
    version_id: str | None = None
    job_id: str | None = None
    chunks: int = 0
    message: str = ""
    sensitivity: str = "normal"
    redactions: int = 0
    duplicate_of_path: str | None = None
    managed_path: str | None = field(default=None, compare=True)

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "kb_id": self.kb_id,
            "doc_id": self.doc_id,
            "version_id": self.version_id,
            "job_id": self.job_id,
            "chunks": self.chunks,
            "message": self.message,
            "sensitivity": self.sensitivity,
            "redactions": self.redactions,
            "duplicate_of_path": self.duplicate_of_path,
            "managed_path": self.managed_path,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "IngestResult":
        return cls(
            status=str(value["status"]),
            kb_id=_optional_string(value.get("kb_id")),
            doc_id=_optional_string(value.get("doc_id")),
            version_id=_optional_string(value.get("version_id")),
            job_id=_optional_string(value.get("job_id")),
            chunks=int(value.get("chunks", 0)),
            message=str(value.get("message", "")),
            sensitivity=str(value.get("sensitivity", "normal")),
            redactions=int(value.get("redactions", 0)),
            duplicate_of_path=_optional_string(value.get("duplicate_of_path")),
            managed_path=_optional_string(value.get("managed_path")),
        )


def _optional_string(value: object) -> str | None:
    return None if value is None else str(value)
