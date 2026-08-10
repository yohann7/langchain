"""Usage accounting callbacks for model calls outside Agent middleware."""

from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from typing import Any
from uuid import UUID

from langchain_core.callbacks import (
    BaseCallbackHandler,
    BaseCallbackManager,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.outputs import LLMResult


RecordUsage = Callable[..., None]


class SummaryUsageCallback(BaseCallbackHandler):
    """Record only chat-model runs created by summarization middleware."""

    def __init__(self, record_usage: RecordUsage) -> None:
        self._record_usage = record_usage
        self._pending_inputs: dict[UUID, int] = {}
        self._lock = Lock()

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        del serialized, parent_run_id, tags, kwargs
        if (metadata or {}).get("lc_source") != "summarization":
            return
        estimated_input = sum(
            int(count_tokens_approximately(message_batch))
            for message_batch in messages
        )
        with self._lock:
            self._pending_inputs[run_id] = estimated_input

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        del parent_run_id, tags, kwargs
        estimated_input = self._pop_input(run_id)
        if estimated_input is None:
            return
        input_tokens, output_tokens = _result_usage(response, estimated_input)
        self._record_usage(
            input_tokens,
            output_tokens,
            purpose="summarization",
        )

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        del parent_run_id, tags, kwargs
        estimated_input = self._pop_input(run_id)
        if estimated_input is None:
            return
        self._record_usage(
            estimated_input,
            0,
            purpose="summarization",
            status="error",
            error_type=type(error).__name__,
        )

    def _pop_input(self, run_id: UUID) -> int | None:
        with self._lock:
            return self._pending_inputs.pop(run_id, None)


def attach_summary_usage_callback(
    model: BaseChatModel,
    record_usage: RecordUsage,
) -> SummaryUsageCallback:
    """Attach summary-only accounting without replacing existing callbacks."""

    handler = SummaryUsageCallback(record_usage)
    callbacks = model.callbacks
    if isinstance(callbacks, BaseCallbackManager):
        callbacks.add_handler(handler)
    else:
        model.callbacks = [*(callbacks or []), handler]
    return handler


def _result_usage(response: LLMResult, estimated_input: int) -> tuple[int, int]:
    messages = [
        generation.message
        for generation_batch in response.generations
        for generation in generation_batch
        if hasattr(generation, "message")
        and isinstance(generation.message, AIMessage)
    ]
    usage_rows = [
        message.usage_metadata
        for message in messages
        if message.usage_metadata
    ]
    if usage_rows:
        return (
            sum(int(row.get("input_tokens", 0)) for row in usage_rows),
            sum(int(row.get("output_tokens", 0)) for row in usage_rows),
        )
    return (
        estimated_input,
        int(count_tokens_approximately(messages)),
    )
