"""Explicit Milvus schema and dense-plus-BM25 hybrid retrieval."""

from __future__ import annotations

import json
from typing import Any

from knowledge_service.errors import KnowledgeUnavailableError
from knowledge_service.models import SearchHit


class MilvusKnowledgeStore:
    def __init__(
        self, *, uri: str, database: str, collection: str, dimension: int,
        token: str | None = None, client: Any | None = None,
    ) -> None:
        self.uri = uri
        self.database = database
        self.collection = collection
        self.dimension = int(dimension)
        self.token = token.strip() if token and token.strip() else None
        self._client = client

    def _connect_kwargs(self, database: str | None = None) -> dict[str, Any]:
        values: dict[str, Any] = {"uri": self.uri}
        if database:
            values["db_name"] = database
        if self.token:
            values["token"] = self.token
        return values

    def client(self):
        if self._client is not None:
            return self._client
        try:
            from pymilvus import MilvusClient

            bootstrap = MilvusClient(**self._connect_kwargs())
            if self.database not in bootstrap.list_databases():
                bootstrap.create_database(self.database)
            bootstrap.close()
            self._client = MilvusClient(**self._connect_kwargs(self.database))
            return self._client
        except Exception as exc:
            raise KnowledgeUnavailableError("Milvus connection failed") from exc

    def close(self) -> None:
        if self._client is not None:
            close = getattr(self._client, "close", None)
            if callable(close):
                close()
            self._client = None

    def ensure_collection(self) -> bool:
        client = self.client()
        if client.has_collection(self.collection):
            self._validate_existing_schema(client)
            client.load_collection(self.collection)
            return False
        try:
            from pymilvus import DataType, Function, FunctionType

            schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
            schema.add_field(
                field_name="chunk_id", datatype=DataType.VARCHAR,
                max_length=64, is_primary=True, auto_id=False,
            )
            for name, length in (
                ("owner_id", 128), ("kb_id", 64), ("doc_id", 64),
                ("version_id", 64), ("source_type", 32),
                ("source_label", 1024), ("language", 32), ("title", 1024),
                ("heading_path", 2048), ("content_hash", 64),
            ):
                schema.add_field(
                    field_name=name, datatype=DataType.VARCHAR, max_length=length
                )
            schema.add_field(field_name="is_active", datatype=DataType.BOOL)
            schema.add_field(field_name="chunk_index", datatype=DataType.INT64)
            schema.add_field(field_name="page_from", datatype=DataType.INT32)
            schema.add_field(field_name="page_to", datatype=DataType.INT32)
            schema.add_field(field_name="line_from", datatype=DataType.INT32)
            schema.add_field(field_name="line_to", datatype=DataType.INT32)
            schema.add_field(field_name="created_at", datatype=DataType.INT64)
            schema.add_field(field_name="metadata", datatype=DataType.JSON)
            schema.add_field(
                field_name="content", datatype=DataType.VARCHAR, max_length=8192,
                enable_analyzer=True,
                multi_analyzer_params={
                    "analyzers": {
                        "english": {"type": "english"},
                        "chinese": {"type": "chinese"},
                        "default": {"tokenizer": "icu"},
                    },
                    "by_field": "language",
                    "alias": {"cn": "chinese", "en": "english"},
                },
            )
            schema.add_field(
                field_name="dense_vector", datatype=DataType.FLOAT_VECTOR,
                dim=self.dimension,
            )
            schema.add_field(
                field_name="sparse_vector", datatype=DataType.SPARSE_FLOAT_VECTOR
            )
            schema.add_function(
                Function(
                    name="content_to_bm25", function_type=FunctionType.BM25,
                    input_field_names=["content"], output_field_names=["sparse_vector"],
                )
            )
            indexes = client.prepare_index_params()
            indexes.add_index(
                field_name="dense_vector", index_name="dense_hnsw",
                index_type="HNSW", metric_type="COSINE",
                params={"M": 16, "efConstruction": 200},
            )
            indexes.add_index(
                field_name="sparse_vector", index_name="sparse_bm25",
                index_type="SPARSE_INVERTED_INDEX", metric_type="BM25",
                params={"inverted_index_algo": "DAAT_MAXSCORE"},
            )
            for field in (
                "owner_id", "kb_id", "doc_id", "version_id", "is_active", "language"
            ):
                indexes.add_index(
                    field_name=field, index_name=f"{field}_scalar", index_type="AUTOINDEX"
                )
            client.create_collection(
                collection_name=self.collection, schema=schema,
                index_params=indexes, consistency_level="Session",
            )
            client.load_collection(self.collection)
            return True
        except Exception as exc:
            raise KnowledgeUnavailableError("Milvus collection initialization failed") from exc

    def _validate_existing_schema(self, client) -> None:
        description = client.describe_collection(self.collection)
        fields = {
            item.get("name"): item
            for item in description.get("fields", [])
            if isinstance(item, dict)
        }
        required = {
            "chunk_id", "owner_id", "kb_id", "doc_id", "version_id",
            "content", "dense_vector", "sparse_vector",
        }
        missing = sorted(required - set(fields))
        if missing:
            raise KnowledgeUnavailableError(
                "Existing Milvus collection is incompatible: " + ", ".join(missing)
            )
        actual = int(fields["dense_vector"].get("params", {}).get("dim", self.dimension))
        if actual != self.dimension:
            raise KnowledgeUnavailableError(
                f"Milvus dimension {actual} does not match {self.dimension}"
            )

    def upsert_chunks(self, rows: list[dict[str, object]]) -> int:
        if not rows:
            return 0
        self.ensure_collection()
        try:
            result = self.client().upsert(collection_name=self.collection, data=rows)
            return int(result.get("upsert_count", result.get("insert_count", len(rows))))
        except Exception as exc:
            raise KnowledgeUnavailableError("Milvus upsert failed") from exc

    def count_version(self, *, owner_id: str, kb_id: str, version_id: str) -> int:
        expression = build_filter_expression(
            owner_id=owner_id, kb_ids=[kb_id], version_id=version_id
        )
        try:
            rows = self.client().query(
                collection_name=self.collection, filter=expression,
                output_fields=["count(*)"], consistency_level="Strong",
            )
            if len(rows) == 1 and "count(*)" in rows[0]:
                return int(rows[0]["count(*)"])
            return len(rows)
        except Exception as exc:
            raise KnowledgeUnavailableError("Milvus verification query failed") from exc

    def delete_version(self, *, owner_id: str, kb_id: str, version_id: str) -> int:
        expression = build_filter_expression(
            owner_id=owner_id, kb_ids=[kb_id], version_id=version_id
        )
        try:
            result = self.client().delete(
                collection_name=self.collection, filter=expression
            )
            return int(result.get("delete_count", 0))
        except Exception as exc:
            raise KnowledgeUnavailableError("Milvus version deletion failed") from exc

    def hybrid_search(
        self, *, owner_id: str, kb_ids: list[str], version_ids: list[str],
        query: str, query_vector: list[float], language: str,
        limit: int, candidate_limit: int,
    ) -> list[SearchHit]:
        if len(query_vector) != self.dimension:
            raise ValueError("Query embedding dimension does not match Milvus")
        self.ensure_collection()
        expression = build_filter_expression(
            owner_id=owner_id, kb_ids=kb_ids, version_ids=version_ids
        )
        try:
            from pymilvus import AnnSearchRequest, RRFRanker

            dense = AnnSearchRequest(
                data=[query_vector], anns_field="dense_vector",
                param={"metric_type": "COSINE", "params": {"ef": 64}},
                limit=candidate_limit, expr=expression,
            )
            sparse = AnnSearchRequest(
                data=[query], anns_field="sparse_vector",
                param={
                    "metric_type": "BM25",
                    "analyzer_name": analyzer_name(language),
                    "drop_ratio_search": "0",
                },
                limit=candidate_limit, expr=expression,
            )
            results = self.client().hybrid_search(
                collection_name=self.collection, reqs=[dense, sparse],
                ranker=RRFRanker(60), limit=max(1, min(int(limit), 20)),
                output_fields=[
                    "chunk_id", "kb_id", "doc_id", "version_id", "source_label",
                    "source_type", "title", "heading_path", "chunk_index",
                    "line_from", "line_to", "content",
                ],
                consistency_level="Bounded",
            )
        except Exception as exc:
            raise KnowledgeUnavailableError("Milvus hybrid search failed") from exc
        hits = results[0] if results else []
        return [_search_hit(hit) for hit in hits]

    def status(self) -> dict[str, object]:
        try:
            collections = self.client().list_collections()
            return {
                "ready": True,
                "database": self.database,
                "collection": self.collection,
                "collection_exists": self.collection in collections,
                "dimension": self.dimension,
            }
        except Exception as exc:
            if isinstance(exc, KnowledgeUnavailableError):
                raise
            raise KnowledgeUnavailableError("Milvus status failed") from exc


