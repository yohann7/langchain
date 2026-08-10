from types import SimpleNamespace
import sys
import inspect

import pytest

from private_agent.tools.search_tools import (
    WebSearchOutcome,
    format_web_search_result,
    format_web_search_sources,
    web_search_result,
    web_search,
)
from private_agent.tool_usage import extract_tool_usage_backend
import private_agent.tools.search_tools as search_tools
from private_agent.search.config import WebSearchConfig


@pytest.fixture(autouse=True)
def disable_retry_sleep(monkeypatch):
    monkeypatch.setattr(search_tools.time, "sleep", lambda _seconds: None)


def _web_config(
    *,
    max_results: int = 10,
    timeout: float = 10.0,
    max_attempts: int = 5,
    delays: list[float] | None = None,
    fallback: bool = True,
) -> WebSearchConfig:
    if delays is None:
        delays = [0.5, 1.0, 2.0, 4.0]
    return WebSearchConfig(
        version=1,
        max_queries_per_turn=3,
        max_results_per_query=max_results,
        request_timeout_seconds=timeout,
        searxng={
            "max_attempts": max_attempts,
            "retry_delays_seconds": delays,
        },
        tavily_fallback_enabled=fallback,
    )


def test_web_search_result_uses_configured_attempts_delays_timeout_and_limit(
    monkeypatch,
):
    attempts = []
    delays = []

    def eventually_succeeds(url, *, params, timeout):
        attempts.append((url, params, timeout))
        if len(attempts) < 3:
            raise search_tools.httpx.ConnectError("offline")
        return FakeSearxngResponse(
            {
                "results": [
                    {
                        "title": f"Result {index}",
                        "url": f"https://example.com/{index}",
                        "content": "evidence",
                    }
                    for index in range(4)
                ]
            }
        )

    monkeypatch.setattr(search_tools.httpx, "get", eventually_succeeds)
    monkeypatch.setattr(search_tools.time, "sleep", delays.append)

    outcome = web_search_result(
        "configured",
        config=_web_config(
            max_results=2,
            timeout=1.25,
            max_attempts=3,
            delays=[0.1, 0.2],
        ),
        searxng_url="http://search.internal:8080",
    )

    assert len(attempts) == 3
    assert [item[2] for item in attempts] == [1.25, 1.25, 1.25]
    assert delays == [0.1, 0.2]
    assert len(outcome.results) == 2


