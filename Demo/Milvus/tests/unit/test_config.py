from pathlib import Path

import pytest

from knowledge_service.config import KnowledgeSettings, load_settings


def test_default_search_recall_is_high_enough_for_agent_research() -> None:
    settings = KnowledgeSettings()

    assert settings.rag_top_k == 10
    assert settings.rag_candidate_limit == 50


def test_settings_require_cuda_device_and_resolve_runtime_paths(tmp_path: Path) -> None:
    settings = KnowledgeSettings(run_dir=tmp_path, embedding_device="cuda:0")

    assert settings.database_path == tmp_path / "knowledge.db"
    assert settings.documents_path == tmp_path / "documents"
    assert settings.transfers_path == tmp_path / "transfers"

    with pytest.raises(ValueError, match="CUDA"):
        KnowledgeSettings(run_dir=tmp_path, embedding_device="cpu")


def test_yaml_defaults_are_overridden_by_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "knowledge.yaml"
    config.write_text(
        "embedding:\n  batch_size: 4\nsearch:\n  top_k: 6\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("KNOWLEDGE_RAG_TOP_K", "9")
    monkeypatch.setenv("KNOWLEDGE_ALLOWED_ROOTS", '["/imports", "/shared"]')

    settings = load_settings(config)

    assert settings.embedding_batch_size == 4
    assert settings.rag_top_k == 9
    assert settings.allowed_roots == [Path("/imports"), Path("/shared")]
