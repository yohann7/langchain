"""Command-line shell for the V0 private agent."""

from __future__ import annotations

from pathlib import Path
import json
from typing import Annotated, Any
from dataclasses import dataclass

import typer
from rich.console import Console
from rich.live import Live
from rich.prompt import Prompt

from private_agent.commands import handle_command
from private_agent.agent_factory import build_tools, create_private_agent, create_resources
from private_agent.agent_runner import (
    AgentRunner,
    extract_final_text,
    format_approval_requests,
)
from private_agent.config import AppSettings, load_settings, running_in_expected_python
from private_agent.input_history import record_cli_history
from private_agent.model_menus import run_model_menu, run_thinking_menu
from private_agent.models import ModelManager
from private_agent.permission_grants import (
    effective_permission_overrides,
    grant_permanent_tool_permission,
)
from private_agent.runtime import RuntimeState, RuntimeStatus
from private_agent.security import PermissionPolicy
from private_agent.terminal_input import create_cli_input_reader
from private_agent.tool_usage import (
    ensure_tool_usage_header,
    extract_tool_usage_backend,
)
from private_agent.knowledge.formatter import extract_knowledge_source_lines
from private_agent.tools.search_tools import format_web_search_sources
from private_agent.tools.registry import ToolRegistry

app = typer.Typer(help="Private Agent V1 shell.")
console = Console()
COMMAND_OUTPUT_SEPARATOR = "-" * 60


@dataclass(frozen=True)
class AuthorizationResult:
    """Authorization decisions chosen by the user."""

    decisions: list[dict]
    permanent_grants: list[str]


def build_policy(settings: AppSettings) -> PermissionPolicy:
    return PermissionPolicy(
        overrides=effective_permission_overrides(settings),
    )


def prepare_agent(
    settings: AppSettings,
    policy: PermissionPolicy,
    runtime: RuntimeState,
) -> tuple[Any | None, ToolRegistry, str | None]:
    """Create the agent when a model is configured, otherwise return tools only."""

    resources = create_resources(settings, policy, runtime)
    build_tools(resources)
    registry = resources.registry
    try:
        agent, resources = create_private_agent(settings, policy, runtime)
        registry = resources.registry
        return agent, registry, None
    except ValueError as exc:
        return None, registry, str(exc)


def prompt_menu_choice(prompt: str, choices: list[str] | None, default: str | None) -> str:
    """Prompt for one menu choice after printing the menu body."""

    console.print(prompt)
    return Prompt.ask("Select", choices=choices, default=default)


def agent_output(message: str) -> str:
    return f"xiaoxu：{message}"


def is_blank_input(text: str) -> bool:
    """Return whether terminal input contains no user content."""

    return not text.strip()


def print_agent_message(message: str) -> None:
    console.print(agent_output(message))


def print_command_separator(target_console: Console = console) -> None:
    """Print a divider before local slash-command output."""

    target_console.print(COMMAND_OUTPUT_SEPARATOR)


def print_command_message(message: str, target_console: Console = console) -> None:
    """Print local slash-command output separated from conversation text."""

    if message.strip():
        print_command_separator(target_console)
    target_console.print(agent_output(message))


def is_natural_exit_intent(text: str) -> bool:
    """Return whether text is an explicit natural-language request to exit."""

    stripped = text.strip()
    lowered = stripped.lower()
    if lowered in {"exit", "quit", "bye", "goodbye"}:
        return True
    compact = (
        stripped.replace(" ", "")
        .replace("，", "")
        .replace("。", "")
        .replace("！", "")
        .replace("!", "")
    )
    if compact.startswith(("不要", "别", "不")):
        return False
    exact_phrases = {
        "退出",
        "退出对话",
        "退出会话",
        "结束",
        "结束对话",
        "结束会话",
        "结束本次会话",
        "关闭对话",
        "关闭会话",
        "停止对话",
        "停止会话",
        "再见",
        "拜拜",
    }
    if compact in exact_phrases:
        return True
    prefixes = ("请", "帮我", "我要", "我想", "麻烦")
    actions = ("退出", "退出对话", "退出会话", "结束对话", "结束本次会话", "关闭会话")
    return any(compact == prefix + action for prefix in prefixes for action in actions)


