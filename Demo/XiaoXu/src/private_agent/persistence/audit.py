"""SQLite-backed audit events with redaction."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Callable
from uuid import uuid4

from private_agent.audit import redact
from private_agent.persistence.database import XiaoXuDatabase


class DatabaseAuditLogger:
    def __init__(
        self,
        database: XiaoXuDatabase,
        user_id_provider: Callable[[], str],
    ) -> None:
        self._database = database
        self._user_id_provider = user_id_provider

    def record(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "payload": redact(payload),
        }
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_events(
                    event_id, user_id, event_type, payload, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    f"audit-{uuid4().hex}",
                    self._user_id_provider(),
                    event_type,
                    json.dumps(entry["payload"], ensure_ascii=False, sort_keys=True),
                    entry["timestamp"],
                ),
            )
        return entry