def analyzer_name(language: str) -> str:
    if language in {"chinese", "cn"}:
        return "cn"
    if language in {"english", "en"}:
        return "english"
    return "default"


def build_filter_expression(
    *,
    owner_id: str,
    kb_ids: list[str],
    version_id: str | None = None,
    version_ids: list[str] | None = None,
) -> str:
    if not owner_id.strip():
        raise ValueError("owner_id must not be empty")
    knowledge_bases = [value.strip() for value in kb_ids if value.strip()]
    if not knowledge_bases:
        raise ValueError("At least one kb_id is required")
    clauses = [
        f"owner_id == {json.dumps(owner_id, ensure_ascii=False)}",
        "kb_id in ["
        + ", ".join(json.dumps(value, ensure_ascii=False) for value in knowledge_bases)
        + "]",
        "is_active == true",
    ]
    if version_id:
        clauses.append(f"version_id == {json.dumps(version_id, ensure_ascii=False)}")
    elif version_ids is not None:
        versions = [value.strip() for value in version_ids if value.strip()]
        if not versions:
            raise ValueError("At least one active version_id is required")
        clauses.append(
            "version_id in ["
            + ", ".join(json.dumps(value, ensure_ascii=False) for value in versions)
            + "]"
        )
    return " and ".join(clauses)


def _search_hit(hit: dict[str, Any]) -> SearchHit:
    entity = hit.get("entity") or {}
    row = dict(entity)
    row.setdefault("chunk_id", hit.get("id", ""))
    return SearchHit.from_row(row, score=float(hit.get("distance", 0.0)))