def handle_natural_exit_intent(
    text: str,
    runtime: RuntimeState,
    confirm_exit,
) -> str | None:
    """Confirm and execute natural-language exit requests."""

    if not is_natural_exit_intent(text):
        return None
    if confirm_exit():
        runtime.stop()
        return "Stopping private agent shell."
    return "已取消结束本次会话。"


def prompt_for_exit_confirmation() -> bool:
    """Ask whether to end the current shell session."""

    print_agent_message("检测到你想结束本次会话。")
    choice = Prompt.ask(
        "是否结束本次会话？",
        choices=["是", "否"],
        default="否",
    )
    return choice == "是"


def handle_line(
    text: str,
    runtime: RuntimeState,
    settings: AppSettings,
    policy: PermissionPolicy,
    registry: ToolRegistry | None = None,
    agent=None,
    approval_callback=None,
) -> str:
    """Handle a single CLI input line."""

    response = handle_command(
        text,
        runtime,
        settings,
        policy,
        registry,
        checkpointer=getattr(agent, "checkpointer", None),
    )
    if response.handled:
        return response.message
    if agent is None:
        return "Agent model is not configured. Set PRIVATE_AGENT_MODEL_NAME or use /help for commands."
    runner = AgentRunner(agent, settings, runtime)
    return runner.invoke(text, approval_callback=approval_callback)


def handle_line_streaming(
    text: str,
    runtime: RuntimeState,
    settings: AppSettings,
    policy: PermissionPolicy,
    registry: ToolRegistry | None = None,
    agent=None,
    approval_callback=None,
    emit_text=None,
    emit_status=None,
    emit_thinking=None,
    emit_thinking_done=None,
    emit_tool_result=None,
) -> str | None:
    """Handle one CLI input line and stream model output through callbacks."""

    response = handle_command(
        text,
        runtime,
        settings,
        policy,
        registry,
        checkpointer=getattr(agent, "checkpointer", None),
    )
    if response.handled:
        return response.message
    if agent is None:
        return "Agent model is not configured. Use /model to select a model or use /help for commands."

    runner = AgentRunner(agent, settings, runtime)
    return runner.stream(
        text,
        approval_callback=approval_callback,
        emit_text=emit_text,
        emit_status=emit_status,
        emit_thinking=emit_thinking,
        show_thinking=should_show_thinking(settings),
        emit_thinking_done=emit_thinking_done,
        emit_tool_result=emit_tool_result,
    )


def should_show_thinking(settings: AppSettings) -> bool:
    """Return whether the active model's thinking stream should be displayed."""

    try:
        manager = ModelManager(settings)
        if not manager.has_active_model():
            return False
        profile = manager.active_profile()
        return profile.supports_thinking and manager.effective_thinking().enabled is True
    except ValueError:
        return False


def resolve_authorization_choices(
    action_requests: list[dict],
    choices: list[str],
    settings: AppSettings | None = None,
) -> AuthorizationResult:
    """Map numbered authorization choices to LangGraph HITL decisions."""

    decisions: list[dict] = []
    permanent_grants: list[str] = []
    for request, choice in zip(action_requests, choices, strict=False):
        name = str(request.get("name", "unknown"))
        if choice == "1":
            decisions.append({"type": "approve"})
            permanent_grants.append(name)
            if settings is not None:
                grant_permanent_tool_permission(settings, name)
        elif choice == "2":
            decisions.append({"type": "approve"})
        else:
            decisions.append({"type": "reject"})
    return AuthorizationResult(decisions=decisions, permanent_grants=permanent_grants)


def prompt_for_approvals(
    action_requests: list[dict],
    settings: AppSettings | None = None,
) -> list[dict]:
    """Prompt the user for permanent, one-time, or rejected authorization."""

    choices: list[str] = []
    for request in action_requests:
        name = request.get("name", "unknown")
        args = request.get("args", {})
        print_agent_message(f"Agent 执行工具没有权限：{name}")
        print_agent_message("工具参数：")
        console.print(json.dumps(args, ensure_ascii=False, indent=2))
        print_agent_message("请选择授权方式：")
        console.print("1. 永久授权")
        console.print("2. 本次授权")
        console.print("3. 不授权")
        choices.append(
            Prompt.ask(
                "Select",
                choices=["1", "2", "3"],
                default="3",
            )
        )
    return resolve_authorization_choices(action_requests, choices, settings).decisions


