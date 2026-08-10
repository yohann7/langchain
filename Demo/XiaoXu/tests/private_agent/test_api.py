import json
from time import sleep

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessageChunk, ToolMessage

from private_agent.api import create_app
from private_agent.config import AppSettings
from private_agent.core.identity import (
    actor_to_user_id,
    conversation_thread_id,
    current_conversation_type,
    current_user_id,
)


AUTH_HEADERS = {
    "Authorization": "Bearer test-token",
    "Accept": "text/event-stream",
}
BASE_PAYLOAD = {
    "request_id": "msg-1",
    "thread_id": "wecom:dm:user-a",
    "actor_id": "user-a",
    "channel": "wecom",
    "conversation_type": "single",
    "message": {
        "type": "text",
        "text": "你好",
    },
}


class StreamingAgent:
    def __init__(self):
        self.calls = []

    def stream(self, payload, config=None, stream_mode=None):
        self.calls.append(
            {
                "payload": payload,
                "config": config,
                "stream_mode": stream_mode,
            }
        )
        yield (
            "messages",
            (
                AIMessageChunk(content="你"),
                {"langgraph_node": "model"},
            ),
        )
        yield (
            "messages",
            (
                AIMessageChunk(
                    content="好",
                    additional_kwargs={"reasoning_content": "不可见思考"},
                ),
                {"langgraph_node": "model"},
            ),
        )


class IdentityCapturingStreamingAgent(StreamingAgent):
    def __init__(self):
        super().__init__()
        self.identities = []

    def stream(self, payload, config=None, stream_mode=None):
        self.identities.append(
            (
                current_user_id("missing"),
                current_conversation_type("missing"),
            )
        )
        yield from super().stream(payload, config, stream_mode)


class FailingAgent:
    def stream(self, payload, config=None, stream_mode=None):
        raise RuntimeError("secret internal model error")
        yield


class LongStreamingAgent:
    def stream(self, payload, config=None, stream_mode=None):
        yield (
            "messages",
            (
                AIMessageChunk(content="你好世界"),
                {"langgraph_node": "model"},
            ),
        )


class SlowStreamingAgent:
    def stream(self, payload, config=None, stream_mode=None):
        sleep(0.1)
        yield (
            "messages",
            (
                AIMessageChunk(content="不应返回"),
                {"langgraph_node": "model"},
            ),
        )


class RagOnlyStreamingAgent:
    def stream(self, payload, config=None, stream_mode=None):
        yield (
            "messages",
            (
                ToolMessage(
                    content="[来源 1] example.pdf（PDF 第 23 页）\nchunk_id=abc\n证据内容",
                    tool_call_id="rag-1",
                    name="search_knowledge",
                ),
                {"langgraph_node": "tools"},
            ),
        )


class RagPreambleStreamingAgent:
    def stream(self, payload, config=None, stream_mode=None):
        yield (
            "messages",
            (
                AIMessageChunk(content="我来帮您查询。"),
                {"langgraph_node": "model"},
            ),
        )
        yield from RagOnlyStreamingAgent().stream(payload, config, stream_mode)


class RagCitationOnlyStreamingAgent:
    def stream(self, payload, config=None, stream_mode=None):
        yield from RagOnlyStreamingAgent().stream(payload, config, stream_mode)
        yield (
            "messages",
            (
                AIMessageChunk(content="证据支持该结论。[来源 1]"),
                {"langgraph_node": "model"},
            ),
        )


class HybridRagStreamingAgent:
    def stream(self, payload, config=None, stream_mode=None):
        yield (
            "messages",
            (
                ToolMessage(
                    content=(
                        "[来源 1] example.pdf（PDF 第 23 页）\n证据内容\n\n"
                        "<!-- tool_usage:knowledge_search=SQLite&Milvus -->"
                    ),
                    tool_call_id="rag-hybrid-1",
                    name="search_knowledge",
                ),
                {"langgraph_node": "tools"},
            ),
        )
        yield (
            "messages",
            (
                AIMessageChunk(content="知识库回答。[来源 1]"),
                {"langgraph_node": "model"},
            ),
        )


