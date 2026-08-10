"""Internal tool-usage markers and user-facing response headers."""

from __future__ import annotations

import re
from typing import Literal


WebSearchBackend = Literal["SearXNG", "Tavily", "None"]
KnowledgeSearchBackend = Literal["SQLite", "Milvus", "SQLite&Milvus", "None"]

_MARKER_RE = re.compile(
    r"<!--\s*tool_usage:(web_search|knowledge_search)="
    r"(SearXNG|Tavily|SQLite&Milvus|SQLite|Milvus|None)\s*-->"
)
_HEADER_RE = re.compile(
    r"^\[web_search[：:](?:SearXNG|Tavily|None),\s*"
    r"knowledge_search:(?:SQLite&Milvus|SQLite|Milvus|None)\]\s*",
)
_LEGACY_SEARXNG_NOTICE_RE = re.compile(
    r"^\[SearXNG失败：[^\]\r\n]+，本次搜索使用Tavily\]\s*",
)


def append_tool_usage_marker(content: str, tool_name: str, backend: str) -> str:
    """Append a machine-readable marker to a tool result."""

    body = content.rstrip()
    separator = "\n\n" if body else ""
    return f"{body}{separator}<!-- tool_usage:{tool_name}={backend} -->"


def extract_tool_usage_backend(content: str, tool_name: str) -> str | None:
    """Extract the most recent backend marker for one tool."""

    matches = [
        match.group(2)
        for match in _MARKER_RE.finditer(content)
        if match.group(1) == tool_name
    ]
    return matches[-1] if matches else None


def ensure_tool_usage_header(
    final_text: str,
    *,
    web_search: WebSearchBackend,
    knowledge_search: KnowledgeSearchBackend,
) -> str:
    """Replace any model-generated status line with the canonical first line."""

    body = _MARKER_RE.sub("", final_text).strip()
    body = _HEADER_RE.sub("", body, count=1).lstrip()
    body = _LEGACY_SEARXNG_NOTICE_RE.sub("", body, count=1).lstrip()
    header = f"[web_search：{web_search}, knowledge_search:{knowledge_search}]"
    return f"{header}\n{body}" if body else header
