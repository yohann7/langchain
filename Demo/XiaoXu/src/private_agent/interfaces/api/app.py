"""Internal FastAPI/SSE adapter for channel integrations such as wxbot."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
import json
import os
import re
import secrets
from threading import Event
from time import monotonic
from typing import Annotated, Any, AsyncIterator, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Security, status
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator

from private_agent.agent_factory import create_private_agent
from private_agent.agent_runner import AgentRunner
from private_agent.config import AppSettings, load_settings
from private_agent.core.identity import (
    actor_to_user_id,
    conversation_thread_id,
    user_context,
)
from private_agent.runtime import RuntimeState
from private_agent.security import PermissionPolicy
from private_agent.tool_usage import (
    ensure_tool_usage_header,
    extract_tool_usage_backend,
)
from private_agent.knowledge.formatter import extract_knowledge_source_lines


@dataclass
class _ThreadLockEntry:
    lock: asyncio.Lock
    references: int = 0


class _ThreadLockRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, _ThreadLockEntry] = {}

    def reserve(self, thread_id: str) -> _ThreadLockEntry:
        entry = self._entries.setdefault(
            thread_id,
            _ThreadLockEntry(asyncio.Lock()),
        )
        entry.references += 1
        return entry

    def release(self, thread_id: str, entry: _ThreadLockEntry) -> None:
        entry.references -= 1
        if entry.references == 0 and not entry.lock.locked():
            self._entries.pop(thread_id, None)

    def __len__(self) -> int:
        return len(self._entries)


class RunMessage(BaseModel):
    """Normalized user message accepted from a trusted channel adapter."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["text", "voice"]
    text: str

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message text must not be blank")
        return value


