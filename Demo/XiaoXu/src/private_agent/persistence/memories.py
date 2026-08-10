"""SQLite repository for user-isolated Agent memories."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from private_agent.memory.models import MemoryRecord
from private_agent.persistence.database import XiaoXuDatabase


class MemoryCapacityExceeded(RuntimeError):
    """Raised when a user has reached the configured memory item limit."""


class MemoryStore:
    """Persistence boundary that requires user_id on every operation."""

    def __init__(self, database: XiaoXuDatabase) -> None:
        self._database = database

    def create(
        self,
        *,
        user_id: str,
        content: str,
        source_thread_id: str | None,
        max_items: int,
    ) -> MemoryRecord:
        now = datetime.now(timezone.utc).isoformat()
        memory_id = f"mem_{uuid4().hex}"
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM agent_memories WHERE user_id = ?",
                    (user_id,),
                ).fetchone()[0]
            )
            if count >= max_items:
                raise MemoryCapacityExceeded(
                    f"memory item limit reached ({count}/{max_items})"
                )
            connection.execute(
                """
                INSERT INTO agent_memories(
                    user_id, memory_id, content, source,
                    source_thread_id, created_at, updated_at
                ) VALUES (?, ?, ?, 'explicit_user_request', ?, ?, ?)
                """,
                (
                    user_id,
                    memory_id,
                    content,
                    source_thread_id,
                    now,
                    now,
                ),
            )
        return MemoryRecord(
            memory_id=memory_id,
            content=content,
            source="explicit_user_request",
            source_thread_id=source_thread_id,
            created_at=now,
            updated_at=now,
        )

    def list(self, *, user_id: str, limit: int) -> list[MemoryRecord]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT memory_id, content, source, source_thread_id,
                       created_at, updated_at
                FROM agent_memories
                WHERE user_id = ?
                ORDER BY updated_at DESC, memory_id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def search(
        self,
        *,
        user_id: str,
        query: str,
        limit: int,
    ) -> list[MemoryRecord]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT memory_id, content, source, source_thread_id,
                       created_at, updated_at
                FROM agent_memories
                WHERE user_id = ?
                  AND instr(lower(content), lower(?)) > 0
                ORDER BY updated_at DESC, memory_id DESC
                LIMIT ?
                """,
                (user_id, query, limit),
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def update(
        self,
        *,
        user_id: str,
        memory_id: str,
        content: str,
    ) -> MemoryRecord | None:
        now = datetime.now(timezone.utc).isoformat()
        with self._database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE agent_memories
                SET content = ?, updated_at = ?
                WHERE user_id = ? AND memory_id = ?
                """,
                (content, now, user_id, memory_id),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute(
                """
                SELECT memory_id, content, source, source_thread_id,
                       created_at, updated_at
                FROM agent_memories
                WHERE user_id = ? AND memory_id = ?
                """,
                (user_id, memory_id),
            ).fetchone()
        return _row_to_record(row)

    def delete(self, *, user_id: str, memory_id: str) -> bool:
        with self._database.connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM agent_memories
                WHERE user_id = ? AND memory_id = ?
                """,
                (user_id, memory_id),
            )
        return cursor.rowcount == 1


def _row_to_record(row) -> MemoryRecord:
    return MemoryRecord(
        memory_id=str(row["memory_id"]),
        content=str(row["content"]),
        source=str(row["source"]),
        source_thread_id=(
            str(row["source_thread_id"])
            if row["source_thread_id"] is not None
            else None
        ),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )

