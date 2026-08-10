"""Knowledge API application factory and composition root."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from knowledge_service.api.routes import install_routes
from knowledge_service.archive import ArchiveManager
from knowledge_service.batch_ingestion import BatchIngestionService
from knowledge_service.config import KnowledgeSettings, load_settings
from knowledge_service.coordinator import MaintenanceActiveError, OperationCoordinator
from knowledge_service.embedding import CudaBgeM3Embedder, CudaUnavailableError
from knowledge_service.errors import IngestionDeniedError, KnowledgeUnavailableError
from knowledge_service.ingestion import IngestionService
from knowledge_service.management import ManagementService
from knowledge_service.parsing.registry import DocumentParser
from knowledge_service.retrieval import RetrievalService
from knowledge_service.storage.catalog import CatalogStore
from knowledge_service.storage.milvus import MilvusKnowledgeStore
from knowledge_service.storage.sqlite import SqliteDatabase


@dataclass
class KnowledgeServices:
    settings: KnowledgeSettings
    coordinator: OperationCoordinator
    catalog: Any
    embeddings: Any
    vectors: Any
    ingestion: Any
    batch_ingestion: Any
    retrieval: Any
    management: Any
    archive: Any


def create_services(settings: KnowledgeSettings) -> KnowledgeServices:
    catalog = CatalogStore(SqliteDatabase(settings.database_path))
    embeddings = CudaBgeM3Embedder(
        model_id=settings.embedding_model,
        revision=settings.embedding_revision,
        dimension=settings.embedding_dimension,
        batch_size=settings.embedding_batch_size,
        device=settings.embedding_device,
        required_gpu_name=settings.embedding_required_gpu_name,
        cache_folder=str(settings.embedding_cache_dir),
    )
    vectors = MilvusKnowledgeStore(
        uri=settings.milvus_uri,
        database=settings.milvus_database,
        collection=settings.milvus_collection,
        dimension=settings.embedding_dimension,
        token=settings.milvus_token,
    )
    parser = DocumentParser(settings)
    ingestion = IngestionService(settings, catalog, parser, embeddings, vectors)
    return KnowledgeServices(
        settings=settings,
        coordinator=OperationCoordinator(),
        catalog=catalog,
        embeddings=embeddings,
        vectors=vectors,
        ingestion=ingestion,
        batch_ingestion=BatchIngestionService(settings, ingestion),
        retrieval=RetrievalService(settings, catalog, embeddings, vectors),
        management=ManagementService(
            settings, catalog, embeddings, vectors, parser=parser
        ),
        archive=ArchiveManager(settings),
    )


def create_api(
    *,
    services: Any | None = None,
    settings: KnowledgeSettings | None = None,
    api_token: str | None = None,
    admin_token: str | None = None,
) -> FastAPI:
    config = settings or load_settings()
    if api_token is not None:
        config.api_token = api_token
    if admin_token is not None:
        config.admin_token = admin_token
    owned_services = services is None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if app.state.services is None:
            app.state.services = create_services(config)
            app.state.services.embeddings.verify()
        yield
        if owned_services:
            close = getattr(app.state.services.vectors, "close", None)
            if callable(close):
                close()

    app = FastAPI(title="Knowledge API", version="1.0.0", lifespan=lifespan)
    app.state.services = services
    _install_error_handlers(app)
    install_routes(app, config)
    return app


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(IngestionDeniedError)
    async def handle_security_error(
        _request: Request, _error: IngestionDeniedError
    ) -> JSONResponse:
        return _safe_error(status.HTTP_403_FORBIDDEN, "INGESTION_DENIED")

    async def handle_unavailable_error(_request: Request, _error: Exception) -> JSONResponse:
        return _safe_error(status.HTTP_503_SERVICE_UNAVAILABLE, "KNOWLEDGE_UNAVAILABLE")

    for error_type in (
        KnowledgeUnavailableError,
        CudaUnavailableError,
        MaintenanceActiveError,
    ):
        app.add_exception_handler(error_type, handle_unavailable_error)

    @app.exception_handler(ValueError)
    async def handle_value_error(_request: Request, _error: ValueError) -> JSONResponse:
        return _safe_error(status.HTTP_400_BAD_REQUEST, "INVALID_REQUEST")

    @app.exception_handler(KeyError)
    async def handle_key_error(_request: Request, _error: KeyError) -> JSONResponse:
        return _safe_error(status.HTTP_404_NOT_FOUND, "NOT_FOUND")


def _safe_error(status_code: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": {"code": code}})


def main() -> None:
    import uvicorn

    settings = load_settings()
    uvicorn.run(
        "knowledge_service.api.app:create_api",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
    )
