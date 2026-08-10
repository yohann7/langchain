from private_agent.security import (
    PermissionDecision,
    PermissionPolicy,
    RiskLevel,
    ToolPermission,
)


def test_dangerous_tools_are_denied_by_default():
    policy = PermissionPolicy()
    tool = ToolPermission(
        name="delete_everything",
        risk=RiskLevel.DANGEROUS,
        requires_approval=False,
        description="dangerous test tool",
    )

    result = policy.decision_for(tool)

    assert result.decision == PermissionDecision.DENY


def test_requires_approval_forces_ask():
    policy = PermissionPolicy()
    tool = ToolPermission(
        name="read_network",
        risk=RiskLevel.READ_SAFE,
        requires_approval=True,
    )

    result = policy.decision_for(tool)

    assert result.decision == PermissionDecision.ASK


def test_tool_permission_has_no_removed_file_capabilities():
    permission = ToolPermission(
        name="read_network",
        risk=RiskLevel.NETWORK_READ,
        requires_approval=True,
    )

    assert not hasattr(permission, "can_read_files")
    assert not hasattr(permission, "can_write_files")
    assert not hasattr(permission, "allowed_roots")


def test_permission_policy_has_no_removed_path_whitelist():
    policy = PermissionPolicy()

    assert not hasattr(policy, "allowed_roots")
    assert not hasattr(policy, "is_path_allowed")


def test_explicit_user_memory_write_is_allowed_by_default():
    policy = PermissionPolicy()
    permission = ToolPermission(
        name="remember_memory",
        risk=RiskLevel.USER_MEMORY_WRITE,
        requires_approval=False,
        audit_arguments=False,
    )

    assert policy.decision_for(permission).decision == PermissionDecision.ALLOW
    assert permission.audit_arguments is False
