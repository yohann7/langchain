"""Readline-backed command history for the interactive shell."""

from __future__ import annotations

import atexit
from typing import Any

from private_agent.config import AppSettings

HISTORY_LENGTH = 1000


def configure_cli_history(
    settings: AppSettings,
    *,
    readline_module: Any | None = None,
    register_atexit: bool = True,
) -> bool:
    """Enable arrow-key history navigation for console.input/input prompts."""

    readline = readline_module or _import_readline()
    if readline is None:
        return False

    history_path = settings.resolve_in_run_dir(settings.command_history_path)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    _bind_history_keys(readline)
    readline.set_history_length(HISTORY_LENGTH)
    try:
        readline.read_history_file(str(history_path))
    except FileNotFoundError:
        pass
    except OSError:
        pass

    if register_atexit:
        atexit.register(_write_history_file, readline, str(history_path))
    return True


def record_cli_history(text: str, *, readline_module: Any | None = None) -> None:
    """Add one submitted command to readline history without consecutive duplicates."""

    stripped = text.strip()
    if not stripped:
        return
    readline = readline_module or _import_readline()
    if readline is None:
        return
    history_length = readline.get_current_history_length()
    if history_length > 0 and readline.get_history_item(history_length) == stripped:
        return
    readline.add_history(stripped)


def _bind_history_keys(readline: Any) -> None:
    for command in (
        "set editing-mode emacs",
        "\\e[A: previous-history",
        "\\e[B: next-history",
    ):
        try:
            readline.parse_and_bind(command)
        except (AttributeError, OSError):
            continue


def _write_history_file(readline: Any, history_path: str) -> None:
    try:
        readline.write_history_file(history_path)
    except OSError:
        pass


def _import_readline():
    try:
        import readline
    except ImportError:
        return None
    return readline
