from io import StringIO

from langchain_core.messages import AIMessage
from langchain_core.messages import AIMessageChunk
from rich.console import Console

from private_agent.cli import (
    COMMAND_OUTPUT_SEPARATOR,
    StreamingConsoleOutput,
    agent_output,
    handle_natural_exit_intent,
    handle_line,
    handle_line_streaming,
    is_blank_input,
    is_natural_exit_intent,
    print_command_message,
    resolve_authorization_choices,
)
from private_agent.config import AppSettings
from private_agent.models import ModelManager
from private_agent.runtime import RuntimeState
from private_agent.security import PermissionPolicy


class InterruptingAgent:
    def __init__(self):
        self.calls = 0

    def invoke(self, payload, config=None):
        self.calls += 1
        if self.calls == 1:
            return {
                "__interrupt__": [
                    type(
                        "Interrupt",
                        (),
                        {
                            "value": {
                                "action_requests": [
                                    {
                                        "name": "web_search",
                                        "args": {"query": "测试"},
                                    }
                                ]
                            }
                        },
                    )()
                ]
            }
        return {"messages": [AIMessage(content="搜索完成")]}


class StreamingAgent:
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []

    def stream(self, payload, config=None, stream_mode=None):
        self.calls.append(
            {
                "payload": payload,
                "config": config,
                "stream_mode": stream_mode,
            }
        )
        yield from self.chunks


class ResumableStreamingAgent:
    def __init__(self, first_chunks, resumed_chunks):
        self.streams = [first_chunks, resumed_chunks]
        self.calls = []

    def stream(self, payload, config=None, stream_mode=None):
        self.calls.append(
            {
                "payload": payload,
                "config": config,
                "stream_mode": stream_mode,
            }
        )
        yield from self.streams[len(self.calls) - 1]


def test_blank_cli_input_is_ignored():
    assert is_blank_input("") is True
    assert is_blank_input(" \t\r\n") is True
    assert is_blank_input("你好") is False


def test_handle_line_resumes_after_approval_callback(tmp_path):
    settings = AppSettings(run_dir=tmp_path)
    runtime = RuntimeState()
    policy = PermissionPolicy()
    agent = InterruptingAgent()

    message = handle_line(
        "创建待办",
        runtime,
        settings,
        policy,
        agent=agent,
        approval_callback=lambda requests: [{"type": "approve"} for _ in requests],
    )

    assert message == "搜索完成"
    assert agent.calls == 2


def test_handle_line_resumes_multiple_interrupts(tmp_path):
    first_interrupt = type(
        "Interrupt",
        (),
        {
            "value": {
                "action_requests": [
                    {
                        "name": "web_search",
                        "args": {"query": "first"},
                    }
                ]
            }
        },
    )()
    second_interrupt = type(
        "Interrupt",
        (),
        {
            "value": {
                "action_requests": [
                    {
                        "name": "web_search",
                        "args": {"query": "second"},
                    }
                ]
            }
        },
    )()

    class MultiInterruptAgent:
        def __init__(self):
            self.calls = 0

        def invoke(self, payload, config=None):
            self.calls += 1
            if self.calls == 1:
                return {"__interrupt__": [first_interrupt]}
            if self.calls == 2:
                return {"__interrupt__": [second_interrupt]}
            return {"messages": [AIMessage(content="完成")]}

    approvals = []
    agent = MultiInterruptAgent()

    message = handle_line(
        "需要多次搜索",
        RuntimeState(),
        AppSettings(run_dir=tmp_path),
        PermissionPolicy(),
        agent=agent,
        approval_callback=lambda requests: approvals.append(requests) or [{"type": "approve"}],
    )

    assert message == "完成"
    assert approvals == [
        [{"name": "web_search", "args": {"query": "first"}}],
        [{"name": "web_search", "args": {"query": "second"}}],
    ]
    assert agent.calls == 3


def test_handle_line_streaming_emits_tokens_from_agent_stream(tmp_path):
    settings = AppSettings(run_dir=tmp_path)
    runtime = RuntimeState()
    policy = PermissionPolicy()
    agent = StreamingAgent(
        [
            (
                "messages",
                (
                    AIMessageChunk(content="你"),
                    {"langgraph_node": "model"},
                ),
            ),
            (
                "messages",
                (
                    AIMessageChunk(content="好"),
                    {"langgraph_node": "model"},
                ),
            ),
        ]
    )
    text_parts = []

    message = handle_line_streaming(
        "你好",
        runtime,
        settings,
        policy,
        agent=agent,
        emit_text=text_parts.append,
        emit_status=lambda status: None,
        emit_thinking=lambda thinking: None,
    )

    assert message is None
    assert text_parts == ["你", "好"]
    assert agent.calls[0]["stream_mode"] == ["updates", "messages"]


