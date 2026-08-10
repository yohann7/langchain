"""Filesystem and metadata validation for skills."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import re


NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def validate_metadata(name: object, description: object, directory: Path) -> tuple[str, str]:
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
        raise ValueError(f"invalid skill name in {directory}")
    if name != directory.name:
        raise ValueError(f"skill name must match directory: {directory}")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(f"skill description must not be blank: {directory}")
    return name, description.strip()


def validate_references(instructions: str, skill_dir: Path) -> None:
    root = skill_dir.resolve(strict=False)
    for raw_target in MARKDOWN_LINK.findall(instructions):
        target = raw_target.split("#", 1)[0].strip()
        if not target or "://" in target or target.startswith("#"):
            continue
        path = PurePosixPath(target)
        if path.is_absolute():
            raise ValueError("skill reference points outside its directory")
        resolved = (root / Path(*path.parts)).resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("skill reference points outside its directory") from exc
