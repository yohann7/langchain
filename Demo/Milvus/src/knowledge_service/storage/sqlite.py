"""SQLite connection and schema management."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class SqliteDatabase:
    SCHEMA_VERSION = 3

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve(strict=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        existed = self.path.exists() and self.path.stat().st_size > 0
        with self.connect() as connection:
            if existed:
                table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_metadata'"
                ).fetchone()
                if table is None:
                    raise RuntimeError("Unsupported knowledge database: schema metadata missing")
                row = connection.execute(
                    "SELECT value FROM schema_metadata WHERE key='schema_version'"
                ).fetchone()
                if row is None or int(row["value"]) != self.SCHEMA_VERSION:
                    actual = row["value"] if row is not None else "missing"
                    raise RuntimeError(
                        f"Unsupported knowledge schema version {actual}; "
                        f"expected {self.SCHEMA_VERSION}"
                    )
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS knowledge_bases (
                    kb_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    scope TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active','disabled','deleted')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(owner_id, name)
                );
                CREATE TABLE IF NOT EXISTS documents (
                    doc_id TEXT PRIMARY KEY,
                    kb_id TEXT NOT NULL REFERENCES knowledge_bases(kb_id),
                    display_name TEXT NOT NULL,
                    canonical_path TEXT NOT NULL,
                    managed_path TEXT,
                    source_type TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    current_version_id TEXT,
                    status TEXT NOT NULL CHECK(status IN ('active','disabled','deleted')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(kb_id, canonical_path)
                );
                CREATE TABLE IF NOT EXISTS document_versions (
                    version_id TEXT PRIMARY KEY,
                    doc_id TEXT NOT NULL REFERENCES documents(doc_id),
                    content_hash TEXT NOT NULL,
                    parser_version TEXT NOT NULL,
                    chunker_version TEXT NOT NULL,
                    embedding_model TEXT NOT NULL,
                    embedding_revision TEXT NOT NULL,
                    embedding_dimension INTEGER NOT NULL CHECK(embedding_dimension > 0),
                    chunk_count INTEGER NOT NULL CHECK(chunk_count >= 0),
                    status TEXT NOT NULL CHECK(status IN ('staging','active','failed','superseded','deleted')),
                    created_at TEXT NOT NULL,
                    activated_at TEXT
                );
                CREATE TABLE IF NOT EXISTS ingest_jobs (
                    job_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    kb_id TEXT NOT NULL REFERENCES knowledge_bases(kb_id),
                    doc_id TEXT NOT NULL REFERENCES documents(doc_id),
                    version_id TEXT NOT NULL REFERENCES document_versions(version_id),
                    request_id TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('running','completed','failed')),
                    current_stage TEXT NOT NULL,
                    total_chunks INTEGER NOT NULL CHECK(total_chunks >= 0),
                    written_chunks INTEGER NOT NULL DEFAULT 0 CHECK(written_chunks >= 0),
                    result_json TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    UNIQUE(owner_id, request_id)
                );
                CREATE INDEX IF NOT EXISTS ix_kb_owner_status ON knowledge_bases(owner_id,status);
                CREATE INDEX IF NOT EXISTS ix_documents_kb_status ON documents(kb_id,status);
                CREATE INDEX IF NOT EXISTS ix_versions_doc_status ON document_versions(doc_id,status);
                CREATE INDEX IF NOT EXISTS ix_jobs_status ON ingest_jobs(status,started_at);
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_metadata(key,value) VALUES('schema_version',?)",
                (str(self.SCHEMA_VERSION),),
            )