def test_handle_line_streaming_emits_thinking_when_current_model_thinking_is_enabled(tmp_path):
    settings = AppSettings(run_dir=tmp_path)
    manager = ModelManager(settings)
    manager.switch_model("deepseek.deepseek-v4-flash")
    manager.set_thinking_enabled(True)
    runtime = RuntimeState()
    policy = PermissionPolicy()
    agent = StreamingAgent(
        [
            (
                "messages",
                (
                    AIMessageChunk(
                        content="答案",
                        additional_kwargs={"reasoning_content": "先思考"},
                    ),
                    {"langgraph_node": "model"},
                ),
            )
        ]
    )
    thinking_parts = []

    message = handle_line_streaming(
        "问题",
        runtime,
        settings,
        policy,
        agent=agent,
        emit_text=lambda text: None,
        emit_status=lambda status: None,
        emit_thinking=thinking_parts.append,
    )

    assert message is None
    assert thinking_parts == ["先思考"]


def test_handle_line_streaming_handles_tuple_interrupt_and_resumes(tmp_path):
    interrupt = type(
        "Interrupt",
        (),
        {
            "value": {
                "action_requests": [
                    {
                        "name": "web_search",
                        "args": {"query": "test"},
                    }
                ]
            }
        },
    )()
    agent = ResumableStreamingAgent(
        first_chunks=[
            (
                "messages",
                (
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "web_search",
                                "args": {"query": "test"},
                                "id": "call_web",
                            }
                        ],
                    ),
                    {"langgraph_node": "model"},
                ),
            ),
            ("updates", {"__interrupt__": (interrupt,)}),
        ],
        resumed_chunks=[
            (
                "messages",
                (
                    AIMessageChunk(content="完成"),
                    {"langgraph_node": "model"},
                ),
            )
        ],
    )
    approvals = []
    text_parts = []

    message = handle_line_streaming(
        "搜索 test",
        RuntimeState(),
        AppSettings(run_dir=tmp_path),
        PermissionPolicy(),
        agent=agent,
        approval_callback=lambda requests: approvals.append(requests) or [{"type": "approve"}],
        emit_text=text_parts.append,
        emit_status=lambda status: None,
        emit_thinking=lambda thinking: None,
    )

    assert message is None
    assert approvals == [[{"name": "web_search", "args": {"query": "test"}}]]
    assert text_parts == ["完成"]
    assert len(agent.calls) == 2


def test_handle_line_streaming_resumes_multiple_tool_interrupts(tmp_path):
    first_interrupt = type(
        "Interrupt",
        (),
        {
            "value": {
                "action_requests": [
                    {
                        "name": "web_search",
                        "args": {"query": "first"},
                    }
                ]
            }
        },
    )()
    second_interrupt = type(
        "Interrupt",
        (),
        {
            "value": {
                "action_requests": [
                    {
                        "name": "web_search",
                        "args": {"query": "second"},
                    }
                ]
            }
        },
    )()
    agent = ResumableStreamingAgent(
        first_chunks=[("updates", {"__interrupt__": (first_interrupt,)})],
        resumed_chunks=[("updates", {"__interrupt__": (second_interrupt,)})],
    )
    agent.streams.append(
        [
            (
                "messages",
                (
                    AIMessageChunk(content="完成"),
                    {"langgraph_node": "model"},
                ),
            )
        ]
    )
    approvals = []
    text_parts = []

    message = handle_line_streaming(
        "需要多次搜索",
        RuntimeState(),
        AppSettings(run_dir=tmp_path),
        PermissionPolicy(),
        agent=agent,
        approval_callback=lambda requests: approvals.append(requests) or [{"type": "approve"}],
        emit_text=text_parts.append,
        emit_status=lambda status: None,
        emit_thinking=lambda thinking: None,
    )

    assert message is None
    assert approvals == [
        [{"name": "web_search", "args": {"query": "first"}}],
        [{"name": "web_search", "args": {"query": "second"}}],
    ]
    assert text_parts == ["完成"]
    assert len(agent.calls) == 3


def test_authorization_choices_map_to_permanent_once_and_reject(tmp_path):
    settings = AppSettings(run_dir=tmp_path)
    requests = [
        {"name": "web_search", "args": {"query": "a"}},
        {"name": "demo_network_tool", "args": {"query": "b"}},
        {"name": "demo_write_tool", "args": {"item_id": "c"}},
    ]

    result = resolve_authorization_choices(requests, ["1", "2", "3"], settings)

    assert result.decisions == [
        {"type": "approve"},
        {"type": "approve"},
        {"type": "reject"},
    ]
    assert result.permanent_grants == ["web_search"]
    assert settings.permission_overrides["web_search"] == "allow"
    assert "demo_network_tool" not in settings.permission_overrides


