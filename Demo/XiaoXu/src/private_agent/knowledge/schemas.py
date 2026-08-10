"""Transport DTOs for Knowledge API v1."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class KnowledgeHit:
    doc_id: str
    chunk_id: str
    document_name: str
    location: str
    content: str
    score: float
    knowledge_base: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "KnowledgeHit":
        try:
            chunk_id = value.get("chunk_id", value.get("source_id"))
            if chunk_id is None:
                raise KeyError("chunk_id")
            return cls(
                doc_id=str(value.get("doc_id", "")),
                chunk_id=str(chunk_id),
                document_name=str(value["document_name"]),
                location=str(value.get("location", "")),
                content=str(value["content"]),
                score=float(value["score"]),
                knowledge_base=str(value["knowledge_base"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid knowledge hit") from exc


@dataclass(frozen=True)
class KnowledgeSearchResponse:
    query: str
    hits: list[KnowledgeHit] = field(default_factory=list)
    backends: list[str] = field(default_factory=list)
    request_id: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "KnowledgeSearchResponse":
        try:
            hits = value.get("hits", [])
            backends = value.get("backends", [])
            if not isinstance(hits, list) or not isinstance(backends, list):
                raise TypeError
            return cls(
                query=str(value["query"]),
                hits=[KnowledgeHit.from_dict(hit) for hit in hits],
                backends=[str(item) for item in backends],
                request_id=str(value["request_id"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid knowledge search response") from exc


def _required_bool(value: dict[str, Any], key: str) -> bool:
    selected = value[key]
    if not isinstance(selected, bool):
        raise TypeError(key)
    return selected


def _required_non_negative_int(value: dict[str, Any], key: str) -> int:
    selected = value[key]
    if isinstance(selected, bool) or not isinstance(selected, int) or selected < 0:
        raise TypeError(key)
    return selected


@dataclass(frozen=True)
class KnowledgeEmbeddingStatus:
    model: str
    revision: str
    dimension: int
    ready: bool

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "KnowledgeEmbeddingStatus":
        if not isinstance(value, dict):
            raise TypeError("embedding")
        model = value["model"]
        revision = value["revision"]
        if not isinstance(model, str) or not isinstance(revision, str):
            raise TypeError("embedding metadata")
        return cls(
            model=model,
            revision=revision,
            dimension=_required_non_negative_int(value, "dimension"),
            ready=_required_bool(value, "ready"),
        )


@dataclass(frozen=True)
class KnowledgeSqliteStatus:
    ready: bool
    knowledge_bases: int
    total_documents: int
    active_chunks: int

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "KnowledgeSqliteStatus":
        if not isinstance(value, dict):
            raise TypeError("sqlite")
        return cls(
            ready=_required_bool(value, "ready"),
            knowledge_bases=_required_non_negative_int(value, "knowledge_bases"),
            total_documents=_required_non_negative_int(value, "total_documents"),
            active_chunks=_required_non_negative_int(value, "active_chunks"),
        )


@dataclass(frozen=True)
class KnowledgeStatusResponse:
    enabled: bool
    embedding: KnowledgeEmbeddingStatus
    sqlite: KnowledgeSqliteStatus
    milvus: dict[str, Any]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "KnowledgeStatusResponse":
        try:
            milvus = value["milvus"]
            if not isinstance(milvus, dict):
                raise TypeError("milvus")
            _required_bool(milvus, "ready")
            return cls(
                enabled=_required_bool(value, "enabled"),
                embedding=KnowledgeEmbeddingStatus.from_dict(value["embedding"]),
                sqlite=KnowledgeSqliteStatus.from_dict(value["sqlite"]),
                milvus=dict(milvus),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid knowledge status response") from exc
