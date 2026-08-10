"""User-scoped hybrid retrieval."""

from knowledge_service.errors import KnowledgeUnavailableError
from knowledge_service.models import SearchResult
from knowledge_service.parsing.common import detect_language


class RetrievalService:
    def __init__(self, settings, catalog, embeddings, vectors) -> None:
        self.settings = settings
        self.catalog = catalog
        self.embeddings = embeddings
        self.vectors = vectors

    def search(
        self,
        *,
        owner_id: str,
        query: str,
        knowledge_bases: list[str] | None = None,
        limit: int | None = None,
    ) -> SearchResult:
        if self.settings.reindex_marker.exists():
            raise KnowledgeUnavailableError(
                "Knowledge index rebuild is required after a restore"
            )
        candidate = query.strip()
        if not candidate:
            raise ValueError("Knowledge query must not be empty")
        available = self.catalog.list_knowledge_bases(owner_id=owner_id)
        requested = {item.strip() for item in knowledge_bases or [] if item.strip()}
        if requested:
            selected = [
                item
                for item in available
                if item["kb_id"] in requested or item["name"] in requested
            ]
            selected_names = {
                value
                for item in selected
                for value in (str(item["kb_id"]), str(item["name"]))
            }
            missing = requested - selected_names
            if missing:
                raise ValueError(
                    "Unknown or unauthorized knowledge bases: " + ", ".join(sorted(missing))
                )
        else:
            selected = available
        kb_ids = [str(item["kb_id"]) for item in selected]
        if not kb_ids:
            return SearchResult(query=candidate)
        version_ids = self.catalog.active_version_ids(owner_id=owner_id, kb_ids=kb_ids)
        if not version_ids:
            return SearchResult(query=candidate, kb_ids=kb_ids)
        vector = self.embeddings.embed_query(candidate)
        hits = self.vectors.hybrid_search(
            owner_id=owner_id,
            kb_ids=kb_ids,
            version_ids=version_ids,
            query=candidate,
            query_vector=vector,
            language=detect_language(candidate),
            limit=limit or self.settings.rag_top_k,
            candidate_limit=self.settings.rag_candidate_limit,
        )
        return SearchResult(
            query=candidate,
            kb_ids=kb_ids,
            hits=hits,
            backends=("SQLite", "Milvus"),
        )
