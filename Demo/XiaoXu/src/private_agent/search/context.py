"""Context-local prepared search passed from policy to tool execution."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator

from private_agent.search.config import KnowledgeSearchConfig, WebSearchConfig
from private_agent.search.coordinator import PreparedSearch, SearchKind

SearchCallConfig = WebSearchConfig | KnowledgeSearchConfig


@dataclass(frozen=True)
class SearchCallContext:
    prepared: PreparedSearch
    config: SearchCallConfig

_CURRENT_SEARCH_CALL: ContextVar[SearchCallContext | None] = ContextVar(
    "xiaoxu_current_search_call",
    default=None,
)


def current_prepared_search(kind: SearchKind | None = None) -> PreparedSearch:
    current = _CURRENT_SEARCH_CALL.get()
    if current is None:
        raise RuntimeError("search tool was called outside SearchPolicyMiddleware")
    prepared = current.prepared
    if kind is not None and prepared.kind != kind:
        raise RuntimeError(
            f"expected prepared {kind.value} search, got {prepared.kind.value}"
        )
    return prepared


def current_web_search_config() -> WebSearchConfig:
    current = _CURRENT_SEARCH_CALL.get()
    if current is None or not isinstance(current.config, WebSearchConfig):
        raise RuntimeError("web search was called outside SearchPolicyMiddleware")
    return current.config


def current_knowledge_search_config() -> KnowledgeSearchConfig:
    current = _CURRENT_SEARCH_CALL.get()
    if current is None or not isinstance(current.config, KnowledgeSearchConfig):
        raise RuntimeError("knowledge search was called outside SearchPolicyMiddleware")
    return current.config


@contextmanager
def prepared_search_context(
    prepared: PreparedSearch,
    config: SearchCallConfig,
) -> Iterator[None]:
    token = _CURRENT_SEARCH_CALL.set(SearchCallContext(prepared=prepared, config=config))
    try:
        yield
    finally:
        _CURRENT_SEARCH_CALL.reset(token)
