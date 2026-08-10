from private_agent.core.identity import (
    actor_to_user_id,
    current_conversation_type,
    user_context,
)


def test_actor_ids_map_to_stable_distinct_internal_users() -> None:
    first = actor_to_user_id("opaque-actor-a", secret="identity-secret")
    repeated = actor_to_user_id("opaque-actor-a", secret="identity-secret")
    second = actor_to_user_id("opaque-actor-b", secret="identity-secret")

    assert first == repeated
    assert first != second
    assert "opaque-actor-a" not in first
    assert first.startswith("usr_")


def test_user_context_scopes_and_restores_conversation_type() -> None:
    assert current_conversation_type() == "single"

    with user_context("user-a", conversation_type="group"):
        assert current_conversation_type() == "group"

    assert current_conversation_type() == "single"
