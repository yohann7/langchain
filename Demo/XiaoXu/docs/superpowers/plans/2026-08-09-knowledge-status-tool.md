# XiaoXu Knowledge Status Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `get_knowledge_status` XiaoXu tool that returns the current user's Knowledge Service readiness and document statistics through the existing HTTP API boundary.

**Architecture:** Extend `KnowledgeClient` with a typed status response, add a focused tool-service function for capability checks and safe error formatting, then register a zero-argument LangChain tool that injects XiaoXu's current user identity. The tool does not participate in search coordination, does not emit a Knowledge search usage marker, and never accesses SQLite or Milvus directly.

**Tech Stack:** Python 3.11+, dataclasses, HTTPX, LangChain 1.2 tools, Pydantic-backed Knowledge API contract, pytest.

## Global Constraints

- Tool name is exactly `get_knowledge_status` and it has no model-visible arguments.
- The current `user_id` comes only from XiaoXu identity context.
- Use the existing Knowledge read token and `GET /v1/knowledge/status`; do not use the admin token or CLI.
- Permission is `READ_SAFE`, `requires_approval=False`, and `uses_network=True`.
- Reuse `CapabilityPolicy.can_search_knowledge` before any HTTP request.
- Do not consume `search_knowledge` query budget or emit `tool_usage:knowledge_search`.
- Preserve Milvus extension fields, but replace any non-empty `milvus.error` with `Milvus 状态异常` before returning it to the model.
- Do not modify Knowledge Service, its API contract, HTTP/SSE interfaces, databases, or WxBot.
- Do not write status results to memory, checkpoints, audit payloads, or XiaoXu persistence.
- Use `D:\Anaconda3\envs\langchain1.2\python.exe` for all tests.
- The current directory is not a Git repository. Execute all implementation and verification steps, but skip commit commands unless Git metadata is supplied later.

## File Structure

- Modify `src/private_agent/knowledge/schemas.py`: own typed status DTO parsing.
- Modify `src/private_agent/knowledge/client.py`: own the authenticated status HTTP request and transport error mapping.
- Create `src/private_agent/tools/knowledge/get_knowledge_status.py`: own capability enforcement, DTO-to-dict conversion, Milvus error sanitization, and stable user-facing errors.
- Modify `src/private_agent/agent/factory.py`: register and invoke the zero-argument LangChain tool with current identity.
- Modify `src/private_agent/agent/profiles.py`: expose the XiaoXu-owned read tool in the low-risk profile.
- Modify `src/private_agent/agent/prompts.py`: tell the model when to use the status tool.
- Modify `docs/tools.md`: document boundary, fields, and search-budget semantics.
- Create `tests/unit/test_knowledge_status_tool.py`: cover DTO, client, tool service, safety, and no-marker behavior.
- Modify `tests/private_agent/test_agent_factory.py`: cover registration, channel allowlist, tool schema, identity injection, and prompt routing.

---

### Task 1: Typed status DTO and Knowledge HTTP client

**Files:**
- Modify: `src/private_agent/knowledge/schemas.py`
- Modify: `src/private_agent/knowledge/client.py`
- Create: `tests/unit/test_knowledge_status_tool.py`

**Interfaces:**
- Consumes: existing `KnowledgeAuthenticationError`, `KnowledgeTimeoutError`, `KnowledgeUnavailableError`, and `KnowledgeProtocolError`.
- Produces: `KnowledgeEmbeddingStatus.from_dict`, `KnowledgeSqliteStatus.from_dict`, `KnowledgeStatusResponse.from_dict`, and `KnowledgeClient.status(*, user_id: str) -> KnowledgeStatusResponse`.

- [ ] **Step 1: Write failing DTO and client tests**

Create `tests/unit/test_knowledge_status_tool.py` with these initial tests:

