"""Authoritative user-scoped catalog and version state."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import sqlite3
from uuid import uuid4

from knowledge_service.errors import IdempotencyConflict
from knowledge_service.models import (
    DocumentRecord,
    JobRecord,
    KnowledgeBaseRecord,
    VersionRecord,
)
from knowledge_service.storage.sqlite import SqliteDatabase
from knowledge_service.storage.jobs import IngestJobStore


def _now() -> str:
    return datetime.now(UTC).isoformat()


class CatalogStore:
    def __init__(self, database: SqliteDatabase) -> None:
        self.database = database
        self.jobs = IngestJobStore(database)

    def ensure_knowledge_base(
        self, *, owner_id: str, name: str, description: str = ""
    ) -> KnowledgeBaseRecord:
        owner = _required("owner_id", owner_id)
        kb_name = _required("name", name)
        now = _now()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO knowledge_bases(
                    kb_id,owner_id,name,description,scope,status,created_at,updated_at
                ) VALUES(?,?,?,?,?,'active',?,?)
                ON CONFLICT(owner_id,name) DO UPDATE SET
                    description=excluded.description, updated_at=excluded.updated_at
                """,
                (f"kb-{uuid4().hex}", owner, kb_name, description, f"knowledge:{kb_name}", now, now),
            )
            row = connection.execute(
                "SELECT * FROM knowledge_bases WHERE owner_id=? AND name=?",
                (owner, kb_name),
            ).fetchone()
        return _record(KnowledgeBaseRecord, row)

    def ensure_document(
        self,
        *,
        kb_id: str,
        display_name: str,
        canonical_path: str,
        managed_path: str | None,
        source_type: str,
        mime_type: str,
    ) -> DocumentRecord:
        now = _now()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO documents(
                    doc_id,kb_id,display_name,canonical_path,managed_path,
                    source_type,mime_type,current_version_id,status,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,NULL,'disabled',?,?)
                ON CONFLICT(kb_id,canonical_path) DO UPDATE SET
                    display_name=excluded.display_name,
                    managed_path=excluded.managed_path,
                    source_type=excluded.source_type,
                    mime_type=excluded.mime_type,
                    updated_at=excluded.updated_at
                """,
                (
                    f"doc-{uuid4().hex}",
                    _required("kb_id", kb_id),
                    _required("display_name", display_name),
                    _required("canonical_path", canonical_path),
                    managed_path,
                    _required("source_type", source_type),
                    _required("mime_type", mime_type),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM documents WHERE kb_id=? AND canonical_path=?",
                (kb_id, canonical_path),
            ).fetchone()
        return _record(DocumentRecord, row)

    def list_knowledge_bases(self, *, owner_id: str) -> list[dict[str, object]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT bases.*,
                    SUM(CASE WHEN documents.status='active' THEN 1 ELSE 0 END) AS document_count
                FROM knowledge_bases AS bases
                LEFT JOIN documents AS documents ON documents.kb_id=bases.kb_id
                WHERE bases.owner_id=? AND bases.status='active'
                GROUP BY bases.kb_id
                ORDER BY bases.name
                """,
                (owner_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_documents(self, *, owner_id: str) -> list[DocumentRecord]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT documents.* FROM documents
                JOIN knowledge_bases AS bases ON bases.kb_id=documents.kb_id
                WHERE bases.owner_id=? AND documents.status!='deleted'
                ORDER BY documents.display_name
                """,
                (owner_id,),
            ).fetchall()
        return [_record(DocumentRecord, row) for row in rows]

    def get_document_by_path(
        self, *, kb_id: str, canonical_path: str
    ) -> DocumentRecord | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE kb_id=? AND canonical_path=?",
                (kb_id, canonical_path),
            ).fetchone()
        return _record(DocumentRecord, row) if row is not None else None

    def current_version(self, *, doc_id: str) -> VersionRecord | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT versions.* FROM document_versions AS versions
                JOIN documents AS documents ON documents.current_version_id=versions.version_id
                WHERE documents.doc_id=? AND versions.status='active'
                """,
                (doc_id,),
            ).fetchone()
        return _record(VersionRecord, row) if row is not None else None

    def find_active_content_match(
        self,
        *,
        kb_id: str,
        content_hash: str,
        embedding_model: str,
        embedding_revision: str,
        embedding_dimension: int,
    ) -> tuple[DocumentRecord, VersionRecord] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT documents.doc_id AS matched_doc_id,
                       versions.version_id AS matched_version_id
                FROM documents
                JOIN document_versions AS versions
                  ON versions.version_id=documents.current_version_id
                WHERE documents.kb_id=? AND documents.status='active'
                  AND versions.status='active' AND versions.content_hash=?
                  AND versions.embedding_model=? AND versions.embedding_revision=?
                  AND versions.embedding_dimension=?
                LIMIT 1
                """,
                (
                    kb_id, content_hash, embedding_model,
                    embedding_revision, int(embedding_dimension),
                ),
            ).fetchone()
            if row is None:
                return None
            document_row = connection.execute(
                "SELECT * FROM documents WHERE doc_id=?", (row["matched_doc_id"],)
            ).fetchone()
            version_row = connection.execute(
                "SELECT * FROM document_versions WHERE version_id=?",
                (row["matched_version_id"],),
            ).fetchone()
        return _record(DocumentRecord, document_row), _record(VersionRecord, version_row)

    def begin_ingest(
        self,
        *,
        owner_id: str,
        kb_id: str,
        doc_id: str,
        request_id: str,
        request_fingerprint: str,
        content_hash: str,
        parser_version: str,
        chunker_version: str,
        embedding_model: str,
        embedding_revision: str,
        embedding_dimension: int,
        total_chunks: int,
    ) -> tuple[VersionRecord, JobRecord]:
        now = _now()
        version_id = f"ver-{uuid4().hex}"
        job_id = f"job-{uuid4().hex}"
        try:
            with self.database.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO document_versions(
                        version_id,doc_id,content_hash,parser_version,chunker_version,
                        embedding_model,embedding_revision,embedding_dimension,chunk_count,
                        status,created_at,activated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,'staging',?,NULL)
                    """,
                    (
                        version_id, doc_id, content_hash, parser_version, chunker_version,
                        embedding_model, embedding_revision, int(embedding_dimension),
                        int(total_chunks), now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO ingest_jobs(
                        job_id,owner_id,kb_id,doc_id,version_id,request_id,
                        request_fingerprint,status,current_stage,total_chunks,
                        written_chunks,result_json,error_code,error_message,started_at,finished_at
                    ) VALUES(?,?,?,?,?,?,?,'running','embedding',?,0,NULL,NULL,NULL,?,NULL)
                    """,
                    (
                        job_id, owner_id, kb_id, doc_id, version_id, request_id,
                        request_fingerprint, int(total_chunks), now,
                    ),
                )
                version_row = connection.execute(
                    "SELECT * FROM document_versions WHERE version_id=?", (version_id,)
                ).fetchone()
                job_row = connection.execute(
                    "SELECT job_id,version_id,status,request_id FROM ingest_jobs WHERE job_id=?",
                    (job_id,),
                ).fetchone()
        except sqlite3.IntegrityError as exc:
            raise IdempotencyConflict("request_id is already in use") from exc
        return _record(VersionRecord, version_row), _record(JobRecord, job_row)

    def activate_ingest(
        self,
        *,
        job_id: str,
        doc_id: str,
        version_id: str,
        managed_path: str | None = None,
        result: dict[str, object],
    ) -> None:
        now = _now()
        with self.database.connect() as connection:
            current = connection.execute(
                "SELECT current_version_id FROM documents WHERE doc_id=?", (doc_id,)
            ).fetchone()
            old_version_id = current["current_version_id"] if current else None
            if old_version_id and old_version_id != version_id:
                connection.execute(
                    "UPDATE document_versions SET status='superseded' WHERE version_id=?",
                    (old_version_id,),
                )
            connection.execute(
                "UPDATE document_versions SET status='active',activated_at=? WHERE version_id=?",
                (now, version_id),
            )
            if managed_path is None:
                connection.execute(
                    "UPDATE documents SET current_version_id=?,status='active',updated_at=? WHERE doc_id=?",
                    (version_id, now, doc_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE documents SET current_version_id=?,managed_path=?,
                        status='active',updated_at=? WHERE doc_id=?
                    """,
                    (version_id, managed_path, now, doc_id),
                )
            connection.execute(
                """
                UPDATE ingest_jobs SET status='completed',current_stage='active',
                    written_chunks=total_chunks,result_json=?,finished_at=?
                WHERE job_id=?
                """,
                (json.dumps(result, ensure_ascii=False, sort_keys=True), now, job_id),
            )

    def active_version_ids(self, *, owner_id: str, kb_ids: list[str]) -> list[str]:
        if not kb_ids:
            return []
        placeholders = ",".join("?" for _ in kb_ids)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT versions.version_id
                FROM document_versions AS versions
                JOIN documents AS documents ON documents.doc_id=versions.doc_id
                JOIN knowledge_bases AS bases ON bases.kb_id=documents.kb_id
                WHERE bases.owner_id=? AND bases.status='active'
                  AND documents.status='active'
                  AND versions.status='active'
                  AND documents.current_version_id=versions.version_id
                  AND bases.kb_id IN ({placeholders})
                ORDER BY versions.version_id
                """,
                (owner_id, *kb_ids),
            ).fetchall()
        return [str(row["version_id"]) for row in rows]

    def get_owned_document(self, *, owner_id: str, document_id: str) -> DocumentRecord:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT documents.* FROM documents
                JOIN knowledge_bases AS bases ON bases.kb_id=documents.kb_id
                WHERE bases.owner_id=? AND documents.doc_id=? AND documents.status!='deleted'
                """,
                (owner_id, document_id),
            ).fetchone()
        return _record(DocumentRecord, row)

    def set_document_status(
        self, *, owner_id: str, document_id: str, status: str
    ) -> DocumentRecord:
        if status not in {"active", "disabled", "deleted"}:
            raise ValueError("invalid document status")
        self.get_owned_document(owner_id=owner_id, document_id=document_id)
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE documents SET status=?,updated_at=? WHERE doc_id=?",
                (status, _now(), document_id),
            )
            row = connection.execute(
                "SELECT * FROM documents WHERE doc_id=?", (document_id,)
            ).fetchone()
        return _record(DocumentRecord, row)

    def active_document_count(self, *, owner_id: str) -> int:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count FROM documents
                JOIN knowledge_bases AS bases ON bases.kb_id=documents.kb_id
                WHERE bases.owner_id=? AND bases.status='active' AND documents.status='active'
                """,
                (owner_id,),
            ).fetchone()
        return int(row["count"])

    def active_chunk_count(self, *, owner_id: str) -> int:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(versions.chunk_count),0) AS count
                FROM document_versions AS versions
                JOIN documents AS documents ON documents.current_version_id=versions.version_id
                JOIN knowledge_bases AS bases ON bases.kb_id=documents.kb_id
                WHERE bases.owner_id=? AND bases.status='active'
                  AND documents.status='active' AND versions.status='active'
                """,
                (owner_id,),
            ).fetchone()
        return int(row["count"])

    def list_rebuild_documents(self) -> list[dict[str, object]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT bases.owner_id, documents.kb_id, documents.doc_id,
                       documents.display_name, documents.managed_path,
                       documents.source_type, documents.mime_type,
                       versions.version_id, versions.content_hash,
                       versions.parser_version, versions.chunker_version,
                       versions.embedding_model, versions.embedding_revision,
                       versions.embedding_dimension, versions.chunk_count
                FROM documents
                JOIN knowledge_bases AS bases ON bases.kb_id=documents.kb_id
                JOIN document_versions AS versions
                  ON versions.version_id=documents.current_version_id
                WHERE bases.status='active' AND documents.status='active'
                  AND versions.status='active' AND documents.managed_path IS NOT NULL
                ORDER BY bases.owner_id, documents.kb_id, documents.doc_id
                """
            ).fetchall()
        return [dict(row) for row in rows]


def _required(name: str, value: str) -> str:
    candidate = value.strip()
    if not candidate:
        raise ValueError(f"{name} must not be empty")
    return candidate


def _record(record_type, row):
    if row is None:
        raise KeyError("record not found")
    return record_type(**{key: row[key] for key in row.keys()})
