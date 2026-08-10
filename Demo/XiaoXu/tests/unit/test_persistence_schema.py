import sqlite3

from private_agent.persistence.database import XiaoXuDatabase


def test_xiaoxu_database_has_agent_state_and_memory_but_no_knowledge(tmp_path):
    path = tmp_path / "xiaoxu.db"
    XiaoXuDatabase(path)

    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert {"model_state", "tool_grants", "audit_events", "agent_memories"} <= tables
    assert "todos" not in tables
    assert "reminders" not in tables
    assert "memories" not in tables
    assert "knowledge_bases" not in tables
    assert "documents" not in tables


def test_schema_v5_adds_agent_memory_without_losing_existing_agent_state(tmp_path):
    path = tmp_path / "xiaoxu.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO schema_metadata(key, value)
            VALUES ('schema_version', '4');
            CREATE TABLE model_state (
                user_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO model_state(user_id, payload, updated_at)
            VALUES ('user-a', '{"active_model":"demo"}', '2026-01-01');
            """
        )

    XiaoXuDatabase(path)

    with sqlite3.connect(path) as connection:
        version = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
        ).fetchone()[0]
        model_state = connection.execute(
            "SELECT payload FROM model_state WHERE user_id = 'user-a'"
        ).fetchone()[0]
        memory_table = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'agent_memories'
            """
        ).fetchone()

    assert version == "5"
    assert model_state == '{"active_model":"demo"}'
    assert memory_table == ("agent_memories",)