class RunRequest(BaseModel):
    """Stable v1 request contract used by wxbot."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    thread_id: str = Field(min_length=1, max_length=256)
    actor_id: str = Field(min_length=1, max_length=128)
    channel: Literal["wecom"]
    conversation_type: Literal["single", "group"]
    message: RunMessage


class HealthResponse(BaseModel):
    status: str
    code: str | None = None


bearer = HTTPBearer(auto_error=False)


def create_app(
    settings: AppSettings | None = None,
    *,
    agent: Any | None = None,
    model: Any | None = None,
    checkpointer: Any | None = None,
) -> FastAPI:
    """Create the internal API application with injectable test dependencies."""

    base_settings = settings or load_settings()
    api_settings = base_settings.model_copy(
        update={
            "max_model_calls_per_run": min(base_settings.max_model_calls_per_run, 4),
            "max_tool_calls_per_run": min(base_settings.max_tool_calls_per_run, 4),
        }
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = api_settings
        app.state.api_token = _resolve_api_token(api_settings)
        app.state.agent = None
        app.state.ready_error = None
        app.state.thread_locks = _ThreadLockRegistry()

        if not app.state.api_token:
            app.state.ready_error = "AUTH_NOT_CONFIGURED"
        elif agent is not None:
            app.state.agent = agent
        else:
            try:
                service_runtime = RuntimeState(thread_id="xiaoxu-api")
                service_policy = PermissionPolicy(
                    overrides=api_settings.permission_overrides
                )
                service_agent, _resources = create_private_agent(
                    api_settings,
                    service_policy,
                    service_runtime,
                    model=model,
                    checkpointer=checkpointer,
                    tool_profile="wecom_chat",
                )
                app.state.agent = service_agent
            except Exception:
                app.state.ready_error = "AGENT_NOT_READY"

        yield

    app = FastAPI(
        title="xiaoxu internal API",
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.get(
        "/health/live",
        response_model=HealthResponse,
        response_model_exclude_none=True,
    )
    async def health_live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/health/ready",
        response_model=HealthResponse,
        response_model_exclude_none=True,
    )
    async def health_ready(request: Request) -> JSONResponse:
        if request.app.state.agent is None or request.app.state.ready_error is not None:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "status": "not_ready",
                    "code": request.app.state.ready_error or "AGENT_NOT_READY",
                },
            )
        return JSONResponse(content={"status": "ready"})

    @app.post(
        "/v1/runs",
        response_class=StreamingResponse,
        responses={
            200: {
                "description": (
                    "SSE stream; event payloads follow "
                    "contracts/sse-events-v1.schema.json."
                ),
                "content": {
                    "text/event-stream": {
                        "schema": {
                            "type": "string",
                            "description": "Server-sent event stream.",
                        }
                    }
                },
            }
        },
    )
    async def create_run(
        payload: RunRequest,
        request: Request,
        credentials: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(bearer),
        ] = None,
    ) -> StreamingResponse:
        _authorize(request, credentials)
        if request.app.state.agent is None or request.app.state.ready_error is not None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "AGENT_NOT_READY"},
            )
        input_size = len(payload.message.text.encode("utf-8"))
        if input_size > request.app.state.settings.api_max_input_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail={"code": "INPUT_TOO_LARGE"},
            )

        internal_thread_id = conversation_thread_id(
            payload.thread_id,
            actor_id=payload.actor_id,
            channel=payload.channel,
            conversation_type=payload.conversation_type,
            secret=request.app.state.settings.identity_secret,
        )
        thread_locks: _ThreadLockRegistry = request.app.state.thread_locks
        lock_entry = thread_locks.reserve(internal_thread_id)
        return StreamingResponse(
            _stream_run_with_lock(
                request,
                payload,
                internal_thread_id,
                thread_locks,
                lock_entry,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return app


async def _stream_run_with_lock(
    request: Request,
    payload: RunRequest,
    internal_thread_id: str,
    registry: _ThreadLockRegistry,
    entry: _ThreadLockEntry,
) -> AsyncIterator[str]:
    try:
        async for chunk in _stream_run(
            request,
            payload,
            internal_thread_id,
            entry.lock,
        ):
            yield chunk
    finally:
        registry.release(internal_thread_id, entry)


async def _stream_run(
    request: Request,
    payload: RunRequest,
    internal_thread_id: str,
    thread_lock: asyncio.Lock,
) -> AsyncIterator[str]:
    run_id = uuid4().hex
    yield _encode_sse(
        "run.started",
        {
            "run_id": run_id,
            "request_id": payload.request_id,
        },
    )

    async with thread_lock:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()
        cancel_event = Event()
        final_text_parts: list[str] = []
        rag_tool_results: list[str] = []
        web_search_backend = "None"
        knowledge_search_backends: set[str] = set()
        output_bytes = 0
        output_truncated = False
        max_output_bytes = request.app.state.settings.api_max_output_bytes

        def publish(event: str, data: dict[str, Any]) -> None:
            if not cancel_event.is_set():
                loop.call_soon_threadsafe(queue.put_nowait, (event, data))

        def publish_done() -> None:
            if not cancel_event.is_set():
                loop.call_soon_threadsafe(queue.put_nowait, None)

        def emit_text(text: str) -> None:
            nonlocal output_bytes, output_truncated
            remaining = max_output_bytes - output_bytes
            if remaining <= 0:
                if text:
                    output_truncated = True
                return
            safe_text = _truncate_utf8(text, remaining)
            if not safe_text:
                output_truncated = bool(text)
                return
            if len(safe_text.encode("utf-8")) < len(text.encode("utf-8")):
                output_truncated = True
            final_text_parts.append(safe_text)
            output_bytes += len(safe_text.encode("utf-8"))
            publish("response.delta", {"text": safe_text})

        def emit_status(_status_text: str) -> None:
            return None

        def emit_tool_result(tool_name: str, content: str) -> None:
            nonlocal web_search_backend
            if tool_name == "search_knowledge" and _is_rag_search_result(content):
                rag_tool_results.append(content)
            if tool_name == "search_knowledge":
                backend = extract_tool_usage_backend(content, "knowledge_search")
                if backend:
                    knowledge_search_backends.update(backend.split("&"))
                elif "milvus" in content.lower():
                    knowledge_search_backends.update({"SQLite", "Milvus"})
                else:
                    knowledge_search_backends.add("SQLite")
            elif tool_name == "web_search":
                backend = extract_tool_usage_backend(content, "web_search")
                if backend == "Tavily" or web_search_backend == "None":
                    web_search_backend = backend or "SearXNG"

        def run_agent() -> None:
            runtime = RuntimeState(thread_id=internal_thread_id)
            runner = AgentRunner(
                request.app.state.agent,
                request.app.state.settings,
                runtime,
            )
            try:
                user_id = actor_to_user_id(
                    payload.actor_id,
                    secret=request.app.state.settings.identity_secret,
                )
                with user_context(
                    user_id,
                    conversation_type=payload.conversation_type,
                ):
                    result = runner.stream(
                        payload.message.text,
                        thread_id=internal_thread_id,
                        emit_text=emit_text,
                        emit_status=emit_status,
                        emit_tool_result=emit_tool_result,
                        show_thinking=False,
                        should_stop=cancel_event.is_set,
                    )
                if cancel_event.is_set():
                    return
                if result is None:
                    final_text = "".join(final_text_parts)
                    if rag_tool_results:
                        supplement = _rag_supplement_text(
                            final_text,
                            rag_tool_results[-1],
                        )
                        if supplement:
                            emit_text(supplement)
                    final_text = "".join(final_text_parts)
                    if {"SQLite", "Milvus"} <= knowledge_search_backends:
                        knowledge_search_backend = "SQLite&Milvus"
                    elif "Milvus" in knowledge_search_backends:
                        knowledge_search_backend = "Milvus"
                    elif "SQLite" in knowledge_search_backends:
                        knowledge_search_backend = "SQLite"
                    else:
                        knowledge_search_backend = "None"
                    final_text = ensure_tool_usage_header(
                        final_text,
                        web_search=web_search_backend,
                        knowledge_search=knowledge_search_backend,
                    )
                    publish(
                        "run.completed",
                        {
                            "run_id": run_id,
                            "final_text": final_text,
                            "truncated": output_truncated,
                        },
                    )
                else:
                    publish(
                        "run.failed",
                        {
                            "run_id": run_id,
                            "code": "AGENT_RUN_FAILED",
                            "user_message": "暂时无法处理，请稍后重试。",
                            "retryable": True,
                        },
                    )
            except Exception:
                publish(
                    "run.failed",
                    {
                        "run_id": run_id,
                        "code": "AGENT_RUN_FAILED",
                        "user_message": "暂时无法处理，请稍后重试。",
                        "retryable": True,
                    },
                )
            finally:
                publish_done()

        worker = asyncio.create_task(asyncio.to_thread(run_agent))
        deadline = monotonic() + request.app.state.settings.api_run_timeout_seconds
        try:
            while True:
                if await request.is_disconnected():
                    cancel_event.set()
                    break
                remaining = deadline - monotonic()
                if remaining <= 0:
                    cancel_event.set()
                    yield _encode_sse(
                        "run.failed",
                        {
                            "run_id": run_id,
                            "code": "RUN_TIMEOUT",
                            "user_message": "处理超时，请稍后重试。",
                            "retryable": True,
                        },
                    )
                    break
                try:
                    queued_event = await asyncio.wait_for(
                        queue.get(),
                        timeout=min(0.25, remaining),
                    )
                except TimeoutError:
                    continue
                if queued_event is None:
                    break
                event_name, event_data = queued_event
                yield _encode_sse(event_name, event_data)
        finally:
            cancel_event.set()
            if not worker.done():
                worker.cancel()
            with suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(worker, timeout=1.0)


def _authorize(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
) -> None:
    expected_token = request.app.state.api_token
    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "AUTH_NOT_CONFIGURED"},
        )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not secrets.compare_digest(credentials.credentials, expected_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED"},
            headers={"WWW-Authenticate": "Bearer"},
        )


def _resolve_api_token(settings: AppSettings) -> str | None:
    token = settings.api_token or os.getenv("WXBOT_XIAOXU_TOKEN")
    return token.strip() if token and token.strip() else None


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _is_rag_search_result(content: str) -> bool:
    return (
        "[来源 " in content
        or content.strip() == "知识库中没有找到可用证据。不要根据知识库编造答案。"
    )


def _rag_supplement_text(final_text: str, tool_result: str) -> str:
    separator = "\n\n" if final_text.strip() else ""
    source_lines = extract_knowledge_source_lines(tool_result)
    if not source_lines:
        if tool_result.strip() in final_text:
            return ""
        if tool_result.strip() == "知识库中没有找到可用证据。不要根据知识库编造答案。":
            return f"{separator}{tool_result.strip()}"
        return (
            f"{separator}知识库检索已完成，但本轮未生成可核验的回答。"
            "请缩小问题范围后重试。"
        )

    missing_source_lines = [
        line for line in source_lines if line not in final_text
    ]
    if not missing_source_lines:
        return ""
    if not re.search(r"\[\s*来源\s*\d+\s*\]", final_text):
        return (
            f"{separator}知识库已检索到相关资料，但本轮未生成完整回答。"
            "请重试或缩小问题范围。\n\n来源明细：\n"
            + "\n".join(missing_source_lines)
        )
    return f"{separator}来源明细：\n" + "\n".join(missing_source_lines)


def _encode_sse(event: str, data: dict[str, Any]) -> str:
    serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {serialized}\n\n"