class WebSearchFallbackStreamingAgent:
    def stream(self, payload, config=None, stream_mode=None):
        yield (
            "messages",
            (
                ToolMessage(
                    content=(
                        "1. Example\n   URL: https://example.com\n   Evidence\n\n"
                        "<!-- tool_usage:web_search=Tavily -->"
                    ),
                    tool_call_id="web-1",
                    name="web_search",
                ),
                {"langgraph_node": "tools"},
            ),
        )
        yield (
            "messages",
            (
                AIMessageChunk(content="根据备用搜索结果生成的回答。"),
                {"langgraph_node": "model"},
            ),
        )


def _settings(tmp_path, **updates):
    return AppSettings(
        run_dir=tmp_path,
        api_token="test-token",
        enable_summarization_middleware=False,
        **updates,
    )


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = block.splitlines()
        event = next(line.removeprefix("event: ") for line in lines if line.startswith("event: "))
        data = next(line.removeprefix("data: ") for line in lines if line.startswith("data: "))
        events.append((event, json.loads(data)))
    return events


def test_health_endpoints_report_live_and_ready(tmp_path):
    app = create_app(_settings(tmp_path), agent=StreamingAgent())

    with TestClient(app) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        ready = client.get("/health/ready")

    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}


def test_completed_run_releases_thread_lock_entry(tmp_path):
    app = create_app(_settings(tmp_path), agent=StreamingAgent())

    with TestClient(app) as client:
        response = client.post(
            "/v1/runs",
            json=BASE_PAYLOAD,
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 200
        assert len(app.state.thread_locks) == 0


def test_ready_requires_api_token(tmp_path):
    settings = AppSettings(
        run_dir=tmp_path,
        api_token=None,
        enable_summarization_middleware=False,
    )
    app = create_app(settings, agent=StreamingAgent())

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "code": "AUTH_NOT_CONFIGURED",
    }


def test_run_requires_valid_bearer_token(tmp_path):
    app = create_app(_settings(tmp_path), agent=StreamingAgent())

    with TestClient(app) as client:
        missing = client.post("/v1/runs", json=BASE_PAYLOAD)
        invalid = client.post(
            "/v1/runs",
            json=BASE_PAYLOAD,
            headers={"Authorization": "Bearer wrong-token"},
        )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"


def test_run_rejects_invalid_message_type(tmp_path):
    app = create_app(_settings(tmp_path), agent=StreamingAgent())
    payload = {
        **BASE_PAYLOAD,
        "message": {"type": "image", "text": "unsupported"},
    }

    with TestClient(app) as client:
        response = client.post("/v1/runs", json=payload, headers=AUTH_HEADERS)

    assert response.status_code == 422


def test_run_rejects_oversized_utf8_input(tmp_path):
    app = create_app(
        _settings(tmp_path, api_max_input_bytes=5),
        agent=StreamingAgent(),
    )
    payload = {
        **BASE_PAYLOAD,
        "message": {"type": "text", "text": "你好"},
    }

    with TestClient(app) as client:
        response = client.post("/v1/runs", json=payload, headers=AUTH_HEADERS)

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "INPUT_TOO_LARGE"


def test_run_streams_started_deltas_and_completed_without_thinking(tmp_path):
    agent = StreamingAgent()
    app = create_app(_settings(tmp_path), agent=agent)

    with TestClient(app) as client:
        response = client.post("/v1/runs", json=BASE_PAYLOAD, headers=AUTH_HEADERS)

    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert [event for event, _data in events] == [
        "run.started",
        "response.delta",
        "response.delta",
        "run.completed",
    ]
    assert [data["text"] for event, data in events if event == "response.delta"] == [
        "你",
        "好",
    ]
    assert events[-1][1]["final_text"] == (
        "[web_search：None, knowledge_search:None]\n你好"
    )
    assert "不可见思考" not in response.text


