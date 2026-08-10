"""In-memory registry containing only cheap skill metadata."""

from __future__ import annotations

from private_agent.skills.schemas import SkillMetadata


class SkillRegistry:
    def __init__(self, skills: list[SkillMetadata]) -> None:
        self._skills = {skill.name: skill for skill in skills}

    def list(self) -> list[SkillMetadata]:
        return list(self._skills.values())

    def get(self, name: str) -> SkillMetadata:
        return self._skills[name]
