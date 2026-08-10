import json
import sys
from types import SimpleNamespace

import pytest
from langchain.agents.middleware import (
    HumanInTheLoopMiddleware,
    SummarizationMiddleware,
)
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.types import Command

from private_agent.agent_runner import AgentRunner
from private_agent.agent.middleware import SearchPolicyMiddleware
from private_agent.agent.governance import ToolExecutionMiddleware
from private_agent.agent_factory import (
    build_middleware,
    build_tools,
    create_private_agent,
    create_resources,
)
from private_agent.config import AppSettings
from private_agent.core.identity import user_context
from private_agent.runtime import RuntimeState
from private_agent.security import PermissionPolicy


class ToolBindableFakeMessagesChatModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self


def _usage(input_tokens: int, output_tokens: int) -> dict[str, int]:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


def _skill_settings(tmp_path, **updates):
    skill_dir = tmp_path / "skills" / "example"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: example\ndescription: Example skill.\n---\nInstructions.\n",
        encoding="utf-8",
    )
    return AppSettings(
        run_dir=tmp_path / "runtime",
        skills_dir=tmp_path / "skills",
        enable_pii_middleware=False,
        enable_summarization_middleware=False,
        **updates,
    )


def test_agent_tool_calls_pass_through_gateway_and_record_usage(tmp_path):
    settings = _skill_settings(tmp_path)
    runtime = RuntimeState()
    model = ToolBindableFakeMessagesChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "activate_skill",
                        "args": {"name": "example"},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
                usage_metadata=_usage(10, 2),
            ),
            AIMessage(content="done", usage_metadata=_usage(4, 1)),
        ]
    )
    agent, resources = create_private_agent(
        settings,
        PermissionPolicy(),
        runtime,
        model=model,
    )

    result = agent.invoke(
        {"messages": [{"role": "user", "content": "activate"}]},
        config={"configurable": {"thread_id": "governance-allow"}},
    )

    assert result["messages"][-1].content == "done"
    assert runtime.usage.tool_calls == 1
    assert runtime.usage.model_calls == 2
    assert resources.gateway.daily_usage().to_dict() == {
        "model_calls": 2,
        "tool_calls": 1,
        "input_tokens": 14,
        "output_tokens": 3,
        "total_tokens": 17,
    }
    with resources.database.connect() as connection:
        rows = connection.execute(
            "SELECT event_type, payload FROM audit_events ORDER BY created_at"
        ).fetchall()
    event_types = [row["event_type"] for row in rows]
    assert event_types.count("tool_permission_check") == 1
    assert event_types.count("tool_execution_started") == 1
    assert event_types.count("tool_execution_finished") == 1
    assert event_types.count("model_usage_recorded") == 2
    assert json.loads(
        next(row["payload"] for row in rows if row["event_type"] == "tool_permission_check")
    )["decision"] == "allow"


def test_deny_is_audited_and_never_executes_or_counts_tool(tmp_path):
    settings = _skill_settings(tmp_path)
    runtime = RuntimeState()
    model = ToolBindableFakeMessagesChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "activate_skill",
                        "args": {"name": "example"},
                        "id": "call-denied",
                        "type": "tool_call",
                    }
                ],
                usage_metadata=_usage(3, 1),
            ),
            AIMessage(content="denied handled", usage_metadata=_usage(2, 1)),
        ]
    )
    agent, resources = create_private_agent(
        settings,
        PermissionPolicy({"activate_skill": "deny"}),
        runtime,
        model=model,
    )

    result = agent.invoke(
        {"messages": [{"role": "user", "content": "activate"}]},
        config={"configurable": {"thread_id": "governance-deny"}},
    )

    assert result["messages"][-1].content == "denied handled"
    assert runtime.usage.tool_calls == 0
    assert resources.gateway.daily_usage().tool_calls == 0
    with resources.database.connect() as connection:
        rows = connection.execute(
            "SELECT event_type, payload FROM audit_events"
        ).fetchall()
    permission_rows = [
        json.loads(row["payload"])
        for row in rows
        if row["event_type"] == "tool_permission_check"
    ]
    assert permission_rows == [
        {
            "args": {"name": "example"},
            "decision": "deny",
            "tool": "activate_skill",
        }
    ]
    assert not any(row["event_type"] == "tool_execution_started" for row in rows)
    assert [row["event_type"] for row in rows].count("tool_execution_blocked") == 1


