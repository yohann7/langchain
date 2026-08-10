"""V0 tool registry primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from private_agent.audit import AuditLogger
from private_agent.runtime import RuntimeState
from private_agent.security import PermissionDecision, PermissionPolicy, ToolPermission

if TYPE_CHECKING:
    from private_agent.agent.governance import ToolExecutionGateway


ToolCallable = Callable[..., Any]


@dataclass
class RegisteredTool:
    """A callable plus mandatory permission metadata."""

    func: ToolCallable
    permission: ToolPermission


@dataclass
class ToolRegistry:
    """Simple registry that prevents tools without permission metadata."""

    _tools: dict[str, RegisteredTool] = field(default_factory=dict)
    _gateway: ToolExecutionGateway | None = field(default=None, repr=False)

    def bind_gateway(self, gateway: ToolExecutionGateway) -> None:
        self._gateway = gateway

    @property
    def gateway(self) -> ToolExecutionGateway | None:
        return self._gateway

    def register(self, func: ToolCallable, permission: ToolPermission) -> None:
        if not permission.name:
            raise ValueError("Tool permission must include a non-empty name.")
        if permission.name in self._tools:
            raise ValueError(f"Tool already registered: {permission.name}")
        self._tools[permission.name] = RegisteredTool(func=func, permission=permission)

    def get(self, name: str) -> RegisteredTool:
        return self._tools[name]

    def remove(self, name: str) -> None:
        self._tools.pop(name, None)

    def list_permissions(self) -> list[ToolPermission]:
        return [tool.permission for tool in self._tools.values()]

    def names(self) -> list[str]:
        return sorted(self._tools)


class ToolExecutionError(RuntimeError):
    """Raised when a tool is blocked by policy or fails during execution."""


def execute_registered_tool(
    registry: ToolRegistry,
    name: str,
    policy: PermissionPolicy,
    runtime: RuntimeState,
    audit: AuditLogger | None = None,
    approved: bool = False,
    **kwargs: Any,
) -> Any:
    """Execute a registered tool through the V1 permission gateway.

    The LangChain HITL middleware handles user-facing approval for tools wired
    into an agent. This function is the local deterministic guard used by
    commands/tests and by internal wrappers.
    """

    if registry.gateway is not None:
        return registry.gateway.execute(
            name,
            approved=approved,
            audit_override=audit,
            **kwargs,
        )

    registered = registry.get(name)
    permission_result = policy.decision_for(registered.permission)
    if audit:
        audit_args: object = kwargs
        if not registered.permission.audit_arguments:
            audit_args = {
                "redacted": True,
                "argument_names": sorted(kwargs),
            }
        audit.record(
            "tool_permission_check",
            {
                "tool": name,
                "decision": permission_result.decision.value,
                "reason": permission_result.reason,
                "args": audit_args,
            },
        )
    if permission_result.decision == PermissionDecision.DENY:
        raise ToolExecutionError(f"Tool denied: {name}. {permission_result.reason}")
    if permission_result.decision == PermissionDecision.ASK and not approved:
        raise ToolExecutionError(f"Tool requires approval: {name}")
    runtime.record_tool_call()
    return registered.func(**kwargs)
