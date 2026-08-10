"""Channel-specific tool profiles."""

from typing import Literal

ToolProfile = Literal["cli", "wecom_chat"]

TOOL_PROFILE_ALLOWLISTS: dict[ToolProfile, frozenset[str] | None] = {
    "cli": None,
    "wecom_chat": frozenset(
        {
            "activate_skill",
            "forget_memory",
            "get_knowledge_status",
            "list_memories",
            "read_skill_resource",
            "remember_memory",
            "search_knowledge",
            "search_memories",
            "update_memory",
        }
    ),
}
