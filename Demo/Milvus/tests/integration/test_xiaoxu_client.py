from pathlib import Path
from types import SimpleNamespace
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

from knowledge_service.api.app import create_api
from knowledge_service.config import KnowledgeSettings
from knowledge_service.coordinator import OperationCoordinator
from knowledge_service.models import SearchHit, SearchResult


XIAOXU_SRC = Path(__file__).resolve().parents[3] / "XiaoXu" / "src"
if not XIAOXU_SRC.exists():
    pytest.skip("sibling XiaoXu checkout is unavailable", allow_module_level=True)
sys.path.insert(0, str(XIAOXU_SRC))

from private_agent.knowledge.client import KnowledgeClient  # noqa: E402


class _Retrieval:
    def search(self, **values):
        assert values["owner_id"] == "alice"
        return SearchResult(
            query=values["query"],
            kb_ids=["kb-personal"],
            hits=[
                SearchHit(
                    chunk_id="chunk-1", score=0.75, kb_id="kb-personal",
                    doc_id="doc-1", version_id="ver-1", source_label="manual.txt",
                    source_type="text", title="manual", heading_path="Architecture",
                    chunk_index=0, line_from=1, line_to=2, content="retrieved evidence",
                )
            ],
            backends=("SQLite", "Milvus"),
        )


class _TestClientTransport(httpx.BaseTransport):
    def __init__(self, client: TestClient) -> None:
        self.client = client

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self.client.request(
            request.method,
            request.url.raw_path.decode("ascii"),
            headers=dict(request.headers),
            content=request.read(),
        )
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            content=response.content,
            request=request,
        )


def test_real_xiaoxu_client_authenticates_and_parses_v1_search_response() -> None:
    settings = KnowledgeSettings(api_token="read-token", admin_token="admin-token")
    services = SimpleNamespace(
        settings=settings,
        coordinator=OperationCoordinator(),
        retrieval=_Retrieval(),
    )
    with TestClient(create_api(services=services, settings=settings)) as api:
        client = KnowledgeClient(
            base_url="http://knowledge:8080",
            token="read-token",
            transport=_TestClientTransport(api),
        )
        try:
            response = client.search(
                query="How is data isolated?",
                user_id="alice",
                knowledge_bases=["personal"],
                limit=3,
            )
        finally:
            client.close()

    assert response.query == "How is data isolated?"
    assert response.hits[0].doc_id == "doc-1"
    assert response.hits[0].chunk_id == "chunk-1"
    assert response.hits[0].content == "retrieved evidence"
    assert response.hits[0].knowledge_base == "kb-personal"
    assert response.backends == ["SQLite", "Milvus"]
