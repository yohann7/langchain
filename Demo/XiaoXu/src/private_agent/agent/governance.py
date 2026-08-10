"""Authoritative execution governance for model and tool calls."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.agents.middleware.types import ExtendedModelResponse
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from private_agent.audit import AuditLogger
from private_agent.core.identity import current_user_id
from private_agent.persistence.audit import DatabaseAuditLogger
from private_agent.persistence.usage import DailyUsage, DailyUsageStore
from private_agent.runtime import RuntimeState, current_runtime_state
from private_agent.security import PermissionDecision, PermissionPolicy
from private_agent.tools.registry import ToolExecutionError, ToolRegistry


class ToolExecutionGateway:
    """Fail-closed policy, accounting, and audit boundary for every tool call."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        policy: PermissionPolicy,
        runtime: RuntimeState,
        audit: DatabaseAuditLogger,
        usage: DailyUsageStore,
        default_user_id: str,
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.runtime = runtime
        self.audit = audit
        self.usage = usage
        self.default_user_id = default_user_id
        registry.bind_gateway(self)

    def user_id(self) -> str:
        return current_user_id(self.default_user_id)

    def daily_usage(self) -> DailyUsage:
        return self.usage.load(user_id=self.user_id())

    def record_model_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        *,
        purpose: str = "agent",
        status: str = "success",
        error_type: str | None = None,
    ) -> None:
        active_runtime = current_runtime_state(self.runtime)
        active_runtime.record_model_call(input_tokens, output_tokens)
        usage = self.usage.record(
            user_id=self.user_id(),
            model_calls=1,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        payload: dict[str, Any] = {
            "purpose": purpose,
            "status": status,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "daily_total_tokens": usage.total_tokens,
        }
        if error_type:
            payload["error_type"] = error_type
        self.audit.record("model_usage_recorded", payload)

    def execute(
        self,
        name: str,
        *,
        approved: bool = False,
        audit_override: AuditLogger | None = None,
        **kwargs: Any,
    ) -> Any:
        selected_audit = audit_override or self.audit
        try:
            registered = self.registry.get(name)
        except KeyError as exc:
            self._record_permission(
                selected_audit,
                name,
                PermissionDecision.DENY,
                kwargs,
            )
            self._record_tool_blocked(
                selected_audit,
                name,
                "unknown_tool",
            )
            raise ToolExecutionError(f"Unknown or unregistered tool: {name}") from exc
        decision = self.policy.decision_for(registered.permission)
        self._record_permission(selected_audit, name, decision.decision, kwargs)
        if decision.decision == PermissionDecision.DENY:
            self._record_tool_blocked(
                selected_audit,
                name,
                "permission_denied",
            )
            raise ToolExecutionError(f"Tool denied: {name}. {decision.reason}")
        if decision.decision == PermissionDecision.ASK and not approved:
            self._record_tool_blocked(
                selected_audit,
                name,
                "approval_required",
            )
            raise ToolExecutionError(f"Tool requires approval: {name}")
        self._record_tool_start(selected_audit, name)
        try:
            result = registered.func(**kwargs)
        except Exception as exc:
            self._record_tool_end(selected_audit, name, "error", type(exc).__name__)
            raise
        self._record_tool_end(selected_audit, name, "success")
        return result

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        name = request.tool.name if request.tool else str(request.tool_call["name"])
        args = request.tool_call.get("args", {})
        decision = self._decision(name)
        self._record_permission(self.audit, name, decision, args)
        if decision == PermissionDecision.DENY:
            return self._blocked_message(
                request,
                name,
                "工具已被权限策略拒绝。",
                reason="permission_denied",
            )
        self._record_tool_start(self.audit, name)
        try:
            result = handler(request)
        except Exception as exc:
            self._record_tool_end(self.audit, name, "error", type(exc).__name__)
            raise
        self._record_tool_end(
            self.audit,
            name,
            "error" if _is_error_tool_result(result) else "success",
        )
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[
            [ToolCallRequest],
            Awaitable[ToolMessage | Command[Any]],
        ],
    ) -> ToolMessage | Command[Any]:
        name = request.tool.name if request.tool else str(request.tool_call["name"])
        args = request.tool_call.get("args", {})
        decision = self._decision(name)
        self._record_permission(self.audit, name, decision, args)
        if decision == PermissionDecision.DENY:
            return self._blocked_message(
                request,
                name,
                "工具已被权限策略拒绝。",
                reason="permission_denied",
            )
        self._record_tool_start(self.audit, name)
        try:
            result = await handler(request)
        except Exception as exc:
            self._record_tool_end(self.audit, name, "error", type(exc).__name__)
            raise
        self._record_tool_end(
            self.audit,
            name,
            "error" if _is_error_tool_result(result) else "success",
        )
        return result

    def _decision(self, name: str) -> PermissionDecision:
        try:
            permission = self.registry.get(name).permission
        except KeyError:
            return PermissionDecision.DENY
        return self.policy.decision_for(permission).decision

    def _blocked_message(
        self,
        request: ToolCallRequest,
        name: str,
        content: str,
        *,
        reason: str,
    ) -> ToolMessage:
        self._record_tool_blocked(self.audit, name, reason)
        return ToolMessage(
            content=content,
            tool_call_id=str(request.tool_call["id"]),
            name=name,
            status="error",
        )

    def _record_permission(
        self,
        audit: DatabaseAuditLogger | AuditLogger,
        name: str,
        decision: PermissionDecision,
        args: object,
    ) -> None:
        audit_args = args
        try:
            permission = self.registry.get(name).permission
        except KeyError:
            permission = None
        if permission is not None and not permission.audit_arguments:
            argument_names = (
                sorted(str(key) for key in args)
                if isinstance(args, dict)
                else []
            )
            audit_args = {
                "redacted": True,
                "argument_names": argument_names,
            }
        audit.record(
            "tool_permission_check",
            {
                "tool": name,
                "decision": decision.value,
                "args": audit_args,
            },
        )

    @staticmethod
    def _record_tool_blocked(
        audit: DatabaseAuditLogger | AuditLogger,
        name: str,
        reason: str,
    ) -> None:
        audit.record(
            "tool_execution_blocked",
            {"tool": name, "reason": reason},
        )

    def _record_tool_start(
        self,
        audit: DatabaseAuditLogger | AuditLogger,
        name: str,
    ) -> None:
        active_runtime = current_runtime_state(self.runtime)
        active_runtime.record_tool_call()
        self.usage.record(user_id=self.user_id(), tool_calls=1)
        audit.record("tool_execution_started", {"tool": name})

    @staticmethod
    def _record_tool_end(
        audit: DatabaseAuditLogger | AuditLogger,
        name: str,
        status: str,
        error_type: str | None = None,
    ) -> None:
        payload: dict[str, str] = {"tool": name, "status": status}
        if error_type:
            payload["error_type"] = error_type
        audit.record("tool_execution_finished", payload)


