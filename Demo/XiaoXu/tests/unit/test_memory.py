import json

import pytest

from private_agent.core.identity import (
    current_conversation_type,
    current_user_id,
    user_context,
)
from private_agent.memory import (
    MemoryAccessDenied,
    MemoryNotFoundError,
    MemoryService,
)
from private_agent.persistence.audit import DatabaseAuditLogger
from private_agent.persistence.database import XiaoXuDatabase
from private_agent.persistence.memories import MemoryCapacityExceeded, MemoryStore


def _service(tmp_path, *, max_content_bytes=4096, max_items=10):
    database = XiaoXuDatabase(tmp_path / "xiaoxu.db")
    audit = DatabaseAuditLogger(
        database,
        lambda: current_user_id("local-user"),
    )
    service = MemoryService(
        store=MemoryStore(database),
        user_id_provider=lambda: current_user_id("local-user"),
        conversation_type_provider=lambda: "single",
        thread_id_provider=lambda: "thread-test",
        audit=audit,
        max_content_bytes=max_content_bytes,
        max_items_per_user=max_items,
        max_results=20,
        max_query_bytes=100,
    )
    return service, database


def test_memory_lifecycle_is_strictly_isolated_by_user(tmp_path):
    service, _database = _service(tmp_path)

    with user_context("user-a"):
        created = service.remember("我喜欢乌龙茶")
        assert service.search("乌龙茶") == [created]

    with user_context("user-b"):
        assert service.list() == []
        with pytest.raises(MemoryNotFoundError, match="not found"):
            service.update(created.memory_id, "越权修改")
        with pytest.raises(MemoryNotFoundError, match="not found"):
            service.forget(created.memory_id)

    with user_context("user-a"):
        updated = service.update(created.memory_id, "我喜欢茉莉乌龙茶")
        assert updated.content == "我喜欢茉莉乌龙茶"
        service.forget(created.memory_id)
        assert service.list() == []


def test_memory_enforces_content_capacity_query_and_result_limits(tmp_path):
    service, _database = _service(
        tmp_path,
        max_content_bytes=8,
        max_items=1,
    )

    with pytest.raises(ValueError, match="must not be blank"):
        service.remember(" ")
    with pytest.raises(ValueError, match="exceeds 8 bytes"):
        service.remember("123456789")

    created = service.remember("12345678")
    assert created.content == "12345678"
    with pytest.raises(MemoryCapacityExceeded, match="limit reached"):
        service.remember("other")
    with pytest.raises(ValueError, match="between 1 and 20"):
        service.list(21)
    with pytest.raises(ValueError, match="query must not be blank"):
        service.search(" ")


def test_memory_audit_never_persists_content_or_query(tmp_path):
    service, database = _service(tmp_path)
    secret_content = "private-memory-marker"
    secret_query = "memory-marker"

    record = service.remember(secret_content)
    service.search(secret_query)
    service.update(record.memory_id, "replacement-marker")
    service.forget(record.memory_id)

    with database.connect() as connection:
        rows = connection.execute(
            "SELECT event_type, payload FROM audit_events ORDER BY created_at"
        ).fetchall()
    serialized = "\n".join(str(row["payload"]) for row in rows)
    assert secret_content not in serialized
    assert secret_query not in serialized
    assert "replacement-marker" not in serialized
    assert {
        row["event_type"] for row in rows
    } >= {
        "memory_created",
        "memory_searched",
        "memory_updated",
        "memory_deleted",
    }
    assert all(isinstance(json.loads(row["payload"]), dict) for row in rows)


def test_memory_is_rejected_in_shared_group_context(tmp_path):
    database = XiaoXuDatabase(tmp_path / "xiaoxu.db")
    audit = DatabaseAuditLogger(
        database,
        lambda: current_user_id("local-user"),
    )
    service = MemoryService(
        store=MemoryStore(database),
        user_id_provider=lambda: current_user_id("local-user"),
        conversation_type_provider=current_conversation_type,
        thread_id_provider=lambda: "group-thread",
        audit=audit,
        max_content_bytes=4096,
        max_items_per_user=10,
        max_results=20,
        max_query_bytes=100,
    )

    with user_context("user-a", conversation_type="group"):
        with pytest.raises(MemoryAccessDenied, match="group conversations"):
            service.remember("不得写入共享群聊")
        with pytest.raises(MemoryAccessDenied, match="group conversations"):
            service.list()

    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM agent_memories"
        ).fetchone()[0]
    assert count == 0
