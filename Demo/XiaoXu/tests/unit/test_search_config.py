from __future__ import annotations

from pathlib import Path

import pytest

from private_agent.search.config import (
    KnowledgeSearchConfig,
    SearchConfigError,
    WebSearchConfig,
    load_knowledge_search_config,
    load_web_search_config,
)


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_load_web_search_config_is_strict_and_frozen(tmp_path):
    path = _write(
        tmp_path / "web.yaml",
        """
version: 1
max_queries_per_turn: 7
max_results_per_query: 12
request_timeout_seconds: 2.5
searxng:
  max_attempts: 3
  retry_delays_seconds: [0.1, 0.2]
tavily_fallback_enabled: false
""",
    )

    config = load_web_search_config(path)

    assert config == WebSearchConfig(
        version=1,
        max_queries_per_turn=7,
        max_results_per_query=12,
        request_timeout_seconds=2.5,
        searxng={"max_attempts": 3, "retry_delays_seconds": [0.1, 0.2]},
        tavily_fallback_enabled=False,
    )
    with pytest.raises(Exception):
        config.max_queries_per_turn = 8


def test_load_knowledge_search_config(tmp_path):
    path = _write(
        tmp_path / "knowledge.yaml",
        """
version: 1
max_queries_per_turn: 4
default_results_per_query: 8
max_results_per_query: 16
request_timeout_seconds: 3.5
""",
    )

    assert load_knowledge_search_config(path) == KnowledgeSearchConfig(
        version=1,
        max_queries_per_turn=4,
        default_results_per_query=8,
        max_results_per_query=16,
        request_timeout_seconds=3.5,
    )


@pytest.mark.parametrize(
    "content",
    [
        "",
        "- not\n- a\n- mapping\n",
        "version: [broken\n",
        "version: 2\nmax_queries_per_turn: 1\nmax_results_per_query: 1\nrequest_timeout_seconds: 1\nsearxng: {max_attempts: 1, retry_delays_seconds: []}\ntavily_fallback_enabled: true\n",
        "version: 1\nmax_queries_per_turn: 0\nmax_results_per_query: 1\nrequest_timeout_seconds: 1\nsearxng: {max_attempts: 1, retry_delays_seconds: []}\ntavily_fallback_enabled: true\n",
        "version: 1\nmax_queries_per_turn: 1\nmax_results_per_query: 1\nrequest_timeout_seconds: 1\nsearxng: {max_attempts: 2, retry_delays_seconds: []}\ntavily_fallback_enabled: true\n",
        "version: 1\nmax_queries_per_turn: 1\nmax_results_per_query: 1\nrequest_timeout_seconds: 1\nsearxng: {max_attempts: 1, retry_delays_seconds: []}\ntavily_fallback_enabled: true\nunknown: rejected\n",
        "version: 1\nmax_queries_per_turn: '1'\nmax_results_per_query: 1\nrequest_timeout_seconds: 1\nsearxng: {max_attempts: 1, retry_delays_seconds: []}\ntavily_fallback_enabled: true\n",
    ],
)
def test_web_search_config_rejects_invalid_documents(tmp_path, content):
    path = _write(tmp_path / "web.yaml", content)

    with pytest.raises(SearchConfigError, match="web search configuration is invalid"):
        load_web_search_config(path)


@pytest.mark.parametrize(
    "content",
    [
        "version: 1\nmax_queries_per_turn: 1\ndefault_results_per_query: 11\nmax_results_per_query: 10\nrequest_timeout_seconds: 1\n",
        "version: 1\nmax_queries_per_turn: 1\ndefault_results_per_query: 10\nmax_results_per_query: 21\nrequest_timeout_seconds: 1\n",
        "version: 1\nmax_queries_per_turn: 1\ndefault_results_per_query: 10\nmax_results_per_query: 20\nrequest_timeout_seconds: -1\n",
        "version: 1\nmax_queries_per_turn: 1\ndefault_results_per_query: 10\nmax_results_per_query: 20\nrequest_timeout_seconds: 1\nunknown: rejected\n",
    ],
)
def test_knowledge_search_config_rejects_invalid_documents(tmp_path, content):
    path = _write(tmp_path / "knowledge.yaml", content)

    with pytest.raises(
        SearchConfigError,
        match="knowledge search configuration is invalid",
    ):
        load_knowledge_search_config(path)


@pytest.mark.parametrize(
    ("loader", "filename", "message"),
    [
        (load_web_search_config, "missing-web.yaml", "web search configuration is invalid"),
        (
            load_knowledge_search_config,
            "missing-knowledge.yaml",
            "knowledge search configuration is invalid",
        ),
    ],
)
def test_search_config_missing_file_is_stable_error(tmp_path, loader, filename, message):
    with pytest.raises(SearchConfigError, match=message):
        loader(tmp_path / filename)


def test_default_search_config_files_match_contract():
    project_root = Path(__file__).resolve().parents[2]

    web = load_web_search_config(project_root / "config" / "web-search.yaml")
    knowledge = load_knowledge_search_config(
        project_root / "config" / "knowledge-search.yaml"
    )

    assert web.max_queries_per_turn == 3
    assert web.max_results_per_query == 10
    assert web.searxng.max_attempts == 5
    assert web.searxng.retry_delays_seconds == (0.5, 1.0, 2.0, 4.0)
    assert knowledge.max_queries_per_turn == 2
    assert knowledge.default_results_per_query == 10
    assert knowledge.max_results_per_query == 20
