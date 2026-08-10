"""Persistent user-granted tool permissions backed by xiaoxu.db."""

from __future__ import annotations

from private_agent.config import AppSettings
from private_agent.persistence.database import XiaoXuDatabase
from private_agent.persistence.permissions import ToolGrantStore
from private_agent.security import PermissionDecision


def _store(settings: AppSettings) -> ToolGrantStore:
    database = XiaoXuDatabase(
        settings.resolve_in_run_dir(settings.sqlite_database_path)
    )
    return ToolGrantStore(database)


def load_persistent_permission_overrides(settings: AppSettings) -> dict[str, str]:
    clean: dict[str, str] = {}
    for tool_name, decision in _store(settings).load(user_id=settings.user_id).items():
        try:
            clean[tool_name] = PermissionDecision(decision).value
        except ValueError:
            continue
    return clean


def effective_permission_overrides(settings: AppSettings) -> dict[str, str]:
    merged = {
        name: PermissionDecision(decision).value
        for name, decision in settings.permission_overrides.items()
    }
    merged.update(load_persistent_permission_overrides(settings))
    return merged


def grant_permanent_tool_permission(
    settings: AppSettings,
    tool_name: str,
    decision: str = PermissionDecision.ALLOW.value,
) -> None:
    permission_decision = PermissionDecision(decision)
    _store(settings).save(
        user_id=settings.user_id,
        tool_id=tool_name,
        decision=permission_decision.value,
    )
    settings.permission_overrides[tool_name] = permission_decision.value
