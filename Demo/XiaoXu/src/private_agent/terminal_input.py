"""Protected terminal input for the private agent shell."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rich.console import Console

from private_agent.config import AppSettings
from private_agent.input_history import configure_cli_history

USER_PROMPT = "user："


@dataclass
class CliInputReader:
    """Read user input with a protected prompt when prompt_toolkit is available."""

    console: Console
    session: Any | None = None
    uses_prompt_toolkit: bool = False

    def read(self) -> str:
        if self.session is not None:
            return self.session.prompt()
        return self.console.input(USER_PROMPT)


def create_cli_input_reader(settings: AppSettings, console: Console) -> CliInputReader:
    """Create a CLI input reader whose visible prompt is not editable text."""

    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory
    except ImportError:
        configure_cli_history(settings)
        return CliInputReader(console)

    session = build_prompt_toolkit_session(settings, PromptSession, FileHistory)
    return CliInputReader(console, session=session, uses_prompt_toolkit=True)


def build_prompt_toolkit_session(
    settings: AppSettings,
    prompt_session_class: type,
    file_history_class: type,
) -> Any:
    """Build a prompt_toolkit session with the prompt outside the edit buffer."""

    history_path = settings.resolve_in_run_dir(settings.command_history_path)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    return prompt_session_class(
        message=USER_PROMPT,
        history=file_history_class(str(history_path)),
        enable_history_search=True,
    )
