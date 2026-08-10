"""Network search tool wrapper."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any

from dotenv import dotenv_values
import httpx

from private_agent.search.config import WebSearchConfig
from private_agent.tool_usage import append_tool_usage_marker


DEFAULT_SEARXNG_URL = "http://searxng:8080"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WebSearchOutcome:
    """Structured backend result used by per-turn deduplication."""

    results: list[dict[str, Any]]
    backend: str
    available: bool
    answer: str | None = None
    error_code: str | None = None
    message: str | None = None


def web_search_result(
    query: str,
    api_key_env: str = "TAVILY_API_KEY",
    *,
    config: WebSearchConfig,
    searxng_url: str | None = None,
) -> WebSearchOutcome:
    """Return structured evidence using one immutable call configuration."""

    selected_searxng_url = (
        searxng_url
        or _environment_value("PRIVATE_AGENT_SEARXNG_URL")
        or DEFAULT_SEARXNG_URL
    )
    failure_reason = "unknown error"
    query_id = _query_id(query)
    for attempt in range(1, config.searxng.max_attempts + 1):
        try:
            result = _search_searxng(
                query,
                selected_searxng_url,
                max_results=config.max_results_per_query,
                timeout_seconds=config.request_timeout_seconds,
            )
            logger.info(
                "SearXNG search succeeded query_id=%s attempt=%s results=%s",
                query_id,
                attempt,
                len(result.get("results", [])),
            )
            return WebSearchOutcome(
                results=_structured_results(result, config.max_results_per_query),
                answer=_string_value(result.get("answer")),
                backend="SearXNG",
                available=True,
            )
        except Exception as exc:
            failure_reason = _simple_failure_reason(exc)
            logger.warning(
                "SearXNG search failed query_id=%s attempt=%s/%s reason=%s",
                query_id,
                attempt,
                config.searxng.max_attempts,
                failure_reason,
            )
            if attempt < config.searxng.max_attempts:
                time.sleep(config.searxng.retry_delays_seconds[attempt - 1])

    logger.error(
        "SearXNG exhausted configured attempts query_id=%s reason=%s",
        query_id,
        failure_reason,
    )
    if not config.tavily_fallback_enabled:
        return WebSearchOutcome(
            results=[],
            backend="SearXNG",
            available=False,
            error_code="SEARCH_BACKEND_UNAVAILABLE",
            message="SearXNG search failed and Tavily fallback is disabled.",
        )
    api_key = _secret_value(api_key_env)
    if not api_key:
        return WebSearchOutcome(
            results=[],
            backend="SearXNG",
            available=False,
            error_code="SEARCH_BACKEND_UNAVAILABLE",
            message=(
                f"SearXNG在配置的尝试次数内搜索失败（{failure_reason}）；"
                f"Tavily搜索未配置，请设置环境变量 {api_key_env}。"
            ),
        )
    try:
        from langchain_tavily import TavilySearch

        search = TavilySearch(
            max_results=config.max_results_per_query,
            tavily_api_key=api_key,
        )
        result = search.invoke({"query": query})
        result_dict = result if isinstance(result, dict) else {"results": []}
        return WebSearchOutcome(
            results=_structured_results(result_dict, config.max_results_per_query),
            answer=_string_value(result_dict.get("answer")),
            backend="Tavily",
            available=True,
        )
    except Exception as exc:
        return WebSearchOutcome(
            results=[],
            backend="Tavily",
            available=False,
            error_code="SEARCH_BACKEND_UNAVAILABLE",
            message=f"Tavily搜索失败：{_simple_failure_reason(exc)}",
        )


def _structured_results(result: dict[str, Any], max_results: int) -> list[dict[str, Any]]:
    raw_results = result.get("results")
    if not isinstance(raw_results, list):
        return []
    normalized: list[dict[str, Any]] = []
    for raw in raw_results:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item["title"] = _string_value(raw.get("title")) or "Untitled"
        item["url"] = _string_value(raw.get("url")) or ""
        item["content"] = (
            _string_value(raw.get("content"))
            or _string_value(raw.get("snippet"))
            or _string_value(raw.get("raw_content"))
            or ""
        )
        normalized.append(item)
        if len(normalized) >= max_results:
            break
    return normalized


def web_search(
    query: str,
    api_key_env: str = "TAVILY_API_KEY",
    *,
    config: WebSearchConfig,
    searxng_url: str | None = None,
) -> str:
    """Search with SearXNG first and use the configured fallback policy."""

    outcome = web_search_result(
        query,
        api_key_env,
        config=config,
        searxng_url=searxng_url,
    )
    if not outcome.available:
        return append_tool_usage_marker(
            outcome.message or "搜索后端不可用。",
            "web_search",
            outcome.backend,
        )
    result: dict[str, Any] = {"results": outcome.results}
    if outcome.answer:
        result["answer"] = outcome.answer
    return append_tool_usage_marker(
        format_web_search_result(result),
        "web_search",
        outcome.backend,
    )


def _search_searxng(
    query: str,
    searxng_url: str,
    *,
    max_results: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    endpoint = f"{searxng_url.rstrip('/')}/search"
    response = httpx.get(
        endpoint,
        params={
            "q": query,
            "format": "json",
        },
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("SearXNG返回内容不是有效JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("SearXNG返回结构无效")

    normalized_results: list[dict[str, str]] = []
    raw_results = payload.get("results")
    if not isinstance(raw_results, list):
        raise ValueError("SearXNG返回结构缺少results列表")
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        url = _string_value(item.get("url"))
        if not url:
            continue
        normalized_results.append(
            {
                "title": _string_value(item.get("title")) or "Untitled",
                "url": url,
                "content": _string_value(item.get("content")) or "",
            }
        )
        if len(normalized_results) >= max_results:
            break
    if raw_results and not normalized_results:
        raise ValueError("SearXNG返回的results条目无效")

    unresponsive_engines = payload.get("unresponsive_engines")
    if unresponsive_engines:
        logger.warning(
            "SearXNG returned partial results query_id=%s results=%s "
            "unresponsive_engines=%s",
            _query_id(query),
            len(normalized_results),
            _bounded_diagnostic(unresponsive_engines),
        )

    result: dict[str, Any] = {"results": normalized_results}
    answers = payload.get("answers")
    if isinstance(answers, list):
        answer_text = "\n".join(
            answer for answer in answers if isinstance(answer, str) and answer.strip()
        )
        if answer_text:
            result["answer"] = answer_text
    return result


def _simple_failure_reason(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "请求超时"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.RequestError):
        return "无法连接SearXNG"
    message = re.sub(r"\s+", " ", str(exc)).strip()
    if message.startswith("SearXNG"):
        message = message.removeprefix("SearXNG")
    return (message or exc.__class__.__name__)[:80]


def _query_id(query: str) -> str:
    """Return a privacy-safe identifier for correlating search logs."""

    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:12]


def _bounded_diagnostic(value: Any, max_chars: int = 300) -> str:
    diagnostic = re.sub(r"\s+", " ", repr(value)).strip()
    return diagnostic[:max_chars]


def format_web_search_result(result: Any) -> str:
    """Format structured web-search results into readable terminal text."""

    if isinstance(result, dict):
        rows = []
        answer = _string_value(result.get("answer"))
        if answer:
            rows.append(f"摘要：{answer}")
        raw_results = result.get("results")
        if isinstance(raw_results, list):
            for index, item in enumerate(raw_results, start=1):
                if not isinstance(item, dict):
                    continue
                source_index = item.get("source_index", index)
                title = _string_value(item.get("title")) or "Untitled"
                url = _string_value(item.get("url"))
                summary = (
                    _string_value(item.get("content"))
                    or _string_value(item.get("snippet"))
                    or _string_value(item.get("raw_content"))
                )
                rows.append(_format_search_item(source_index, title, url, summary))
        if rows:
            return "\n\n".join(rows)
    return str(result)


def format_web_search_sources(result: Any) -> str:
    """Format only searched page titles and URLs for terminal display."""

    if isinstance(result, dict):
        rows = _source_rows_from_result_dict(result)
        return "\n".join(rows) if rows else "未返回可展示的网址。"
    if isinstance(result, str):
        rows = _source_rows_from_formatted_text(result)
        return "\n".join(rows) if rows else "未返回可展示的网址。"
    return "未返回可展示的网址。"


def _source_rows_from_result_dict(result: dict[str, Any]) -> list[str]:
    raw_results = result.get("results")
    if not isinstance(raw_results, list):
        return []
    rows: list[str] = []
    for fallback_index, item in enumerate(raw_results, start=1):
        if not isinstance(item, dict):
            continue
        title = _string_value(item.get("title")) or "Untitled"
        url = _string_value(item.get("url"))
        if not url:
            continue
        source_index = item.get("source_index", fallback_index)
        rows.extend(_format_source_rows(source_index, title, url))
    return rows


def _source_rows_from_formatted_text(text: str) -> list[str]:
    rows: list[str] = []
    pending_title: str | None = None
    pending_index: str | None = None
    for line in text.splitlines():
        title_match = re.match(r"^\s*(\d+)\.\s+(.+?)\s*$", line)
        if title_match:
            pending_index = title_match.group(1)
            pending_title = title_match.group(2)
            continue
        url_match = re.match(r"^\s*URL:\s*(\S+)\s*$", line)
        if url_match and pending_title and pending_index:
            rows.extend(
                _format_source_rows(pending_index, pending_title, url_match.group(1))
            )
            pending_index = None
            pending_title = None
    return rows


def _format_source_rows(index: object, title: str, url: str) -> list[str]:
    return [f"{index}. {title}", f"   URL: {url}"]


def _format_search_item(
    index: object,
    title: str,
    url: str | None,
    summary: str | None,
) -> str:
    rows = [f"{index}. {title}"]
    if url:
        rows.append(f"   URL: {url}")
    if summary:
        rows.append(f"   {summary}")
    return "\n".join(rows)


def _string_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _secret_value(env_name: str) -> str | None:
    return _environment_value(env_name)


def _environment_value(env_name: str) -> str | None:
    value = os.getenv(env_name)
    if value:
        return value
    lowered_env_name = env_name.lower()
    for key, env_value in os.environ.items():
        if key.lower() == lowered_env_name and env_value:
            return env_value
    dotenv = dotenv_values(".env")
    dotenv_value = dotenv.get(env_name)
    if dotenv_value:
        return str(dotenv_value)
    for key, env_value in dotenv.items():
        if key.lower() == lowered_env_name and env_value:
            return str(env_value)
    return None
