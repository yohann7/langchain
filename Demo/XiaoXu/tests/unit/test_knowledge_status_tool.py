from __future__ import annotations

import httpx
import pytest

from private_agent.knowledge.client import KnowledgeClient
from private_agent.knowledge.errors import (
    KnowledgeAuthenticationError,
    KnowledgeProtocolError,
    KnowledgeTimeoutError,
    KnowledgeUnavailableError,
)
from private_agent.knowledge.schemas import KnowledgeStatusResponse
from private_agent.tools.knowledge.get_knowledge_status import get_knowledge_status


def _status_payload() -> dict[str, object]:
    return {
        "enabled": True,
        "embedding": {
            "model": "BAAI/bge-m3",
            "revision": "fixed",
            "dimension": 1024,
            "ready": True,
        },
        "sqlite": {
            "ready": True,
            "knowledge_bases": 2,
            "total_documents": 10,
            "active_chunks": 120,
        },
        "milvus": {
            "ready": True,
            "database": "knowledge",
            "collection": "knowledge_chunks_v1",
            "dimension": 1024,
        },
    }


def test_status_response_parses_core_fields_and_preserves_milvus_extensions():
    status = KnowledgeStatusResponse.from_dict(_status_payload())

    assert status.enabled is True
    assert status.embedding.model == "BAAI/bge-m3"
    assert status.embedding.dimension == 1024
    assert status.sqlite.total_documents == 10
    assert status.sqlite.active_chunks == 120
    assert status.milvus["collection"] == "knowledge_chunks_v1"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.pop("enabled"),
        lambda value: value["embedding"].update({"ready": "yes"}),
        lambda value: value["sqlite"].update({"total_documents": -1}),
        lambda value: value.update({"milvus": {"ready": "yes"}}),
    ],
)
def test_status_response_rejects_invalid_core_fields(mutate):
    payload = _status_payload()
    mutate(payload)

    with pytest.raises(ValueError, match="invalid knowledge status response"):
        KnowledgeStatusResponse.from_dict(payload)


def test_knowledge_client_status_uses_get_path_and_current_user():
    client = KnowledgeClient(base_url="http://knowledge", token="token")
    client._client.close()
    captured = {}

    class FakeHttpClient:
        def get(self, path, **kwargs):
            captured.update({"path": path, **kwargs})
            return httpx.Response(200, json=_status_payload())

        def close(self):
            return None

    client._client = FakeHttpClient()

    status = client.status(user_id="user-1")

    assert status.sqlite.knowledge_bases == 2
    assert captured == {
        "path": "/v1/knowledge/status",
        "params": {"user_id": "user-1"},
    }


def test_knowledge_client_status_rejects_invalid_payload():
    client = KnowledgeClient(base_url="http://knowledge", token="token")
    client._client.close()

    class FakeHttpClient:
        def get(self, path, **kwargs):
            del path, kwargs
            return httpx.Response(200, json={"enabled": True})

        def close(self):
            return None

    client._client = FakeHttpClient()

    with pytest.raises(KnowledgeProtocolError):
        client.status(user_id="user-1")


def test_knowledge_client_status_rejects_non_json_payload():
    client = KnowledgeClient(base_url="http://knowledge", token="token")
    client._client.close()

    class FakeResponse:
        status_code = 200
        is_success = True

        def json(self):
            raise ValueError("not json")

    class FakeHttpClient:
        def get(self, path, **kwargs):
            del path, kwargs
            return FakeResponse()

        def close(self):
            return None

    client._client = FakeHttpClient()

    with pytest.raises(KnowledgeProtocolError):
        client.status(user_id="user-1")


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (401, KnowledgeAuthenticationError),
        (403, KnowledgeAuthenticationError),
        (503, KnowledgeUnavailableError),
        (400, KnowledgeProtocolError),
    ],
)
def test_knowledge_client_status_maps_http_failures(status_code, error_type):
    client = KnowledgeClient(base_url="http://knowledge", token="token")
    client._client.close()

    class FakeHttpClient:
        def get(self, path, **kwargs):
            del path, kwargs
            return httpx.Response(status_code, json={"detail": "internal-secret"})

        def close(self):
            return None

    client._client = FakeHttpClient()

    with pytest.raises(error_type):
        client.status(user_id="user-1")


@pytest.mark.parametrize(
    ("transport_error", "error_type"),
    [
        (httpx.ReadTimeout("slow"), KnowledgeTimeoutError),
        (httpx.ConnectError("offline"), KnowledgeUnavailableError),
    ],
)
def test_knowledge_client_status_maps_transport_failures(
    transport_error, error_type
):
    client = KnowledgeClient(base_url="http://knowledge", token="token")
    client._client.close()

    class FakeHttpClient:
        def get(self, path, **kwargs):
            del path, kwargs
            raise transport_error

        def close(self):
            return None

    client._client = FakeHttpClient()

    with pytest.raises(error_type):
        client.status(user_id="user-1")


class FakeCapabilities:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed

    def can_search_knowledge(self, user_id: str) -> bool:
        assert user_id
        return self.allowed


class FakeStatusClient:
    def __init__(
        self,
        payload: dict[str, object] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.payload = payload or _status_payload()
        self.error = error
        self.calls: list[str] = []

    def status(self, *, user_id: str) -> KnowledgeStatusResponse:
        self.calls.append(user_id)
        if self.error:
            raise self.error
        return KnowledgeStatusResponse.from_dict(self.payload)


def test_get_knowledge_status_returns_full_status_and_sanitizes_milvus_error():
    payload = _status_payload()
    payload["milvus"]["ready"] = False
    payload["milvus"]["error"] = "token=secret host=internal-milvus"
    client = FakeStatusClient(payload)

    result = get_knowledge_status(
        user_id="user-1",
        client=client,
        capabilities=FakeCapabilities(True),
    )

    assert client.calls == ["user-1"]
    assert result["sqlite"]["total_documents"] == 10
    assert result["milvus"]["database"] == "knowledge"
    assert result["milvus"]["error"] == "Milvus 状态异常"
    assert "secret" not in repr(result)


def test_get_knowledge_status_denied_user_never_calls_api():
    client = FakeStatusClient()

    with pytest.raises(PermissionError):
        get_knowledge_status(
            user_id="user-2",
            client=client,
            capabilities=FakeCapabilities(False),
        )

    assert client.calls == []


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (
            KnowledgeAuthenticationError("token=secret"),
            "KNOWLEDGE_AUTHENTICATION_FAILED",
        ),
        (KnowledgeTimeoutError("host=internal"), "KNOWLEDGE_TIMEOUT"),
        (KnowledgeUnavailableError("http://internal"), "KNOWLEDGE_UNAVAILABLE"),
        (KnowledgeProtocolError("raw response"), "KNOWLEDGE_UNAVAILABLE"),
    ],
)
def test_get_knowledge_status_returns_sanitized_errors(error, code):
    result = get_knowledge_status(
        user_id="user-1",
        client=FakeStatusClient(error=error),
        capabilities=FakeCapabilities(True),
    )

    assert result["error"]["code"] == code
    assert str(error) not in repr(result)
