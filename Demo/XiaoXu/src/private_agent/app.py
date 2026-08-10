"""The only composition entry point for XiaoXu runtimes."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from private_agent.core.settings import AppSettings, load_settings


def load_application_settings(config_path: str | Path | None = None) -> AppSettings:
    """Load and validate configuration once at the process boundary."""

    return load_settings(config_path)


def run(mode: Literal["cli", "api"], config_path: str | Path | None = None) -> None:
    settings = load_application_settings(config_path)
    if mode == "cli":
        from private_agent.interfaces.cli.app import app

        app()
        return
    from private_agent.interfaces.api.app import create_app
    import uvicorn

    uvicorn.run(create_app(settings), host="0.0.0.0", port=8000)
