"""Durable, owner-scoped ingestion job state and idempotent replay."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import sqlite3
from uuid import uuid4

from knowledge_service.errors import IdempotencyConflict
from knowledge_service.storage.sqlite import SqliteDatabase


class IngestJobStore:
    def __init__(self, database: SqliteDatabase) -> None:
        self.database = database

    def replay(
        self, *, owner_id: str, request_id: str, request_fingerprint: str
    ) -> dict[str, object] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT request_fingerprint,status,result_json
                FROM ingest_jobs WHERE owner_id=? AND request_id=?
                """,
                (owner_id, request_id),
            ).fetchone()
        if row is None:
            return None
        if row["request_fingerprint"] != request_fingerprint:
            raise IdempotencyConflict("request_id was used for a different ingestion")
        if row["status"] == "completed" and row["result_json"]:
            value = json.loads(row["result_json"])
            return value if isinstance(value, dict) else None
        return None

    def update(self, *, job_id: str, stage: str, written_chunks: int = 0) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE ingest_jobs SET current_stage=?,written_chunks=?
                WHERE job_id=? AND status='running'
                """,
                (stage, int(written_chunks), job_id),
            )

    def complete_existing(
        self,
        *,
        owner_id: str,
        kb_id: str,
        doc_id: str,
        version_id: str,
        request_id: str,
        request_fingerprint: str,
        stage: str,
        total_chunks: int,
        result: dict[str, object],
    ) -> str:
        now = datetime.now(UTC).isoformat()
        job_id = f"job-{uuid4().hex}"
        try:
            with self.database.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO ingest_jobs(
                        job_id,owner_id,kb_id,doc_id,version_id,request_id,
                        request_fingerprint,status,current_stage,total_chunks,
                        written_chunks,result_json,error_code,error_message,
                        started_at,finished_at
                    ) VALUES(?,?,?,?,?,?,?,'completed',?,?,?,?,NULL,NULL,?,?)
                    """,
                    (
                        job_id,
                        owner_id,
                        kb_id,
                        doc_id,
                        version_id,
                        request_id,
                        request_fingerprint,
                        stage,
                        int(total_chunks),
                        int(total_chunks),
                        json.dumps(result, ensure_ascii=False, sort_keys=True),
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise IdempotencyConflict("request_id is already in use") from exc
        return job_id

    def fail(
        self,
        *,
        job_id: str,
        version_id: str,
        error_code: str,
        error_message: str,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE document_versions SET status='failed' WHERE version_id=?",
                (version_id,),
            )
            connection.execute(
                """
                UPDATE ingest_jobs SET status='failed',current_stage='failed',
                    error_code=?,error_message=?,finished_at=? WHERE job_id=?
                """,
                (error_code, error_message[:2000], now, job_id),
            )
