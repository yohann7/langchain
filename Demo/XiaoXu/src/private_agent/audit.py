"""JSONL audit logging with conservative redaction."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d -]{8,}\d)(?!\d)")
API_KEY_RE = re.compile(r"\b(?:sk|pk|rk|ak)-[A-Za-z0-9_\-]{8,}\b")
BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{10,}\b", re.IGNORECASE)


def redact_text(value: str) -> str:
    """Redact common sensitive values in text."""

    value = API_KEY_RE.sub("[REDACTED_API_KEY]", value)
    value = BEARER_RE.sub("Bearer [REDACTED_TOKEN]", value)
    value = EMAIL_RE.sub("[REDACTED_EMAIL]", value)
    value = PHONE_RE.sub("[REDACTED_PHONE]", value)
    return value


def redact(value: Any) -> Any:
    """Recursively redact values before audit persistence."""

    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        return {str(key): redact(item) for key, item in value.items()}
    return value


class AuditLogger:
    """Append-only audit log writer."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def record(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "payload": redact(payload),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
        return entry
