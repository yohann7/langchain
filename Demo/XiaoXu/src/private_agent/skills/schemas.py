"""Skill metadata and activated-skill models."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str
    path: Path


@dataclass(frozen=True)
class LoadedSkill:
    metadata: SkillMetadata
    instructions: str
