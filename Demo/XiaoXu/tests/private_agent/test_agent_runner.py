from types import SimpleNamespace

from langchain_core.messages import AIMessage, AIMessageChunk

from private_agent.agent_runner import AgentRunner
from private_agent.agent.prompts import SYSTEM_PROMPT
from private_agent.config import AppSettings
from private_agent.runtime import RuntimeState, current_runtime_state


class InvokeAgent:
    def __init__(self):
        self.calls = []

    def invoke(self, payload, config=None):
        self.calls.append({"payload": payload, "config": config})
        return {"messages": [AIMessage(content="完成")]}


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
                AIMessageChunk(content="好"),
                {"langgraph_node": "model"},
            ),
        )


class FailingAgent:
    def invoke(self, payload, config=None):
        raise RuntimeError("model unavailable")


class SearchStateProbeAgent:
    def __init__(self, runtime):
        self.runtime = runtime
        self.seen_states = []

    def invoke(self, payload, config=None):
        del payload, config
        self.seen_states.append(current_runtime_state(self.runtime).search_turn_state)
        return {"messages": [AIMessage(content="ok")]}


class ApprovalSearchStateProbeAgent(SearchStateProbeAgent):
    def invoke(self, payload, config=None):
        del payload, config
        self.seen_states.append(current_runtime_state(self.runtime).search_turn_state)
        if len(self.seen_states) == 1:
            return {
                "__interrupt__": [
                    SimpleNamespace(value={"action_requests": [{"name": "web_search"}]})
                ]
            }
        return {"messages": [AIMessage(content="approved")]}


def test_runner_invokes_agent_with_explicit_thread_id(tmp_path):
    settings = AppSettings(run_dir=tmp_path, thread_id="default")
    runtime = RuntimeState()
    agent = InvokeAgent()
    runner = AgentRunner(agent, settings, runtime)

    result = runner.invoke("你好", thread_id="wecom:dm:user-a")

    assert result == "完成"
    assert agent.calls[0]["config"] == {
        "configurable": {"thread_id": "wecom:dm:user-a"}
    }
    assert runtime.status.value == "idle"


def test_runner_keeps_thread_configuration_isolated_between_calls(tmp_path):
    settings = AppSettings(run_dir=tmp_path)
    agent = InvokeAgent()
    runner = AgentRunner(agent, settings, RuntimeState())

    runner.invoke("第一条", thread_id="thread-a")
    runner.invoke("第二条", thread_id="thread-b")

    assert [call["config"]["configurable"]["thread_id"] for call in agent.calls] == [
        "thread-a",
        "thread-b",
    ]


def test_runner_streams_channel_neutral_callbacks(tmp_path):
    settings = AppSettings(run_dir=tmp_path)
    runtime = RuntimeState()
    agent = StreamingAgent()
    runner = AgentRunner(agent, settings, runtime)
    text_parts = []

    result = runner.stream(
        "你好",
        thread_id="wecom:dm:user-a",
        emit_text=text_parts.append,
    )

    assert result is None
    assert text_parts == ["你", "好"]
    assert agent.calls[0]["config"] == {
        "configurable": {"thread_id": "wecom:dm:user-a"}
    }
    assert agent.calls[0]["stream_mode"] == ["updates", "messages"]
    assert runtime.status.value == "idle"


def test_runner_returns_stable_error_and_resets_runtime(tmp_path):
    settings = AppSettings(run_dir=tmp_path)
    runtime = RuntimeState()
    runner = AgentRunner(FailingAgent(), settings, runtime)

    result = runner.invoke("你好")

    assert result == "Agent run failed: model unavailable"
    assert runtime.last_error == "model unavailable"
    assert runtime.status.value == "idle"


def test_runner_creates_a_fresh_search_state_for_each_user_turn(tmp_path):
    settings = AppSettings(run_dir=tmp_path)
    runtime = RuntimeState()
    agent = SearchStateProbeAgent(runtime)
    runner = AgentRunner(agent, settings, runtime)

    runner.invoke("first")
    runner.invoke("second")

    assert len(agent.seen_states) == 2
    assert agent.seen_states[0] is not agent.seen_states[1]
    assert runtime.search_turn_state is None


def test_search_prompt_uses_runtime_progress_instead_of_fixed_limits():
    assert "remaining_queries" in SYSTEM_PROMPT
    assert "最多扩展到三个" not in SYSTEM_PROMPT
    assert "最多允许调整一次" not in SYSTEM_PROMPT


def test_runner_preserves_search_state_across_approval_resume(tmp_path):
    settings = AppSettings(run_dir=tmp_path)
    runtime = RuntimeState()
    agent = ApprovalSearchStateProbeAgent(runtime)
    runner = AgentRunner(agent, settings, runtime)

    result = runner.invoke(
        "search",
        approval_callback=lambda requests: [
            {"type": "approve"} for _request in requests
        ],
    )

    assert result == "approved"
    assert len(agent.seen_states) == 2
    assert agent.seen_states[0] is agent.seen_states[1]
    assert runtime.search_turn_state is None
