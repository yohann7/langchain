"""Stable identity translation at the XiaoXu API boundary."""

from __future__ import annotations

import hashlib
import hmac
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator


_CURRENT_USER_ID: ContextVar[str | None] = ContextVar(
    "xiaoxu_current_user_id",
    default=None,
)
_CURRENT_CONVERSATION_TYPE: ContextVar[str | None] = ContextVar(
    "xiaoxu_current_conversation_type",
    default=None,
)


def actor_to_user_id(actor_id: str, *, secret: str) -> str:
    """Map an opaque channel actor id to a XiaoXu-local user id."""

    actor = actor_id.strip()
    if not actor:
        raise ValueError("actor_id must not be blank")
    if not secret:
        raise ValueError("identity secret must not be blank")
    digest = hmac.new(
        secret.encode("utf-8"),
        f"xiaoxu:user:v1\0{actor}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"usr_{digest[:32]}"


def conversation_thread_id(
    external_thread_id: str,
    *,
    actor_id: str,
    channel: str,
    conversation_type: str,
    secret: str,
) -> str:
    """Derive a non-reversible, identity-isolated checkpoint key."""

    thread = external_thread_id.strip()
    actor = actor_id.strip()
    selected_channel = channel.strip().lower()
    if not thread or not actor or not selected_channel:
        raise ValueError("thread, actor, and channel must not be blank")
    if conversation_type not in {"single", "group"}:
        raise ValueError("conversation_type must be single or group")
    if not secret:
        raise ValueError("identity secret must not be blank")

    if conversation_type == "single":
        scope = f"actor={actor}\0conversation={thread}"
    else:
        scope = f"conversation={thread}"
    digest = hmac.new(
        secret.encode("utf-8"),
        (
            "xiaoxu:thread:v1\0"
            f"channel={selected_channel}\0type={conversation_type}\0{scope}"
        ).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"thr_{digest[:40]}"


def current_user_id(default: str) -> str:
    return _CURRENT_USER_ID.get() or default


def current_conversation_type(default: str = "single") -> str:
    return _CURRENT_CONVERSATION_TYPE.get() or default


@contextmanager
def user_context(
    user_id: str,
    *,
    conversation_type: str | None = None,
) -> Iterator[None]:
    user_token = _CURRENT_USER_ID.set(user_id)
    conversation_token = (
        _CURRENT_CONVERSATION_TYPE.set(conversation_type)
        if conversation_type is not None
        else None
    )
    try:
        yield
    finally:
        if conversation_token is not None:
            _CURRENT_CONVERSATION_TYPE.reset(conversation_token)
        _CURRENT_USER_ID.reset(user_token)
