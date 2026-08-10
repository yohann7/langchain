from pathlib import Path

import pytest

from private_agent.skills.loader import SkillLoader


def test_skill_loader_reads_metadata_before_full_instructions(tmp_path: Path) -> None:
    skill_dir = tmp_path / "knowledge-research"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: knowledge-research\n"
        "description: Search and cite private knowledge.\n"
        "---\n"
        "# Instructions\n\nUse search_knowledge.\n",
        encoding="utf-8",
    )
    loader = SkillLoader(tmp_path)

    metadata = loader.scan()

    assert metadata[0].name == "knowledge-research"
    assert not hasattr(metadata[0], "instructions")
    loaded = loader.load("knowledge-research")
    assert "Use search_knowledge." in loaded.instructions


def test_skill_loader_rejects_references_outside_skill_directory(tmp_path: Path) -> None:
    skill_dir = tmp_path / "unsafe"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: unsafe\ndescription: Unsafe example.\n---\n"
        "Read [outside](../secret.md).\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="outside"):
        SkillLoader(tmp_path).load("unsafe")


def test_skill_loader_reads_third_level_reference_on_demand(tmp_path: Path) -> None:
    skill_dir = tmp_path / "research"
    references = skill_dir / "references"
    references.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: research\ndescription: Research workflow.\n---\n"
        "Read [guide](references/guide.md) when needed.\n",
        encoding="utf-8",
    )
    (references / "guide.md").write_text("bounded reference", encoding="utf-8")
    loader = SkillLoader(tmp_path)

    assert loader.load_resource("research", "references/guide.md") == "bounded reference"


@pytest.mark.parametrize(
    "path",
    [
        "../secret.md",
        "references/../../secret.md",
        "other/file.md",
        "C:/secret.md",
    ],
)
def test_skill_loader_rejects_unsafe_resource_paths(
    tmp_path: Path,
    path: str,
) -> None:
    skill_dir = tmp_path / "research"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: research\ndescription: Research workflow.\n---\nInstructions.\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="references/ or assets/"):
        SkillLoader(tmp_path).load_resource("research", path)


def test_skill_loader_enforces_instruction_and_resource_size_limits(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "large"
    references = skill_dir / "references"
    references.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: large\ndescription: Large workflow.\n---\n123456789",
        encoding="utf-8",
    )
    (references / "large.txt").write_text("123456789", encoding="utf-8")
    loader = SkillLoader(
        tmp_path,
        max_instructions_bytes=8,
        max_resource_bytes=8,
    )

    assert [metadata.name for metadata in loader.scan()] == ["large"]
    with pytest.raises(ValueError, match="instructions exceed"):
        loader.load("large")
    with pytest.raises(ValueError, match="resource exceeds"):
        loader.load_resource("large", "references/large.txt")
