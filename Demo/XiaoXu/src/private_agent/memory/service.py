"""Explicit, user-isolated long-term memory service."""

from __future__ import annotations

from collections.abc import Callable

from private_agent.memory.models import MemoryRecord
from private_agent.persistence.audit import DatabaseAuditLogger
from private_agent.persistence.memories import MemoryStore


class MemoryNotFoundError(LookupError):
    """Raised without revealing whether an id belongs to another user."""


class MemoryAccessDenied(PermissionError):
    """Raised when private memory is requested from a shared conversation."""


class MemoryService:
    """Memory lifecycle API with no automatic extraction or recall."""

    def __init__(
        self,
        *,
        store: MemoryStore,
        user_id_provider: Callable[[], str],
        conversation_type_provider: Callable[[], str],
        thread_id_provider: Callable[[], str | None],
        audit: DatabaseAuditLogger,
        max_content_bytes: int,
        max_items_per_user: int,
        max_results: int,
        max_query_bytes: int,
    ) -> None:
        self._store = store
        self._user_id_provider = user_id_provider
        self._conversation_type_provider = conversation_type_provider
        self._thread_id_provider = thread_id_provider
        self._audit = audit
        self._max_content_bytes = max_content_bytes
        self._max_items_per_user = max_items_per_user
        self._max_results = max_results
        self._max_query_bytes = max_query_bytes

    def remember(self, content: str) -> MemoryRecord:
        self._ensure_private_context()
        clean = self._validate_content(content)
        record = self._store.create(
            user_id=self._user_id(),
            content=clean,
            source_thread_id=self._thread_id_provider(),
            max_items=self._max_items_per_user,
        )
        self._audit.record(
            "memory_created",
            {
                "memory_id": record.memory_id,
                "content_bytes": len(clean.encode("utf-8")),
                "source": record.source,
                "source_thread_id": record.source_thread_id,
            },
        )
        return record

    def list(self, limit: int = 20) -> list[MemoryRecord]:
        self._ensure_private_context()
        selected_limit = self._validate_limit(limit)
        records = self._store.list(
            user_id=self._user_id(),
            limit=selected_limit,
        )
        self._audit.record(
            "memory_listed",
            {"result_count": len(records), "limit": selected_limit},
        )
        return records

    def search(self, query: str, limit: int = 10) -> list[MemoryRecord]:
        self._ensure_private_context()
        clean_query = query.strip()
        if not clean_query:
            raise ValueError("memory query must not be blank")
        if len(clean_query.encode("utf-8")) > self._max_query_bytes:
            raise ValueError(
                f"memory query exceeds {self._max_query_bytes} bytes"
            )
        selected_limit = self._validate_limit(limit)
        records = self._store.search(
            user_id=self._user_id(),
            query=clean_query,
            limit=selected_limit,
        )
        self._audit.record(
            "memory_searched",
            {
                "query_bytes": len(clean_query.encode("utf-8")),
                "result_count": len(records),
                "limit": selected_limit,
            },
        )
        return records

    def update(self, memory_id: str, content: str) -> MemoryRecord:
        self._ensure_private_context()
        selected_id = _validate_memory_id(memory_id)
        clean = self._validate_content(content)
        record = self._store.update(
            user_id=self._user_id(),
            memory_id=selected_id,
            content=clean,
        )
        if record is None:
            self._audit.record(
                "memory_update_rejected",
                {"memory_id": selected_id, "reason": "not_found"},
            )
            raise MemoryNotFoundError(f"memory not found: {selected_id}")
        self._audit.record(
            "memory_updated",
            {
                "memory_id": selected_id,
                "content_bytes": len(clean.encode("utf-8")),
            },
        )
        return record

    def forget(self, memory_id: str) -> None:
        self._ensure_private_context()
        selected_id = _validate_memory_id(memory_id)
        deleted = self._store.delete(
            user_id=self._user_id(),
            memory_id=selected_id,
        )
        if not deleted:
            self._audit.record(
                "memory_delete_rejected",
                {"memory_id": selected_id, "reason": "not_found"},
            )
            raise MemoryNotFoundError(f"memory not found: {selected_id}")
        self._audit.record(
            "memory_deleted",
            {"memory_id": selected_id},
        )

    def _user_id(self) -> str:
        user_id = self._user_id_provider().strip()
        if not user_id:
            raise RuntimeError("memory user identity is not available")
        return user_id

    def _ensure_private_context(self) -> None:
        if self._conversation_type_provider() == "group":
            self._audit.record(
                "memory_access_rejected",
                {"reason": "shared_group_conversation"},
            )
            raise MemoryAccessDenied(
                "private long-term memory is unavailable in group conversations"
            )

    def _validate_content(self, content: str) -> str:
        clean = content.strip()
        if not clean:
            raise ValueError("memory content must not be blank")
        size = len(clean.encode("utf-8"))
        if size > self._max_content_bytes:
            raise ValueError(
                f"memory content exceeds {self._max_content_bytes} bytes"
            )
        return clean

    def _validate_limit(self, limit: int) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("memory result limit must be an integer")
        if not 1 <= limit <= self._max_results:
            raise ValueError(
                f"memory result limit must be between 1 and {self._max_results}"
            )
        return limit


def _validate_memory_id(memory_id: str) -> str:
    selected_id = memory_id.strip()
    if (
        len(selected_id) != 36
        or not selected_id.startswith("mem_")
        or any(character not in "0123456789abcdef" for character in selected_id[4:])
    ):
        raise ValueError("invalid memory id")
    return selected_id
