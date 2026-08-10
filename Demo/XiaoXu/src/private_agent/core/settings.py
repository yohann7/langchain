"""Configuration for the private agent runtime."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

import yaml
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

WINDOWS_EXPECTED_PYTHON = Path(r"D:\Anaconda3\envs\langchain1.2\python.exe")
POSIX_EXPECTED_PYTHON = Path("/opt/anaconda3/envs/langchain1.2/bin/python")
CONTAINER_EXPECTED_PYTHON = Path("/usr/local/bin/python")


def expected_python_for_platform(
    platform: str | None = None,
    *,
    containerized: bool | None = None,
) -> Path:
    """Return the project interpreter path for the requested operating system."""

    current_platform = platform or sys.platform
    if current_platform.startswith("win"):
        return WINDOWS_EXPECTED_PYTHON
    if containerized is None:
        containerized = platform is None and Path("/.dockerenv").exists()
    if current_platform.startswith("linux") and containerized:
        return CONTAINER_EXPECTED_PYTHON
    return POSIX_EXPECTED_PYTHON


EXPECTED_PYTHON = expected_python_for_platform()


class AppSettings(BaseSettings):
    """Runtime settings loaded from env vars and optional YAML."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="PRIVATE_AGENT_",
        extra="ignore",
    )

    python_path: Path = EXPECTED_PYTHON
    user_id: str = "local-user"
    thread_id: str = "default"
    model_name: str = "not-configured"
    model_provider: str | None = None
    model_base_url: str | None = None
    model_api_key_env: str | None = None
    summarization_model_name: str | None = None
    active_model: str | None = None
    model_catalog: dict[str, Any] = Field(default_factory=dict)
    model_catalog_path: Path = Path("config/model-catalog.yaml")
    run_dir: Path = Path(".private_agent")
    audit_log_path: Path = Path(".private_agent/audit.jsonl")
    model_state_path: Path = Path(".private_agent/model_state.json")
    permission_state_path: Path = Path(".private_agent/permissions.json")
    command_history_path: Path = Path(".private_agent/history")
    sqlite_database_path: Path = Path(".private_agent/xiaoxu.db")
    skills_dir: Path = Path("skills")
    identity_secret: str = "change-me"
    knowledge_api_url: str = "http://knowledge:8080"
    knowledge_api_token: str | None = None
    web_search_config_path: Path = Path("config/web-search.yaml")
    knowledge_search_config_path: Path = Path("config/knowledge-search.yaml")
    knowledge_denied_users: list[str] = Field(default_factory=list)
    memory_max_content_bytes: int = Field(default=4 * 1024, gt=0)
    memory_max_items_per_user: int = Field(default=500, gt=0)
    memory_max_results: int = Field(default=50, gt=0)
    memory_max_query_bytes: int = Field(default=1024, gt=0)
    max_model_calls_per_run: int = Field(default=8, gt=0)
    max_tool_calls_per_run: int = Field(default=20, gt=0)
    enable_pii_middleware: bool = True
    enable_summarization_middleware: bool = True
    summarization_trigger_tokens: int = Field(default=12_000, gt=0)
    summarization_keep_tokens: int = Field(default=4_000, gt=0)
    skill_max_frontmatter_bytes: int = Field(default=16 * 1024, gt=0)
    skill_max_instructions_bytes: int = Field(default=64 * 1024, gt=0)
    skill_max_resource_bytes: int = Field(default=256 * 1024, gt=0)
    searxng_url: str = "http://searxng:8080"
    tavily_api_key_env: str = "TAVILY_API_KEY"
    permission_overrides: dict[str, str] = Field(default_factory=dict)
    api_token: str | None = None
    api_run_timeout_seconds: float = Field(default=120.0, gt=0)
    api_max_input_bytes: int = Field(default=8 * 1024, gt=0)
    api_max_output_bytes: int = Field(default=20_000, gt=0)

    @model_validator(mode="after")
    def validate_summarization_window(self) -> "AppSettings":
        if self.summarization_keep_tokens >= self.summarization_trigger_tokens:
            raise ValueError(
                "summarization_keep_tokens must be less than "
                "summarization_trigger_tokens"
            )
        return self

    def is_expected_python(self) -> bool:
        """Return whether the configured Python path is the project-required path."""

        return self.python_path.expanduser().resolve(strict=False) == EXPECTED_PYTHON.resolve(
            strict=False
        )

    def resolve_in_run_dir(self, path: Path) -> Path:
        """Resolve relative runtime paths under run_dir."""

        if path.is_absolute():
            return path
        return (self.run_dir / path.name).expanduser().resolve(strict=False)


def running_in_expected_python() -> bool:
    """Return whether the current interpreter is the required project Python."""

    return Path(sys.executable).resolve(strict=False) == EXPECTED_PYTHON.resolve(strict=False)


def _load_yaml_settings(config_path: Path | None) -> dict[str, Any]:
    if config_path is None:
        return {}
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ValueError("Config file must contain a mapping at the top level.")
    return raw


def load_settings(config_path: str | Path | None = None) -> AppSettings:
    """Load settings from env vars and optional YAML file.

    YAML values override environment-derived defaults because they represent an
    explicit runtime profile selected by the user.
    """

    path = Path(config_path).expanduser() if config_path else None
    return AppSettings(**_load_yaml_settings(path))
