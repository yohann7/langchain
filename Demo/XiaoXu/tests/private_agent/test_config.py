from pathlib import Path

from private_agent.config import (
    CONTAINER_EXPECTED_PYTHON,
    EXPECTED_PYTHON,
    POSIX_EXPECTED_PYTHON,
    WINDOWS_EXPECTED_PYTHON,
    AppSettings,
    expected_python_for_platform,
    load_settings,
    running_in_expected_python,
)


def test_default_python_path_matches_project_requirement():
    settings = AppSettings()

    assert settings.python_path == EXPECTED_PYTHON
    assert settings.is_expected_python() is True


def test_sqlite_database_defaults_under_run_dir(tmp_path):
    settings = AppSettings(run_dir=tmp_path)

    assert settings.resolve_in_run_dir(settings.sqlite_database_path) == (
        tmp_path / "xiaoxu.db"
    ).resolve(strict=False)


def test_expected_python_path_is_selected_by_platform():
    assert expected_python_for_platform("win32") == WINDOWS_EXPECTED_PYTHON
    assert expected_python_for_platform("linux") == POSIX_EXPECTED_PYTHON
    assert expected_python_for_platform("darwin") == POSIX_EXPECTED_PYTHON
    assert (
        expected_python_for_platform("linux", containerized=True)
        == CONTAINER_EXPECTED_PYTHON
    )


def test_running_interpreter_matches_project_python_symlink():
    assert running_in_expected_python() is True


def test_yaml_config_overrides_defaults(tmp_path):
    config_path = tmp_path / "agent.yaml"
    config_path.write_text(
        "user_id: test-user\n",
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    assert settings.user_id == "test-user"


def test_app_settings_has_no_removed_tool_configuration():
    settings = AppSettings()

    assert not hasattr(settings, "todo_store_path")
    assert not hasattr(settings, "reminder_store_path")
    assert not hasattr(settings, "allowed_roots")
    assert not hasattr(settings, "normalized_allowed_roots")


def test_app_settings_has_no_search_backend_config():
    settings = AppSettings()

    assert not hasattr(settings, "search")


def test_search_config_paths_are_the_only_behavior_settings():
    settings = AppSettings()

    assert settings.web_search_config_path == Path("config/web-search.yaml")
    assert settings.knowledge_search_config_path == Path(
        "config/knowledge-search.yaml"
    )


def test_search_config_paths_support_environment_overrides(tmp_path, monkeypatch):
    web_path = tmp_path / "web.yaml"
    knowledge_path = tmp_path / "knowledge.yaml"
    monkeypatch.setenv("PRIVATE_AGENT_WEB_SEARCH_CONFIG_PATH", str(web_path))
    monkeypatch.setenv(
        "PRIVATE_AGENT_KNOWLEDGE_SEARCH_CONFIG_PATH",
        str(knowledge_path),
    )

    settings = AppSettings(_env_file=None)

    assert settings.web_search_config_path == web_path
    assert settings.knowledge_search_config_path == knowledge_path


def test_removed_search_behavior_settings_are_ignored():
    settings = AppSettings(
        web_search_max_results=99,
        web_search_max_queries_per_run=99,
        knowledge_search_max_queries_per_run=99,
        searxng_timeout_seconds=99,
        knowledge_api_timeout_seconds=99,
    )

    assert not hasattr(settings, "web_search_max_results")
    assert not hasattr(settings, "web_search_max_queries_per_run")
    assert not hasattr(settings, "knowledge_search_max_queries_per_run")
    assert not hasattr(settings, "searxng_timeout_seconds")
    assert not hasattr(settings, "knowledge_api_timeout_seconds")