```python
from __future__ import annotations

import httpx
import pytest

from private_agent.knowledge.client import KnowledgeClient
from private_agent.knowledge.errors import (
    KnowledgeAuthenticationError,
    KnowledgeProtocolError,
    KnowledgeTimeoutError,
    KnowledgeUnavailableError,
)
from private_agent.knowledge.schemas import KnowledgeStatusResponse


def _status_payload() -> dict[str, object]:
    return {
        "enabled": True,
        "embedding": {
            "model": "BAAI/bge-m3",
            "revision": "fixed",
            "dimension": 1024,
            "ready": True,
        },
        "sqlite": {
            "ready": True,
            "knowledge_bases": 2,
            "total_documents": 10,
            "active_chunks": 120,
        },
        "milvus": {
            "ready": True,
            "database": "knowledge",
            "collection": "knowledge_chunks_v1",
            "dimension": 1024,
        },
    }


def test_status_response_parses_core_fields_and_preserves_milvus_extensions():
    status = KnowledgeStatusResponse.from_dict(_status_payload())

    assert status.enabled is True
    assert status.embedding.model == "BAAI/bge-m3"
    assert status.embedding.dimension == 1024
    assert status.sqlite.total_documents == 10
    assert status.sqlite.active_chunks == 120
    assert status.milvus["collection"] == "knowledge_chunks_v1"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.pop("enabled"),
        lambda value: value["embedding"].update({"ready": "yes"}),
        lambda value: value["sqlite"].update({"total_documents": -1}),
        lambda value: value.update({"milvus": {"ready": "yes"}}),
    ],
)
def test_status_response_rejects_invalid_core_fields(mutate):
    payload = _status_payload()
    mutate(payload)

    with pytest.raises(ValueError, match="invalid knowledge status response"):
        KnowledgeStatusResponse.from_dict(payload)


def test_knowledge_client_status_uses_get_path_and_current_user():
    client = KnowledgeClient(base_url="http://knowledge", token="token")
    client._client.close()
    captured = {}

    class FakeHttpClient:
        def get(self, path, **kwargs):
            captured.update({"path": path, **kwargs})
            return httpx.Response(200, json=_status_payload())

        def close(self):
            return None

    client._client = FakeHttpClient()

    status = client.status(user_id="user-1")

    assert status.sqlite.knowledge_bases == 2
    assert captured == {
        "path": "/v1/knowledge/status",
        "params": {"user_id": "user-1"},
    }


def test_knowledge_client_status_rejects_invalid_payload():
    client = KnowledgeClient(base_url="http://knowledge", token="token")
    client._client.close()

    class FakeHttpClient:
        def get(self, path, **kwargs):
            del path, kwargs
            return httpx.Response(200, json={"enabled": True})

        def close(self):
            return None

    client._client = FakeHttpClient()

    with pytest.raises(KnowledgeProtocolError):
        client.status(user_id="user-1")


def test_knowledge_client_status_rejects_non_json_payload():
    client = KnowledgeClient(base_url="http://knowledge", token="token")
    client._client.close()

    class FakeResponse:
        status_code = 200
        is_success = True

        def json(self):
            raise ValueError("not json")

    class FakeHttpClient:
        def get(self, path, **kwargs):
            del path, kwargs
            return FakeResponse()

        def close(self):
            return None

    client._client = FakeHttpClient()

    with pytest.raises(KnowledgeProtocolError):
        client.status(user_id="user-1")


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (401, KnowledgeAuthenticationError),
        (403, KnowledgeAuthenticationError),
        (503, KnowledgeUnavailableError),
        (400, KnowledgeProtocolError),
    ],
)
def test_knowledge_client_status_maps_http_failures(status_code, error_type):
    client = KnowledgeClient(base_url="http://knowledge", token="token")
    client._client.close()

    class FakeHttpClient:
        def get(self, path, **kwargs):
            del path, kwargs
            return httpx.Response(status_code, json={"detail": "internal-secret"})

        def close(self):
            return None

    client._client = FakeHttpClient()

    with pytest.raises(error_type):
        client.status(user_id="user-1")


@pytest.mark.parametrize(
    ("transport_error", "error_type"),
    [
        (httpx.ReadTimeout("slow"), KnowledgeTimeoutError),
        (httpx.ConnectError("offline"), KnowledgeUnavailableError),
    ],
)
def test_knowledge_client_status_maps_transport_failures(
    transport_error, error_type
):
    client = KnowledgeClient(base_url="http://knowledge", token="token")
    client._client.close()

    class FakeHttpClient:
        def get(self, path, **kwargs):
            del path, kwargs
            raise transport_error

        def close(self):
            return None

    client._client = FakeHttpClient()

    with pytest.raises(error_type):
        client.status(user_id="user-1")
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```powershell
D:\Anaconda3\envs\langchain1.2\python.exe -B -m pytest tests/unit/test_knowledge_status_tool.py -q
```

Expected: collection fails because `KnowledgeStatusResponse` and `KnowledgeClient.status` do not exist.

- [ ] **Step 3: Implement strict immutable status DTOs**

Append to `src/private_agent/knowledge/schemas.py`:

```python
def _required_bool(value: dict[str, Any], key: str) -> bool:
    selected = value[key]
    if not isinstance(selected, bool):
        raise TypeError(key)
    return selected


