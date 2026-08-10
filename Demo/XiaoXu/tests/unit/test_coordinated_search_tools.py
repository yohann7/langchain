from __future__ import annotations

import json

from private_agent.agent_factory import build_tools, create_resources
from private_agent.config import AppSettings
from private_agent.knowledge.schemas import KnowledgeHit, KnowledgeSearchResponse
from private_agent.knowledge.errors import KnowledgeUnavailableError
from private_agent.runtime import RuntimeState, runtime_context
from private_agent.search import SearchCoordinator
from private_agent.search.config import KnowledgeSearchConfig, WebSearchConfig
from private_agent.search.context import prepared_search_context
from private_agent.security import PermissionPolicy
from private_agent.tools.search_tools import WebSearchOutcome
from private_agent.tool_usage import extract_tool_usage_backend
import private_agent.agent.factory as agent_factory


def _tool(tools, name):
    return next(item for item in tools if item.name == name)


def _progress(text: str) -> dict[str, int]:
    line = next(row for row in text.splitlines() if row.startswith("SEARCH_PROGRESS "))
    return json.loads(line.removeprefix("SEARCH_PROGRESS "))


def _web_config(max_results: int = 10) -> WebSearchConfig:
    return WebSearchConfig(
        version=1,
        max_queries_per_turn=3,
        max_results_per_query=max_results,
        request_timeout_seconds=2.5,
        searxng={"max_attempts": 1, "retry_delays_seconds": []},
        tavily_fallback_enabled=False,
    )


def _knowledge_config() -> KnowledgeSearchConfig:
    return KnowledgeSearchConfig(
        version=1,
        max_queries_per_turn=2,
        default_results_per_query=10,
        max_results_per_query=20,
        request_timeout_seconds=4.5,
    )


def test_web_tool_uses_configured_result_limit_and_cross_query_source_numbers(
    tmp_path, monkeypatch
):
    settings = AppSettings(
        run_dir=tmp_path,
        enable_summarization_middleware=False,
    )
    runtime = RuntimeState()
    resources = create_resources(settings, PermissionPolicy(), runtime)
    tool = _tool(build_tools(resources), "web_search")
    calls = []
    outcomes = iter(
        [
            WebSearchOutcome(
                results=[
                    {
                        "title": "First",
                        "url": "https://example.com/a?utm_source=x",
                        "content": "A",
                    }
                ],
                backend="SearXNG",
                available=True,
            ),
            WebSearchOutcome(
                results=[
                    {
                        "title": "First duplicate",
                        "url": "https://EXAMPLE.com:443/a#fragment",
                        "content": "A richer summary",
                    },
                    {
                        "title": "Second",
                        "url": "https://example.com/b",
                        "content": "B",
                    },
                ],
                backend="Tavily",
                available=True,
            ),
        ]
    )

    def fake_search(query, api_key_env, *, config, **kwargs):
        calls.append((query, api_key_env, config, kwargs))
        return next(outcomes)

    monkeypatch.setattr(agent_factory, "web_search_result", fake_search)
    state = runtime.begin_search_turn()
    coordinator = SearchCoordinator(state)
    config = _web_config()

    with runtime_context(runtime):
        first = coordinator.prepare_web(
            " first ", max_queries=config.max_queries_per_turn
        )
        with prepared_search_context(first, config):
            first_text = tool.invoke({"query": first.query})
        second = coordinator.prepare_web(
            "second", max_queries=config.max_queries_per_turn
        )
        with prepared_search_context(second, config):
            second_text = tool.invoke({"query": second.query})

    assert calls[0][2] is config
    assert calls[0][2].max_results_per_query == 10
    assert "1. First" in first_text
    assert "2. Second" in second_text
    assert "SOURCE_UPDATE 1" in second_text
    assert _progress(second_text) == {
        "duplicate_results": 1,
        "new_results": 1,
        "query_index": 2,
        "remaining_queries": 1,
        "total_unique_results": 2,
        "updated_results": 1,
    }


