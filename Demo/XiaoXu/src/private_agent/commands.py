"""Deterministic slash-command handling for V0."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any, Callable

from private_agent.config import AppSettings
from private_agent.runtime import RuntimeState
from private_agent.security import PermissionPolicy
from private_agent.tools.registry import (
    ToolExecutionError,
    ToolRegistry,
    execute_registered_tool,
)


@dataclass(frozen=True)
class CommandResponse:
    """Response produced by local command handling."""

    handled: bool
    message: str
    should_exit: bool = False
    action: str | None = None


CommandHandler = Callable[
    [RuntimeState, AppSettings, PermissionPolicy, ToolRegistry | None],
    CommandResponse,
]


def _help(
    runtime: RuntimeState,
    settings: AppSettings,
    policy: PermissionPolicy,
    registry: ToolRegistry | None,
) -> CommandResponse:
    return CommandResponse(
        True,
        "\n".join(
            [
                "Available V1 commands:",
                "/help - Show commands",
                "/status - Show runtime status",
                "/permissions - Show permission policy summary",
                "/usage - Show local usage counters",
                "/clear - Delete this conversation's persisted checkpoint",
                "/compact - Request context compaction on the next agent run",
                "/tools - Show registered tools and permission metadata",
                "/rag status - Show SQLite, embedding, and Milvus status",
                "/rag list - List local knowledge bases",
                "/rag search <query> - Search local knowledge bases",
                "/rag ingest - Show the approved-ingest usage",
                "/memory add <content> - Explicitly save long-term memory",
                "/memory list [limit] - List your long-term memories",
                "/memory search <query> - Search your long-term memories",
                "/memory update <memory_id> <content> - Replace one memory",
                "/memory delete <memory_id> - Permanently delete one memory",
                "/model - Open interactive model management menu",
                "/thinking - Open interactive thinking mode menu",
                "/exit - Stop the private agent shell",
            ]
        ),
    )


def _status(
    runtime: RuntimeState,
    settings: AppSettings,
    policy: PermissionPolicy,
    registry: ToolRegistry | None,
) -> CommandResponse:
    return CommandResponse(True, json.dumps(runtime.snapshot(), ensure_ascii=False, indent=2))


def _permissions(
    runtime: RuntimeState,
    settings: AppSettings,
    policy: PermissionPolicy,
    registry: ToolRegistry | None,
) -> CommandResponse:
    return CommandResponse(True, json.dumps(policy.describe(), ensure_ascii=False, indent=2))


def _usage(
    runtime: RuntimeState,
    settings: AppSettings,
    policy: PermissionPolicy,
    registry: ToolRegistry | None,
) -> CommandResponse:
    usage = runtime.snapshot()["usage"]
    usage_date = datetime.now(timezone.utc).date().isoformat()
    if registry is not None and registry.gateway is not None:
        usage_record = registry.gateway.daily_usage()
        usage_date = usage_record.usage_date
        usage = usage_record.to_dict()
    return CommandResponse(
        True,
        json.dumps(
            {"usage_date": usage_date, "usage": usage},
            ensure_ascii=False,
            indent=2,
        ),
    )


def _exit(
    runtime: RuntimeState,
    settings: AppSettings,
    policy: PermissionPolicy,
    registry: ToolRegistry | None,
) -> CommandResponse:
    runtime.stop()
    return CommandResponse(True, "Stopping private agent shell.", should_exit=True)


def _clear(
    runtime: RuntimeState,
    settings: AppSettings,
    policy: PermissionPolicy,
    registry: ToolRegistry | None,
    checkpointer: Any | None = None,
) -> CommandResponse:
    delete_thread = getattr(checkpointer, "delete_thread", None)
    if not callable(delete_thread):
        return CommandResponse(
            True,
            "Conversation was not cleared: the active checkpointer does not support deletion.",
        )
    try:
        delete_thread(runtime.thread_id)
    except Exception as exc:
        return CommandResponse(
            True,
            f"Conversation was not cleared: {type(exc).__name__}.",
        )
    runtime.clear_conversation()
    return CommandResponse(True, "Conversation cleared.")


def _compact(
    runtime: RuntimeState,
    settings: AppSettings,
    policy: PermissionPolicy,
    registry: ToolRegistry | None,
) -> CommandResponse:
    runtime.request_compaction()
    return CommandResponse(True, "Context compaction marker recorded.")


def _tools(
    runtime: RuntimeState,
    settings: AppSettings,
    policy: PermissionPolicy,
    registry: ToolRegistry | None,
) -> CommandResponse:
    if registry is None or not registry.list_permissions():
        return CommandResponse(True, "No V1 tools are registered.")
    rows = []
    for permission in sorted(registry.list_permissions(), key=lambda item: item.name):
        decision = policy.decision_for(permission)
        rows.append(
            " | ".join(
                [
                    permission.name,
                    f"risk={permission.risk.value}",
                    f"decision={decision.decision.value}",
                    f"approval={permission.requires_approval}",
                    f"network={permission.uses_network}",
                ]
            )
        )
    return CommandResponse(True, "\n".join(rows))


def _model(
    runtime: RuntimeState,
    settings: AppSettings,
    policy: PermissionPolicy,
    registry: ToolRegistry | None,
) -> CommandResponse:
    return CommandResponse(
        True,
        "Opening model management menu.",
        action="model_menu",
    )


def _thinking(
    runtime: RuntimeState,
    settings: AppSettings,
    policy: PermissionPolicy,
    registry: ToolRegistry | None,
) -> CommandResponse:
    return CommandResponse(
        True,
        "Opening thinking mode menu.",
        action="thinking_menu",
    )


COMMANDS: dict[str, CommandHandler] = {
    "/help": _help,
    "/status": _status,
    "/permissions": _permissions,
    "/usage": _usage,
    "/compact": _compact,
    "/tools": _tools,
    "/model": _model,
    "/thinking": _thinking,
    "/exit": _exit,
}


def is_slash_command(text: str) -> bool:
    return text.strip().startswith("/")


def handle_command(
    text: str,
    runtime: RuntimeState,
    settings: AppSettings,
    policy: PermissionPolicy,
    registry: ToolRegistry | None = None,
    *,
    checkpointer: Any | None = None,
) -> CommandResponse:
    """Handle a local slash command.

    Natural language is intentionally not handled here. Future Agent versions
    should only receive input after this parser declines it.
    """

    stripped = text.strip()
    if not stripped.startswith("/"):
        return CommandResponse(False, "")

    parts = stripped.split(maxsplit=1)
    command = parts[0]
    if command == "/clear":
        return _clear(
            runtime,
            settings,
            policy,
            registry,
            checkpointer,
        )
    if command == "/rag":
        return _handle_rag_subcommand(
            parts[1] if len(parts) > 1 else "",
            runtime,
            settings,
            policy,
            registry,
        )
    if command == "/memory":
        return _handle_memory_subcommand(
            parts[1] if len(parts) > 1 else "",
            runtime,
            settings,
            policy,
            registry,
        )
    if command == "/model" and len(parts) > 1:
        return CommandResponse(True, "模型管理只使用 /model。请输入 /model 后在菜单中选择功能。")
    if command == "/thinking" and len(parts) > 1:
        return CommandResponse(True, "思考模式只使用 /thinking。请输入 /thinking 后在菜单中选择功能。")
    handler = COMMANDS.get(command)
    if handler is None:
        return CommandResponse(
            True,
            f"Unknown command: {command}. Use /help to see available commands.",
        )
    return handler(runtime, settings, policy, registry)


def _handle_rag_subcommand(
    arguments: str,
    runtime: RuntimeState,
    settings: AppSettings,
    policy: PermissionPolicy,
    registry: ToolRegistry | None,
) -> CommandResponse:
    if registry is None:
        return CommandResponse(True, "Knowledge tool is not initialized.")
    verb, _, remainder = arguments.strip().partition(" ")
    if verb in {"", "help"}:
        return CommandResponse(
            True,
            "Usage: /rag search <query>",
        )
    if verb != "search":
        return CommandResponse(True, f"Unknown /rag subcommand: {verb}")
    if not remainder.strip():
        return CommandResponse(True, "Usage: /rag search <query>")
    try:
        output = execute_registered_tool(
            registry,
            "search_knowledge",
            policy,
            runtime,
            query=remainder.strip(),
        )
    except (KeyError, RuntimeError, ToolExecutionError, ValueError) as exc:
        return CommandResponse(True, f"RAG command failed: {exc}")
    return CommandResponse(True, str(output))


def _handle_memory_subcommand(
    arguments: str,
    runtime: RuntimeState,
    settings: AppSettings,
    policy: PermissionPolicy,
    registry: ToolRegistry | None,
) -> CommandResponse:
    if registry is None:
        return CommandResponse(True, "Memory tools are not initialized.")
    verb, _, remainder = arguments.strip().partition(" ")
    if verb in {"", "help"}:
        return CommandResponse(
            True,
            (
                "Usage: /memory add <content> | list [limit] | "
                "search <query> | update <memory_id> <content> | "
                "delete <memory_id>"
            ),
        )

    tool_name: str
    kwargs: dict[str, Any]
    if verb == "add":
        if not remainder.strip():
            return CommandResponse(True, "Usage: /memory add <content>")
        tool_name = "remember_memory"
        kwargs = {"content": remainder.strip()}
    elif verb == "list":
        raw_limit = remainder.strip()
        if raw_limit:
            try:
                limit = int(raw_limit)
            except ValueError:
                return CommandResponse(True, "Memory list limit must be an integer.")
        else:
            limit = 20
        tool_name = "list_memories"
        kwargs = {"limit": limit}
    elif verb == "search":
        if not remainder.strip():
            return CommandResponse(True, "Usage: /memory search <query>")
        tool_name = "search_memories"
        kwargs = {"query": remainder.strip()}
    elif verb == "update":
        memory_id, separator, content = remainder.strip().partition(" ")
        if not separator or not content.strip():
            return CommandResponse(
                True,
                "Usage: /memory update <memory_id> <content>",
            )
        tool_name = "update_memory"
        kwargs = {"memory_id": memory_id, "content": content.strip()}
    elif verb in {"delete", "forget"}:
        memory_id = remainder.strip()
        if not memory_id or " " in memory_id:
            return CommandResponse(
                True,
                "Usage: /memory delete <memory_id>",
            )
        tool_name = "forget_memory"
        kwargs = {"memory_id": memory_id}
    else:
        return CommandResponse(True, f"Unknown /memory subcommand: {verb}")

    try:
        output = execute_registered_tool(
            registry,
            tool_name,
            policy,
            runtime,
            approved=True,
            **kwargs,
        )
    except (KeyError, LookupError, RuntimeError, ToolExecutionError, ValueError) as exc:
        return CommandResponse(True, f"Memory command failed: {exc}")
    return CommandResponse(True, str(output))
