"""Knowledge API v1 transport models."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    user_id: str = Field(min_length=1, max_length=128)
    knowledge_bases: list[str] | None = None
    limit: int | None = Field(default=None, ge=1, le=20)
    request_id: str | None = Field(default=None, max_length=128)


class IngestionRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    knowledge_base: str = Field(min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=4096)
    request_id: str | None = Field(default=None, max_length=128)


class DocumentPatchRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    status: str = Field(pattern="^(active|disabled)$")


class TransferRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


class HealthResponse(BaseModel):
    status: str


class EmbeddingStatusResponse(BaseModel):
    model: str
    revision: str
    dimension: int
    ready: bool


class SqliteStatusResponse(BaseModel):
    ready: bool
    knowledge_bases: int
    total_documents: int
    active_chunks: int


class MilvusStatusResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    ready: bool
    error: str | None = None


class KnowledgeStatusResponse(BaseModel):
    enabled: bool
    embedding: EmbeddingStatusResponse
    sqlite: SqliteStatusResponse
    milvus: MilvusStatusResponse


class KnowledgeBaseResponse(BaseModel):
    kb_id: str
    owner_id: str
    name: str
    description: str
    scope: str
    status: str
    created_at: str
    updated_at: str
    document_count: int


class KnowledgeBaseListResponse(BaseModel):
    items: list[KnowledgeBaseResponse]


class DocumentResponse(BaseModel):
    doc_id: str
    kb_id: str
    display_name: str
    canonical_path: str
    managed_path: str | None
    source_type: str
    mime_type: str
    current_version_id: str | None
    status: str
    created_at: str
    updated_at: str


class DocumentListResponse(BaseModel):
    items: list[DocumentResponse]


class SearchSourceResponse(BaseModel):
    doc_id: str
    chunk_id: str
    document_name: str
    location: str


class SearchHitResponse(SearchSourceResponse):
    content: str
    score: float
    knowledge_base: str


class SearchResponseModel(BaseModel):
    query: str
    hits: list[SearchHitResponse]
    sources: list[SearchSourceResponse]
    backends: list[str]
    request_id: str


class IngestionResponse(BaseModel):
    status: str
    kb_id: str | None = None
    doc_id: str | None = None
    version_id: str | None = None
    job_id: str | None = None
    chunks: int = 0
    message: str = ""
    sensitivity: str = "normal"
    redactions: int = 0
    duplicate_of_path: str | None = None


class DocumentDeleteResponse(BaseModel):
    document_id: str
    status: str
    cleanup_pending: bool


class TransferResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    path: str
    export_id: str | None = None
    import_id: str | None = None
    backup_path: str | None = None
    reindex_required: bool | None = None


class RebuildResponse(BaseModel):
    status: str
    documents: int
    chunks: int
