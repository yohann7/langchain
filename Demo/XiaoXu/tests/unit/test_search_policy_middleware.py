from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from private_agent.agent.middleware import SearchPolicyMiddleware
from private_agent.runtime import RuntimeState, runtime_context
from private_agent.search import SearchKind
from private_agent.search import SearchCoordinator
from private_agent.search.context import (
    current_prepared_search,
    current_web_search_config,
)


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def record(self, event_type: str, payload: dict[str, object]):
        self.events.append((event_type, payload))
        return payload


def _write_search_configs(
    tmp_path: Path,
    *,
    web_max_queries: int = 3,
    knowledge_max_queries: int = 2,
) -> tuple[Path, Path]:
    web_path = tmp_path / "web-search.yaml"
    knowledge_path = tmp_path / "knowledge-search.yaml"
    web_path.write_text(
        f"""version: 1
max_queries_per_turn: {web_max_queries}
max_results_per_query: 10
request_timeout_seconds: 10.0
searxng:
  max_attempts: 2
  retry_delays_seconds: [0.01]
tavily_fallback_enabled: true
""",
        encoding="utf-8",
    )
    knowledge_path.write_text(
        f"""version: 1
max_queries_per_turn: {knowledge_max_queries}
default_results_per_query: 10
max_results_per_query: 20
request_timeout_seconds: 15.0
""",
        encoding="utf-8",
    )
    return web_path, knowledge_path


def _complete_empty_web(runtime: RuntimeState, request: ToolCallRequest) -> ToolMessage:
    prepared = current_prepared_search(SearchKind.WEB)
    SearchCoordinator(runtime.search_turn_state).accept_web(prepared, [])
    return ToolMessage(
        content="ok",
        tool_call_id=str(request.tool_call["id"]),
        name="web_search",
    )


def _request(
    name: str,
    call_id: str,
    *,
    state: dict[str, object] | None = None,
    **args: object,
) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={
            "name": name,
            "args": args,
            "id": call_id,
            "type": "tool_call",
        },
        tool=None,
        state=state or {},
        runtime=None,
    )


def test_middleware_normalizes_execution_query_and_exposes_prepared_search():
    runtime = RuntimeState()
    runtime.begin_search_turn()
    middleware = SearchPolicyMiddleware(runtime=runtime, audit=FakeAudit())
    captured = {}

    def handler(request):
        captured["query"] = request.tool_call["args"]["query"]
        captured["prepared"] = current_prepared_search(SearchKind.WEB)
        return ToolMessage(content="ok", tool_call_id="web-1", name="web_search")

    with runtime_context(runtime):
        result = middleware.wrap_tool_call(
            _request("web_search", "web-1", query="  ＡＩ\n  agent  "),
            handler,
        )

    assert result.content == "ok"
    assert captured["query"] == "AI agent"
    assert captured["prepared"].query_index == 1


def test_duplicate_query_is_audited_and_does_not_enter_execution_handler():
    runtime = RuntimeState()
    runtime.begin_search_turn()
    audit = FakeAudit()
    middleware = SearchPolicyMiddleware(runtime=runtime, audit=audit)
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return ToolMessage(
            content="executed",
            tool_call_id=request.tool_call["id"],
            name=request.tool_call["name"],
        )

    with runtime_context(runtime):
        middleware.wrap_tool_call(
            _request("web_search", "web-1", query="Python?"), handler
        )
        blocked = middleware.wrap_tool_call(
            _request("web_search", "web-2", query=" python！ "), handler
        )

    assert calls == 1
    assert blocked.status == "error"
    payload = json.loads(str(blocked.content))
    assert payload["error"]["code"] == "SEARCH_DUPLICATE_QUERY"
    assert payload["query_index"] == 1
    assert payload["new_results"] == 0
    assert payload["total_unique_results"] == 0
    assert payload["duplicate_results"] == 0
    assert payload["remaining_queries"] == 2
    assert audit.events[-1] == (
        "tool_execution_blocked",
        {"tool": "web_search", "reason": "SEARCH_DUPLICATE_QUERY"},
    )
    assert runtime.usage.tool_calls == 0


