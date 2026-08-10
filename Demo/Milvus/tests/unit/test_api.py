from types import SimpleNamespace

from fastapi.testclient import TestClient

from knowledge_service.api.app import create_api
from knowledge_service.config import KnowledgeSettings
from knowledge_service.coordinator import OperationCoordinator
from knowledge_service.models import IngestResult, SearchHit, SearchResult


class _Catalog:
    def list_knowledge_bases(self, *, owner_id: str):
        return [{
            "kb_id": "kb-1", "owner_id": owner_id, "name": "personal",
            "description": "", "scope": "knowledge:personal", "status": "active",
            "created_at": "now", "updated_at": "now", "document_count": 1,
        }]


class _Retrieval:
    def search(self, **values):
        return SearchResult(
            query=values["query"], kb_ids=["kb-1"],
            hits=[SearchHit(
                chunk_id="chunk-1", score=0.9, kb_id="kb-1", doc_id="doc-1",
                version_id="ver-1", source_label="manual.txt", source_type="text",
                title="manual", heading_path="Heading", chunk_index=0,
                line_from=1, line_to=2, content="evidence",
            )], backends=("SQLite", "Milvus"),
        )


class _Ingestion:
    def ingest(self, **_values):
        return IngestResult(status="active", kb_id="kb-1", doc_id="doc-1", chunks=1)


class _Management:
    def ready(self):
        return True

    def status(self, *, owner_id: str):
        return {
            "enabled": True,
            "embedding": {"model": "BAAI/bge-m3", "revision": "fixed", "dimension": 3, "ready": True},
            "sqlite": {"ready": True, "knowledge_bases": 1, "total_documents": 1, "active_chunks": 1},
            "milvus": {"ready": True},
        }

    def list_documents(self, *, owner_id: str):
        del owner_id
        return []

    def update_document(self, **values):
        return {
            "doc_id": values["document_id"], "kb_id": "kb-1", "display_name": "manual.txt",
            "canonical_path": "/imports/manual.txt", "managed_path": "documents/doc-1/manual.txt",
            "source_type": "text", "mime_type": "text/plain", "current_version_id": "ver-1",
            "status": values["status"], "created_at": "now", "updated_at": "now",
        }

    def delete_document(self, **values):
        return {"document_id": values["document_id"], "status": "deleted", "cleanup_pending": False}

    def rebuild_index(self):
        return {"status": "not_required", "documents": 0, "chunks": 0}


class _Archive:
    def export_to(self, path):
        return {"status": "exported", "path": path, "export_id": "exp-1"}

    def restore_from(self, path):
        return {"status": "restored", "path": path, "import_id": "imp-1", "reindex_required": True}


def _client() -> TestClient:
    settings = KnowledgeSettings(api_token="read-token", admin_token="admin-token")
    services = SimpleNamespace(
        settings=settings,
        coordinator=OperationCoordinator(),
        catalog=_Catalog(), retrieval=_Retrieval(), ingestion=_Ingestion(),
        management=_Management(), archive=_Archive(),
    )
    return TestClient(create_api(services=services, settings=settings))


def test_search_uses_read_token_and_preserves_xiaoxu_contract() -> None:
    client = _client()
    assert client.post("/v1/knowledge/search", json={"query": "q", "user_id": "alice"}).status_code == 401
    response = client.post(
        "/v1/knowledge/search",
        headers={"Authorization": "Bearer read-token"},
        json={"query": "q", "user_id": "alice", "knowledge_bases": ["personal"]},
    )
    assert response.status_code == 200
    assert response.json()["hits"][0] == {
        "doc_id": "doc-1", "chunk_id": "chunk-1",
        "document_name": "manual.txt",
        "location": "Heading", "content": "evidence", "score": 0.9,
        "knowledge_base": "kb-1",
    }
    assert response.json()["sources"][0] == {
        "doc_id": "doc-1", "chunk_id": "chunk-1",
        "document_name": "manual.txt", "location": "Heading",
    }
    assert "source_id" not in response.json()["hits"][0]
    assert response.json()["backends"] == ["SQLite", "Milvus"]


def test_admin_routes_reject_read_token() -> None:
    client = _client()
    denied = client.post(
        "/v1/knowledge/ingestions",
        headers={"Authorization": "Bearer read-token"},
        json={"user_id": "alice", "knowledge_base": "personal", "path": "/imports/a.txt"},
    )
    allowed = client.post(
        "/v1/knowledge/ingestions",
        headers={"Authorization": "Bearer admin-token"},
        json={"user_id": "alice", "knowledge_base": "personal", "path": "/imports/a.txt"},
    )
    assert denied.status_code == 401
    assert allowed.status_code == 200


def test_health_is_public() -> None:
    client = _client()
    assert client.get("/health/live").json() == {"status": "live"}
    assert client.get("/health/ready").json() == {"status": "ready"}
