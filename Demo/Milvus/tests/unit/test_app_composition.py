from types import SimpleNamespace

from knowledge_service.api import app
from knowledge_service.batch_ingestion import BatchIngestionService


def test_create_services_composes_batch_ingestion_from_single_file_service(
    monkeypatch,
) -> None:
    catalog = object()
    embeddings = object()
    vectors = object()
    ingestion = object()
    retrieval = object()
    management = object()
    archive = object()
    settings = SimpleNamespace(
        database_path="knowledge.db",
        embedding_model="model",
        embedding_revision="revision",
        embedding_dimension=1024,
        embedding_batch_size=4,
        embedding_device="cuda:0",
        embedding_required_gpu_name="gpu",
        embedding_cache_dir="cache",
        milvus_uri="http://milvus:19530",
        milvus_database="knowledge",
        milvus_collection="chunks",
        milvus_token="",
    )

    monkeypatch.setattr(app, "SqliteDatabase", lambda _path: object())
    monkeypatch.setattr(app, "CatalogStore", lambda _database: catalog)
    monkeypatch.setattr(app, "CudaBgeM3Embedder", lambda **_values: embeddings)
    monkeypatch.setattr(app, "MilvusKnowledgeStore", lambda **_values: vectors)
    monkeypatch.setattr(app, "DocumentParser", lambda _settings: object())
    monkeypatch.setattr(app, "IngestionService", lambda *_values: ingestion)
    monkeypatch.setattr(app, "RetrievalService", lambda *_values: retrieval)
    monkeypatch.setattr(app, "ManagementService", lambda *_values, **_kwargs: management)
    monkeypatch.setattr(app, "ArchiveManager", lambda _settings: archive)

    services = app.create_services(settings)

    assert services.ingestion is ingestion
    assert isinstance(services.batch_ingestion, BatchIngestionService)
    assert services.batch_ingestion.ingestion is ingestion
    assert services.batch_ingestion.settings is settings