def test_knowledge_fingerprint_sorts_scope_and_ignores_limit():
    runtime = RuntimeState()
    runtime.begin_search_turn()
    middleware = SearchPolicyMiddleware(runtime=runtime, audit=FakeAudit())

    with runtime_context(runtime):
        middleware.wrap_tool_call(
            _request(
                "search_knowledge",
                "kb-1",
                query="deployment",
                knowledge_bases=["work", "personal"],
                limit=5,
            ),
            lambda request: ToolMessage(
                content="ok", tool_call_id="kb-1", name="search_knowledge"
            ),
        )
        blocked = middleware.wrap_tool_call(
            _request(
                "search_knowledge",
                "kb-2",
                query="Deployment.",
                knowledge_bases=["personal", "work", "personal"],
                limit=20,
            ),
            lambda request: (_ for _ in ()).throw(AssertionError("must not execute")),
        )

    assert blocked.status == "error"
    assert json.loads(str(blocked.content))["error"]["code"] == "SEARCH_DUPLICATE_QUERY"


def test_contextvar_keeps_concurrent_request_search_states_isolated():
    default_runtime = RuntimeState(thread_id="default")
    middleware = SearchPolicyMiddleware(runtime=default_runtime, audit=FakeAudit())

    def execute(thread_id: str) -> tuple[str, int]:
        runtime = RuntimeState(thread_id=thread_id)
        runtime.begin_search_turn()
        with runtime_context(runtime):
            result = middleware.wrap_tool_call(
                _request("web_search", thread_id, query="same query"),
                lambda request: ToolMessage(
                    content=thread_id,
                    tool_call_id=thread_id,
                    name="web_search",
                ),
            )
        return str(result.content), runtime.search_turn_state.web_query_count

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(execute, ("user-a:thread-1", "user-b:thread-2")))

    assert sorted(results) == [
        ("user-a:thread-1", 1),
        ("user-b:thread-2", 1),
    ]
    assert default_runtime.search_turn_state is None


def test_inner_gateway_error_closes_search_and_clears_in_flight_state():
    runtime = RuntimeState()
    state = runtime.begin_search_turn()
    middleware = SearchPolicyMiddleware(runtime=runtime, audit=FakeAudit())

    with runtime_context(runtime):
        denied = middleware.wrap_tool_call(
            _request("web_search", "web-denied", query="private"),
            lambda request: ToolMessage(
                content="permission denied",
                tool_call_id=request.tool_call["id"],
                name="web_search",
                status="error",
            ),
        )
        blocked = middleware.wrap_tool_call(
            _request("web_search", "web-again", query="another query"),
            lambda request: (_ for _ in ()).throw(AssertionError("must not execute")),
        )

    assert denied.status == "error"
    assert state.web_failed is True
    assert state.web_awaiting_result is False
    assert json.loads(str(blocked.content))["error"]["code"] == (
        "SEARCH_BACKEND_UNAVAILABLE"
    )


def test_later_parallel_search_call_is_blocked_even_if_scheduled_first():
    runtime = RuntimeState()
    runtime.begin_search_turn()
    middleware = SearchPolicyMiddleware(runtime=runtime, audit=FakeAudit())
    model_message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "web_search",
                "args": {"query": "first fact"},
                "id": "web-first",
                "type": "tool_call",
            },
            {
                "name": "web_search",
                "args": {"query": "second fact"},
                "id": "web-second",
                "type": "tool_call",
            },
        ],
    )

    with runtime_context(runtime):
        blocked = middleware.wrap_tool_call(
            _request(
                "web_search",
                "web-second",
                state={"messages": [model_message]},
                query="second fact",
            ),
            lambda request: (_ for _ in ()).throw(AssertionError("must not execute")),
        )

    assert blocked.status == "error"
    assert json.loads(str(blocked.content))["error"]["code"] == (
        "SEARCH_QUERY_LIMIT_REACHED"
    )
    assert runtime.search_turn_state.web_query_count == 0


def test_middleware_reloads_web_config_between_calls_and_uses_call_snapshot(tmp_path):
    web_path, knowledge_path = _write_search_configs(tmp_path, web_max_queries=1)
    runtime = RuntimeState()
    runtime.begin_search_turn()
    middleware = SearchPolicyMiddleware(
        runtime=runtime,
        audit=FakeAudit(),
        web_config_path=web_path,
        knowledge_config_path=knowledge_path,
    )
    captured = []

    def handler(request):
        first = current_web_search_config()
        web_path.write_text(
            web_path.read_text(encoding="utf-8").replace(
                "max_queries_per_turn: 1", "max_queries_per_turn: 2"
            ),
            encoding="utf-8",
        )
        second = current_web_search_config()
        captured.append((first, second))
        return _complete_empty_web(runtime, request)

    with runtime_context(runtime):
        first = middleware.wrap_tool_call(
            _request("web_search", "web-1", query="first"), handler
        )
        second = middleware.wrap_tool_call(
            _request("web_search", "web-2", query="second"),
            lambda request: _complete_empty_web(runtime, request),
        )

    assert first.status == "success"
    assert second.status == "success"
    assert captured[0][0] is captured[0][1]
    assert captured[0][0].max_queries_per_turn == 1
    assert runtime.search_turn_state.web_query_count == 2