class ToolExecutionMiddleware(AgentMiddleware):
    def __init__(self, gateway: ToolExecutionGateway) -> None:
        self.gateway = gateway

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        return self.gateway.wrap_tool_call(request, handler)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[
            [ToolCallRequest],
            Awaitable[ToolMessage | Command[Any]],
        ],
    ) -> ToolMessage | Command[Any]:
        return await self.gateway.awrap_tool_call(request, handler)


class ModelUsageMiddleware(AgentMiddleware):
    def __init__(self, gateway: ToolExecutionGateway) -> None:
        self.gateway = gateway

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Any],
    ) -> Any:
        estimated_input = _request_input_tokens(request)
        try:
            response = handler(request)
        except Exception as exc:
            self.gateway.record_model_usage(
                estimated_input,
                0,
                status="error",
                error_type=type(exc).__name__,
            )
            raise
        input_tokens, output_tokens = _model_usage(request, response)
        self.gateway.record_model_usage(input_tokens, output_tokens)
        return response

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[Any]],
    ) -> Any:
        estimated_input = _request_input_tokens(request)
        try:
            response = await handler(request)
        except Exception as exc:
            self.gateway.record_model_usage(
                estimated_input,
                0,
                status="error",
                error_type=type(exc).__name__,
            )
            raise
        input_tokens, output_tokens = _model_usage(request, response)
        self.gateway.record_model_usage(input_tokens, output_tokens)
        return response


def _request_input_tokens(request: ModelRequest) -> int:
    request_messages: list[BaseMessage] = list(request.messages)
    if request.system_message is not None:
        request_messages.insert(0, request.system_message)
    return int(count_tokens_approximately(request_messages))


def _model_usage(request: ModelRequest, response: object) -> tuple[int, int]:
    messages = _response_messages(response)
    usage_rows = [
        message.usage_metadata
        for message in messages
        if isinstance(message, AIMessage) and message.usage_metadata
    ]
    if usage_rows:
        return (
            sum(int(row.get("input_tokens", 0)) for row in usage_rows),
            sum(int(row.get("output_tokens", 0)) for row in usage_rows),
        )
    return (
        _request_input_tokens(request),
        int(count_tokens_approximately(messages)),
    )


def _response_messages(response: object) -> list[BaseMessage]:
    if isinstance(response, AIMessage):
        return [response]
    if isinstance(response, ExtendedModelResponse):
        return list(response.model_response.result)
    if isinstance(response, ModelResponse):
        return list(response.result)
    return []


def _is_error_tool_result(result: object) -> bool:
    return isinstance(result, ToolMessage) and result.status == "error"