def test_agent_output_adds_xiaoxu_prefix():
    assert agent_output("你好") == "xiaoxu：你好"


def test_print_command_message_adds_separator_before_output():
    output = StringIO()
    test_console = Console(file=output, force_terminal=False, color_system=None, width=100)

    print_command_message("Available commands", test_console)

    rendered = output.getvalue()
    assert rendered.splitlines() == [
        COMMAND_OUTPUT_SEPARATOR,
        "xiaoxu：Available commands",
    ]


def test_natural_exit_intent_detection_is_conservative():
    assert is_natural_exit_intent("exit") is True
    assert is_natural_exit_intent("quit") is True
    assert is_natural_exit_intent("结束对话") is True
    assert is_natural_exit_intent("请结束本次会话") is True
    assert is_natural_exit_intent("如何使用 exit 命令？") is False
    assert is_natural_exit_intent("不要退出") is False


def test_natural_exit_intent_stops_only_after_confirmation():
    runtime = RuntimeState()

    message = handle_natural_exit_intent("exit", runtime, lambda: True)

    assert message == "Stopping private agent shell."
    assert runtime.status.value == "stopping"


def test_natural_exit_intent_can_be_cancelled():
    runtime = RuntimeState()

    message = handle_natural_exit_intent("结束对话", runtime, lambda: False)

    assert message == "已取消结束本次会话。"
    assert runtime.status.value == "idle"


def test_non_exit_intent_returns_none():
    runtime = RuntimeState()

    assert handle_natural_exit_intent("你好", runtime, lambda: True) is None
    assert runtime.status.value == "idle"


def test_streaming_console_output_leaves_only_thinking_done_and_answer():
    output = StringIO()
    test_console = Console(file=output, force_terminal=False, color_system=None, width=100)
    stream_output = StreamingConsoleOutput(test_console, use_live=False)

    stream_output.emit_thinking("先分析")
    stream_output.emit_thinking("再判断")
    stream_output.emit_text("答案")
    stream_output.finish()

    rendered = output.getvalue()
    assert "xiaoxu：思考已完成。" in rendered
    assert "[web_search：None, knowledge_search:None]" in rendered
    assert "答案" in rendered
    assert "先分析" not in rendered
    assert "再判断" not in rendered


def test_streaming_console_output_finishes_thinking_before_status():
    output = StringIO()
    test_console = Console(file=output, force_terminal=False, color_system=None, width=100)
    stream_output = StreamingConsoleOutput(test_console, use_live=False)

    stream_output.emit_thinking("需要工具")
    stream_output.emit_status("正在调用工具：web_search")
    stream_output.finish()

    rendered = output.getvalue()
    assert "需要工具" not in rendered
    assert rendered.index("xiaoxu：思考已完成。") < rendered.index(
        "xiaoxu：正在调用工具：web_search"
    )


def test_streaming_console_output_shows_web_search_titles_and_urls_before_answer():
    output = StringIO()
    test_console = Console(file=output, force_terminal=False, color_system=None, width=100)
    stream_output = StreamingConsoleOutput(test_console, use_live=False)

    stream_output.emit_tool_result(
        "web_search",
        "\n\n".join(
            [
                "1. Example\n   URL: https://example.com\n   Example summary",
                "2. Docs\n   URL: https://docs.example.com\n   Docs summary",
            ]
        ),
    )
    stream_output.emit_text("答案")
    stream_output.finish()

    rendered = output.getvalue()
    assert "xiaoxu：web_search 搜索网址：" in rendered
    assert "1. Example" in rendered
    assert "URL: https://example.com" in rendered
    assert "2. Docs" in rendered
    assert "URL: https://docs.example.com" in rendered
    assert "[web_search：SearXNG, knowledge_search:None]" in rendered
    assert "答案" in rendered
    assert "Example summary" not in rendered
    assert "Docs summary" not in rendered


def test_streaming_console_output_shows_only_rag_sources_not_chunk_bodies():
    output = StringIO()
    test_console = Console(file=output, force_terminal=False, color_system=None, width=100)
    stream_output = StreamingConsoleOutput(test_console, use_live=False)
    long_chunk = "不应直接输出的知识库原文" * 200

    stream_output.emit_tool_result(
        "search_knowledge",
        (
            "以下内容来自知识库：\n"
            "[来源 1] example.pdf（PDF 第 23 页）\n"
            f"chunk_id=abc\n{long_chunk}"
        ),
    )
    stream_output.emit_text("归纳后的答案。[来源 1]")
    stream_output.finish()

    rendered = output.getvalue()
    assert "xiaoxu：search_knowledge 来源：" in rendered
    assert "[来源 1] example.pdf（PDF 第 23 页）" in rendered
    assert "归纳后的答案。[来源 1]" in rendered
    assert "不应直接输出的知识库原文" not in rendered
    assert "chunk_id=abc" not in rendered


