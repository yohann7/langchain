"""Knowledge catalog management and index readiness."""

from dataclasses import asdict, replace
from hashlib import sha256
from pathlib import Path


class ManagementService:
    def __init__(self, settings, catalog, embeddings, vectors, *, parser=None) -> None:
        self.settings = settings
        self.catalog = catalog
        self.embeddings = embeddings
        self.vectors = vectors
        self.parser = parser

    def ready(self) -> bool:
        if self.settings.reindex_marker.exists():
            return False
        try:
            return bool(self.embeddings.is_ready()) and bool(
                self.vectors.status().get("ready")
            )
        except Exception:
            return False

    def status(self, *, owner_id: str) -> dict[str, object]:
        knowledge_bases = self.catalog.list_knowledge_bases(owner_id=owner_id)
        try:
            milvus = self.vectors.status()
        except Exception as exc:
            milvus = {"ready": False, "error": str(exc)}
        return {
            "enabled": self.settings.rag_enabled,
            "embedding": {
                "model": self.settings.embedding_model,
                "revision": self.settings.embedding_revision,
                "dimension": self.settings.embedding_dimension,
                "ready": self.embeddings.is_ready(),
            },
            "sqlite": {
                "ready": True,
                "knowledge_bases": len(knowledge_bases),
                "total_documents": self.catalog.active_document_count(owner_id=owner_id),
                "active_chunks": self.catalog.active_chunk_count(owner_id=owner_id),
            },
            "milvus": milvus,
        }

    def list_documents(self, *, owner_id: str) -> list[dict[str, object]]:
        return [asdict(item) for item in self.catalog.list_documents(owner_id=owner_id)]

    def update_document(
        self, *, owner_id: str, document_id: str, status: str
    ) -> dict[str, object]:
        if status not in {"active", "disabled"}:
            raise ValueError("document status must be active or disabled")
        return asdict(
            self.catalog.set_document_status(
                owner_id=owner_id, document_id=document_id, status=status
            )
        )

    def delete_document(self, *, owner_id: str, document_id: str) -> dict[str, object]:
        document = self.catalog.get_owned_document(
            owner_id=owner_id, document_id=document_id
        )
        current = self.catalog.current_version(doc_id=document.doc_id)
        self.catalog.set_document_status(
            owner_id=owner_id, document_id=document_id, status="deleted"
        )
        cleanup_pending = False
        if current is not None:
            try:
                self.vectors.delete_version(
                    owner_id=owner_id,
                    kb_id=document.kb_id,
                    version_id=current.version_id,
                )
            except Exception:
                cleanup_pending = True
        if document.managed_path:
            try:
                self._delete_managed_file(document.managed_path)
            except (ValueError, OSError):
                cleanup_pending = True
        return {
            "document_id": document_id,
            "status": "deleted",
            "cleanup_pending": cleanup_pending,
        }

    def _delete_managed_file(self, managed_path: str) -> None:
        documents = self.settings.documents_path.resolve(strict=False)
        managed = (self.settings.run_dir / managed_path).resolve(strict=False)
        managed.relative_to(documents)
        if managed.is_dir():
            raise ValueError("managed document path must identify a file")
        managed.unlink(missing_ok=True)
        parent = managed.parent
        while parent != documents:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    def rebuild_index(self) -> dict[str, object]:
        if not self.settings.reindex_marker.exists():
            return {"status": "not_required", "documents": 0, "chunks": 0}
        if self.parser is None:
            raise RuntimeError("document parser is required for index rebuild")
        from knowledge_service.ingestion import IngestionService, build_vector_rows
        from knowledge_service.parsing.chunking import split_document

        document_count = 0
        chunk_count = 0
        for record in self.catalog.list_rebuild_documents():
            valid_parser_versions = {
                IngestionService.PARSER_VERSION,
                f"{IngestionService.PARSER_VERSION}+redacted",
            }
            if (
                record["parser_version"] not in valid_parser_versions
                or record["chunker_version"] != IngestionService.CHUNKER_VERSION
            ):
                raise ValueError("processing contract mismatch during rebuild")
            if (
                record["embedding_model"] != self.settings.embedding_model
                or record["embedding_revision"] != self.settings.embedding_revision
                or int(record["embedding_dimension"]) != self.settings.embedding_dimension
            ):
                raise ValueError("Embedding contract mismatch during rebuild")
            managed = (self.settings.run_dir / str(record["managed_path"])).resolve(strict=True)
            managed.relative_to(self.settings.documents_path.resolve(strict=False))
            loaded = self.parser.load(managed)
            loaded = replace(
                loaded,
                path=Path(str(record["display_name"])),
                source_type=str(record["source_type"]),
                mime_type=str(record["mime_type"]),
            )
            content_hash = sha256(loaded.text.encode("utf-8")).hexdigest()
            if content_hash != record["content_hash"]:
                raise ValueError(f"Managed source hash mismatch for {record['doc_id']}")
            chunks = split_document(
                loaded.text,
                source_type=loaded.source_type,
                chunk_size_chars=self.settings.rag_chunk_size_chars,
                overlap_chars=self.settings.rag_chunk_overlap_chars,
            )
            if len(chunks) != int(record["chunk_count"]):
                raise ValueError(f"Chunk contract mismatch for {record['doc_id']}")
            vectors = self.embeddings.embed_documents([chunk.text for chunk in chunks])
            rows = build_vector_rows(
                settings=self.settings, owner_id=str(record["owner_id"]),
                kb_id=str(record["kb_id"]), doc_id=str(record["doc_id"]),
                version_id=str(record["version_id"]), loaded=loaded,
                chunks=chunks, vectors=vectors, sensitivity="restored", redactions=0,
            )
            self.vectors.delete_version(
                owner_id=str(record["owner_id"]), kb_id=str(record["kb_id"]),
                version_id=str(record["version_id"]),
            )
            written = self.vectors.upsert_chunks(rows)
            actual = self.vectors.count_version(
                owner_id=str(record["owner_id"]), kb_id=str(record["kb_id"]),
                version_id=str(record["version_id"]),
            )
            if written != len(rows) or actual != len(rows):
                raise RuntimeError(f"Milvus rebuild verification failed for {record['doc_id']}")
            document_count += 1
            chunk_count += len(rows)
        self.settings.reindex_marker.unlink()
        return {"status": "rebuilt", "documents": document_count, "chunks": chunk_count}
