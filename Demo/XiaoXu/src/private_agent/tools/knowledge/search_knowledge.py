"""Read-only Agent tool backed exclusively by Knowledge API."""

from __future__ import annotations

from typing import Protocol

from private_agent.knowledge.errors import (
    KnowledgeAuthenticationError,
    KnowledgeError,
    KnowledgeTimeoutError,
)
from private_agent.knowledge.formatter import format_tool_result


class KnowledgeSearchClient(Protocol):
    def search(self, **kwargs: object): ...


class KnowledgeCapabilities(Protocol):
    def can_search_knowledge(self, user_id: str) -> bool: ...


def search_knowledge(
    *,
    query: str,
    user_id: str,
    client: KnowledgeSearchClient,
    capabilities: KnowledgeCapabilities,
    knowledge_bases: list[str] | None = None,
    limit: int | None = None,
    timeout_seconds: float,
) -> dict[str, object]:
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("query must not be blank")
    if limit is not None and not 1 <= limit <= 20:
        raise ValueError("limit must be between 1 and 20")
    if not capabilities.can_search_knowledge(user_id):
        raise PermissionError("knowledge search is not allowed for this user")
    try:
        response = client.search(
            query=normalized_query,
            user_id=user_id,
            knowledge_bases=knowledge_bases,
            limit=limit,
            timeout_seconds=timeout_seconds,
        )
        return format_tool_result(response)
    except KnowledgeAuthenticationError:
        return _safe_error(
            normalized_query,
            "KNOWLEDGE_AUTHENTICATION_FAILED",
            "知识库认证失败。",
        )
    except KnowledgeTimeoutError:
        return _safe_error(
            normalized_query,
            "KNOWLEDGE_TIMEOUT",
            "知识库请求超时，请稍后重试。",
        )
    except KnowledgeError:
        return _safe_error(
            normalized_query,
            "KNOWLEDGE_UNAVAILABLE",
            "知识库暂时不可用。",
        )


def _safe_error(query: str, code: str, message: str) -> dict[str, object]:
    return {
        "query": query,
        "hits": [],
        "sources": [],
        "backends": [],
        "request_id": "",
        "error": {"code": code, "message": message},
    }