def test_tool_retry_counts_and_audits_each_execution_attempt(tmp_path):
    settings = _skill_settings(tmp_path)
    runtime = RuntimeState()
    model = ToolBindableFakeMessagesChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "activate_skill",
                        "args": {"name": "missing"},
                        "id": "call-retry",
                        "type": "tool_call",
                    }
                ],
                usage_metadata=_usage(3, 1),
            ),
            AIMessage(content="failed safely", usage_metadata=_usage(2, 1)),
        ]
    )
    agent, resources = create_private_agent(
        settings,
        PermissionPolicy(),
        runtime,
        model=model,
    )

    result = agent.invoke(
        {"messages": [{"role": "user", "content": "activate"}]},
        config={"configurable": {"thread_id": "governance-retry"}},
    )

    assert result["messages"][-1].content == "failed safely"
    assert runtime.usage.tool_calls == 3
    assert resources.gateway.daily_usage().tool_calls == 3
    with resources.database.connect() as connection:
        rows = connection.execute(
            "SELECT event_type FROM audit_events"
        ).fetchall()
    assert [row["event_type"] for row in rows].count("tool_execution_started") == 3
    assert [row["event_type"] for row in rows].count("tool_execution_finished") == 3


def test_persistent_allow_removes_hitl_interrupt(tmp_path):
    settings = _skill_settings(tmp_path)
    resources = create_resources(
        settings,
        PermissionPolicy({"web_search": "allow"}),
        RuntimeState(),
    )
    build_tools(resources)

    middleware = build_middleware(settings, resources, model=None)
    hitl = next(item for item in middleware if isinstance(item, HumanInTheLoopMiddleware))

    assert "web_search" not in hitl.interrupt_on


def test_search_policy_runs_after_approval_and_before_execution_gateway(tmp_path):
    settings = _skill_settings(tmp_path)
    resources = create_resources(settings, PermissionPolicy(), RuntimeState())
    build_tools(resources)

    middleware = build_middleware(settings, resources, model=None)

    hitl_index = next(
        index
        for index, item in enumerate(middleware)
        if isinstance(item, HumanInTheLoopMiddleware)
    )
    policy_index = next(
        index
        for index, item in enumerate(middleware)
        if isinstance(item, SearchPolicyMiddleware)
    )
    gateway_index = next(
        index
        for index, item in enumerate(middleware)
        if isinstance(item, ToolExecutionMiddleware)
    )
    assert hitl_index < policy_index < gateway_index


def test_duplicate_web_query_never_reaches_backend_or_execution_counter(
    tmp_path, monkeypatch
):
    settings = _skill_settings(tmp_path)
    runtime = RuntimeState()
    model = ToolBindableFakeMessagesChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "web_search",
                        "args": {"query": "Python?"},
                        "id": "web-1",
                        "type": "tool_call",
                    }
                ],
                usage_metadata=_usage(3, 1),
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "web_search",
                        "args": {"query": " python！ "},
                        "id": "web-2",
                        "type": "tool_call",
                    }
                ],
                usage_metadata=_usage(3, 1),
            ),
            AIMessage(content="done", usage_metadata=_usage(2, 1)),
        ]
    )
    agent, resources = create_private_agent(
        settings,
        PermissionPolicy({"web_search": "allow"}),
        runtime,
        model=model,
    )
    backend_calls = []
    monkeypatch.setattr(
        "private_agent.agent.factory.web_search_result",
        lambda *args, **kwargs: (
            backend_calls.append((args, kwargs))
            or __import__(
                "private_agent.tools.search_tools", fromlist=["WebSearchOutcome"]
            ).WebSearchOutcome(
                results=[
                    {
                        "title": "Python",
                        "url": "https://python.org/",
                        "content": "Python language",
                    }
                ],
                backend="SearXNG",
                available=True,
            )
        ),
    )

    result = AgentRunner(agent, settings, runtime).invoke("search")

    assert result == "done"
    assert len(backend_calls) == 1
    assert runtime.usage.tool_calls == 1
    assert resources.gateway.daily_usage().tool_calls == 1
    with resources.database.connect() as connection:
        blocked = connection.execute(
            "SELECT payload FROM audit_events WHERE event_type = 'tool_execution_blocked'"
        ).fetchall()
    assert any("SEARCH_DUPLICATE_QUERY" in row["payload"] for row in blocked)


