"""Authoritative SQLite schema for Agent-owned state only."""

from __future__ import annotations

from pathlib import Path
import sqlite3


class XiaoXuDatabase:
    SCHEMA_VERSION = 5

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve(strict=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            checkpoint_columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(checkpoints)"
                ).fetchall()
            }
            if checkpoint_columns == {"thread_id", "payload", "updated_at"}:
                connection.execute(
                    "ALTER TABLE checkpoints RENAME TO legacy_checkpoints_v1"
                )
            connection.executescript(
                """
                DROP TABLE IF EXISTS todos;
                DROP TABLE IF EXISTS reminders;
                CREATE TABLE IF NOT EXISTS schema_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_state (
                    user_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tool_grants (
                    user_id TEXT NOT NULL,
                    tool_id TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, tool_id)
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    user_id TEXT,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS daily_usage (
                    user_id TEXT NOT NULL,
                    usage_date TEXT NOT NULL,
                    model_calls INTEGER NOT NULL DEFAULT 0,
                    tool_calls INTEGER NOT NULL DEFAULT 0,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, usage_date)
                );
                CREATE TABLE IF NOT EXISTS agent_memories (
                    user_id TEXT NOT NULL,
                    memory_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_thread_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, memory_id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_memories_user_updated
                    ON agent_memories(user_id, updated_at DESC);
                """
            )
            connection.execute(
                """
                INSERT INTO schema_metadata(key, value) VALUES ('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(self.SCHEMA_VERSION),),
            )
