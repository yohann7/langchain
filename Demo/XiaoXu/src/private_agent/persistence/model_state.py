"""SQLite repository for per-user model selection state."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from private_agent.persistence.database import XiaoXuDatabase


class ModelStateStore:
    def __init__(self, database: XiaoXuDatabase) -> None:
        self._database = database

    def load(self, *, user_id: str) -> dict[str, Any] | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM model_state WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        raw = json.loads(str(row["payload"]))
        return raw if isinstance(raw, dict) else None

    def save(self, *, user_id: str, payload: dict[str, Any]) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO model_state(user_id, payload, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