def test_run_falls_back_to_rag_result_when_model_returns_no_answer(tmp_path):
    app = create_app(_settings(tmp_path), agent=RagOnlyStreamingAgent())

    with TestClient(app) as client:
        response = client.post("/v1/runs", json=BASE_PAYLOAD, headers=AUTH_HEADERS)

    events = _parse_sse(response.text)
    final_text = events[-1][1]["final_text"]
    assert events[-1][0] == "run.completed"
    assert "本轮未生成完整回答" in final_text
    assert "[来源 1] example.pdf（PDF 第 23 页）" in final_text
    assert "证据内容" not in final_text


def test_run_appends_rag_result_when_model_only_returns_preamble(tmp_path):
    app = create_app(_settings(tmp_path), agent=RagPreambleStreamingAgent())

    with TestClient(app) as client:
        response = client.post("/v1/runs", json=BASE_PAYLOAD, headers=AUTH_HEADERS)

    events = _parse_sse(response.text)
    final_text = events[-1][1]["final_text"]
    assert final_text.startswith(
        "[web_search：None, knowledge_search:SQLite]\n我来帮您查询。"
    )
    assert "本轮未生成完整回答" in final_text
    assert "[来源 1] example.pdf（PDF 第 23 页）" in final_text
    assert "证据内容" not in final_text


def test_run_appends_source_details_when_model_keeps_only_citation_number(tmp_path):
    app = create_app(_settings(tmp_path), agent=RagCitationOnlyStreamingAgent())

    with TestClient(app) as client:
        response = client.post("/v1/runs", json=BASE_PAYLOAD, headers=AUTH_HEADERS)

    events = _parse_sse(response.text)
    final_text = events[-1][1]["final_text"]
    assert "证据支持该结论。[来源 1]" in final_text
    assert "来源明细：" in final_text
    assert "[来源 1] example.pdf（PDF 第 23 页）" in final_text


def test_run_reports_sqlite_and_milvus_when_hybrid_rag_was_used(tmp_path):
    app = create_app(_settings(tmp_path), agent=HybridRagStreamingAgent())

    with TestClient(app) as client:
        response = client.post("/v1/runs", json=BASE_PAYLOAD, headers=AUTH_HEADERS)

    events = _parse_sse(response.text)
    final_text = events[-1][1]["final_text"]
    assert final_text.startswith(
        "[web_search：None, knowledge_search:SQLite&Milvus]\n"
    )
    assert "tool_usage:" not in final_text


def test_run_reports_tavily_backend_without_legacy_failure_notice(tmp_path):
    app = create_app(_settings(tmp_path), agent=WebSearchFallbackStreamingAgent())

    with TestClient(app) as client:
        response = client.post("/v1/runs", json=BASE_PAYLOAD, headers=AUTH_HEADERS)

    events = _parse_sse(response.text)
    final_text = events[-1][1]["final_text"]
    assert final_text == (
        "[web_search：Tavily, knowledge_search:None]\n"
        "根据备用搜索结果生成的回答。"
    )
    assert "[SearXNG失败：" not in final_text


def test_run_passes_isolated_thread_ids_to_agent(tmp_path):
    agent = StreamingAgent()
    app = create_app(_settings(tmp_path), agent=agent)
    second_payload = {
        **BASE_PAYLOAD,
        "request_id": "msg-2",
        "thread_id": "wecom:dm:user-b",
        "actor_id": "user-b",
    }

    with TestClient(app) as client:
        first = client.post("/v1/runs", json=BASE_PAYLOAD, headers=AUTH_HEADERS)
        second = client.post("/v1/runs", json=second_payload, headers=AUTH_HEADERS)

    assert first.status_code == 200
    assert second.status_code == 200
    assert [call["config"]["configurable"]["thread_id"] for call in agent.calls] == [
        conversation_thread_id(
            BASE_PAYLOAD["thread_id"],
            actor_id=BASE_PAYLOAD["actor_id"],
            channel="wecom",
            conversation_type="single",
            secret="change-me",
        ),
        conversation_thread_id(
            second_payload["thread_id"],
            actor_id=second_payload["actor_id"],
            channel="wecom",
            conversation_type="single",
            secret="change-me",
        ),
    ]


