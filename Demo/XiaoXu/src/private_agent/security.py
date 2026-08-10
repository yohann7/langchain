"""Tool permission primitives."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RiskLevel(StrEnum):
    """Risk categories used by the tool registry."""

    READ_SAFE = "read_safe"
    USER_MEMORY_WRITE = "user_memory_write"
    NETWORK_READ = "network_read"
    WRITE_LOCAL = "write_local"
    EXTERNAL_WRITE = "external_write"
    MCP = "mcp"
    DANGEROUS = "dangerous"


class PermissionDecision(StrEnum):
    """Execution decision returned by the permission system."""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True)
class ToolPermission:
    """Metadata every executable tool must provide."""

    name: str
    risk: RiskLevel
    requires_approval: bool
    uses_network: bool = False
    description: str = ""
    audit_arguments: bool = True


@dataclass(frozen=True)
class PermissionResult:
    """Result of evaluating whether a tool may execute."""

    decision: PermissionDecision
    reason: str


DEFAULT_RISK_DECISIONS: dict[RiskLevel, PermissionDecision] = {
    RiskLevel.READ_SAFE: PermissionDecision.ALLOW,
    RiskLevel.USER_MEMORY_WRITE: PermissionDecision.ALLOW,
    RiskLevel.NETWORK_READ: PermissionDecision.ASK,
    RiskLevel.WRITE_LOCAL: PermissionDecision.ASK,
    RiskLevel.EXTERNAL_WRITE: PermissionDecision.ASK,
    RiskLevel.MCP: PermissionDecision.ASK,
    RiskLevel.DANGEROUS: PermissionDecision.DENY,
}


class PermissionPolicy:
    """Policy engine for tool risk and approval decisions."""

    def __init__(
        self,
        overrides: dict[str, PermissionDecision | str] | None = None,
    ) -> None:
        self.overrides = {
            name: PermissionDecision(decision)
            for name, decision in (overrides or {}).items()
        }

    def decision_for(self, tool: ToolPermission) -> PermissionResult:
        if tool.name in self.overrides:
            decision = self.overrides[tool.name]
            return PermissionResult(decision, f"override for {tool.name}")
        if tool.requires_approval:
            return PermissionResult(PermissionDecision.ASK, "tool requires approval")
        decision = DEFAULT_RISK_DECISIONS[tool.risk]
        return PermissionResult(decision, f"default decision for {tool.risk.value}")

    def describe(self) -> dict[str, object]:
        return {
            "default_decisions": {
                risk.value: decision.value
                for risk, decision in DEFAULT_RISK_DECISIONS.items()
            },
            "overrides": {
                name: decision.value for name, decision in self.overrides.items()
            },
        }