def test_web_search_result_skips_tavily_when_fallback_is_disabled(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TAVILY_API_KEY", "must-not-be-used")
    monkeypatch.setattr(
        search_tools.httpx,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            search_tools.httpx.ConnectError("offline")
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "langchain_tavily",
        SimpleNamespace(
            TavilySearch=lambda **kwargs: (_ for _ in ()).throw(
                AssertionError("fallback must remain disabled")
            )
        ),
    )

    outcome = web_search_result(
        "no fallback",
        config=_web_config(max_attempts=1, delays=[], fallback=False),
    )

    assert outcome.available is False
    assert outcome.backend == "SearXNG"


def test_format_web_search_result_renders_structured_tavily_results():
    result = format_web_search_result(
        {
            "answer": "示例答案",
            "results": [
                {
                    "title": "Example",
                    "url": "https://example.com",
                    "content": "Example summary",
                },
                {
                    "title": "Docs",
                    "url": "https://docs.example.com",
                    "snippet": "Docs summary",
                },
            ],
        }
    )

    assert "摘要：示例答案" in result
    assert "1. Example" in result
    assert "URL: https://example.com" in result
    assert "Example summary" in result
    assert "2. Docs" in result
    assert "Docs summary" in result


def test_web_search_compatibility_entrypoint_requires_typed_config():
    parameters = inspect.signature(web_search).parameters

    assert "max_results" not in parameters
    assert parameters["config"].default is inspect.Parameter.empty


def test_format_web_search_sources_renders_only_titles_and_urls():
    result = format_web_search_sources(
        {
            "answer": "示例答案",
            "results": [
                {
                    "title": "Example",
                    "url": "https://example.com",
                    "content": "Example summary",
                },
                {
                    "title": "Docs",
                    "url": "https://docs.example.com",
                    "snippet": "Docs summary",
                },
            ],
        }
    )

    assert result == "\n".join(
        [
            "1. Example",
            "   URL: https://example.com",
            "2. Docs",
            "   URL: https://docs.example.com",
        ]
    )
    assert "示例答案" not in result
    assert "Example summary" not in result
    assert "Docs summary" not in result


def test_format_web_search_sources_preserves_turn_global_source_numbers():
    structured = format_web_search_sources(
        {
            "results": [
                {
                    "source_index": 7,
                    "title": "Later source",
                    "url": "https://example.com/later",
                }
            ]
        }
    )
    formatted_text = format_web_search_sources(
        "7. Later source\n   URL: https://example.com/later"
    )

    assert structured.startswith("7. Later source")
    assert formatted_text.startswith("7. Later source")


class FakeSearxngResponse:
    def __init__(self, payload, *, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            request = search_tools.httpx.Request("GET", "http://searxng:8080/search")
            response = search_tools.httpx.Response(self.status_code, request=request)
            raise search_tools.httpx.HTTPStatusError(
                "SearXNG request failed",
                request=request,
                response=response,
            )

    def json(self):
        return self.payload


def test_web_search_prefers_searxng_and_does_not_call_tavily(monkeypatch):
    captured = []

    def fake_get(url, *, params, timeout):
        captured.append({"url": url, "params": params, "timeout": timeout})
        return FakeSearxngResponse(
            {
                "results": [
                    {
                        "title": "SearXNG result",
                        "url": "https://example.com/searxng",
                        "content": "SearXNG summary",
                    }
                ]
            }
        )

    class UnexpectedTavilySearch:
        def __init__(self, *args, **kwargs):
            raise AssertionError("Tavily must not be used when SearXNG succeeds")

    monkeypatch.setattr(search_tools.httpx, "get", fake_get)
    monkeypatch.setitem(
        sys.modules,
        "langchain_tavily",
        SimpleNamespace(TavilySearch=UnexpectedTavilySearch),
    )

    result = web_search(
        "优先使用 SearXNG",
        config=_web_config(max_results=3, timeout=2.5),
        searxng_url="http://search.internal:8080/",
    )

    assert captured == [
        {
            "url": "http://search.internal:8080/search",
            "params": {
                "q": "优先使用 SearXNG",
                "format": "json",
            },
            "timeout": 2.5,
        }
    ]
    assert result.startswith("1. SearXNG result")
    assert "SearXNG summary" in result
    assert "本次搜索使用Tavily" not in result
    assert extract_tool_usage_backend(result, "web_search") == "SearXNG"


def test_web_search_retries_searxng_five_times_then_reads_tavily_key_from_dotenv(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    (tmp_path / ".env").write_text("TAVILY_API_KEY=dotenv-tavily-key\n", encoding="utf-8")
    captured = {}
    searxng_attempts = []

    def failing_get(url, *, params, timeout):
        searxng_attempts.append((url, params, timeout))
        raise search_tools.httpx.ConnectError("connection refused")

    class FakeTavilySearch:
        def __init__(self, max_results, tavily_api_key):
            captured["max_results"] = max_results
            captured["tavily_api_key"] = tavily_api_key

        def invoke(self, payload):
            captured["payload"] = payload
            return {
                "results": [
                    {
                        "title": "Example",
                        "url": "https://example.com",
                        "content": "Example summary",
                    }
                ]
            }

    monkeypatch.setitem(
        sys.modules,
        "langchain_tavily",
        SimpleNamespace(TavilySearch=FakeTavilySearch),
    )
    monkeypatch.setattr(search_tools.httpx, "get", failing_get)

    result = web_search(
        "马克思 马斯克",
        config=_web_config(max_results=3),
        searxng_url="http://searxng:8080",
    )

    assert len(searxng_attempts) == 5
    assert captured == {
        "max_results": 3,
        "tavily_api_key": "dotenv-tavily-key",
        "payload": {"query": "马克思 马斯克"},
    }
    assert not result.startswith("[SearXNG失败：")
    assert "Example summary" in result
    assert extract_tool_usage_backend(result, "web_search") == "Tavily"


def test_web_search_result_treats_valid_empty_searxng_response_as_success(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    attempts = 0

    def empty_get(url, *, params, timeout):
        nonlocal attempts
        attempts += 1
        return FakeSearxngResponse({"results": []})

    monkeypatch.setattr(search_tools.httpx, "get", empty_get)

    outcome = web_search_result("no matching evidence", config=_web_config())

    assert attempts == 1
    assert outcome.available is True
    assert outcome.backend == "SearXNG"
    assert outcome.results == []


def test_web_search_reports_missing_tavily_after_five_searxng_failures(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setattr(
        search_tools.httpx,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            search_tools.httpx.ConnectError("offline")
        ),
    )

    result = web_search("无可用搜索服务", config=_web_config(max_results=4))

    assert not result.startswith("[SearXNG失败：")
    assert extract_tool_usage_backend(result, "web_search") == "SearXNG"
    assert "Tavily搜索未配置" in result


def test_web_search_uses_backoff_between_searxng_attempts(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    delays = []
    attempts = 0

    def failing_get(url, *, params, timeout):
        nonlocal attempts
        attempts += 1
        raise search_tools.httpx.ConnectError("offline")

    monkeypatch.setattr(search_tools.httpx, "get", failing_get)
    monkeypatch.setattr(search_tools.time, "sleep", delays.append)

    result = web_search("backoff test", config=_web_config())

    assert attempts == 5
    assert delays == [0.5, 1.0, 2.0, 4.0]
    assert extract_tool_usage_backend(result, "web_search") == "SearXNG"


def test_web_search_result_exposes_structured_evidence_for_turn_dedup(monkeypatch):
    monkeypatch.setattr(
        search_tools.httpx,
        "get",
        lambda *args, **kwargs: FakeSearxngResponse(
            {
                "results": [
                    {
                        "title": "Structured",
                        "url": "https://example.com/?utm_source=test",
                        "content": "Evidence",
                    }
                ]
            }
        ),
    )

    outcome = web_search_result("structured", config=_web_config())

    assert isinstance(outcome, WebSearchOutcome)
    assert outcome.backend == "SearXNG"
    assert outcome.available is True
    assert outcome.results[0]["title"] == "Structured"
    assert outcome.message is None


def test_web_search_result_marks_all_backends_unavailable(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setattr(
        search_tools.httpx,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            search_tools.httpx.ConnectError("offline")
        ),
    )

    outcome = web_search_result("unavailable", config=_web_config())

    assert outcome.available is False
    assert outcome.results == []
    assert outcome.error_code == "SEARCH_BACKEND_UNAVAILABLE"


def test_web_search_result_normalizes_tavily_raw_content(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    monkeypatch.setattr(
        search_tools.httpx,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            search_tools.httpx.ConnectError("offline")
        ),
    )

    class FakeTavilySearch:
        def __init__(self, **kwargs):
            del kwargs

        def invoke(self, payload):
            del payload
            return {
                "results": [
                    {
                        "title": "Raw",
                        "url": "https://example.com/raw",
                        "raw_content": "longer raw evidence",
                    }
                ]
            }

    monkeypatch.setitem(
        sys.modules,
        "langchain_tavily",
        SimpleNamespace(TavilySearch=FakeTavilySearch),
    )

    outcome = web_search_result("raw content", config=_web_config())

    assert outcome.backend == "Tavily"
    assert outcome.results[0]["content"] == "longer raw evidence"