def _required_non_negative_int(value: dict[str, Any], key: str) -> int:
    selected = value[key]
    if isinstance(selected, bool) or not isinstance(selected, int) or selected < 0:
        raise TypeError(key)
    return selected


@dataclass(frozen=True)
class KnowledgeEmbeddingStatus:
    model: str
    revision: str
    dimension: int
    ready: bool

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "KnowledgeEmbeddingStatus":
        if not isinstance(value, dict):
            raise TypeError("embedding")
        model = value["model"]
        revision = value["revision"]
        if not isinstance(model, str) or not isinstance(revision, str):
            raise TypeError("embedding metadata")
        return cls(
            model=model,
            revision=revision,
            dimension=_required_non_negative_int(value, "dimension"),
            ready=_required_bool(value, "ready"),
        )


@dataclass(frozen=True)
class KnowledgeSqliteStatus:
    ready: bool
    knowledge_bases: int
    total_documents: int
    active_chunks: int

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "KnowledgeSqliteStatus":
        if not isinstance(value, dict):
            raise TypeError("sqlite")
        return cls(
            ready=_required_bool(value, "ready"),
            knowledge_bases=_required_non_negative_int(value, "knowledge_bases"),
            total_documents=_required_non_negative_int(value, "total_documents"),
            active_chunks=_required_non_negative_int(value, "active_chunks"),
        )


