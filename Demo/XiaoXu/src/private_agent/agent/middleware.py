"""Agent middleware owned by the agent layer."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from private_agent.runtime import RuntimeState, current_runtime_state
from private_agent.search import (
    SearchCoordinator,
    SearchKind,
    SearchPolicyCode,
    SearchPolicyError,
)
from private_agent.search.config import (
    KnowledgeSearchConfig,
    SearchConfigError,
    WebSearchConfig,
    load_knowledge_search_config,
    load_web_search_config,
)
from private_agent.search.context import prepared_search_context


class SearchPolicyMiddleware(AgentMiddleware):
    """Apply per-turn search query limits before any backend execution."""

    _TOOL_KINDS = {
        "search_knowledge": SearchKind.KNOWLEDGE,
        "web_search": SearchKind.WEB,
    }

    def __init__(
        self,
        *,
        runtime: RuntimeState,
        audit: Any,
        web_config_path: str | Path = "config/web-search.yaml",
        knowledge_config_path: str | Path = "config/knowledge-search.yaml",
    ) -> None:
        self.runtime = runtime
        self.audit = audit
        self.web_config_path = Path(web_config_path)
        self.knowledge_config_path = Path(knowledge_config_path)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        prepared_or_message = self._prepare(request)
        if prepared_or_message is None:
            return handler(request)
        if isinstance(prepared_or_message, ToolMessage):
            return prepared_or_message
        prepared, normalized_request, config = prepared_or_message
        try:
            with prepared_search_context(prepared, config):
                result = handler(normalized_request)
        except Exception:
            self._fail(prepared, "tool execution raised")
            raise
        if isinstance(result, ToolMessage) and result.status == "error":
            self._fail(prepared, "tool execution was blocked or failed")
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[
            [ToolCallRequest], Awaitable[ToolMessage | Command[Any]]
        ],
    ) -> ToolMessage | Command[Any]:
        prepared_or_message = self._prepare(request)
        if prepared_or_message is None:
            return await handler(request)
        if isinstance(prepared_or_message, ToolMessage):
            return prepared_or_message
        prepared, normalized_request, config = prepared_or_message
        try:
            with prepared_search_context(prepared, config):
                result = await handler(normalized_request)
        except Exception:
            self._fail(prepared, "tool execution raised")
            raise
        if isinstance(result, ToolMessage) and result.status == "error":
            self._fail(prepared, "tool execution was blocked or failed")
        return result

    def _prepare(
        self,
        request: ToolCallRequest,
    ) -> tuple[
        Any,
        ToolCallRequest,
        WebSearchConfig | KnowledgeSearchConfig,
    ] | ToolMessage | None:
        name = request.tool.name if request.tool else str(request.tool_call["name"])
        kind = self._TOOL_KINDS.get(name)
        if kind is None:
            return None

        runtime = current_runtime_state(self.runtime)
        if runtime.search_turn_state is None:
            runtime.begin_search_turn()
        coordinator = SearchCoordinator(runtime.search_turn_state)
        args = request.tool_call.get("args", {})
        if not isinstance(args, dict):
            args = {}
        try:
            config: WebSearchConfig | KnowledgeSearchConfig
            if kind == SearchKind.KNOWLEDGE:
                config = load_knowledge_search_config(self.knowledge_config_path)
            else:
                config = load_web_search_config(self.web_config_path)
        except SearchConfigError:
            coordinator.fail_kind(kind)
            return self._blocked_message(
                request,
                name=name,
                kind=kind,
                code=SearchPolicyCode.CONFIG_INVALID,
                message="Search configuration is invalid for this turn.",
                max_queries=0,
            )
        try:
            if _has_earlier_parallel_search_call(request, name):
                raise SearchPolicyError(
                    SearchPolicyCode.QUERY_LIMIT_REACHED,
                    "同一次模型输出只允许每个搜索后端执行一个查询；请先评估结果。",
                )
            if kind == SearchKind.KNOWLEDGE:
                if not isinstance(config, KnowledgeSearchConfig):
                    raise RuntimeError("knowledge search configuration type mismatch")
                normalized_limit = _knowledge_limit(args.get("limit"), config)
                prepared = coordinator.prepare_knowledge(
                    str(args.get("query", "")),
                    _knowledge_bases(args.get("knowledge_bases")),
                    max_queries=config.max_queries_per_turn,
                )
            else:
                if not isinstance(config, WebSearchConfig):
                    raise RuntimeError("web search configuration type mismatch")
                prepared = coordinator.prepare_web(
                    str(args.get("query", "")),
                    max_queries=config.max_queries_per_turn,
                )
        except SearchPolicyError as exc:
            return self._blocked_message(
                request,
                name=name,
                kind=kind,
                code=exc.code,
                message=str(exc),
                max_queries=config.max_queries_per_turn,
            )
        except ValueError as exc:
            return self._blocked_message(
                request,
                name=name,
                kind=kind,
                code=SearchPolicyCode.INVALID_ARGUMENT,
                message=str(exc),
                max_queries=config.max_queries_per_turn,
            )

        normalized_args = dict(args)
        normalized_args["query"] = prepared.query
        if kind == SearchKind.KNOWLEDGE:
            normalized_args["limit"] = normalized_limit
        normalized_call = {**request.tool_call, "args": normalized_args}
        return prepared, request.override(tool_call=normalized_call), config

    def _blocked_message(
        self,
        request: ToolCallRequest,
        *,
        name: str,
        kind: SearchKind,
        code: SearchPolicyCode,
        message: str,
        max_queries: int,
    ) -> ToolMessage:
        self.audit.record(
            "tool_execution_blocked",
            {"tool": name, "reason": code.value},
        )
        runtime = current_runtime_state(self.runtime)
        state = runtime.search_turn_state
        if state is None:
            query_index = 0
            total_unique = 0
            remaining = 0
        elif kind == SearchKind.KNOWLEDGE:
            query_index = state.knowledge_query_count
            total_unique = len(state.seen_knowledge)
            remaining = (
                0
                if state.knowledge_closed or state.knowledge_failed
                else max(0, max_queries - query_index)
            )
        else:
            query_index = state.web_query_count
            total_unique = len(state.seen_web)
            remaining = (
                0
                if state.web_closed or state.web_failed
                else max(0, max_queries - query_index)
            )
        return ToolMessage(
            content=json.dumps(
                {
                    "error": {"code": code.value, "message": message},
                    "query_index": query_index,
                    "new_results": 0,
                    "total_unique_results": total_unique,
                    "duplicate_results": 0,
                    "updated_results": 0,
                    "remaining_queries": remaining,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            tool_call_id=str(request.tool_call["id"]),
            name=name,
            status="error",
        )

    def _fail(self, prepared: Any, reason: str) -> None:
        runtime = current_runtime_state(self.runtime)
        if runtime.search_turn_state is not None:
            SearchCoordinator(runtime.search_turn_state).fail(prepared, reason)


def _knowledge_bases(value: object) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        return [item.strip() for item in stripped.split(",") if item.strip()]
    return [str(value)]


def _knowledge_limit(value: object, config: KnowledgeSearchConfig) -> int:
    if value is None:
        return config.default_results_per_query
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("limit must be an integer")
    if value < 1 or value > config.max_results_per_query:
        raise ValueError(
            f"limit must be between 1 and {config.max_results_per_query}"
        )
    return value


def _has_earlier_parallel_search_call(
    request: ToolCallRequest,
    tool_name: str,
) -> bool:
    state = request.state
    if isinstance(state, dict):
        messages = state.get("messages", [])
    else:
        messages = getattr(state, "messages", [])
    if not isinstance(messages, (list, tuple)):
        return False
    last_ai = next(
        (message for message in reversed(messages) if isinstance(message, AIMessage)),
        None,
    )
    if last_ai is None:
        return False
    current_id = str(request.tool_call.get("id", ""))
    for tool_call in last_ai.tool_calls:
        if str(tool_call.get("id", "")) == current_id:
            return False
        if tool_call.get("name") == tool_name:
            return True
    return False
