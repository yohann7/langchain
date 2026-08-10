"""User capability policy owned by XiaoXu, not by channel adapters."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CapabilityPolicy:
    """Small deny-aware policy; richer role rules can be added without tool changes."""

    knowledge_denied_users: frozenset[str] = field(default_factory=frozenset)

    def can_search_knowledge(self, user_id: str) -> bool:
        return bool(user_id) and user_id not in self.knowledge_denied_users
