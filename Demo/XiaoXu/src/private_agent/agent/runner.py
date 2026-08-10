"""Reusable execution layer shared by CLI and future service adapters."""

from __future__ import annotations

from typing import Any, Callable

from langgraph.types import Command

from private_agent.config import AppSettings
from private_agent.runtime import RuntimeState, runtime_context
from private_agent.streaming import stream_agent_response


ApprovalCallback = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
EmitText = Callable[[str], None]
EmitStatus = Callable[[str], None]
EmitThinkingDone = Callable[[], None]
EmitToolResult = Callable[[str, str], None]
ShouldStop = Callable[[], bool]


class AgentRunner:
    """Run one configured agent without coupling execution to a UI channel."""

    def __init__(
        self,
        agent: Any,
        settings: AppSettings,
        runtime: RuntimeState,
    ) -> None:
        self.agent = agent
        self.settings = settings
        self.runtime = runtime

    def invoke(
        self,
        text: str,
        *,
        thread_id: str | None = None,
        approval_callback: ApprovalCallback | None = None,
    ) -> str:
        """Run one non-streaming turn and return the final user-facing text."""

        with runtime_context(self.runtime):
            self._begin_search_turn()
            try:
                return self._invoke(
                    text,
                    thread_id=thread_id,
                    approval_callback=approval_callback,
                )
            finally:
                self.runtime.end_search_turn()

    def _invoke(
        self,
        text: str,
        *,
        thread_id: str | None,
        approval_callback: ApprovalCallback | None,
    ) -> str:
        config = self._config(thread_id)
        self.runtime.start_task()
        try:
            result = self.agent.invoke(
                {"messages": [{"role": "user", "content": text}]},
                config=config,
            )
            approval_rounds = 0
            while result.get("__interrupt__", []):
                approval_rounds += 1
                if approval_rounds > self.settings.max_tool_calls_per_run:
                    self.runtime.finish_task()
                    return "Agent run failed: too many approval rounds in one run."
                interrupts = result.get("__interrupt__", [])
                self.runtime.wait_for_approval()
                action_requests = interrupts[0].value.get("action_requests", [])
                if approval_callback is None:
                    self.runtime.finish_task()
                    return format_approval_requests(action_requests)
                decisions = {"decisions": approval_callback(action_requests)}
                self.runtime.start_task()
                result = self.agent.invoke(
                    Command(resume=decisions),
                    config=config,
                )
            self.runtime.finish_task()
            return extract_final_text(result)
        except Exception as exc:
            self.runtime.last_error = str(exc)
            self.runtime.finish_task()
            return f"Agent run failed: {exc}"

    def stream(
        self,
        text: str,
        *,
        thread_id: str | None = None,
        approval_callback: ApprovalCallback | None = None,
        emit_text: EmitText | None = None,
        emit_status: EmitStatus | None = None,
        emit_thinking: EmitText | None = None,
        show_thinking: bool = False,
        emit_thinking_done: EmitThinkingDone | None = None,
        emit_tool_result: EmitToolResult | None = None,
        should_stop: ShouldStop | None = None,
    ) -> str | None:
        """Run one streaming turn and emit channel-neutral progress callbacks."""

        with runtime_context(self.runtime):
            self._begin_search_turn()
            try:
                return self._stream(
                    text,
                    thread_id=thread_id,
                    approval_callback=approval_callback,
                    emit_text=emit_text,
                    emit_status=emit_status,
                    emit_thinking=emit_thinking,
                    show_thinking=show_thinking,
                    emit_thinking_done=emit_thinking_done,
                    emit_tool_result=emit_tool_result,
                    should_stop=should_stop,
                )
            finally:
                self.runtime.end_search_turn()

    def _stream(
        self,
        text: str,
        *,
        thread_id: str | None,
        approval_callback: ApprovalCallback | None,
        emit_text: EmitText | None,
        emit_status: EmitStatus | None,
        emit_thinking: EmitText | None,
        show_thinking: bool,
        emit_thinking_done: EmitThinkingDone | None,
        emit_tool_result: EmitToolResult | None,
        should_stop: ShouldStop | None,
    ) -> str | None:
        emit_text = emit_text or (lambda value: None)
        emit_status = emit_status or (lambda value: None)
        emit_thinking = emit_thinking or (lambda value: None)
        emit_thinking_done = emit_thinking_done or (lambda: None)
        emit_tool_result = emit_tool_result or (lambda name, content: None)
        config = self._config(thread_id)
        self.runtime.start_task()
        try:
            payload: dict[str, Any] | Command = {
                "messages": [{"role": "user", "content": text}]
            }
            approval_rounds = 0
            while True:
                result = stream_agent_response(
                    self.agent,
                    payload,
                    config=config,
                    emit_text=emit_text,
                    emit_status=emit_status,
                    emit_thinking=emit_thinking,
                    show_thinking=show_thinking,
                    emit_thinking_done=emit_thinking_done,
                    emit_tool_result=emit_tool_result,
                    should_stop=should_stop,
                )
                if result.cancelled:
                    self.runtime.finish_task()
                    return "Agent run cancelled."
                if not result.interrupts:
                    break
                approval_rounds += 1
                if approval_rounds > self.settings.max_tool_calls_per_run:
                    self.runtime.finish_task()
                    return "Agent run failed: too many approval rounds in one run."
                self.runtime.wait_for_approval()
                action_requests = result.interrupts[0].value.get("action_requests", [])
                if approval_callback is None:
                    self.runtime.finish_task()
                    return format_approval_requests(action_requests)
                decisions = {"decisions": approval_callback(action_requests)}
                self.runtime.start_task()
                payload = Command(resume=decisions)
            self.runtime.finish_task()
            return None
        except Exception as exc:
            self.runtime.last_error = str(exc)
            self.runtime.finish_task()
            return f"Agent run failed: {exc}"

    def _config(self, thread_id: str | None) -> dict[str, dict[str, str]]:
        selected_thread_id = thread_id or self.settings.thread_id
        return {"configurable": {"thread_id": selected_thread_id}}

    def _begin_search_turn(self) -> None:
        self.runtime.begin_search_turn()


def extract_final_text(result: dict[str, Any]) -> str:
    """Return the last non-empty message content from a LangChain result."""

    for message in reversed(result.get("messages", [])):
        content = getattr(message, "content", None)
        if content:
            return str(content)
    return "Agent returned no text response."


def format_approval_requests(action_requests: list[dict[str, Any]]) -> str:
    """Format approval requests for interactive channels such as the CLI."""

    if not action_requests:
        return "Tool execution requires approval, but no action details were provided."
    rows = ["Tool execution requires approval:"]
    for index, request in enumerate(action_requests, start=1):
        rows.append(
            f"{index}. {request.get('name', 'unknown')} args={request.get('args', {})}"
        )
    return "\n".join(rows)
