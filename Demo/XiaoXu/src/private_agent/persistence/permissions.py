"""SQLite repository for user tool-grant decisions."""

from __future__ import annotations

from datetime import datetime, timezone

from private_agent.persistence.database import XiaoXuDatabase


class ToolGrantStore:
    def __init__(self, database: XiaoXuDatabase) -> None:
        self._database = database

    def load(self, *, user_id: str) -> dict[str, str]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT tool_id, decision FROM tool_grants
                WHERE user_id = ? ORDER BY tool_id
                """,
                (user_id,),
            ).fetchall()
        return {str(row["tool_id"]): str(row["decision"]) for row in rows}

    def save(
        self,
        *,
        user_id: str,
        tool_id: str,
        decision: str,
    ) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO tool_grants(user_id, tool_id, decision, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, tool_id) DO UPDATE SET
                    decision = excluded.decision,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    tool_id,
                    decision,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