def test_single_chat_thread_key_is_isolated_by_actor(tmp_path):
    agent = StreamingAgent()
    app = create_app(_settings(tmp_path), agent=agent)
    second_payload = {
        **BASE_PAYLOAD,
        "request_id": "msg-2",
        "actor_id": "user-b",
    }

    with TestClient(app) as client:
        client.post("/v1/runs", json=BASE_PAYLOAD, headers=AUTH_HEADERS)
        client.post("/v1/runs", json=second_payload, headers=AUTH_HEADERS)

    keys = [call["config"]["configurable"]["thread_id"] for call in agent.calls]
    assert keys[0] != keys[1]


def test_group_chat_thread_key_is_shared_by_group_not_actor(tmp_path):
    agent = StreamingAgent()
    app = create_app(_settings(tmp_path), agent=agent)
    group_payload = {
        **BASE_PAYLOAD,
        "thread_id": "wecom:group:engineering",
        "conversation_type": "group",
    }
    second_payload = {
        **group_payload,
        "request_id": "msg-2",
        "actor_id": "user-b",
    }

    with TestClient(app) as client:
        client.post("/v1/runs", json=group_payload, headers=AUTH_HEADERS)
        client.post("/v1/runs", json=second_payload, headers=AUTH_HEADERS)

    keys = [call["config"]["configurable"]["thread_id"] for call in agent.calls]
    assert keys[0] == keys[1]


def test_api_propagates_actor_and_conversation_type_context(tmp_path):
    agent = IdentityCapturingStreamingAgent()
    app = create_app(_settings(tmp_path), agent=agent)
    payload = {
        **BASE_PAYLOAD,
        "thread_id": "wecom:group:engineering",
        "conversation_type": "group",
    }

    with TestClient(app) as client:
        response = client.post("/v1/runs", json=payload, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert agent.identities == [
        (
            actor_to_user_id("user-a", secret="change-me"),
            "group",
        )
    ]


def test_run_truncates_output_on_utf8_boundary(tmp_path):
    app = create_app(
        _settings(tmp_path, api_max_output_bytes=7),
        agent=LongStreamingAgent(),
    )

    with TestClient(app) as client:
        response = client.post("/v1/runs", json=BASE_PAYLOAD, headers=AUTH_HEADERS)

    events = _parse_sse(response.text)
    assert [data["text"] for event, data in events if event == "response.delta"] == [
        "你好"
    ]
    assert events[-1][1]["final_text"] == (
        "[web_search：None, knowledge_search:None]\n你好"
    )
    assert events[-1][1]["truncated"] is True


def test_run_times_out_with_sanitized_terminal_event(tmp_path):
    app = create_app(
        _settings(tmp_path, api_run_timeout_seconds=0.02),
        agent=SlowStreamingAgent(),
    )

    with TestClient(app) as client:
        response = client.post("/v1/runs", json=BASE_PAYLOAD, headers=AUTH_HEADERS)

    events = _parse_sse(response.text)
    assert [event for event, _data in events] == [
        "run.started",
        "run.failed",
    ]
    assert events[-1][1]["code"] == "RUN_TIMEOUT"
    assert "不应返回" not in response.text


def test_run_returns_sanitized_failure_event(tmp_path):
    app = create_app(_settings(tmp_path), agent=FailingAgent())

    with TestClient(app) as client:
        response = client.post("/v1/runs", json=BASE_PAYLOAD, headers=AUTH_HEADERS)

    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert [event for event, _data in events] == [
        "run.started",
        "run.failed",
    ]
    assert events[-1][1]["code"] == "AGENT_RUN_FAILED"
    assert "secret internal model error" not in response.text
