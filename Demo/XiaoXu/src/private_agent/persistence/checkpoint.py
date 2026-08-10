"""LangGraph SQLite checkpointer factory."""

from __future__ import annotations

import sqlite3

from private_agent.persistence.database import XiaoXuDatabase


def create_sqlite_checkpointer(database: XiaoXuDatabase):
    """Create the process-owned checkpointer on xiaoxu.db."""

    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as exc:
        raise RuntimeError(
            "langgraph-checkpoint-sqlite is required for persistent checkpoints"
        ) from exc
    connection = sqlite3.connect(
        database.path,
        timeout=5,
        check_same_thread=False,
    )
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return SqliteSaver(connection)
