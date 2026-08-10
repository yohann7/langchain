"""Validated settings with YAML defaults and environment overrides."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
import yaml


class KnowledgeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KNOWLEDGE_",
        env_file=".env",
        extra="ignore",
    )

    run_dir: Path = Path("runtime/knowledge")
    database_name: str = "knowledge.db"
    documents_directory: str = "documents"
    transfers_directory: str = "transfers"
    allowed_roots: list[Path] = Field(default_factory=lambda: [Path("/imports")])

    api_token: str = ""
    admin_token: str = ""
    api_host: str = "0.0.0.0"
    api_port: int = Field(default=8080, ge=1, le=65535)

    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_database: str = "knowledge"
    milvus_collection: str = "knowledge_chunks_v1"
    milvus_token: str | None = None

    rag_enabled: bool = True
    rag_default_knowledge_base: str = "personal"
    rag_chunk_size_chars: int = Field(default=1600, ge=200, le=8000)
    rag_chunk_overlap_chars: int = Field(default=200, ge=0, le=2000)
    rag_min_non_sensitive_chars: int = Field(default=20, ge=1, le=1000)
    rag_candidate_limit: int = Field(default=50, ge=5, le=100)
    rag_top_k: int = Field(default=10, ge=1, le=20)

    embedding_model: str = "BAAI/bge-m3"
    embedding_revision: str = "5617a9f61b028005a4858fdac845db406aefb181"
    embedding_dimension: int = Field(default=1024, ge=2, le=32768)
    embedding_batch_size: int = Field(default=4, ge=1, le=128)
    embedding_device: str = "cuda:0"
    embedding_required_gpu_name: str = "NVIDIA GeForce RTX 4070 Laptop GPU"
    embedding_cache_dir: Path = Path("/opt/models/bge-m3")

    maximum_source_bytes: int = Field(default=50 * 1024 * 1024, ge=1024)
    maximum_extracted_characters: int = Field(default=10_000_000, ge=1024)
    maximum_office_uncompressed_bytes: int = Field(
        default=200 * 1024 * 1024, ge=1024
    )
    maximum_office_archive_entries: int = Field(default=20_000, ge=1)
    maximum_pdf_pages: int = Field(default=1_000, ge=1)
    maximum_xlsx_rows_per_sheet: int = Field(default=100_000, ge=1)
    maximum_xlsx_columns: int = Field(default=512, ge=1)
    maximum_xlsx_nonempty_cells: int = Field(default=500_000, ge=1)
    maximum_pptx_slides: int = Field(default=1_000, ge=1)
    maximum_pptx_shapes: int = Field(default=100_000, ge=1)

    maximum_archive_bytes: int = Field(default=2 * 1024**3, ge=1024)
    maximum_archive_members: int = Field(default=10_000, ge=1)
    maximum_extracted_bytes: int = Field(default=4 * 1024**3, ge=1024)
    maximum_compression_ratio: float = Field(default=100.0, ge=1.0)

    @field_validator("embedding_device")
    @classmethod
    def require_cuda(cls, value: str) -> str:
        candidate = value.strip().lower()
        if not candidate.startswith("cuda"):
            raise ValueError("embedding device must be a CUDA device")
        return candidate

    @model_validator(mode="after")
    def validate_relationships(self) -> "KnowledgeSettings":
        if self.rag_chunk_overlap_chars >= self.rag_chunk_size_chars:
            raise ValueError("chunk overlap must be smaller than chunk size")
        if self.rag_top_k > self.rag_candidate_limit:
            raise ValueError("top_k must not exceed candidate_limit")
        return self

    @property
    def database_path(self) -> Path:
        return self.run_dir / self.database_name

    @property
    def documents_path(self) -> Path:
        return self.run_dir / self.documents_directory

    @property
    def transfers_path(self) -> Path:
        return self.run_dir / self.transfers_directory

    @property
    def reindex_marker(self) -> Path:
        return self.run_dir / ".reindex-required"


def load_settings(path: str | Path = "config/knowledge.yaml") -> KnowledgeSettings:
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(f"configuration must contain a mapping: {config_path}")
    values = _flatten_config(raw)
    for field_name in KnowledgeSettings.model_fields:
        env_name = f"KNOWLEDGE_{field_name.upper()}"
        if env_name in os.environ:
            raw_value: Any = os.environ[env_name]
            if field_name == "allowed_roots":
                import json

                try:
                    raw_value = json.loads(raw_value)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"{env_name} must be a JSON array of paths"
                    ) from exc
            values[field_name] = raw_value
    return KnowledgeSettings(**values)


def _flatten_config(raw: dict[str, Any]) -> dict[str, Any]:
    values = {key: value for key, value in raw.items() if not isinstance(value, dict)}
    sections: dict[str, dict[str, str]] = {
        "runtime": {
            "run_dir": "run_dir",
            "database_name": "database_name",
            "documents_directory": "documents_directory",
            "transfers_directory": "transfers_directory",
            "allowed_roots": "allowed_roots",
        },
        "api": {
            "host": "api_host",
            "port": "api_port",
        },
        "milvus": {
            "uri": "milvus_uri",
            "database": "milvus_database",
            "collection": "milvus_collection",
        },
        "embedding": {
            "model": "embedding_model",
            "revision": "embedding_revision",
            "dimension": "embedding_dimension",
            "batch_size": "embedding_batch_size",
            "device": "embedding_device",
            "required_gpu_name": "embedding_required_gpu_name",
            "cache_dir": "embedding_cache_dir",
        },
        "search": {
            "top_k": "rag_top_k",
            "candidate_limit": "rag_candidate_limit",
        },
        "ingestion": {
            "chunk_size_chars": "rag_chunk_size_chars",
            "chunk_overlap_chars": "rag_chunk_overlap_chars",
            "minimum_non_sensitive_chars": "rag_min_non_sensitive_chars",
            "maximum_source_bytes": "maximum_source_bytes",
            "maximum_extracted_characters": "maximum_extracted_characters",
            "maximum_office_uncompressed_bytes": "maximum_office_uncompressed_bytes",
            "maximum_office_archive_entries": "maximum_office_archive_entries",
            "maximum_pdf_pages": "maximum_pdf_pages",
            "maximum_xlsx_rows_per_sheet": "maximum_xlsx_rows_per_sheet",
            "maximum_xlsx_columns": "maximum_xlsx_columns",
            "maximum_xlsx_nonempty_cells": "maximum_xlsx_nonempty_cells",
            "maximum_pptx_slides": "maximum_pptx_slides",
            "maximum_pptx_shapes": "maximum_pptx_shapes",
        },
        "transfer": {
            "maximum_archive_bytes": "maximum_archive_bytes",
            "maximum_archive_members": "maximum_archive_members",
            "maximum_extracted_bytes": "maximum_extracted_bytes",
            "maximum_compression_ratio": "maximum_compression_ratio",
        },
    }
    for section, mapping in sections.items():
        nested = raw.get(section, {})
        if not isinstance(nested, dict):
            raise ValueError(f"configuration section must be a mapping: {section}")
        for source_name, target_name in mapping.items():
            if source_name in nested:
                values[target_name] = nested[source_name]
    return values