def test_five_searxng_attempts_are_one_logical_tool_execution(tmp_path, monkeypatch):
    settings = _skill_settings(tmp_path)
    runtime = RuntimeState()
    model = ToolBindableFakeMessagesChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "web_search",
                        "args": {"query": "one logical query"},
                        "id": "web-retry",
                        "type": "tool_call",
                    }
                ],
                usage_metadata=_usage(3, 1),
            ),
            AIMessage(content="done", usage_metadata=_usage(2, 1)),
        ]
    )
    agent, resources = create_private_agent(
        settings,
        PermissionPolicy({"web_search": "allow"}),
        runtime,
        model=model,
    )
    attempts = 0

    def failing_get(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        from private_agent.tools.search_tools import httpx

        raise httpx.ConnectError("offline")

    class FakeTavilySearch:
        def __init__(self, **kwargs):
            del kwargs

        def invoke(self, payload):
            del payload
            return {
                "results": [
                    {
                        "title": "Fallback",
                        "url": "https://example.com/fallback",
                        "content": "evidence",
                    }
                ]
            }

    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    monkeypatch.setattr("private_agent.tools.web.service.httpx.get", failing_get)
    monkeypatch.setattr("private_agent.tools.web.service.time.sleep", lambda value: None)
    monkeypatch.setitem(
        sys.modules,
        "langchain_tavily",
        SimpleNamespace(TavilySearch=FakeTavilySearch),
    )

    result = AgentRunner(agent, settings, runtime).invoke("search")

    assert result == "done"
    assert attempts == 5
    assert runtime.usage.tool_calls == 1
    assert resources.gateway.daily_usage().tool_calls == 1


def test_ask_does_not_count_as_executed_before_approval(tmp_path):
    settings = _skill_settings(tmp_path)
    runtime = RuntimeState()
    model = ToolBindableFakeMessagesChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "activate_skill",
                        "args": {"name": "example"},
                        "id": "call-ask",
                        "type": "tool_call",
                    }
                ],
                usage_metadata=_usage(3, 1),
            )
        ]
    )
    agent, resources = create_private_agent(
        settings,
        PermissionPolicy({"activate_skill": "ask"}),
        runtime,
        model=model,
    )

    result = agent.invoke(
        {"messages": [{"role": "user", "content": "activate"}]},
        config={"configurable": {"thread_id": "governance-ask"}},
    )

    assert result["__interrupt__"]
    assert runtime.usage.tool_calls == 0
    assert resources.gateway.daily_usage().tool_calls == 0


def test_approved_ask_executes_and_counts_once(tmp_path):
    settings = _skill_settings(tmp_path)
    runtime = RuntimeState()
    model = ToolBindableFakeMessagesChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "activate_skill",
                        "args": {"name": "example"},
                        "id": "call-approved",
                        "type": "tool_call",
                    }
                ],
                usage_metadata=_usage(3, 1),
            ),
            AIMessage(content="done", usage_metadata=_usage(2, 1)),
        ]
    )
    agent, resources = create_private_agent(
        settings,
        PermissionPolicy({"activate_skill": "ask"}),
        runtime,
        model=model,
    )
    config = {"configurable": {"thread_id": "governance-approved"}}

    first = agent.invoke(
        {"messages": [{"role": "user", "content": "activate"}]},
        config=config,
    )
    assert first["__interrupt__"]
    resumed = agent.invoke(
        Command(resume={"decisions": [{"type": "approve"}]}),
        config=config,
    )

    assert resumed["messages"][-1].content == "done"
    assert runtime.usage.tool_calls == 1
    assert resources.gateway.daily_usage().tool_calls == 1


