"""Persistent per-user daily usage accounting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from private_agent.persistence.database import XiaoXuDatabase


@dataclass(frozen=True)
class DailyUsage:
    usage_date: str
    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, int]:
        return {
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }


class DailyUsageStore:
    def __init__(self, database: XiaoXuDatabase) -> None:
        self._database = database

    def load(self, *, user_id: str, usage_date: str | None = None) -> DailyUsage:
        selected_date = usage_date or _utc_date()
        with self._database.connect() as connection:
            row = connection.execute(
                """
                SELECT model_calls, tool_calls, input_tokens, output_tokens
                FROM daily_usage
                WHERE user_id = ? AND usage_date = ?
                """,
                (user_id, selected_date),
            ).fetchone()
        if row is None:
            return DailyUsage(usage_date=selected_date)
        return DailyUsage(
            usage_date=selected_date,
            model_calls=int(row["model_calls"]),
            tool_calls=int(row["tool_calls"]),
            input_tokens=int(row["input_tokens"]),
            output_tokens=int(row["output_tokens"]),
        )

    def record(
        self,
        *,
        user_id: str,
        model_calls: int = 0,
        tool_calls: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        usage_date: str | None = None,
    ) -> DailyUsage:
        values = (model_calls, tool_calls, input_tokens, output_tokens)
        if any(value < 0 for value in values):
            raise ValueError("usage increments must not be negative")
        selected_date = usage_date or _utc_date()
        updated_at = datetime.now(timezone.utc).isoformat()
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO daily_usage(
                    user_id, usage_date, model_calls, tool_calls,
                    input_tokens, output_tokens, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, usage_date) DO UPDATE SET
                    model_calls = model_calls + excluded.model_calls,
                    tool_calls = tool_calls + excluded.tool_calls,
                    input_tokens = input_tokens + excluded.input_tokens,
                    output_tokens = output_tokens + excluded.output_tokens,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    selected_date,
                    model_calls,
                    tool_calls,
                    input_tokens,
                    output_tokens,
                    updated_at,
                ),
            )
        return self.load(user_id=user_id, usage_date=selected_date)


def _utc_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()
