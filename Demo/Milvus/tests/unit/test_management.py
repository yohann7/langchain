from pathlib import Path

from knowledge_service.config import KnowledgeSettings
from knowledge_service.management import ManagementService
from knowledge_service.storage.catalog import CatalogStore
from knowledge_service.storage.sqlite import SqliteDatabase


class _ReadyEmbedding:
    model_id = "BAAI/bge-m3"
    revision = "fixed"
    dimension = 3

    def is_ready(self) -> bool:
        return True


class _Vectors:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def status(self):
        return {"ready": True, "collection": "knowledge_chunks_v1"}

    def delete_version(self, *, owner_id: str, kb_id: str, version_id: str) -> int:
        del owner_id, kb_id
        self.deleted.append(version_id)
        return 1


def test_document_disable_enable_delete_is_owner_scoped(tmp_path: Path) -> None:
    settings = KnowledgeSettings(
        run_dir=tmp_path / "runtime",
        embedding_device="cuda:0",
        embedding_dimension=3,
        embedding_revision="fixed",
    )
    catalog = CatalogStore(SqliteDatabase(settings.database_path))
    kb = catalog.ensure_knowledge_base(owner_id="alice", name="personal")
    managed = settings.documents_path / "doc-placeholder" / "manual.txt"
    document = catalog.ensure_document(
        kb_id=kb.kb_id,
        display_name="manual.txt",
        canonical_path="/imports/manual.txt",
        managed_path=None,
        source_type="text",
        mime_type="text/plain",
    )
    managed = settings.documents_path / document.doc_id / "manual.txt"
    managed.parent.mkdir(parents=True)
    managed.write_text("content", encoding="utf-8")
    document = catalog.ensure_document(
        kb_id=kb.kb_id,
        display_name="manual.txt",
        canonical_path="/imports/manual.txt",
        managed_path=managed.relative_to(settings.run_dir).as_posix(),
        source_type="text",
        mime_type="text/plain",
    )
    version, job = catalog.begin_ingest(
        owner_id="alice", kb_id=kb.kb_id, doc_id=document.doc_id,
        request_id="r1", request_fingerprint="f1", content_hash="hash",
        parser_version="p", chunker_version="c", embedding_model="BAAI/bge-m3",
        embedding_revision="fixed", embedding_dimension=3, total_chunks=1,
    )
    catalog.activate_ingest(
        job_id=job.job_id, doc_id=document.doc_id, version_id=version.version_id,
        result={"status": "active"},
    )
    vectors = _Vectors()
    management = ManagementService(settings, catalog, _ReadyEmbedding(), vectors)

    disabled = management.update_document(
        owner_id="alice", document_id=document.doc_id, status="disabled"
    )
    assert disabled["status"] == "disabled"
    assert catalog.active_version_ids(owner_id="alice", kb_ids=[kb.kb_id]) == []

    enabled = management.update_document(
        owner_id="alice", document_id=document.doc_id, status="active"
    )
    assert enabled["status"] == "active"

    deleted = management.delete_document(owner_id="alice", document_id=document.doc_id)
    assert deleted == {
        "document_id": document.doc_id,
        "status": "deleted",
        "cleanup_pending": False,
    }
    assert not managed.exists()
    assert vectors.deleted == [version.version_id]


def test_delete_never_follows_a_managed_path_outside_documents(tmp_path: Path) -> None:
    settings = KnowledgeSettings(run_dir=tmp_path / "runtime", embedding_device="cuda:0")
    catalog = CatalogStore(SqliteDatabase(settings.database_path))
    kb = catalog.ensure_knowledge_base(owner_id="alice", name="personal")
    document = catalog.ensure_document(
        kb_id=kb.kb_id,
        display_name="unsafe.txt",
        canonical_path="/imports/unsafe.txt",
        managed_path="documents/../../outside/keep.txt",
        source_type="text",
        mime_type="text/plain",
    )
    outside = tmp_path / "outside" / "keep.txt"
    outside.parent.mkdir()
    outside.write_text("must survive", encoding="utf-8")
    management = ManagementService(settings, catalog, _ReadyEmbedding(), _Vectors())

    result = management.delete_document(owner_id="alice", document_id=document.doc_id)

    assert result["cleanup_pending"] is True
    assert outside.read_text(encoding="utf-8") == "must survive"
