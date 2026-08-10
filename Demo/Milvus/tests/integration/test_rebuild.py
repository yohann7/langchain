from pathlib import Path

import pytest

from knowledge_service.config import KnowledgeSettings
from knowledge_service.management import ManagementService
from knowledge_service.parsing.registry import DocumentParser
from knowledge_service.storage.catalog import CatalogStore
from knowledge_service.storage.sqlite import SqliteDatabase


class _Embedding:
    model_id = "BAAI/bge-m3"
    revision = "fixed"
    dimension = 3

    def is_ready(self):
        return True

    def embed_documents(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]


class _Vectors:
    def __init__(self):
        self.rows = []

    def status(self):
        return {"ready": True}

    def delete_version(self, **_values):
        self.rows = []
        return 0

    def upsert_chunks(self, rows):
        self.rows = list(rows)
        return len(rows)

    def count_version(self, **_values):
        return len(self.rows)


def test_rebuild_uses_managed_documents_and_clears_marker(tmp_path: Path) -> None:
    settings = KnowledgeSettings(
        run_dir=tmp_path / "runtime", allowed_roots=[tmp_path / "imports"],
        embedding_device="cuda:0", embedding_dimension=3,
        embedding_revision="fixed", rag_chunk_size_chars=200,
        rag_chunk_overlap_chars=20,
    )
    catalog = CatalogStore(SqliteDatabase(settings.database_path))
    kb = catalog.ensure_knowledge_base(owner_id="alice", name="personal")
    document = catalog.ensure_document(
        kb_id=kb.kb_id, display_name="manual.txt", canonical_path="/imports/manual.txt",
        managed_path=None, source_type="text", mime_type="text/plain",
    )
    managed = settings.documents_path / document.doc_id / "manual.txt"
    managed.parent.mkdir(parents=True)
    managed.write_text("managed rebuild content", encoding="utf-8")
    document = catalog.ensure_document(
        kb_id=kb.kb_id, display_name="manual.txt", canonical_path="/imports/manual.txt",
        managed_path=managed.relative_to(settings.run_dir).as_posix(),
        source_type="text", mime_type="text/plain",
    )
    from hashlib import sha256
    content_hash = sha256("managed rebuild content".encode()).hexdigest()
    version, job = catalog.begin_ingest(
        owner_id="alice", kb_id=kb.kb_id, doc_id=document.doc_id,
        request_id="r1", request_fingerprint="f1", content_hash=content_hash,
        parser_version="parser-v3", chunker_version="chunker-v3",
        embedding_model="BAAI/bge-m3", embedding_revision="fixed",
        embedding_dimension=3, total_chunks=1,
    )
    catalog.activate_ingest(
        job_id=job.job_id, doc_id=document.doc_id, version_id=version.version_id,
        result={"status": "active"},
    )
    settings.reindex_marker.write_text("restore-id", encoding="utf-8")
    vectors = _Vectors()
    management = ManagementService(
        settings, catalog, _Embedding(), vectors, parser=DocumentParser(settings)
    )

    result = management.rebuild_index()

    assert result == {"status": "rebuilt", "documents": 1, "chunks": 1}
    assert len(vectors.rows) == 1
    assert not settings.reindex_marker.exists()

    with catalog.database.connect() as connection:
        connection.execute(
            "UPDATE document_versions SET parser_version='legacy-parser' WHERE version_id=?",
            (version.version_id,),
        )
    settings.reindex_marker.write_text("restore-id-2", encoding="utf-8")
    with pytest.raises(ValueError, match="processing contract"):
        management.rebuild_index()
    assert settings.reindex_marker.exists()