def test_streaming_console_output_handles_multiple_thinking_segments_without_leaking():
    output = StringIO()
    test_console = Console(file=output, force_terminal=False, color_system=None, width=100)
    stream_output = StreamingConsoleOutput(test_console, use_live=False)

    stream_output.emit_thinking("工具前思考")
    stream_output.emit_status("正在调用工具：web_search")
    stream_output.emit_tool_result("web_search", "搜索结果内容")
    stream_output.emit_thinking("工具后思考")
    stream_output.emit_text("最终答案")
    stream_output.finish()

    rendered = output.getvalue()
    assert rendered.count("xiaoxu：思考已完成。") == 1
    assert "xiaoxu：web_search 搜索网址：" in rendered
    assert "[web_search：SearXNG, knowledge_search:None]" in rendered
    assert "最终答案" in rendered
    assert "工具前思考" not in rendered
    assert "工具后思考" not in rendered
    assert "搜索结果内容" not in rendered


def test_streaming_console_output_restarts_answer_prefix_after_tool_output():
    output = StringIO()
    test_console = Console(file=output, force_terminal=False, color_system=None, width=100)
    stream_output = StreamingConsoleOutput(test_console, use_live=False)

    stream_output.emit_text("我来搜索。")
    stream_output.emit_status("正在调用工具：web_search")
    stream_output.emit_tool_result("web_search", "搜索结果内容")
    stream_output.emit_text("最终答案")
    stream_output.finish()

    rendered = output.getvalue()
    assert "xiaoxu：我来搜索。" not in rendered
    assert "[web_search：SearXNG, knowledge_search:None]" in rendered
    assert "最终答案" in rendered
    assert "搜索结果内容" not in rendered


def test_streaming_console_output_uses_actual_searxng_backend_in_final_header():
    output = StringIO()
    test_console = Console(file=output, force_terminal=False, color_system=None, width=100)
    stream_output = StreamingConsoleOutput(test_console, use_live=False)

    stream_output.emit_text(
        "[web_search:Tavily, knowledge_search:None]\n我来帮您搜索。"
    )
    stream_output.emit_status("正在调用工具：web_search")
    stream_output.emit_tool_result(
        "web_search",
        (
            "1. Example\n"
            "   URL: https://example.com\n\n"
            "<!-- tool_usage:web_search=SearXNG -->"
        ),
    )
    stream_output.emit_text(
        "[web_search:Tavily, knowledge_search:None]\n最终答案"
    )
    stream_output.finish()

    rendered = output.getvalue()
    assert rendered.count("[web_search：SearXNG, knowledge_search:None]") == 1
    assert "[web_search:Tavily, knowledge_search:None]" not in rendered
    assert "我来帮您搜索。" not in rendered
    assert "最终答案" in rendered


def test_streaming_console_output_uses_none_when_no_search_tool_was_called():
    output = StringIO()
    test_console = Console(file=output, force_terminal=False, color_system=None, width=100)
    stream_output = StreamingConsoleOutput(test_console, use_live=False)

    stream_output.emit_text(
        "[web_search:Tavily, knowledge_search:Milvus]\n直接回答"
    )
    stream_output.finish()

    rendered = output.getvalue()
    assert "[web_search：None, knowledge_search:None]" in rendered
    assert "[web_search:Tavily, knowledge_search:Milvus]" not in rendered


def test_streaming_console_output_reports_tavily_only_from_actual_tool_marker():
    output = StringIO()
    test_console = Console(file=output, force_terminal=False, color_system=None, width=100)
    stream_output = StreamingConsoleOutput(test_console, use_live=False)

    stream_output.emit_tool_result(
        "web_search",
        (
            "1. Fallback result\n"
            "   URL: https://example.com/fallback\n\n"
            "<!-- tool_usage:web_search=Tavily -->"
        ),
    )
    stream_output.emit_text(
        "[web_search:SearXNG, knowledge_search:None]\n备用搜索结果"
    )
    stream_output.finish()

    rendered = output.getvalue()
    assert "[web_search：Tavily, knowledge_search:None]" in rendered
    assert "[web_search:SearXNG, knowledge_search:None]" not in rendered