@dataclass(frozen=True)
class KnowledgeStatusResponse:
    enabled: bool
    embedding: KnowledgeEmbeddingStatus
    sqlite: KnowledgeSqliteStatus
    milvus: dict[str, Any]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "KnowledgeStatusResponse":
        try:
            milvus = value["milvus"]
            if not isinstance(milvus, dict):
                raise TypeError("milvus")
            _required_bool(milvus, "ready")
            return cls(
                enabled=_required_bool(value, "enabled"),
                embedding=KnowledgeEmbeddingStatus.from_dict(value["embedding"]),
                sqlite=KnowledgeSqliteStatus.from_dict(value["sqlite"]),
                milvus=dict(milvus),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid knowledge status response") from exc
```

Dimension is allowed to be zero only because the transport contract declares an integer; readiness determines whether the component is usable. Counts must be non-negative.

- [ ] **Step 4: Implement `KnowledgeClient.status`**

Import `KnowledgeStatusResponse` in `client.py` and add:

```python
def status(self, *, user_id: str) -> KnowledgeStatusResponse:
    normalized_user_id = user_id.strip()
    if not normalized_user_id:
        raise ValueError("user_id must not be blank")
    try:
        response = self._client.get(
            "/v1/knowledge/status",
            params={"user_id": normalized_user_id},
        )
    except httpx.TimeoutException as exc:
        raise KnowledgeTimeoutError("knowledge request timed out") from exc
    except httpx.HTTPError as exc:
        raise KnowledgeUnavailableError("knowledge service unavailable") from exc
    if response.status_code in {401, 403}:
        raise KnowledgeAuthenticationError("knowledge service rejected credentials")
    if response.status_code >= 500:
        raise KnowledgeUnavailableError("knowledge service unavailable")
    if not response.is_success:
        raise KnowledgeProtocolError("knowledge service rejected request")
    try:
        body = response.json()
        if not isinstance(body, dict):
            raise TypeError
        return KnowledgeStatusResponse.from_dict(body)
    except (TypeError, ValueError) as exc:
        raise KnowledgeProtocolError("invalid knowledge service response") from exc
```

- [ ] **Step 5: Run DTO/client tests and existing Knowledge tests**

Run:

```powershell
D:\Anaconda3\envs\langchain1.2\python.exe -B -m pytest tests/unit/test_knowledge_status_tool.py tests/unit/test_knowledge_tool.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Record the task boundary**

Current checkout has no Git metadata, so do not run a commit command. If Git is later restored, the exact commit would be:

```powershell
git add src/private_agent/knowledge/schemas.py src/private_agent/knowledge/client.py tests/unit/test_knowledge_status_tool.py
git commit -m "feat: add typed knowledge status client"
```

---

### Task 2: Safe status tool service

**Files:**
- Create: `src/private_agent/tools/knowledge/get_knowledge_status.py`
- Modify: `tests/unit/test_knowledge_status_tool.py`

**Interfaces:**
- Consumes: `KnowledgeStatusResponse`, a client with `status(*, user_id: str)`, and a capability object with `can_search_knowledge(user_id: str) -> bool`.
- Produces: `get_knowledge_status(*, user_id: str, client: KnowledgeStatusClient, capabilities: KnowledgeCapabilities) -> dict[str, object]`.

- [ ] **Step 1: Add failing service tests**

Extend `tests/unit/test_knowledge_status_tool.py`:

```python
from private_agent.knowledge.errors import (
    KnowledgeAuthenticationError,
    KnowledgeTimeoutError,
    KnowledgeUnavailableError,
)
from private_agent.tools.knowledge.get_knowledge_status import get_knowledge_status


class FakeCapabilities:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed

    def can_search_knowledge(self, user_id: str) -> bool:
        assert user_id
        return self.allowed


class FakeStatusClient:
    def __init__(self, payload=None, error: Exception | None = None) -> None:
        self.payload = payload or _status_payload()
        self.error = error
        self.calls: list[str] = []

    def status(self, *, user_id: str) -> KnowledgeStatusResponse:
        self.calls.append(user_id)
        if self.error:
            raise self.error
        return KnowledgeStatusResponse.from_dict(self.payload)


def test_get_knowledge_status_returns_full_status_and_sanitizes_milvus_error():
    payload = _status_payload()
    payload["milvus"]["ready"] = False
    payload["milvus"]["error"] = "token=secret host=internal-milvus"
    client = FakeStatusClient(payload)

    result = get_knowledge_status(
        user_id="user-1",
        client=client,
        capabilities=FakeCapabilities(True),
    )

    assert client.calls == ["user-1"]
    assert result["sqlite"]["total_documents"] == 10
    assert result["milvus"]["database"] == "knowledge"
    assert result["milvus"]["error"] == "Milvus 状态异常"
    assert "secret" not in repr(result)


def test_get_knowledge_status_denied_user_never_calls_api():
    client = FakeStatusClient()

    with pytest.raises(PermissionError):
        get_knowledge_status(
            user_id="user-2",
            client=client,
            capabilities=FakeCapabilities(False),
        )

    assert client.calls == []


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (KnowledgeAuthenticationError("token=secret"), "KNOWLEDGE_AUTHENTICATION_FAILED"),
        (KnowledgeTimeoutError("host=internal"), "KNOWLEDGE_TIMEOUT"),
        (KnowledgeUnavailableError("http://internal"), "KNOWLEDGE_UNAVAILABLE"),
    ],
)
def test_get_knowledge_status_returns_sanitized_errors(error, code):
    result = get_knowledge_status(
        user_id="user-1",
        client=FakeStatusClient(error=error),
        capabilities=FakeCapabilities(True),
    )

    assert result["error"]["code"] == code
    assert str(error) not in repr(result)
```

- [ ] **Step 2: Run service tests and verify RED**

Run:

```powershell
D:\Anaconda3\envs\langchain1.2\python.exe -B -m pytest tests/unit/test_knowledge_status_tool.py -q
```

Expected: collection fails because `private_agent.tools.knowledge.get_knowledge_status` does not exist.

- [ ] **Step 3: Implement the focused service module**

Create `src/private_agent/tools/knowledge/get_knowledge_status.py`:

```python
"""Read-only current-user Knowledge Service status tool."""

from __future__ import annotations

from dataclasses import asdict
from typing import Protocol

from private_agent.knowledge.errors import (
    KnowledgeAuthenticationError,
    KnowledgeError,
    KnowledgeTimeoutError,
)
from private_agent.knowledge.schemas import KnowledgeStatusResponse


class KnowledgeStatusClient(Protocol):
    def status(self, *, user_id: str) -> KnowledgeStatusResponse: ...


class KnowledgeCapabilities(Protocol):
    def can_search_knowledge(self, user_id: str) -> bool: ...


def get_knowledge_status(
    *,
    user_id: str,
    client: KnowledgeStatusClient,
    capabilities: KnowledgeCapabilities,
) -> dict[str, object]:
    if not capabilities.can_search_knowledge(user_id):
        raise PermissionError("knowledge status is not allowed for this user")
    try:
        result = asdict(client.status(user_id=user_id))
    except KnowledgeAuthenticationError:
        return _safe_error(
            "KNOWLEDGE_AUTHENTICATION_FAILED",
            "知识库认证失败。",
        )
    except KnowledgeTimeoutError:
        return _safe_error("KNOWLEDGE_TIMEOUT", "知识库状态查询超时。")
    except KnowledgeError:
        return _safe_error("KNOWLEDGE_UNAVAILABLE", "知识库状态暂时不可用。")

    milvus = result.get("milvus")
    if isinstance(milvus, dict) and milvus.get("error"):
        milvus["error"] = "Milvus 状态异常"
    return result


def _safe_error(code: str, message: str) -> dict[str, object]:
    return {"error": {"code": code, "message": message}}
```

- [ ] **Step 4: Run service tests and verify GREEN**

Run:

```powershell
D:\Anaconda3\envs\langchain1.2\python.exe -B -m pytest tests/unit/test_knowledge_status_tool.py -q
```

Expected: all status tests pass.

- [ ] **Step 5: Record the task boundary**

Skip commit in the current non-Git checkout. If Git is later restored:

```powershell
git add src/private_agent/tools/knowledge/get_knowledge_status.py tests/unit/test_knowledge_status_tool.py
git commit -m "feat: add safe knowledge status service"
```

---

### Task 3: LangChain tool registration, identity injection, prompt, and docs

**Files:**
- Modify: `src/private_agent/agent/factory.py`
- Modify: `src/private_agent/agent/profiles.py`
- Modify: `src/private_agent/agent/prompts.py`
- Modify: `docs/tools.md`
- Modify: `tests/private_agent/test_agent_factory.py`
- Modify: `tests/unit/test_knowledge_status_tool.py`

**Interfaces:**
- Consumes: Task 2 `get_knowledge_status` service function.
- Produces: zero-argument LangChain tool named `get_knowledge_status`, registered as `READ_SAFE`, no approval, network-reading, available in CLI and XiaoXu's low-risk profile.

- [ ] **Step 1: Add failing registration and identity tests**

Update expected registry names in `tests/private_agent/test_agent_factory.py` so both CLI and `wecom_chat` include `get_knowledge_status`, and add:

```python
import json

from private_agent.agent_factory import build_tools, create_resources
from private_agent.security import RiskLevel


def test_knowledge_status_tool_has_no_model_arguments_and_uses_current_user(
    tmp_path, monkeypatch
):
    settings = AppSettings(
        run_dir=tmp_path,
        user_id="current-user",
        enable_summarization_middleware=False,
    )
    resources = create_resources(settings, PermissionPolicy(), RuntimeState())
    captured = {}

    def fake_status(*, user_id):
        captured["user_id"] = user_id
        from private_agent.knowledge.schemas import KnowledgeStatusResponse

        return KnowledgeStatusResponse.from_dict(
            {
                "enabled": True,
                "embedding": {
                    "model": "BAAI/bge-m3",
                    "revision": "fixed",
                    "dimension": 1024,
                    "ready": True,
                },
                "sqlite": {
                    "ready": True,
                    "knowledge_bases": 2,
                    "total_documents": 10,
                    "active_chunks": 120,
                },
                "milvus": {
                    "ready": True,
                    "database": "knowledge",
                    "collection": "knowledge_chunks_v1",
                    "dimension": 1024,
                },
            }
        )

    monkeypatch.setattr(resources.knowledge, "status", fake_status)
    tool = next(item for item in build_tools(resources) if item.name == "get_knowledge_status")

    result = json.loads(tool.invoke({}))

    assert tool.args == {}
    assert captured == {"user_id": "current-user"}
    assert result["sqlite"]["total_documents"] == 10
    assert "tool_usage:knowledge_search" not in json.dumps(result)
    permission = resources.registry.get("get_knowledge_status").permission
    assert permission.risk == RiskLevel.READ_SAFE
    assert permission.requires_approval is False
    assert permission.uses_network is True
    assert resources.runtime.search_turn_state is None
```

Add a prompt assertion:

```python
def test_system_prompt_routes_knowledge_status_questions_to_status_tool():
    assert "get_knowledge_status" in SYSTEM_PROMPT
    assert "文档数量" in SYSTEM_PROMPT
    assert "分块数量" in SYSTEM_PROMPT
```

- [ ] **Step 2: Run registration tests and verify RED**

Run:

```powershell
D:\Anaconda3\envs\langchain1.2\python.exe -B -m pytest tests/private_agent/test_agent_factory.py tests/unit/test_knowledge_status_tool.py -q
```

Expected: failures show the tool is absent from registry/profile and prompt.

- [ ] **Step 3: Register the tool in `factory.py`**

Import the Task 2 service as `get_knowledge_status_result` to avoid shadowing the LangChain tool function:

```python
from private_agent.tools.knowledge.get_knowledge_status import (
    get_knowledge_status as get_knowledge_status_result,
)
```

Inside `build_tools`, before `search_knowledge_tool`, add:

```python
@_tool_with_permission(
    resources,
    ToolPermission(
        name="get_knowledge_status",
        risk=RiskLevel.READ_SAFE,
        requires_approval=False,
        uses_network=True,
        description=(
            "Read the current user's Knowledge Service readiness, knowledge-base, "
            "document, chunk, embedding, and Milvus status without retrieving content."
        ),
    ),
)
def get_knowledge_status_tool() -> str:
    """查询当前用户知识库是否启用、是否就绪以及知识库、文档和分块数量。"""

    result = get_knowledge_status_result(
        user_id=current_user_id(resources.settings.user_id),
        client=resources.knowledge,
        capabilities=resources.capabilities,
    )
    return json.dumps(result, ensure_ascii=False, sort_keys=True)
```

Add `get_knowledge_status_tool` to `all_tools`. Do not add it to `SearchPolicyMiddleware._TOOL_KINDS`.

- [ ] **Step 4: Add the tool to the low-risk XiaoXu profile**

Add `"get_knowledge_status"` to the `wecom_chat` allowlist in `agent/profiles.py`. This changes only XiaoXu's channel tool set; it does not add any WxBot code or database access.

- [ ] **Step 5: Update prompt routing and tool documentation**

Add this prompt rule immediately after the existing Knowledge routing rules:

```text
当用户询问知识库是否启用、是否就绪、知识库数量、文档数量、分块数量、Embedding 或 Milvus 状态时，调用 get_knowledge_status；该工具只查询当前用户状态，不检索文档正文。
```

Add a `get_knowledge_status` section to `docs/tools.md` documenting:

```markdown
`get_knowledge_status` 通过 Knowledge HTTP API 查询当前用户的启用状态、
Embedding、SQLite 统计和 Milvus 状态。它没有用户参数，不读取文档正文，不消耗
`search_knowledge` 查询次数，也不产生知识检索来源标记。WxBot 即使未来接入，
也只能通过 XiaoXu 使用该工具，不能直接访问 Knowledge 数据库或 Milvus。
```

- [ ] **Step 6: Run focused registration, prompt, and status tests**

Run:

```powershell
D:\Anaconda3\envs\langchain1.2\python.exe -B -m pytest tests/private_agent/test_agent_factory.py tests/unit/test_knowledge_status_tool.py tests/unit/test_knowledge_tool.py -q
```

Expected: all focused tests pass.

- [ ] **Step 7: Record the task boundary**

Skip commit in the current non-Git checkout. If Git is later restored:

```powershell
git add src/private_agent/agent/factory.py src/private_agent/agent/profiles.py src/private_agent/agent/prompts.py docs/tools.md tests/private_agent/test_agent_factory.py tests/unit/test_knowledge_status_tool.py
git commit -m "feat: expose current-user knowledge status tool"
```

---

### Task 4: Regression and real Knowledge API acceptance

**Files:**
- Verify only; no production file changes expected.

**Interfaces:**
- Consumes: completed `get_knowledge_status` client, service, and LangChain registration.
- Produces: fresh automated and real-backend evidence without changing Knowledge data.

- [ ] **Step 1: Run the Knowledge/status regression subset**

Run:

```powershell
D:\Anaconda3\envs\langchain1.2\python.exe -B -m pytest tests/unit/test_knowledge_status_tool.py tests/unit/test_knowledge_tool.py tests/private_agent/test_agent_factory.py tests/private_agent/test_governance.py tests/private_agent/test_api.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run the full XiaoXu suite**

Run:

```powershell
D:\Anaconda3\envs\langchain1.2\python.exe -B -m pytest -q
```

Expected: zero failures. Report any third-party deprecation warning separately rather than describing output as warning-free.

- [ ] **Step 3: Start existing Knowledge dependencies only if currently stopped**

First inspect without changing state:

```powershell
D:\DockerDesktop\resources\bin\docker.exe ps --format "table {{.Names}}\t{{.Status}}"
```

If the engine is stopped, start Docker Desktop and verify the server becomes available:

```powershell
Start-Process -FilePath "D:\DockerDesktop\Docker Desktop.exe" -WindowStyle Hidden
D:\DockerDesktop\resources\bin\docker.exe version --format "{{.Server.Version}}"
```

If `milvus-knowledge-1` and `milvus-milvus-1` were stopped before acceptance, start them offline with:

```powershell
Set-Location D:\Code\Python\langchain\Demo\Milvus
D:\DockerDesktop\resources\bin\docker.exe compose --env-file .env up -d --no-build --pull never milvus knowledge
D:\DockerDesktop\resources\bin\docker.exe compose --env-file .env ps
```

Require both services to report healthy. Do not build, pull, recreate, use `down`, or delete volumes.

- [ ] **Step 4: Invoke the real status path and assert the detailed contract**

From `Demo/XiaoXu`, run this exact read-only acceptance script:

```powershell
$env:PYTHONPATH="src"
@'
import json

from private_agent.config import AppSettings
from private_agent.core.capabilities import CapabilityPolicy
from private_agent.knowledge.client import KnowledgeClient
from private_agent.tools.knowledge.get_knowledge_status import get_knowledge_status

settings = AppSettings()
client = KnowledgeClient(
    base_url=settings.knowledge_api_url,
    token=settings.knowledge_api_token or "",
)
try:
    result = get_knowledge_status(
        user_id=settings.user_id,
        client=client,
        capabilities=CapabilityPolicy(),
    )
finally:
    client.close()

assert set(result) == {"enabled", "embedding", "sqlite", "milvus"}
assert isinstance(result["enabled"], bool)
assert result["embedding"]["dimension"] >= 0
assert result["sqlite"]["knowledge_bases"] >= 0
assert result["sqlite"]["total_documents"] >= 0
assert result["sqlite"]["active_chunks"] >= 0
assert isinstance(result["milvus"]["ready"], bool)
assert "token" not in repr(result).lower()

print(json.dumps({
    "enabled": result["enabled"],
    "embedding_ready": result["embedding"]["ready"],
    "knowledge_bases": result["sqlite"]["knowledge_bases"],
    "total_documents": result["sqlite"]["total_documents"],
    "active_chunks": result["sqlite"]["active_chunks"],
    "milvus_ready": result["milvus"]["ready"],
}, ensure_ascii=False, sort_keys=True))
'@ | D:\Anaconda3\envs\langchain1.2\python.exe -B -
```

Print only a sanitized summary containing readiness booleans and integer counts. Never print the Knowledge token, raw service errors, document names, or user content.

- [ ] **Step 5: Restore runtime state**

If Task 4 Step 3 started containers that were initially stopped, restore only those exact services with:

```powershell
D:\DockerDesktop\resources\bin\docker.exe compose --env-file .env stop knowledge milvus
```

Do not run `down` or remove `milvus-data`. If Docker Desktop itself was started only for this acceptance and was initially stopped, close it after the containers stop.

- [ ] **Step 6: Final scope audit**

Use `rg` to confirm:

```powershell
rg -n "get_knowledge_status" src tests docs
rg -n "tool_usage:knowledge_search|SearchPolicyMiddleware" src/private_agent/tools/knowledge/get_knowledge_status.py
```

The first command must show DTO/client/service/registration/prompt/docs/tests. The second must show no matches, proving the status tool is not coupled to search markers or search governance.
