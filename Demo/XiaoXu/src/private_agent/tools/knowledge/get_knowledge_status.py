"""Read-only current-user Knowledge Service status tool."""

from __future__ import annotations

from dataclasses import asdict
from typing import Protocol

from private_agent.knowledge.errors import (
    KnowledgeAuthenticationError,
    KnowledgeError,
    KnowledgeTimeoutError,
)
from private_agent.knowledge.schemas import KnowledgeStatusResponse


class KnowledgeStatusClient(Protocol):
    def status(self, *, user_id: str) -> KnowledgeStatusResponse: ...


class KnowledgeCapabilities(Protocol):
    def can_search_knowledge(self, user_id: str) -> bool: ...


def get_knowledge_status(
    *,
    user_id: str,
    client: KnowledgeStatusClient,
    capabilities: KnowledgeCapabilities,
) -> dict[str, object]:
    if not capabilities.can_search_knowledge(user_id):
        raise PermissionError("knowledge status is not allowed for this user")
    try:
        result = asdict(client.status(user_id=user_id))
    except KnowledgeAuthenticationError:
        return _safe_error(
            "KNOWLEDGE_AUTHENTICATION_FAILED",
            "知识库认证失败。",
        )
    except KnowledgeTimeoutError:
        return _safe_error("KNOWLEDGE_TIMEOUT", "知识库状态查询超时。")
    except KnowledgeError:
        return _safe_error("KNOWLEDGE_UNAVAILABLE", "知识库状态暂时不可用。")

    milvus = result.get("milvus")
    if isinstance(milvus, dict) and milvus.get("error"):
        milvus["error"] = "Milvus 状态异常"
    return result


def _safe_error(code: str, message: str) -> dict[str, object]:
    return {"error": {"code": code, "message": message}}