def test_former_daily_limit_never_blocks_model_or_tool_execution(tmp_path):
    settings = _skill_settings(tmp_path)
    runtime = RuntimeState()
    model = ToolBindableFakeMessagesChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "activate_skill",
                        "args": {"name": "example"},
                        "id": "over-old-limit",
                        "type": "tool_call",
                    }
                ],
                usage_metadata=_usage(10, 2),
            ),
            AIMessage(content="done", usage_metadata=_usage(4, 1)),
        ]
    )
    agent, resources = create_private_agent(
        settings,
        PermissionPolicy(),
        runtime,
        model=model,
    )
    resources.usage.record(user_id=settings.user_id, input_tokens=100_001)

    result = agent.invoke(
        {"messages": [{"role": "user", "content": "activate"}]},
        config={"configurable": {"thread_id": "usage-only"}},
    )

    assert result["messages"][-1].content == "done"
    assert resources.gateway.daily_usage().total_tokens == 100_018
    assert resources.gateway.daily_usage().tool_calls == 1


def test_daily_usage_is_isolated_by_current_user(tmp_path):
    settings = _skill_settings(tmp_path)
    resources = create_resources(settings, PermissionPolicy(), RuntimeState())

    with user_context("user-a"):
        resources.gateway.record_model_usage(4, 1)
    with user_context("user-b"):
        resources.gateway.record_model_usage(2, 1)

    with user_context("user-a"):
        assert resources.gateway.daily_usage().total_tokens == 5
    with user_context("user-b"):
        assert resources.gateway.daily_usage().total_tokens == 3


def test_memory_tool_gateway_redacts_content_and_isolates_users(tmp_path):
    settings = _skill_settings(tmp_path)
    resources = create_resources(settings, PermissionPolicy(), RuntimeState())
    build_tools(resources)
    secret = "gateway-private-memory"

    with user_context("user-a"):
        created = resources.gateway.execute(
            "remember_memory",
            content=secret,
        )
        assert "memory_id=" in created
    with user_context("user-b"):
        assert "没有匹配" in resources.gateway.execute(
            "search_memories",
            query="gateway",
        )

    with resources.database.connect() as connection:
        rows = connection.execute(
            """
            SELECT payload FROM audit_events
            WHERE event_type = 'tool_permission_check'
            """
        ).fetchall()
    serialized = "\n".join(str(row["payload"]) for row in rows)
    assert secret not in serialized
    assert '"redacted": true' in serialized


def test_summarization_uses_selected_model_and_token_thresholds(tmp_path):
    settings = AppSettings(
        run_dir=tmp_path,
        enable_pii_middleware=False,
        enable_summarization_middleware=True,
        summarization_trigger_tokens=1000,
        summarization_keep_tokens=300,
    )
    model = ToolBindableFakeMessagesChatModel(responses=[AIMessage(content="ok")])
    resources = create_resources(settings, PermissionPolicy(), RuntimeState())
    build_tools(resources)

    middleware = build_middleware(settings, resources, model=model)
    summary = next(
        item for item in middleware if isinstance(item, SummarizationMiddleware)
    )

    assert summary.model is model
    assert summary.trigger == ("tokens", 1000)
    assert summary.keep == ("tokens", 300)


def test_summarization_and_agent_calls_are_each_counted_once(tmp_path):
    settings = AppSettings(
        run_dir=tmp_path,
        enable_pii_middleware=False,
        enable_summarization_middleware=True,
        summarization_trigger_tokens=20,
        summarization_keep_tokens=5,
    )
    model = ToolBindableFakeMessagesChatModel(
        responses=[
            AIMessage(content="first", usage_metadata=_usage(3, 1)),
            AIMessage(content="summary", usage_metadata=_usage(7, 2)),
            AIMessage(content="second", usage_metadata=_usage(4, 1)),
        ]
    )
    agent, resources = create_private_agent(
        settings,
        PermissionPolicy(),
        RuntimeState(),
        model=model,
    )
    config = {"configurable": {"thread_id": "summary-accounting"}}

    agent.invoke(
        {"messages": [{"role": "user", "content": "A" * 200}]},
        config=config,
    )
    agent.invoke(
        {"messages": [{"role": "user", "content": "continue"}]},
        config=config,
    )

    usage = resources.gateway.daily_usage()
    assert usage.model_calls == 3
    assert usage.input_tokens == 14
    assert usage.output_tokens == 4
    with resources.database.connect() as connection:
        rows = connection.execute(
            "SELECT payload FROM audit_events "
            "WHERE event_type='model_usage_recorded' ORDER BY created_at"
        ).fetchall()
    assert [json.loads(row["payload"])["purpose"] for row in rows] == [
        "agent",
        "summarization",
        "agent",
    ]
