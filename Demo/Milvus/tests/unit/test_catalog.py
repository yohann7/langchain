from pathlib import Path
import sqlite3

import pytest

from knowledge_service.errors import IdempotencyConflict
from knowledge_service.storage.catalog import CatalogStore
from knowledge_service.storage.sqlite import SqliteDatabase


def _catalog(path: Path) -> CatalogStore:
    return CatalogStore(SqliteDatabase(path))


def test_catalog_isolates_owners_and_activates_only_verified_version(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path / "knowledge.db")
    kb_a = catalog.ensure_knowledge_base(owner_id="alice", name="personal")
    kb_b = catalog.ensure_knowledge_base(owner_id="bob", name="personal")
    assert kb_a.kb_id != kb_b.kb_id

    document = catalog.ensure_document(
        kb_id=kb_a.kb_id,
        display_name="manual.txt",
        canonical_path="/imports/manual.txt",
        managed_path="documents/doc/manual.txt",
        source_type="text",
        mime_type="text/plain",
    )
    version, job = catalog.begin_ingest(
        owner_id="alice",
        kb_id=kb_a.kb_id,
        doc_id=document.doc_id,
        request_id="request-1",
        request_fingerprint="fingerprint-1",
        content_hash="hash",
        parser_version="parser-v1",
        chunker_version="chunker-v1",
        embedding_model="BAAI/bge-m3",
        embedding_revision="fixed",
        embedding_dimension=1024,
        total_chunks=2,
    )
    assert catalog.active_version_ids(owner_id="alice", kb_ids=[kb_a.kb_id]) == []

    result = {"status": "active", "chunks": 2}
    catalog.activate_ingest(
        job_id=job.job_id,
        doc_id=document.doc_id,
        version_id=version.version_id,
        result=result,
    )

    assert catalog.active_version_ids(owner_id="alice", kb_ids=[kb_a.kb_id]) == [
        version.version_id
    ]
    assert catalog.active_version_ids(owner_id="bob", kb_ids=[kb_a.kb_id]) == []
    assert catalog.jobs.replay(
        owner_id="alice",
        request_id="request-1",
        request_fingerprint="fingerprint-1",
    ) == result

    with pytest.raises(IdempotencyConflict):
        catalog.jobs.replay(
            owner_id="alice",
            request_id="request-1",
            request_fingerprint="different",
        )


def test_schema_rejects_incompatible_existing_database(tmp_path: Path) -> None:
    database = SqliteDatabase(tmp_path / "knowledge.db")
    with database.connect() as connection:
        connection.execute(
            "UPDATE schema_metadata SET value = '2' WHERE key = 'schema_version'"
        )

    with pytest.raises(RuntimeError, match="schema version"):
        SqliteDatabase(database.path)

    legacy = tmp_path / "legacy.db"
    with sqlite3.connect(legacy) as connection:
        connection.execute("CREATE TABLE legacy_items(id INTEGER PRIMARY KEY)")
    with pytest.raises(RuntimeError, match="metadata"):
        SqliteDatabase(legacy)


def test_database_context_closes_connection(tmp_path: Path) -> None:
    database = SqliteDatabase(tmp_path / "knowledge.db")
    with database.connect() as connection:
        connection.execute("SELECT 1").fetchone()

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
