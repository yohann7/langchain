"""Explicit full-instruction activation after metadata matching."""

from private_agent.skills.loader import SkillLoader
from private_agent.skills.schemas import LoadedSkill


def activate_skill(loader: SkillLoader, name: str) -> LoadedSkill:
    return loader.load(name)
