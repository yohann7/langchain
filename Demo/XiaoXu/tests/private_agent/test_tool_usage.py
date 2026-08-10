from private_agent.tool_usage import (
    append_tool_usage_marker,
    ensure_tool_usage_header,
    extract_tool_usage_backend,
)


def test_tool_usage_marker_round_trip():
    content = append_tool_usage_marker("结果", "web_search", "SearXNG")

    assert extract_tool_usage_backend(content, "web_search") == "SearXNG"
    assert extract_tool_usage_backend(content, "knowledge_search") is None


def test_header_replaces_model_header_and_removes_internal_marker():
    final_text = (
        "[web_search：None, knowledge_search:None]\n"
        "回答正文\n"
        "<!-- tool_usage:web_search=Tavily -->"
    )

    result = ensure_tool_usage_header(
        final_text,
        web_search="Tavily",
        knowledge_search="SQLite&Milvus",
    )

    assert result == (
        "[web_search：Tavily, knowledge_search:SQLite&Milvus]\n回答正文"
    )


def test_header_removes_legacy_searxng_failure_notice():
    result = ensure_tool_usage_header(
        "[SearXNG失败：请求超时，本次搜索使用Tavily]\n\n回答正文",
        web_search="Tavily",
        knowledge_search="None",
    )

    assert result == "[web_search：Tavily, knowledge_search:None]\n回答正文"
