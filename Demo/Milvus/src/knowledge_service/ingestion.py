"""Transactional document ingestion with SQLite-controlled activation."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import shutil
from time import time
from uuid import uuid4

from knowledge_service.config import KnowledgeSettings
from knowledge_service.errors import IngestionDeniedError, KnowledgeUnavailableError
from knowledge_service.models import IngestResult, LoadedDocument, TextChunk
from knowledge_service.parsing.chunking import split_document
from knowledge_service.parsing.redaction import redact_sensitive_content


class IngestionService:
    PARSER_VERSION = "parser-v3"
    CHUNKER_VERSION = "chunker-v3"

    def __init__(self, settings, catalog, parser, embeddings, vectors) -> None:
        self.settings: KnowledgeSettings = settings
        self.catalog = catalog
        self.parser = parser
        self.embeddings = embeddings
        self.vectors = vectors

    def ingest(
        self,
        *,
        owner_id: str,
        knowledge_base: str,
        path: str | Path,
        request_id: str | None = None,
    ) -> IngestResult:
        if self.settings.reindex_marker.exists():
            raise KnowledgeUnavailableError(
                "Knowledge index rebuild is required after a restore"
            )
        source = Path(path).expanduser().resolve(strict=True)
        if not self._path_allowed(source):
            raise IngestionDeniedError("Document is outside the configured imports roots")
        loaded = self.parser.load(source)
        redaction = redact_sensitive_content(loaded.text)
        if redaction.was_redacted and not redaction.has_meaningful_content(
            self.settings.rag_min_non_sensitive_chars
        ):
            raise IngestionDeniedError("Document is almost entirely sensitive")
        loaded = replace(loaded, text=redaction.text)
        content_hash = sha256(loaded.text.encode("utf-8")).hexdigest()
        actual_request_id = request_id or f"ingest-{uuid4().hex}"
        fingerprint = self._request_fingerprint(
            owner_id=owner_id,
            knowledge_base=knowledge_base,
            source=source,
            content_hash=content_hash,
        )
        replay = self.catalog.jobs.replay(
            owner_id=owner_id,
            request_id=actual_request_id,
            request_fingerprint=fingerprint,
        )
        if replay is not None:
            return IngestResult.from_dict(replay)

        kb = self.catalog.ensure_knowledge_base(owner_id=owner_id, name=knowledge_base)
        canonical_path = str(source)
        document = self.catalog.get_document_by_path(
            kb_id=kb.kb_id, canonical_path=canonical_path
        )
        current = self.catalog.current_version(doc_id=document.doc_id) if document else None
        if current and self._same_contract(current, content_hash):
            result = IngestResult(
                status="unchanged",
                kb_id=kb.kb_id,
                doc_id=document.doc_id,
                version_id=current.version_id,
                chunks=current.chunk_count,
                message="Document content and embedding contract are unchanged.",
                sensitivity=redaction.sensitivity,
                redactions=redaction.redaction_count,
                managed_path=document.managed_path,
            )
            self._record_existing_result(
                owner_id=owner_id,
                kb_id=kb.kb_id,
                doc_id=document.doc_id,
                version_id=current.version_id,
                request_id=actual_request_id,
                request_fingerprint=fingerprint,
                result=result,
            )
            return result
        if document is None:
            duplicate = self.catalog.find_active_content_match(
                kb_id=kb.kb_id,
                content_hash=content_hash,
                embedding_model=self.settings.embedding_model,
                embedding_revision=self.settings.embedding_revision,
                embedding_dimension=self.settings.embedding_dimension,
            )
            if duplicate:
                duplicate_document, duplicate_version = duplicate
                result = IngestResult(
                    status="duplicate",
                    kb_id=kb.kb_id,
                    doc_id=duplicate_document.doc_id,
                    version_id=duplicate_version.version_id,
                    chunks=duplicate_version.chunk_count,
                    message="Duplicate content is already active in this knowledge base.",
                    sensitivity=redaction.sensitivity,
                    redactions=redaction.redaction_count,
                    duplicate_of_path=duplicate_document.canonical_path,
                    managed_path=duplicate_document.managed_path,
                )
                self._record_existing_result(
                    owner_id=owner_id,
                    kb_id=kb.kb_id,
                    doc_id=duplicate_document.doc_id,
                    version_id=duplicate_version.version_id,
                    request_id=actual_request_id,
                    request_fingerprint=fingerprint,
                    result=result,
                )
                return result
            document = self.catalog.ensure_document(
                kb_id=kb.kb_id,
                display_name=source.name,
                canonical_path=canonical_path,
                managed_path=None,
                source_type=loaded.source_type,
                mime_type=loaded.mime_type,
            )
        chunks = split_document(
            loaded.text,
            source_type=loaded.source_type,
            chunk_size_chars=self.settings.rag_chunk_size_chars,
            overlap_chars=self.settings.rag_chunk_overlap_chars,
        )
        if not chunks:
            raise ValueError("Document produced no indexable chunks")
        version, job = self.catalog.begin_ingest(
            owner_id=owner_id,
            kb_id=kb.kb_id,
            doc_id=document.doc_id,
            request_id=actual_request_id,
            request_fingerprint=fingerprint,
            content_hash=content_hash,
            parser_version=self.PARSER_VERSION + ("+redacted" if redaction.was_redacted else ""),
            chunker_version=self.CHUNKER_VERSION,
            embedding_model=self.settings.embedding_model,
            embedding_revision=self.settings.embedding_revision,
            embedding_dimension=self.settings.embedding_dimension,
            total_chunks=len(chunks),
        )
        managed_path: str | None = None
        try:
            managed_path = self._store_managed(
                document.doc_id,
                version.version_id,
                loaded,
                redaction.was_redacted,
            )
            vectors = self.embeddings.embed_documents([chunk.text for chunk in chunks])
            rows = build_vector_rows(
                settings=self.settings,
                owner_id=owner_id,
                kb_id=kb.kb_id,
                doc_id=document.doc_id,
                version_id=version.version_id,
                loaded=loaded,
                chunks=chunks,
                vectors=vectors,
                sensitivity=redaction.sensitivity,
                redactions=redaction.redaction_count,
            )
            self.catalog.jobs.update(job_id=job.job_id, stage="writing_milvus")
            written = self.vectors.upsert_chunks(rows)
            self.catalog.jobs.update(
                job_id=job.job_id, stage="verifying", written_chunks=written
            )
            actual = self.vectors.count_version(
                owner_id=owner_id, kb_id=kb.kb_id, version_id=version.version_id
            )
            if actual != len(rows):
                raise RuntimeError(
                    f"Milvus verification returned {actual} chunks; expected {len(rows)}"
                )
            result = IngestResult(
                status="active",
                kb_id=kb.kb_id,
                doc_id=document.doc_id,
                version_id=version.version_id,
                job_id=job.job_id,
                chunks=len(chunks),
                message="Document indexed and activated.",
                sensitivity=redaction.sensitivity,
                redactions=redaction.redaction_count,
                managed_path=managed_path,
            )
            self.catalog.activate_ingest(
                job_id=job.job_id,
                doc_id=document.doc_id,
                version_id=version.version_id,
                managed_path=managed_path,
                result=result.to_dict(),
            )
            if document.managed_path and document.managed_path != managed_path:
                self._discard_managed_path(document.managed_path)
            if current is not None:
                try:
                    self.vectors.delete_version(
                        owner_id=owner_id,
                        kb_id=kb.kb_id,
                        version_id=current.version_id,
                    )
                except Exception:
                    pass
            return result
        except Exception as exc:
            self.catalog.jobs.fail(
                job_id=job.job_id,
                version_id=version.version_id,
                error_code=type(exc).__name__,
                error_message=str(exc),
            )
            try:
                self.vectors.delete_version(
                    owner_id=owner_id,
                    kb_id=kb.kb_id,
                    version_id=version.version_id,
                )
            except Exception:
                pass
            self._discard_managed_version(document.doc_id, version.version_id)
            raise

    def _path_allowed(self, source: Path) -> bool:
        for root in self.settings.allowed_roots:
            candidate = root.expanduser().resolve(strict=False)
            try:
                source.relative_to(candidate)
                return True
            except ValueError:
                continue
        return False

    def _record_existing_result(
        self,
        *,
        owner_id: str,
        kb_id: str,
        doc_id: str,
        version_id: str,
        request_id: str,
        request_fingerprint: str,
        result: IngestResult,
    ) -> None:
        self.catalog.jobs.complete_existing(
            owner_id=owner_id,
            kb_id=kb_id,
            doc_id=doc_id,
            version_id=version_id,
            request_id=request_id,
            request_fingerprint=request_fingerprint,
            stage=result.status,
            total_chunks=result.chunks,
            result=result.to_dict(),
        )

    def _store_managed(
        self,
        doc_id: str,
        version_id: str,
        loaded: LoadedDocument,
        was_redacted: bool,
    ) -> str:
        target_dir = self.settings.documents_path / doc_id / version_id
        target_dir.mkdir(parents=True, exist_ok=True)
        name = f"{loaded.path.name}.redacted.txt" if was_redacted else loaded.path.name
        target = target_dir / name
        if was_redacted:
            target.write_text(loaded.text, encoding="utf-8")
        else:
            shutil.copy2(loaded.path, target)
        return target.relative_to(self.settings.run_dir).as_posix()

    def _discard_managed_version(self, doc_id: str, version_id: str) -> None:
        target = self.settings.documents_path / doc_id / version_id
        shutil.rmtree(target, ignore_errors=True)

    def _discard_managed_path(self, managed_path: str) -> None:
        target = (self.settings.run_dir / managed_path).resolve(strict=False)
        documents = self.settings.documents_path.resolve(strict=False)
        try:
            target.relative_to(documents)
        except ValueError:
            return
        target.unlink(missing_ok=True)
        parent = target.parent
        while parent != documents:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    def _same_contract(self, version, content_hash: str) -> bool:
        return (
            version.content_hash == content_hash
            and version.embedding_model == self.settings.embedding_model
            and version.embedding_revision == self.settings.embedding_revision
            and version.embedding_dimension == self.settings.embedding_dimension
        )

    def _request_fingerprint(
        self, *, owner_id: str, knowledge_base: str, source: Path, content_hash: str
    ) -> str:
        material = "|".join((owner_id, knowledge_base, str(source), content_hash))
        return sha256(material.encode("utf-8")).hexdigest()



def build_vector_rows(
    *, settings: KnowledgeSettings, owner_id: str, kb_id: str, doc_id: str,
    version_id: str, loaded: LoadedDocument, chunks: list[TextChunk],
    vectors: list[list[float]], sensitivity: str, redactions: int,
) -> list[dict[str, object]]:
    if len(chunks) != len(vectors):
        raise ValueError("Embedding count does not match chunk count")
    created_at = int(time() * 1000)
    rows: list[dict[str, object]] = []
    for chunk, vector in zip(chunks, vectors, strict=True):
        chunk_id = sha256(
            f"{kb_id}|{doc_id}|{version_id}|{chunk.index}|{chunk.content_hash}|{settings.embedding_revision}".encode()
        ).hexdigest()
        rows.append({
            "chunk_id": chunk_id, "owner_id": owner_id, "kb_id": kb_id,
            "doc_id": doc_id, "version_id": version_id, "is_active": True,
            "source_type": loaded.source_type, "source_label": loaded.path.name,
            "language": loaded.language, "title": loaded.title,
            "heading_path": chunk.heading_path, "chunk_index": chunk.index,
            "page_from": 0, "page_to": 0, "line_from": chunk.line_from,
            "line_to": chunk.line_to, "content_hash": chunk.content_hash,
            "content": chunk.text, "dense_vector": vector, "created_at": created_at,
            "metadata": {
                "embedding_model": settings.embedding_model,
                "embedding_revision": settings.embedding_revision,
                "sensitivity": sensitivity, "redacted": redactions > 0,
                "redaction_count": redactions,
            },
        })
    return rows