def test_knowledge_tool_deduplicates_chunks_and_closes_after_new_evidence(
    tmp_path, monkeypatch
):
    settings = AppSettings(run_dir=tmp_path, enable_summarization_middleware=False)
    runtime = RuntimeState()
    resources = create_resources(settings, PermissionPolicy(), runtime)
    tool = _tool(build_tools(resources), "search_knowledge")
    calls = []

    def fake_search(**kwargs):
        calls.append(kwargs)
        return KnowledgeSearchResponse(
            query=str(kwargs["query"]),
            hits=[
                KnowledgeHit(
                    doc_id="D1",
                    chunk_id="C1",
                    document_name="guide.md",
                    location="section 1",
                    content="Evidence",
                    score=0.9,
                    knowledge_base="personal",
                ),
                KnowledgeHit(
                    doc_id="D1",
                    chunk_id="C1",
                    document_name="guide.md",
                    location="section 1",
                    content="Duplicate",
                    score=0.8,
                    knowledge_base="personal",
                ),
            ],
            backends=["Milvus"],
            request_id="req-1",
        )

    monkeypatch.setattr(resources.knowledge, "search", fake_search)
    state = runtime.begin_search_turn()
    coordinator = SearchCoordinator(state)
    config = _knowledge_config()
    prepared = coordinator.prepare_knowledge(
        "deployment", max_queries=config.max_queries_per_turn
    )

    with runtime_context(runtime), prepared_search_context(prepared, config):
        text = tool.invoke({"query": prepared.query})

    assert calls[0]["limit"] == 10
    assert calls[0]["timeout_seconds"] == 4.5
    assert text.count("Evidence") == 1
    assert "doc_id=D1 chunk_id=C1" in text
    assert _progress(text)["new_results"] == 1
    assert _progress(text)["duplicate_results"] == 1
    assert _progress(text)["remaining_queries"] == 0
    assert state.knowledge_closed is True


def test_web_backend_failure_closes_only_web_search(tmp_path, monkeypatch):
    settings = AppSettings(run_dir=tmp_path, enable_summarization_middleware=False)
    runtime = RuntimeState()
    resources = create_resources(settings, PermissionPolicy(), runtime)
    tool = _tool(build_tools(resources), "web_search")
    monkeypatch.setattr(
        agent_factory,
        "web_search_result",
        lambda *args, **kwargs: WebSearchOutcome(
            results=[],
            backend="Tavily",
            available=False,
            error_code="SEARCH_BACKEND_UNAVAILABLE",
            message="all backends unavailable",
        ),
    )
    state = runtime.begin_search_turn()
    coordinator = SearchCoordinator(state)
    config = _web_config()
    prepared = coordinator.prepare_web(
        "status", max_queries=config.max_queries_per_turn
    )

    with runtime_context(runtime), prepared_search_context(prepared, config):
        text = tool.invoke({"query": prepared.query})

    payload = json.loads(text.split("\n\n<!--", 1)[0])
    assert payload["error"]["code"] == "SEARCH_BACKEND_UNAVAILABLE"
    assert payload["query_index"] == 1
    assert payload["new_results"] == 0
    assert payload["total_unique_results"] == 0
    assert payload["duplicate_results"] == 0
    assert payload["updated_results"] == 0
    assert payload["remaining_queries"] == 0
    assert state.web_failed is True
    assert state.knowledge_failed is False


def test_knowledge_backend_failure_reports_none_and_closes_knowledge(
    tmp_path, monkeypatch
):
    settings = AppSettings(run_dir=tmp_path, enable_summarization_middleware=False)
    runtime = RuntimeState()
    resources = create_resources(settings, PermissionPolicy(), runtime)
    tool = _tool(build_tools(resources), "search_knowledge")
    monkeypatch.setattr(
        resources.knowledge,
        "search",
        lambda **kwargs: (_ for _ in ()).throw(
            KnowledgeUnavailableError("offline")
        ),
    )
    state = runtime.begin_search_turn()
    config = _knowledge_config()
    prepared = SearchCoordinator(state).prepare_knowledge(
        "private", max_queries=config.max_queries_per_turn
    )

    with runtime_context(runtime), prepared_search_context(prepared, config):
        text = tool.invoke({"query": prepared.query})

    assert "SEARCH_BACKEND_UNAVAILABLE" in text
    assert extract_tool_usage_backend(text, "knowledge_search") == "None"
    assert state.knowledge_failed is True
    assert state.web_failed is False
