"""The only HTTP client XiaoXu uses for knowledge operations."""

from __future__ import annotations

from typing import Any

import httpx

from private_agent.knowledge.errors import (
    KnowledgeAuthenticationError,
    KnowledgeProtocolError,
    KnowledgeTimeoutError,
    KnowledgeUnavailableError,
)
from private_agent.knowledge.schemas import (
    KnowledgeSearchResponse,
    KnowledgeStatusResponse,
)


class KnowledgeClient:
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def status(self, *, user_id: str) -> KnowledgeStatusResponse:
        normalized_user_id = user_id.strip()
        if not normalized_user_id:
            raise ValueError("user_id must not be blank")
        try:
            response = self._client.get(
                "/v1/knowledge/status",
                params={"user_id": normalized_user_id},
            )
        except httpx.TimeoutException as exc:
            raise KnowledgeTimeoutError("knowledge request timed out") from exc
        except httpx.HTTPError as exc:
            raise KnowledgeUnavailableError("knowledge service unavailable") from exc
        if response.status_code in {401, 403}:
            raise KnowledgeAuthenticationError("knowledge service rejected credentials")
        if response.status_code >= 500:
            raise KnowledgeUnavailableError("knowledge service unavailable")
        if not response.is_success:
            raise KnowledgeProtocolError("knowledge service rejected request")
        try:
            body = response.json()
            if not isinstance(body, dict):
                raise TypeError
            return KnowledgeStatusResponse.from_dict(body)
        except (TypeError, ValueError) as exc:
            raise KnowledgeProtocolError("invalid knowledge service response") from exc

    def search(
        self,
        *,
        query: str,
        user_id: str,
        knowledge_bases: list[str] | None = None,
        limit: int | None = None,
        timeout_seconds: float,
    ) -> KnowledgeSearchResponse:
        payload: dict[str, Any] = {"query": query, "user_id": user_id}
        if knowledge_bases is not None:
            payload["knowledge_bases"] = knowledge_bases
        if limit is not None:
            payload["limit"] = limit
        try:
            response = self._client.post(
                "/v1/knowledge/search",
                json=payload,
                timeout=timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise KnowledgeTimeoutError("knowledge request timed out") from exc
        except httpx.HTTPError as exc:
            raise KnowledgeUnavailableError("knowledge service unavailable") from exc
        if response.status_code in {401, 403}:
            raise KnowledgeAuthenticationError("knowledge service rejected credentials")
        if response.status_code >= 500:
            raise KnowledgeUnavailableError("knowledge service unavailable")
        if not response.is_success:
            raise KnowledgeProtocolError("knowledge service rejected request")
        try:
            body = response.json()
            if not isinstance(body, dict):
                raise TypeError
            return KnowledgeSearchResponse.from_dict(body)
        except (TypeError, ValueError) as exc:
            raise KnowledgeProtocolError("invalid knowledge service response") from exc
