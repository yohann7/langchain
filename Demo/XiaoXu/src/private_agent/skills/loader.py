"""Metadata-first loader for the Agent Skills directory format."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from private_agent.skills.schemas import LoadedSkill, SkillMetadata
from private_agent.skills.validation import validate_metadata, validate_references


class SkillLoader:
    def __init__(
        self,
        root: str | Path,
        *,
        max_frontmatter_bytes: int = 16 * 1024,
        max_instructions_bytes: int = 64 * 1024,
        max_resource_bytes: int = 256 * 1024,
    ) -> None:
        self.root = Path(root).expanduser().resolve(strict=False)
        self.max_frontmatter_bytes = max_frontmatter_bytes
        self.max_instructions_bytes = max_instructions_bytes
        self.max_resource_bytes = max_resource_bytes
        self._metadata: dict[str, SkillMetadata] = {}

    def scan(self) -> list[SkillMetadata]:
        found: dict[str, SkillMetadata] = {}
        if not self.root.exists():
            self._metadata = {}
            return []
        for skill_file in sorted(self.root.glob("*/SKILL.md")):
            frontmatter = _read_frontmatter(
                skill_file,
                max_bytes=self.max_frontmatter_bytes,
            )
            name, description = validate_metadata(
                frontmatter.get("name"),
                frontmatter.get("description"),
                skill_file.parent,
            )
            found[name] = SkillMetadata(name, description, skill_file.parent)
        self._metadata = found
        return list(found.values())

    def load(self, name: str) -> LoadedSkill:
        if not self._metadata:
            self.scan()
        try:
            metadata = self._metadata[name]
        except KeyError as exc:
            raise KeyError(f"unknown skill: {name}") from exc
        _, instructions = _parse_skill_file(
            metadata.path / "SKILL.md",
            max_frontmatter_bytes=self.max_frontmatter_bytes,
            max_instructions_bytes=self.max_instructions_bytes,
        )
        validate_references(instructions, metadata.path)
        return LoadedSkill(metadata=metadata, instructions=instructions.strip())

    def load_resource(self, name: str, relative_path: str) -> str:
        """Load one bounded text resource below references/ or assets/."""

        if not self._metadata:
            self.scan()
        try:
            metadata = self._metadata[name]
        except KeyError as exc:
            raise KeyError(f"unknown skill: {name}") from exc

        normalized = relative_path.strip().replace("\\", "/")
        candidate_path = PurePosixPath(normalized)
        if (
            not normalized
            or candidate_path.is_absolute()
            or ".." in candidate_path.parts
            or candidate_path.parts[0] not in {"references", "assets"}
        ):
            raise ValueError(
                "skill resource path must be relative and begin with references/ or assets/"
            )

        skill_root = metadata.path.resolve(strict=True)
        resource_path = (skill_root / Path(*candidate_path.parts)).resolve(strict=True)
        if skill_root not in resource_path.parents or not resource_path.is_file():
            raise ValueError("skill resource resolves outside the selected skill")
        with resource_path.open("rb") as handle:
            raw_content = handle.read(self.max_resource_bytes + 1)
        if len(raw_content) > self.max_resource_bytes:
            raise ValueError(
                f"skill resource exceeds {self.max_resource_bytes} bytes: {relative_path}"
            )
        try:
            return raw_content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("skill resources must be UTF-8 text") from exc


def _read_frontmatter(path: Path, *, max_bytes: int) -> dict[str, Any]:
    """Read only YAML frontmatter so discovery never loads the skill body."""

    frontmatter_lines: list[str] = []
    consumed = 0
    with path.open("r", encoding="utf-8") as handle:
        first = handle.readline()
        consumed += len(first.encode("utf-8"))
        if first.strip() != "---":
            raise ValueError(f"SKILL.md is missing YAML frontmatter: {path}")
        for line in handle:
            consumed += len(line.encode("utf-8"))
            if consumed > max_bytes:
                raise ValueError(
                    f"SKILL.md frontmatter exceeds {max_bytes} bytes: {path}"
                )
            if line.strip() == "---":
                break
            frontmatter_lines.append(line)
        else:
            raise ValueError(f"SKILL.md frontmatter is not closed: {path}")

    raw = yaml.safe_load("".join(frontmatter_lines)) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"SKILL.md frontmatter must be a mapping: {path}")
    return raw


def _parse_skill_file(
    path: Path,
    *,
    max_frontmatter_bytes: int = 16 * 1024,
    max_instructions_bytes: int = 64 * 1024,
) -> tuple[dict[str, Any], str]:
    frontmatter = _read_frontmatter(path, max_bytes=max_frontmatter_bytes)
    instruction_lines: list[str] = []
    instruction_bytes = 0
    frontmatter_closed = False
    with path.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if index == 0:
                continue
            if not frontmatter_closed:
                if line.strip() == "---":
                    frontmatter_closed = True
                continue
            instruction_bytes += len(line.encode("utf-8"))
            if instruction_bytes > max_instructions_bytes:
                raise ValueError(
                    f"SKILL.md instructions exceed {max_instructions_bytes} bytes: {path}"
                )
            instruction_lines.append(line)
    return frontmatter, "".join(instruction_lines)