class StreamingConsoleOutput:
    """Console output controller for streaming answer and transient thinking."""

    def __init__(self, target_console: Console, *, use_live: bool = True) -> None:
        self.console = target_console
        self.use_live = use_live
        self._answer_started = False
        self._thinking_started = False
        self._thinking_done_printed = False
        self._thinking_buffer = ""
        self._live: Live | None = None
        self._tool_result_started = False
        self._tool_result_done_printed = False
        self._tool_result_name = ""
        self._tool_result_content = ""
        self._tool_result_live: Live | None = None
        self._answer_parts: list[str] = []
        self._web_search_backend = "None"
        self._knowledge_search_backends: set[str] = set()

    def emit_text(self, text: str) -> None:
        self.emit_thinking_done()
        self.emit_tool_result_done()
        self._answer_parts.append(text)
        self._answer_started = True

    def emit_status(self, status: str) -> None:
        self.emit_thinking_done()
        self.emit_tool_result_done()
        if "正在调用工具：" in status:
            self._discard_intermediate_answer()
        self.console.print(f"\n[cyan]{agent_output(status)}[/cyan]")
        self._answer_started = False

    def emit_tool_result(self, tool_name: str, content: str) -> None:
        self.emit_thinking_done()
        self.emit_tool_result_done()
        self._discard_intermediate_answer()
        self._record_tool_usage(tool_name, content)
        if tool_name == "web_search":
            self.console.print(f"\n[green]{agent_output('web_search 搜索网址：')}[/green]")
            self.console.print(format_web_search_sources(content))
        elif tool_name == "search_knowledge":
            source_lines = extract_knowledge_source_lines(content)
            if source_lines:
                self.console.print(
                    f"\n[green]{agent_output('search_knowledge 来源：')}[/green]"
                )
                self.console.print("\n".join(source_lines))
        else:
            self.console.print(f"\n[green]{agent_output(f'{tool_name} 结果：')}[/green]")
            self.console.print(content)
        self._answer_started = False

    def emit_thinking(self, thinking: str) -> None:
        if not thinking:
            return
        self.emit_tool_result_done()
        if not self._thinking_started:
            self._thinking_buffer = ""
        self._thinking_started = True
        self._thinking_buffer += thinking
        if not self.use_live:
            return
        if self._live is None:
            self._live = Live(
                self._thinking_renderable(),
                console=self.console,
                transient=True,
                refresh_per_second=12,
            )
            self._live.start()
        else:
            self._live.update(self._thinking_renderable())

    def emit_thinking_done(self) -> None:
        if not self._thinking_started:
            return
        if self._live is not None:
            self._live.stop()
            self._live = None
        self._thinking_started = False
        self._thinking_buffer = ""
        if not self._thinking_done_printed:
            self._thinking_done_printed = True
            self.console.print(agent_output("思考已完成。"))

    def emit_tool_result_done(self) -> None:
        if not self._tool_result_started:
            return
        if self._tool_result_live is not None:
            self._tool_result_live.stop()
            self._tool_result_live = None
        self._tool_result_started = False
        tool_name = self._tool_result_name or "tool"
        self._tool_result_name = ""
        self._tool_result_content = ""
        if not self._tool_result_done_printed:
            self._tool_result_done_printed = True
            self.console.print(agent_output(f"{tool_name} 结果已完成。"))
        self._answer_started = False

    def finish(self) -> None:
        self.emit_thinking_done()
        self.emit_tool_result_done()
        if self._answer_parts:
            answer = ensure_tool_usage_header(
                "".join(self._answer_parts),
                web_search=self._web_search_backend,
                knowledge_search=self._knowledge_search_backend(),
            )
            self.console.print(agent_output(answer), markup=False)
        self._answer_parts.clear()
        self._answer_started = False

    def _thinking_renderable(self) -> str:
        return agent_output(f"思考：{self._thinking_buffer}")

    def _tool_result_renderable(self) -> str:
        return agent_output(f"{self._tool_result_name} 结果：\n{self._tool_result_content}")

    def _discard_intermediate_answer(self) -> None:
        self._answer_parts.clear()
        self._answer_started = False

    def _record_tool_usage(self, tool_name: str, content: str) -> None:
        if tool_name == "web_search":
            backend = extract_tool_usage_backend(content, "web_search")
            if backend == "Tavily" or self._web_search_backend == "None":
                self._web_search_backend = backend or "SearXNG"
            return
        if tool_name != "search_knowledge":
            return
        backend = extract_tool_usage_backend(content, "knowledge_search")
        if backend:
            self._knowledge_search_backends.update(backend.split("&"))
        elif "milvus" in content.lower():
            self._knowledge_search_backends.update({"SQLite", "Milvus"})
        else:
            self._knowledge_search_backends.add("SQLite")

    def _knowledge_search_backend(self) -> str:
        if {"SQLite", "Milvus"} <= self._knowledge_search_backends:
            return "SQLite&Milvus"
        if "Milvus" in self._knowledge_search_backends:
            return "Milvus"
        if "SQLite" in self._knowledge_search_backends:
            return "SQLite"
        return "None"


