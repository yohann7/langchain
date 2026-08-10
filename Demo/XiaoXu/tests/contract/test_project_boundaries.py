from importlib.util import find_spec
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src" / "private_agent"


def test_xiaoxu_source_does_not_import_milvus_or_document_loaders() -> None:
    forbidden = ("pymilvus", "knowledge_service", ".rag.loaders")
    violations: list[str] = []
    for path in SOURCE_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if any(token in text for token in forbidden):
            violations.append(str(path.relative_to(PROJECT_ROOT)))
    assert violations == []


def test_memory_is_explicit_service_without_automatic_middleware() -> None:
    memory_dir = SOURCE_ROOT / "memory"
    assert sorted(path.name for path in memory_dir.glob("*.py")) == [
        "__init__.py",
        "models.py",
        "service.py",
    ]
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in memory_dir.glob("*.py")
    )
    assert "MemoryService" in text
    assert "AgentMiddleware" not in text


def test_removed_tool_modules_are_not_importable() -> None:
    removed_modules = [
        "private_agent.storage",
        "private_agent.persistence.reminders",
        "private_agent.persistence.todos",
        "private_agent.tools.file_tools",
        "private_agent.tools.files",
        "private_agent.tools.math_tools",
        "private_agent.tools.personal",
        "private_agent.tools.reminder_tools",
        "private_agent.tools.time_tools",
        "private_agent.tools.todo_tools",
        "private_agent.tools.utility",
    ]

    assert {
        module_name
        for module_name in removed_modules
        if find_spec(module_name) is not None
    } == set()
