"""Knowledge API v1 route registration."""

from dataclasses import asdict
from uuid import uuid4

from fastapi import Depends, FastAPI, Query, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials

from knowledge_service.api.auth import authorize_token, bearer
from knowledge_service.api.schemas import (
    DocumentDeleteResponse,
    DocumentListResponse,
    DocumentPatchRequest,
    DocumentResponse,
    HealthResponse,
    IngestionRequest,
    IngestionResponse,
    KnowledgeBaseListResponse,
    KnowledgeStatusResponse,
    RebuildResponse,
    SearchRequest,
    SearchResponseModel,
    TransferRequest,
    TransferResponse,
)


def install_routes(app: FastAPI, settings) -> None:
    def authorize(
        credentials: HTTPAuthorizationCredentials | None = Security(bearer),
    ) -> None:
        authorize_token(settings.api_token, credentials, "AUTH_NOT_CONFIGURED")

    def authorize_admin(
        credentials: HTTPAuthorizationCredentials | None = Security(bearer),
    ) -> None:
        authorize_token(
            settings.admin_token, credentials, "ADMIN_AUTH_NOT_CONFIGURED"
        )

    def services():
        return app.state.services

    @app.get("/health/live", response_model=HealthResponse)
    def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready", response_model=HealthResponse)
    def ready() -> JSONResponse:
        runtime = services()
        is_ready = not runtime.coordinator.maintenance_pending and runtime.management.ready()
        return JSONResponse(
            status_code=status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "ready" if is_ready else "not_ready"},
        )

    @app.get(
        "/v1/knowledge/status",
        response_model=KnowledgeStatusResponse,
        dependencies=[Depends(authorize)],
    )
    def service_status(user_id: str = Query(min_length=1, max_length=128)):
        runtime = services()
        with runtime.coordinator.read():
            return runtime.management.status(owner_id=user_id)

    @app.get(
        "/v1/knowledge-bases",
        response_model=KnowledgeBaseListResponse,
        dependencies=[Depends(authorize)],
    )
    def list_knowledge_bases(user_id: str = Query(min_length=1, max_length=128)):
        runtime = services()
        with runtime.coordinator.read():
            return {"items": runtime.catalog.list_knowledge_bases(owner_id=user_id)}

    @app.get(
        "/v1/knowledge/documents",
        response_model=DocumentListResponse,
        dependencies=[Depends(authorize)],
    )
    def list_documents(user_id: str = Query(min_length=1, max_length=128)):
        runtime = services()
        with runtime.coordinator.read():
            return {"items": runtime.management.list_documents(owner_id=user_id)}

    @app.post(
        "/v1/knowledge/search",
        response_model=SearchResponseModel,
        dependencies=[Depends(authorize)],
    )
    def search(request: SearchRequest) -> dict[str, object]:
        runtime = services()
        with runtime.coordinator.read():
            result = runtime.retrieval.search(
                owner_id=request.user_id,
                query=request.query,
                knowledge_bases=request.knowledge_bases,
                limit=request.limit,
            )
        hits = [
            {
                "doc_id": hit.doc_id,
                "chunk_id": hit.chunk_id,
                "document_name": hit.source_label,
                "location": _location(hit),
                "content": hit.content,
                "score": hit.score,
                "knowledge_base": hit.kb_id,
            }
            for hit in result.hits
        ]
        return {
            "query": result.query,
            "hits": hits,
            "sources": [
                {
                    "doc_id": item["doc_id"],
                    "chunk_id": item["chunk_id"],
                    "document_name": item["document_name"],
                    "location": item["location"],
                }
                for item in hits
            ],
            "backends": list(result.backends),
            "request_id": request.request_id or f"ks_{uuid4().hex}",
        }

    @app.post(
        "/v1/knowledge/ingestions",
        response_model=IngestionResponse,
        dependencies=[Depends(authorize_admin)],
    )
    def ingest(request: IngestionRequest):
        runtime = services()
        with runtime.coordinator.mutation():
            return asdict(
                runtime.ingestion.ingest(
                    owner_id=request.user_id,
                    knowledge_base=request.knowledge_base,
                    path=request.path,
                    request_id=request.request_id,
                )
            )

    @app.patch(
        "/v1/knowledge/documents/{document_id}",
        response_model=DocumentResponse,
        dependencies=[Depends(authorize_admin)],
    )
    def patch_document(document_id: str, request: DocumentPatchRequest):
        runtime = services()
        with runtime.coordinator.mutation():
            return runtime.management.update_document(
                owner_id=request.user_id,
                document_id=document_id,
                status=request.status,
            )

    @app.delete(
        "/v1/knowledge/documents/{document_id}",
        response_model=DocumentDeleteResponse,
        dependencies=[Depends(authorize_admin)],
    )
    def delete_document(
        document_id: str,
        user_id: str = Query(min_length=1, max_length=128),
    ):
        runtime = services()
        with runtime.coordinator.mutation():
            return runtime.management.delete_document(
                owner_id=user_id, document_id=document_id
            )

    @app.post(
        "/v1/knowledge/exports",
        response_model=TransferResponse,
        dependencies=[Depends(authorize_admin)],
    )
    def export_knowledge(request: TransferRequest):
        runtime = services()
        with runtime.coordinator.maintenance():
            return runtime.archive.export_to(request.path)

    @app.post(
        "/v1/knowledge/imports",
        response_model=TransferResponse,
        dependencies=[Depends(authorize_admin)],
    )
    def import_knowledge(request: TransferRequest):
        runtime = services()
        with runtime.coordinator.maintenance():
            return runtime.archive.restore_from(request.path)

    @app.post(
        "/v1/knowledge/rebuild",
        response_model=RebuildResponse,
        dependencies=[Depends(authorize_admin)],
    )
    def rebuild_knowledge():
        runtime = services()
        with runtime.coordinator.maintenance():
            return runtime.management.rebuild_index()


def _location(hit) -> str:
    if hit.heading_path:
        return hit.heading_path
    if hit.line_from and hit.line_to:
        return f"lines {hit.line_from}-{hit.line_to}"
    return ""