@app.command()
def run(
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Optional YAML config path."),
    ] = None,
) -> None:
    """Run the local V1 shell."""

    settings = load_settings(config)
    input_reader = create_cli_input_reader(settings, console)
    runtime = RuntimeState(thread_id=settings.thread_id)
    policy = build_policy(settings)
    agent, registry, model_error = prepare_agent(settings, policy, runtime)

    if not settings.is_expected_python() or not running_in_expected_python():
        console.print(
            "[yellow]Warning:[/yellow] project Python should be "
            f"{settings.python_path}; current interpreter may differ."
        )

    print_agent_message("Private Agent V1 shell. Type /help for commands.")
    if model_error:
        print_agent_message(f"Model not ready: {model_error}")
    while runtime.status != RuntimeStatus.STOPPING:
        try:
            text = input_reader.read()
        except (EOFError, KeyboardInterrupt):
            runtime.stop()
            print_agent_message("Stopping private agent shell.")
            break
        if is_blank_input(text):
            continue
        if not input_reader.uses_prompt_toolkit:
            record_cli_history(text)
        command_response = handle_command(
            text,
            runtime,
            settings,
            policy,
            registry,
            checkpointer=getattr(agent, "checkpointer", None),
        )
        if command_response.action == "model_menu":
            print_command_separator()
            menu_result = run_model_menu(ModelManager(settings), prompt_menu_choice)
            print_agent_message(menu_result.message)
            if menu_result.requires_agent_rebuild:
                policy = build_policy(settings)
                agent, registry, model_error = prepare_agent(settings, policy, runtime)
                if model_error:
                    print_agent_message(f"Model not ready: {model_error}")
            continue
        if command_response.action == "thinking_menu":
            print_command_separator()
            menu_result = run_thinking_menu(ModelManager(settings), prompt_menu_choice)
            print_agent_message(menu_result.message)
            if menu_result.requires_agent_rebuild:
                policy = build_policy(settings)
                agent, registry, model_error = prepare_agent(settings, policy, runtime)
                if model_error:
                    print_agent_message(f"Model not ready: {model_error}")
            continue
        if command_response.handled:
            print_command_message(command_response.message)
            if command_response.should_exit:
                break
            continue
        exit_message = handle_natural_exit_intent(text, runtime, prompt_for_exit_confirmation)
        if exit_message is not None:
            print_agent_message(exit_message)
            if runtime.status == RuntimeStatus.STOPPING:
                break
            continue
        before_overrides = build_policy(settings).describe()["overrides"]
        stream_output = StreamingConsoleOutput(console)
        message = handle_line_streaming(
            text,
            runtime,
            settings,
            policy,
            registry,
            agent,
            approval_callback=lambda requests: prompt_for_approvals(requests, settings),
            emit_text=stream_output.emit_text,
            emit_status=stream_output.emit_status,
            emit_thinking=stream_output.emit_thinking,
            emit_thinking_done=stream_output.emit_thinking_done,
            emit_tool_result=stream_output.emit_tool_result,
        )
        stream_output.finish()
        if message:
            console.print(agent_output(message))
        after_overrides = build_policy(settings).describe()["overrides"]
        if after_overrides != before_overrides:
            policy = build_policy(settings)
            agent, registry, model_error = prepare_agent(settings, policy, runtime)
            if model_error:
                print_agent_message(f"Model not ready: {model_error}")


if __name__ == "__main__":
    app()
