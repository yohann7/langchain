"""Runtime state for the private agent shell."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from contextlib import contextmanager
from contextvars import ContextVar
from time import time
from typing import Any, Iterator

from private_agent.search import SearchTurnState


_CURRENT_RUNTIME: ContextVar["RuntimeState | None"] = ContextVar(
    "xiaoxu_current_runtime",
    default=None,
)


class RuntimeStatus(StrEnum):
    """Top-level runtime states."""

    IDLE = "idle"
    BUSY = "busy"
    AWAITING_APPROVAL = "awaiting_approval"
    PAUSED = "paused"
    STOPPING = "stopping"


@dataclass
class UsageStats:
    """Local usage counters tracked before model integration exists."""

    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class RuntimeState:
    """Mutable runtime state used by CLI commands and future agent execution."""

    def __init__(self, thread_id: str = "default") -> None:
        self.thread_id = thread_id
        self.status = RuntimeStatus.IDLE
        self.usage = UsageStats()
        self.started_at = time()
        self.last_error: str | None = None
        self.compaction_requests = 0
        self.clear_requests = 0
        self.search_turn_state: SearchTurnState | None = None

    @property
    def is_busy(self) -> bool:
        return self.status == RuntimeStatus.BUSY

    @property
    def is_waiting_for_approval(self) -> bool:
        return self.status == RuntimeStatus.AWAITING_APPROVAL

    def start_task(self) -> None:
        self.status = RuntimeStatus.BUSY

    def finish_task(self) -> None:
        self.status = RuntimeStatus.IDLE

    def wait_for_approval(self) -> None:
        self.status = RuntimeStatus.AWAITING_APPROVAL

    def pause(self) -> None:
        self.status = RuntimeStatus.PAUSED

    def resume(self) -> None:
        self.status = RuntimeStatus.IDLE

    def stop(self) -> None:
        self.status = RuntimeStatus.STOPPING

    def clear_conversation(self) -> None:
        self.clear_requests += 1

    def begin_search_turn(self) -> SearchTurnState:
        """Create request-local search policy state for one user message."""

        state = SearchTurnState()
        self.search_turn_state = state
        return state

    def end_search_turn(self) -> None:
        """Discard non-persistent search state after the user turn completes."""

        self.search_turn_state = None

    def request_compaction(self) -> None:
        self.compaction_requests += 1

    def record_model_call(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        self.usage.model_calls += 1
        self.usage.input_tokens += input_tokens
        self.usage.output_tokens += output_tokens

    def record_tool_call(self) -> None:
        self.usage.tool_calls += 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "status": self.status.value,
            "is_busy": self.is_busy,
            "awaiting_approval": self.is_waiting_for_approval,
            "usage": asdict(self.usage) | {"total_tokens": self.usage.total_tokens},
            "uptime_seconds": max(0, int(time() - self.started_at)),
            "last_error": self.last_error,
            "clear_requests": self.clear_requests,
            "compaction_requests": self.compaction_requests,
        }


def current_runtime_state(default: RuntimeState) -> RuntimeState:
    return _CURRENT_RUNTIME.get() or default


@contextmanager
def runtime_context(runtime: RuntimeState) -> Iterator[None]:
    token = _CURRENT_RUNTIME.set(runtime)
    try:
        yield
    finally:
        _CURRENT_RUNTIME.reset(token)
