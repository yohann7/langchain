from pathlib import Path

import pytest

from knowledge_service.config import KnowledgeSettings
from knowledge_service.errors import KnowledgeUnavailableError
from knowledge_service.errors import IdempotencyConflict
from knowledge_service.ingestion import IngestionService
from knowledge_service.models import SearchHit
from knowledge_service.parsing.registry import DocumentParser
from knowledge_service.retrieval import RetrievalService
from knowledge_service.storage.catalog import CatalogStore
from knowledge_service.storage.sqlite import SqliteDatabase


class _Embedding:
    model_id = "BAAI/bge-m3"
    revision = "fixed"
    dimension = 3

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, _text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    def is_ready(self) -> bool:
        return True


class _Vectors:
    def __init__(self, *, wrong_count: bool = False) -> None:
        self.rows: dict[str, dict[str, object]] = {}
        self.wrong_count = wrong_count
        self.search_limits: list[tuple[int, int]] = []

    def upsert_chunks(self, rows: list[dict[str, object]]) -> int:
        self.rows.update({str(row["chunk_id"]): row for row in rows})
        return len(rows)

    def count_version(self, *, owner_id: str, kb_id: str, version_id: str) -> int:
        count = sum(
            row["owner_id"] == owner_id
            and row["kb_id"] == kb_id
            and row["version_id"] == version_id
            for row in self.rows.values()
        )
        return count - 1 if self.wrong_count and count else count

    def delete_version(self, *, owner_id: str, kb_id: str, version_id: str) -> int:
        keys = [
            key
            for key, row in self.rows.items()
            if row["owner_id"] == owner_id
            and row["kb_id"] == kb_id
            and row["version_id"] == version_id
        ]
        for key in keys:
            del self.rows[key]
        return len(keys)

    def hybrid_search(
        self,
        *,
        owner_id: str,
        kb_ids: list[str],
        version_ids: list[str],
        query: str,
        query_vector: list[float],
        language: str,
        limit: int,
        candidate_limit: int,
    ) -> list[SearchHit]:
        del query, query_vector, language
        self.search_limits.append((limit, candidate_limit))
        hits = []
        for row in self.rows.values():
            if (
                row["owner_id"] == owner_id
                and row["kb_id"] in kb_ids
                and row["version_id"] in version_ids
            ):
                hits.append(SearchHit.from_row(row, score=1.0))
        return hits[:limit]

    def status(self) -> dict[str, object]:
        return {"ready": True}


def _services(tmp_path: Path, vectors: _Vectors):
    imports = tmp_path / "imports"
    imports.mkdir()
    settings = KnowledgeSettings(
        run_dir=tmp_path / "runtime" / "knowledge",
        allowed_roots=[imports],
        embedding_device="cuda:0",
        embedding_dimension=3,
        embedding_revision="fixed",
        rag_chunk_size_chars=200,
        rag_chunk_overlap_chars=20,
    )
    catalog = CatalogStore(SqliteDatabase(settings.database_path))
    embedding = _Embedding()
    parser = DocumentParser(settings)
    return (
        imports,
        settings,
        catalog,
        IngestionService(settings, catalog, parser, embedding, vectors),
        RetrievalService(settings, catalog, embedding, vectors),
    )


def test_ingestion_activates_verified_vectors_and_search_is_owner_scoped(
    tmp_path: Path,
) -> None:
    vectors = _Vectors()
    imports, settings, catalog, ingestion, retrieval = _services(tmp_path, vectors)
    source = imports / "manual.txt"
    source.write_text("GPU embeddings and Milvus hybrid retrieval.", encoding="utf-8")

    result = ingestion.ingest(
        owner_id="alice",
        knowledge_base="personal",
        path=source,
        request_id="request-1",
    )

    assert result.status == "active"
    assert result.chunks == 1
    assert (settings.run_dir / str(result.managed_path)).is_file()
    assert retrieval.search(owner_id="alice", query="hybrid").hits
    assert vectors.search_limits[-1] == (10, 50)
    assert retrieval.search(owner_id="bob", query="hybrid").hits == []

    replay = ingestion.ingest(
        owner_id="alice",
        knowledge_base="personal",
        path=source,
        request_id="request-1",
    )
    assert replay == result
    assert len(vectors.rows) == 1

    unchanged = ingestion.ingest(
        owner_id="alice",
        knowledge_base="personal",
        path=source,
        request_id="request-unchanged",
    )
    assert unchanged.status == "unchanged"
    assert ingestion.ingest(
        owner_id="alice",
        knowledge_base="personal",
        path=source,
        request_id="request-unchanged",
    ) == unchanged
    source.write_text("different payload", encoding="utf-8")
    with pytest.raises(IdempotencyConflict):
        ingestion.ingest(
            owner_id="alice",
            knowledge_base="personal",
            path=source,
            request_id="request-unchanged",
        )

    settings.reindex_marker.write_text("restore-id", encoding="utf-8")
    with pytest.raises(KnowledgeUnavailableError, match="rebuild"):
        retrieval.search(owner_id="alice", query="hybrid")
    with pytest.raises(KnowledgeUnavailableError, match="rebuild"):
        ingestion.ingest(
            owner_id="alice",
            knowledge_base="personal",
            path=source,
            request_id="request-2",
        )


def test_failed_vector_verification_never_activates_version(tmp_path: Path) -> None:
    vectors = _Vectors(wrong_count=True)
    imports, _settings, catalog, ingestion, _retrieval = _services(tmp_path, vectors)
    source = imports / "manual.txt"
    source.write_text("content", encoding="utf-8")

    with pytest.raises(RuntimeError, match="verification"):
        ingestion.ingest(
            owner_id="alice",
            knowledge_base="personal",
            path=source,
            request_id="request-1",
        )

    knowledge_bases = catalog.list_knowledge_bases(owner_id="alice")
    assert catalog.active_version_ids(
        owner_id="alice", kb_ids=[knowledge_bases[0]["kb_id"]]
    ) == []
    assert vectors.rows == {}


def test_failed_reingest_preserves_the_active_managed_document(tmp_path: Path) -> None:
    vectors = _Vectors()
    imports, settings, catalog, ingestion, _retrieval = _services(tmp_path, vectors)
    source = imports / "manual.txt"
    source.write_text("old active content", encoding="utf-8")
    original = ingestion.ingest(
        owner_id="alice",
        knowledge_base="personal",
        path=source,
        request_id="request-1",
    )
    original_managed = settings.run_dir / str(original.managed_path)

    source.write_text("new content that must not become active", encoding="utf-8")
    vectors.wrong_count = True
    with pytest.raises(RuntimeError, match="verification"):
        ingestion.ingest(
            owner_id="alice",
            knowledge_base="personal",
            path=source,
            request_id="request-2",
        )

    document = catalog.get_owned_document(owner_id="alice", document_id=original.doc_id)
    assert document.current_version_id == original.version_id
    assert document.managed_path == original.managed_path
    assert original_managed.read_text(encoding="utf-8") == "old active content"
    assert sorted(path.name for path in original_managed.parents[1].iterdir()) == [
        original.version_id
    ]
