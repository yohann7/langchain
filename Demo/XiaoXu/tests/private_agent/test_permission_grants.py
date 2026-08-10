import sqlite3

from private_agent.config import AppSettings
from private_agent.cli import build_policy
from private_agent.permission_grants import (
    effective_permission_overrides,
    grant_permanent_tool_permission,
    load_persistent_permission_overrides,
)
from private_agent.security import PermissionDecision, RiskLevel, ToolPermission


def test_grant_permanent_tool_permission_persists_allow_and_updates_settings(tmp_path):
    settings = AppSettings(run_dir=tmp_path)

    grant_permanent_tool_permission(settings, "web_search")

    database_path = settings.resolve_in_run_dir(settings.sqlite_database_path)
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT tool_id, decision FROM tool_grants
            WHERE user_id = ?
            """,
            (settings.user_id,),
        ).fetchone()
    assert row == ("web_search", "allow")
    assert settings.permission_overrides["web_search"] == "allow"
    assert load_persistent_permission_overrides(settings) == {"web_search": "allow"}


def test_effective_permission_overrides_merges_yaml_and_persistent_overrides(tmp_path):
    settings = AppSettings(
        run_dir=tmp_path,
        permission_overrides={"demo_tool": "ask"},
    )
    grant_permanent_tool_permission(settings, "web_search")

    assert effective_permission_overrides(settings) == {
        "demo_tool": "ask",
        "web_search": "allow",
    }


def test_build_policy_applies_persistent_permission_override(tmp_path):
    settings = AppSettings(run_dir=tmp_path)
    grant_permanent_tool_permission(settings, "web_search")

    policy = build_policy(settings)
    result = policy.decision_for(
        ToolPermission(
            name="web_search",
            risk=RiskLevel.NETWORK_READ,
            requires_approval=True,
            uses_network=True,
        )
    )

    assert result.decision == PermissionDecision.ALLOW