def test_lowered_limit_blocks_next_call_without_consuming_query(tmp_path):
    web_path, knowledge_path = _write_search_configs(tmp_path, web_max_queries=3)
    runtime = RuntimeState()
    middleware = SearchPolicyMiddleware(
        runtime=runtime,
        audit=FakeAudit(),
        web_config_path=web_path,
        knowledge_config_path=knowledge_path,
    )

    with runtime_context(runtime):
        for index in (1, 2):
            middleware.wrap_tool_call(
                _request("web_search", f"web-{index}", query=f"query {index}"),
                lambda request: _complete_empty_web(runtime, request),
            )
        web_path.write_text(
            web_path.read_text(encoding="utf-8").replace(
                "max_queries_per_turn: 3", "max_queries_per_turn: 1"
            ),
            encoding="utf-8",
        )
        blocked = middleware.wrap_tool_call(
            _request("web_search", "web-3", query="query 3"),
            lambda request: (_ for _ in ()).throw(AssertionError("must not execute")),
        )

    assert json.loads(str(blocked.content))["error"]["code"] == (
        "SEARCH_QUERY_LIMIT_REACHED"
    )
    assert runtime.search_turn_state.web_query_count == 2


def test_invalid_web_config_is_sanitized_not_counted_and_only_closes_web(tmp_path):
    web_path, knowledge_path = _write_search_configs(tmp_path)
    web_path.write_text("secret: must-not-appear\n", encoding="utf-8")
    runtime = RuntimeState()
    audit = FakeAudit()
    middleware = SearchPolicyMiddleware(
        runtime=runtime,
        audit=audit,
        web_config_path=web_path,
        knowledge_config_path=knowledge_path,
    )
    executed = []

    with runtime_context(runtime):
        blocked = middleware.wrap_tool_call(
            _request("web_search", "web-invalid", query="public"),
            lambda request: executed.append("web"),
        )
        knowledge = middleware.wrap_tool_call(
            _request("search_knowledge", "kb-ok", query="private"),
            lambda request: ToolMessage(
                content="ok", tool_call_id="kb-ok", name="search_knowledge"
            ),
        )

    payload = json.loads(str(blocked.content))
    assert payload["error"]["code"] == "SEARCH_CONFIG_INVALID"
    assert "secret" not in str(blocked.content)
    assert "must-not-appear" not in str(blocked.content)
    assert executed == []
    assert runtime.search_turn_state.web_query_count == 0
    assert runtime.search_turn_state.web_failed is True
    assert runtime.search_turn_state.knowledge_failed is False
    assert knowledge.status == "success"
    assert audit.events[0] == (
        "tool_execution_blocked",
        {"tool": "web_search", "reason": "SEARCH_CONFIG_INVALID"},
    )
    assert "secret" not in repr(audit.events)


def test_knowledge_limit_is_validated_before_query_is_consumed(tmp_path):
    web_path, knowledge_path = _write_search_configs(tmp_path)
    runtime = RuntimeState()
    middleware = SearchPolicyMiddleware(
        runtime=runtime,
        audit=FakeAudit(),
        web_config_path=web_path,
        knowledge_config_path=knowledge_path,
    )
    executed = []

    with runtime_context(runtime):
        blocked = middleware.wrap_tool_call(
            _request("search_knowledge", "kb-invalid", query="private", limit=21),
            lambda request: executed.append("invalid"),
        )
        accepted = middleware.wrap_tool_call(
            _request("search_knowledge", "kb-valid", query="private", limit=20),
            lambda request: ToolMessage(
                content=str(request.tool_call["args"]["limit"]),
                tool_call_id="kb-valid",
                name="search_knowledge",
            ),
        )

    assert json.loads(str(blocked.content))["error"]["code"] == (
        "SEARCH_INVALID_ARGUMENT"
    )
    assert executed == []
    assert runtime.search_turn_state.knowledge_failed is False
    assert runtime.search_turn_state.knowledge_query_count == 1
    assert accepted.content == "20"
