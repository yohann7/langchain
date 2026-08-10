"""Per-turn query limits and result deduplication."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from threading import RLock
from typing import Any

from private_agent.search.normalization import (
    canonicalize_web_url,
    normalize_search_query,
    search_query_fingerprint,
)


class SearchKind(StrEnum):
    KNOWLEDGE = "knowledge"
    WEB = "web"


class SearchPolicyCode(StrEnum):
    CONFIG_INVALID = "SEARCH_CONFIG_INVALID"
    INVALID_ARGUMENT = "SEARCH_INVALID_ARGUMENT"
    DUPLICATE_QUERY = "SEARCH_DUPLICATE_QUERY"
    QUERY_LIMIT_REACHED = "SEARCH_QUERY_LIMIT_REACHED"
    KNOWLEDGE_EVIDENCE_FOUND = "KNOWLEDGE_EVIDENCE_ALREADY_FOUND"
    BACKEND_UNAVAILABLE = "SEARCH_BACKEND_UNAVAILABLE"


class SearchPolicyError(RuntimeError):
    def __init__(self, code: SearchPolicyCode, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class PreparedSearch:
    kind: SearchKind
    query: str
    fingerprint: str
    query_index: int
    max_queries: int

    @property
    def remaining_queries(self) -> int:
        return max(0, self.max_queries - self.query_index)


@dataclass(frozen=True)
class SearchProgress:
    query_index: int
    remaining_queries: int
    new_results: int
    total_unique_results: int
    duplicate_results: int
    updated_results: int = 0


@dataclass(frozen=True)
class SearchBatch:
    items: list[dict[str, Any]]
    updates: list[dict[str, Any]]
    progress: SearchProgress


@dataclass
class SearchTurnState:
    knowledge_query_count: int = 0
    web_query_count: int = 0
    knowledge_closed: bool = False
    web_closed: bool = False
    knowledge_failed: bool = False
    web_failed: bool = False
    knowledge_awaiting_result: bool = False
    web_awaiting_result: bool = False
    knowledge_fingerprints: set[str] = field(default_factory=set)
    web_fingerprints: set[str] = field(default_factory=set)
    seen_knowledge: set[tuple[str, str]] = field(default_factory=set)
    seen_web: dict[str, dict[str, Any]] = field(default_factory=dict)
    next_web_source_index: int = 1
    lock: RLock = field(default_factory=RLock, repr=False, compare=False)


class SearchCoordinator:
    def __init__(self, state: SearchTurnState) -> None:
        self.state = state

    def prepare_knowledge(
        self,
        query: str,
        knowledge_bases: list[str] | None = None,
        *,
        max_queries: int,
    ) -> PreparedSearch:
        normalized_query = normalize_search_query(query)
        if not normalized_query:
            raise ValueError("knowledge query must not be blank")
        normalized_bases = tuple(
            sorted(
                {
                    normalized.casefold()
                    for value in knowledge_bases or []
                    if (normalized := normalize_search_query(str(value)))
                }
            )
        )
        fingerprint = f"{search_query_fingerprint(normalized_query)}|{normalized_bases!r}"
        with self.state.lock:
            if self.state.knowledge_failed:
                raise SearchPolicyError(
                    SearchPolicyCode.BACKEND_UNAVAILABLE,
                    "知识库后端本轮不可用，已停止继续检索。",
                )
            if self.state.knowledge_closed:
                raise SearchPolicyError(
                    SearchPolicyCode.KNOWLEDGE_EVIDENCE_FOUND,
                    "知识库已返回可用证据，请直接基于现有结果回答。",
                )
            if fingerprint in self.state.knowledge_fingerprints:
                raise SearchPolicyError(
                    SearchPolicyCode.DUPLICATE_QUERY,
                    "已阻止本轮重复的知识库查询。",
                )
            if self.state.knowledge_awaiting_result:
                raise SearchPolicyError(
                    SearchPolicyCode.QUERY_LIMIT_REACHED,
                    "必须先等待当前知识库查询完成；只有成功空结果才可针对缺失事实改写查询。",
                )
            if self.state.knowledge_query_count >= max_queries:
                raise SearchPolicyError(
                    SearchPolicyCode.QUERY_LIMIT_REACHED,
                    "本轮知识库查询次数已用尽。",
                )
            self.state.knowledge_fingerprints.add(fingerprint)
            self.state.knowledge_query_count += 1
            self.state.knowledge_awaiting_result = True
            return PreparedSearch(
                kind=SearchKind.KNOWLEDGE,
                query=normalized_query,
                fingerprint=fingerprint,
                query_index=self.state.knowledge_query_count,
                max_queries=max_queries,
            )

    def prepare_web(self, query: str, *, max_queries: int) -> PreparedSearch:
        normalized_query = normalize_search_query(query)
        if not normalized_query:
            raise ValueError("web query must not be blank")
        fingerprint = search_query_fingerprint(normalized_query)
        with self.state.lock:
            if self.state.web_failed:
                raise SearchPolicyError(
                    SearchPolicyCode.BACKEND_UNAVAILABLE,
                    "网页搜索后端本轮不可用，已停止继续检索。",
                )
            if self.state.web_closed:
                raise SearchPolicyError(
                    SearchPolicyCode.BACKEND_UNAVAILABLE,
                    "网页搜索本轮已经关闭。",
                )
            if fingerprint in self.state.web_fingerprints:
                raise SearchPolicyError(
                    SearchPolicyCode.DUPLICATE_QUERY,
                    "已阻止本轮重复的网页查询。",
                )
            if self.state.web_awaiting_result:
                raise SearchPolicyError(
                    SearchPolicyCode.QUERY_LIMIT_REACHED,
                    "必须先评估当前网页查询结果，再决定是否针对缺失事实扩展查询。",
                )
            if self.state.web_query_count >= max_queries:
                raise SearchPolicyError(
                    SearchPolicyCode.QUERY_LIMIT_REACHED,
                    "本轮网页查询次数已用尽。",
                )
            self.state.web_fingerprints.add(fingerprint)
            self.state.web_query_count += 1
            self.state.web_awaiting_result = True
            return PreparedSearch(
                kind=SearchKind.WEB,
                query=normalized_query,
                fingerprint=fingerprint,
                query_index=self.state.web_query_count,
                max_queries=max_queries,
            )

    def accept_knowledge(
        self,
        prepared: PreparedSearch,
        hits: list[dict[str, Any]],
    ) -> SearchBatch:
        self._ensure_kind(prepared, SearchKind.KNOWLEDGE)
        items: list[dict[str, Any]] = []
        duplicate_results = 0
        with self.state.lock:
            self.state.knowledge_awaiting_result = False
            for raw in hits:
                chunk_id = str(raw.get("chunk_id") or raw.get("source_id") or "")
                if not chunk_id:
                    raise ValueError("knowledge hit is missing chunk_id")
                doc_id = str(raw.get("doc_id") or "")
                key = (doc_id, chunk_id)
                if key in self.state.seen_knowledge:
                    duplicate_results += 1
                    continue
                self.state.seen_knowledge.add(key)
                item = dict(raw)
                item["doc_id"] = doc_id
                item["chunk_id"] = chunk_id
                item.pop("source_id", None)
                items.append(item)
            if items:
                self.state.knowledge_closed = True
            remaining = (
                0
                if self.state.knowledge_closed
                else max(
                    0,
                    prepared.max_queries - self.state.knowledge_query_count,
                )
            )
            progress = SearchProgress(
                query_index=prepared.query_index,
                remaining_queries=remaining,
                new_results=len(items),
                total_unique_results=len(self.state.seen_knowledge),
                duplicate_results=duplicate_results,
            )
        return SearchBatch(items=items, updates=[], progress=progress)

    def accept_web(
        self,
        prepared: PreparedSearch,
        results: list[dict[str, Any]],
    ) -> SearchBatch:
        self._ensure_kind(prepared, SearchKind.WEB)
        items: list[dict[str, Any]] = []
        updates: list[dict[str, Any]] = []
        duplicate_results = 0
        with self.state.lock:
            self.state.web_awaiting_result = False
            for raw in results:
                canonical_url = canonicalize_web_url(str(raw.get("url") or ""))
                if canonical_url is None:
                    continue
                existing = self.state.seen_web.get(canonical_url)
                if existing is not None:
                    duplicate_results += 1
                    update = _richer_web_result(existing, raw, canonical_url)
                    if update is not None:
                        self.state.seen_web[canonical_url] = update
                        updates.append(dict(update))
                    continue
                item = dict(raw)
                item["url"] = canonical_url
                item["source_index"] = self.state.next_web_source_index
                self.state.next_web_source_index += 1
                self.state.seen_web[canonical_url] = item
                items.append(dict(item))
            progress = SearchProgress(
                query_index=prepared.query_index,
                remaining_queries=max(
                    0, prepared.max_queries - self.state.web_query_count
                ),
                new_results=len(items),
                total_unique_results=len(self.state.seen_web),
                duplicate_results=duplicate_results,
                updated_results=len(updates),
            )
        return SearchBatch(items=items, updates=updates, progress=progress)

    def fail(self, prepared: PreparedSearch, reason: str) -> None:
        del reason
        self.fail_kind(prepared.kind)

    def fail_kind(self, kind: SearchKind) -> None:
        """Close one search backend for the remainder of the current turn."""

        with self.state.lock:
            if kind == SearchKind.KNOWLEDGE:
                self.state.knowledge_failed = True
                self.state.knowledge_awaiting_result = False
                return
            if kind == SearchKind.WEB:
                self.state.web_failed = True
                self.state.web_closed = True
                self.state.web_awaiting_result = False
                return
        raise ValueError(f"unknown search kind: {kind}")

    @staticmethod
    def _ensure_kind(prepared: PreparedSearch, expected: SearchKind) -> None:
        if prepared.kind != expected:
            raise ValueError(f"expected {expected.value} search, got {prepared.kind.value}")


def _richer_web_result(
    existing: dict[str, Any],
    candidate: dict[str, Any],
    canonical_url: str,
) -> dict[str, Any] | None:
    existing_title = str(existing.get("title") or "")
    existing_content = str(existing.get("content") or "")
    candidate_title = str(candidate.get("title") or "")
    candidate_content = str(candidate.get("content") or "")
    if len(candidate_title) <= len(existing_title) and len(candidate_content) <= len(
        existing_content
    ):
        return None
    update = dict(existing)
    if len(candidate_title) > len(existing_title):
        update["title"] = candidate_title
    if len(candidate_content) > len(existing_content):
        update["content"] = candidate_content
    update["url"] = canonical_url
    update["updated"] = True
    return update
