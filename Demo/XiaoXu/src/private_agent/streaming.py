"""Streaming helpers for CLI agent runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from langchain_core.messages import BaseMessage, ToolMessage


EmitFunc = Callable[[str], None]
DoneFunc = Callable[[], None]
ToolResultFunc = Callable[[str, str], None]


@dataclass
class StreamRunResult:
    """Result collected while streaming one agent run."""

    final_text: str = ""
    interrupts: list[Any] = field(default_factory=list)
    cancelled: bool = False


def stream_agent_response(
    agent: Any,
    payload: dict[str, Any],
    *,
    config: dict[str, Any],
    emit_text: EmitFunc,
    emit_status: EmitFunc,
    emit_thinking: EmitFunc,
    show_thinking: bool,
    emit_thinking_done: DoneFunc | None = None,
    emit_tool_result: ToolResultFunc | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> StreamRunResult:
    """Stream an agent response and emit text, tool status, and thinking text."""

    result = StreamRunResult()
    started_tools: set[str] = set()
    finished_tools: set[str] = set()
    thinking_started = False
    thinking_seen_text = ""
    emit_thinking_done = emit_thinking_done or (lambda: None)
    emit_tool_result = emit_tool_result or (lambda name, content: None)
    should_stop = should_stop or (lambda: False)

    def finish_thinking() -> None:
        nonlocal thinking_started
        if thinking_started:
            emit_thinking_done()
            thinking_started = False

    stream = agent.stream(payload, config=config, stream_mode=["updates", "messages"])
    for raw_chunk in stream:
        if should_stop():
            result.cancelled = True
            break
        mode, chunk = _normalize_stream_chunk(raw_chunk)
        if mode == "updates":
            interrupts = _extract_interrupts(chunk)
            if interrupts:
                finish_thinking()
            result.interrupts.extend(interrupts)
            continue
        if mode != "messages":
            continue
        message, metadata = _message_stream_parts(chunk)
        if message is None:
            continue
        node = metadata.get("langgraph_node") if isinstance(metadata, dict) else None
        if node == "model":
            tool_calls = list(_tool_call_names(message))
            if tool_calls:
                finish_thinking()
            for tool_call_id, tool_name in tool_calls:
                if tool_call_id not in started_tools:
                    started_tools.add(tool_call_id)
                    emit_status(f"正在调用工具：{tool_name}")
            if show_thinking:
                for thinking in _thinking_fragments(message):
                    new_thinking, thinking_seen_text = _new_thinking_fragment(
                        thinking_seen_text,
                        thinking,
                    )
                    if new_thinking:
                        thinking_started = True
                        emit_thinking(new_thinking)
            text_fragments = _text_fragments(message)
            if text_fragments:
                finish_thinking()
            for text in text_fragments:
                result.final_text += text
                emit_text(text)
        elif node == "tools" or isinstance(message, ToolMessage):
            finish_thinking()
            tool_call_id, tool_name = _tool_message_name(message)
            if tool_call_id and tool_call_id not in finished_tools:
                finished_tools.add(tool_call_id)
                emit_status(f"工具调用完成：{tool_name}")
            if tool_name in {"web_search", "search_knowledge"}:
                tool_result = _tool_message_content(message)
                if tool_result:
                    emit_tool_result(tool_name, tool_result)
    if should_stop():
        result.cancelled = True
    finish_thinking()
    return result


def _normalize_stream_chunk(raw_chunk: Any) -> tuple[str, Any]:
    if (
        isinstance(raw_chunk, tuple)
        and len(raw_chunk) == 2
        and raw_chunk[0] in {"updates", "messages"}
    ):
        return raw_chunk
    if isinstance(raw_chunk, tuple) and len(raw_chunk) == 2:
        return "messages", raw_chunk
    return "updates", raw_chunk


def _message_stream_parts(chunk: Any) -> tuple[BaseMessage | None, dict[str, Any]]:
    if isinstance(chunk, tuple) and len(chunk) == 2 and isinstance(chunk[0], BaseMessage):
        metadata = chunk[1] if isinstance(chunk[1], dict) else {}
        return chunk[0], metadata
    if isinstance(chunk, BaseMessage):
        return chunk, {}
    return None, {}


def _extract_interrupts(chunk: Any) -> list[Any]:
    if not isinstance(chunk, dict):
        return []
    interrupts = chunk.get("__interrupt__", [])
    if isinstance(interrupts, list | tuple):
        return list(interrupts)
    return [interrupts]


def _tool_call_names(message: BaseMessage) -> Iterable[tuple[str, str]]:
    tool_calls = getattr(message, "tool_calls", []) or []
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        name = tool_call.get("name")
        if not name:
            continue
        tool_call_id = str(tool_call.get("id") or name)
        yield tool_call_id, str(name)

    tool_call_chunks = getattr(message, "tool_call_chunks", []) or []
    for chunk in tool_call_chunks:
        if not isinstance(chunk, dict):
            continue
        name = chunk.get("name")
        if not name:
            continue
        tool_call_id = str(chunk.get("id") or name)
        yield tool_call_id, str(name)


def _tool_message_name(message: BaseMessage) -> tuple[str | None, str]:
    tool_call_id = getattr(message, "tool_call_id", None)
    name = getattr(message, "name", None) or "unknown"
    if tool_call_id is None:
        return None, str(name)
    return str(tool_call_id), str(name)


def _tool_message_content(message: BaseMessage) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_coerce_text_fragments(content))
    return str(content) if content is not None else ""


def _text_fragments(message: BaseMessage) -> list[str]:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return [content] if content else []
    fragments: list[str] = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, str) and item:
                fragments.append(item)
            elif isinstance(item, dict) and _content_block_type(item) not in {
                "reasoning",
                "thinking",
                "reasoning_delta",
            }:
                text = _first_text_value(item, ("text", "content"))
                if text:
                    fragments.append(text)
    return fragments


def _thinking_fragments(message: BaseMessage) -> list[str]:
    fragments: list[str] = []
    additional_kwargs = getattr(message, "additional_kwargs", {}) or {}
    response_metadata = getattr(message, "response_metadata", {}) or {}
    for source in (additional_kwargs, response_metadata):
        for key in (
            "reasoning_content",
            "reasoning",
            "thinking",
            "reasoning_details",
            "reasoning_delta",
        ):
            fragments.extend(_coerce_text_fragments(source.get(key)))

    content = getattr(message, "content", None)
    if isinstance(content, list):
        fragments.extend(_thinking_fragments_from_blocks(content))
    content_blocks = getattr(message, "content_blocks", None)
    if isinstance(content_blocks, list):
        fragments.extend(_thinking_fragments_from_blocks(content_blocks))
    return [fragment for fragment in fragments if fragment]


def _thinking_fragments_from_blocks(blocks: list[Any]) -> list[str]:
    fragments: list[str] = []
    for item in blocks:
        if not isinstance(item, dict):
            continue
        if _content_block_type(item) in {"reasoning", "thinking", "reasoning_delta"}:
            fragments.extend(
                _coerce_text_fragments(
                    _first_text_value(
                        item,
                        (
                            "reasoning",
                            "thinking",
                            "reasoning_content",
                            "delta",
                            "text",
                            "content",
                            "summary",
                        ),
                    )
                )
            )
            fragments.extend(_coerce_text_fragments(item.get("extras")))
    return fragments


def _new_thinking_fragment(previous: str, current: str) -> tuple[str, str]:
    if not previous:
        return current, current
    if current.startswith(previous):
        return current[len(previous) :], current
    if previous.endswith(current):
        return "", previous
    return current, previous + current


def _coerce_text_fragments(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, dict):
        fragments: list[str] = []
        text = _first_text_value(
            value,
            (
                "text",
                "content",
                "reasoning",
                "summary",
                "reasoning_content",
                "thinking",
                "delta",
            ),
        )
        if text:
            fragments.append(text)
        for nested_key in ("extras", "reasoning_details", "details", "data"):
            fragments.extend(_coerce_text_fragments(value.get(nested_key)))
        return fragments
    if isinstance(value, list):
        fragments: list[str] = []
        for item in value:
            fragments.extend(_coerce_text_fragments(item))
        return fragments
    return []


def _first_text_value(mapping: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _content_block_type(block: dict[str, Any]) -> str:
    block_type = block.get("type") or block.get("kind")
    return str(block_type).lower() if block_type else ""
