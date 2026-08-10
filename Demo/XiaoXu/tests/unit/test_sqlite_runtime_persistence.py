import sqlite3

from private_agent.persistence.checkpoint import create_sqlite_checkpointer
from private_agent.persistence.database import XiaoXuDatabase


def test_database_migration_drops_obsolete_personal_tables_and_preserves_state(
    tmp_path,
):
    path = tmp_path / "xiaoxu.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE todos(
                todo_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE reminders(
                reminder_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE model_state(
                user_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO todos VALUES ('todo-1', 'user-a', '{}', '2026-07-29');
            INSERT INTO reminders VALUES ('reminder-1', 'user-a', '{}', '2026-07-29');
            INSERT INTO model_state
            VALUES ('user-a', '{"active":"demo"}', '2026-07-29');
            """
        )

    XiaoXuDatabase(path)

    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        model_payload = connection.execute(
            "SELECT payload FROM model_state WHERE user_id='user-a'"
        ).fetchone()[0]

    assert "todos" not in tables
    assert "reminders" not in tables
    assert model_payload == '{"active":"demo"}'


def test_langgraph_checkpointer_uses_xiaoxu_database_and_migrates_placeholder(
    tmp_path,
):
    path = tmp_path / "xiaoxu.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE checkpoints(
                thread_id TEXT PRIMARY KEY,
                payload BLOB NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    database = XiaoXuDatabase(path)
    checkpointer = create_sqlite_checkpointer(database)
    checkpointer.setup()

    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        checkpoint_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(checkpoints)")
        }

    checkpointer.conn.close()
    assert "legacy_checkpoints_v1" in tables
    assert {"thread_id", "checkpoint_ns", "checkpoint_id"} <= checkpoint_columns
