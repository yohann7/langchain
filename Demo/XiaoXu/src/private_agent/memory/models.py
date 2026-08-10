"""Domain models for explicit per-user Agent memory."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryRecord:
    """One user-owned, explicitly persisted memory."""

    memory_id: str
    content: str
    source: str
    source_thread_id: str | None
    created_at: str
    updated_at: str

