from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from private_agent.streaming import stream_agent_response


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


def test_stream_agent_response_emits_model_tokens():
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
    statuses = []
    thinking_parts = []

    result = stream_agent_response(
        agent,
        {"messages": [{"role": "user", "content": "hi"}]},
        config={"configurable": {"thread_id": "test"}},
        emit_text=text_parts.append,
        emit_status=statuses.append,
        emit_thinking=thinking_parts.append,
        show_thinking=False,
    )

    assert text_parts == ["你", "好"]
    assert statuses == []
    assert thinking_parts == []
    assert result.final_text == "你好"
    assert agent.calls[0]["stream_mode"] == ["updates", "messages"]


def test_stream_agent_response_emits_tool_start_and_finish_statuses():
    agent = StreamingAgent(
        [
            (
                "messages",
                (
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "search_knowledge",
                                "args": {"query": "XiaoXu"},
                                "id": "call_1",
                            }
                        ],
                    ),
                    {"langgraph_node": "model"},
                ),
            ),
            (
                "messages",
                (
                    ToolMessage(
                        content="没有找到资料",
                        name="search_knowledge",
                        tool_call_id="call_1",
                    ),
                    {"langgraph_node": "tools"},
                ),
            ),
        ]
    )
    statuses = []

    result = stream_agent_response(
        agent,
        {"messages": [{"role": "user", "content": "1+1"}]},
        config={"configurable": {"thread_id": "test"}},
        emit_text=lambda text: None,
        emit_status=statuses.append,
        emit_thinking=lambda text: None,
        show_thinking=False,
    )

    assert statuses == [
        "正在调用工具：search_knowledge",
        "工具调用完成：search_knowledge",
    ]
    assert result.final_text == ""


def test_stream_agent_response_emits_web_search_tool_result():
    agent = StreamingAgent(
        [
            (
                "messages",
                (
                    ToolMessage(
                        content="1. Example\n   URL: https://example.com",
                        name="web_search",
                        tool_call_id="call_web",
                    ),
                    {"langgraph_node": "tools"},
                ),
            ),
        ]
    )
    tool_results = []

    stream_agent_response(
        agent,
        {"messages": [{"role": "user", "content": "search"}]},
        config={"configurable": {"thread_id": "test"}},
        emit_text=lambda text: None,
        emit_status=lambda status: None,
        emit_thinking=lambda text: None,
        emit_tool_result=lambda name, content: tool_results.append((name, content)),
        show_thinking=False,
    )

    assert tool_results == [
        ("web_search", "1. Example\n   URL: https://example.com"),
    ]


def test_stream_agent_response_emits_search_knowledge_tool_result():
    agent = StreamingAgent(
        [
            (
                "messages",
                (
                    ToolMessage(
                        content="[来源 1] example.pdf（PDF 第 2 页）\n知识内容",
                        tool_call_id="rag-1",
                        name="search_knowledge",
                    ),
                    {"langgraph_node": "tools"},
                ),
            )
        ]
    )
    tool_results = []

    stream_agent_response(
        agent,
        {"messages": [{"role": "user", "content": "search knowledge"}]},
        config={"configurable": {"thread_id": "test"}},
        emit_text=lambda text: None,
        emit_status=lambda status: None,
        emit_thinking=lambda text: None,
        emit_tool_result=lambda name, content: tool_results.append((name, content)),
        show_thinking=False,
    )

    assert tool_results == [
        ("search_knowledge", "[来源 1] example.pdf（PDF 第 2 页）\n知识内容"),
    ]


def test_stream_agent_response_emits_thinking_only_when_enabled():
    chunks = [
        (
            "messages",
            (
                AIMessageChunk(
                    content="答案",
                    additional_kwargs={"reasoning_content": "先分析问题"},
                ),
                {"langgraph_node": "model"},
            ),
        )
    ]
    hidden_thinking = []
    visible_thinking = []

    stream_agent_response(
        StreamingAgent(chunks),
        {"messages": [{"role": "user", "content": "问题"}]},
        config={"configurable": {"thread_id": "hidden"}},
        emit_text=lambda text: None,
        emit_status=lambda status: None,
        emit_thinking=hidden_thinking.append,
        show_thinking=False,
    )
    stream_agent_response(
        StreamingAgent(chunks),
        {"messages": [{"role": "user", "content": "问题"}]},
        config={"configurable": {"thread_id": "visible"}},
        emit_text=lambda text: None,
        emit_status=lambda status: None,
        emit_thinking=visible_thinking.append,
        show_thinking=True,
    )

    assert hidden_thinking == []
    assert visible_thinking == ["先分析问题"]


def test_stream_agent_response_extracts_reasoning_blocks_and_content_blocks():
    content_block_message = AIMessageChunk(
        content=[
            {"type": "reasoning_delta", "reasoning": "先判断"},
            {"type": "text", "text": "答案"},
        ]
    )
    content_blocks_message = AIMessageChunk(
        content="",
        content_blocks=[
            {"type": "reasoning", "reasoning": "再验证"},
        ],
    )
    agent = StreamingAgent(
        [
            ("messages", (content_block_message, {"langgraph_node": "model"})),
            ("messages", (content_blocks_message, {"langgraph_node": "model"})),
        ]
    )
    thinking_parts = []
    text_parts = []

    result = stream_agent_response(
        agent,
        {"messages": [{"role": "user", "content": "问题"}]},
        config={"configurable": {"thread_id": "test"}},
        emit_text=text_parts.append,
        emit_status=lambda status: None,
        emit_thinking=thinking_parts.append,
        show_thinking=True,
    )

    assert thinking_parts == ["先判断", "再验证"]
    assert text_parts == ["答案"]
    assert result.final_text == "答案"


def test_stream_agent_response_extracts_nested_reasoning_blocks():
    message = AIMessageChunk(
        content="",
        content_blocks=[
            {
                "type": "reasoning",
                "extras": {
                    "reasoning_content": "嵌套思考",
                },
            },
        ],
    )
    thinking_parts = []

    stream_agent_response(
        StreamingAgent([("messages", (message, {"langgraph_node": "model"}))]),
        {"messages": [{"role": "user", "content": "问题"}]},
        config={"configurable": {"thread_id": "test"}},
        emit_text=lambda text: None,
        emit_status=lambda status: None,
        emit_thinking=thinking_parts.append,
        show_thinking=True,
    )

    assert thinking_parts == ["嵌套思考"]


def test_stream_agent_response_deduplicates_cumulative_thinking_chunks():
    agent = StreamingAgent(
        [
            (
                "messages",
                (
                    AIMessageChunk(
                        content="",
                        additional_kwargs={"reasoning_content": "先"},
                    ),
                    {"langgraph_node": "model"},
                ),
            ),
            (
                "messages",
                (
                    AIMessageChunk(
                        content="",
                        additional_kwargs={"reasoning_content": "先分析"},
                    ),
                    {"langgraph_node": "model"},
                ),
            ),
            (
                "messages",
                (
                    AIMessageChunk(content="答案"),
                    {"langgraph_node": "model"},
                ),
            ),
        ]
    )
    thinking_parts = []
    done_events = []
    text_parts = []

    result = stream_agent_response(
        agent,
        {"messages": [{"role": "user", "content": "问题"}]},
        config={"configurable": {"thread_id": "test"}},
        emit_text=text_parts.append,
        emit_status=lambda status: None,
        emit_thinking=thinking_parts.append,
        emit_thinking_done=lambda: done_events.append("done"),
        show_thinking=True,
    )

    assert thinking_parts == ["先", "分析"]
    assert done_events == ["done"]
    assert text_parts == ["答案"]
    assert result.final_text == "答案"


def test_stream_agent_response_finishes_thinking_before_tool_status():
    agent = StreamingAgent(
        [
            (
                "messages",
                (
                    AIMessageChunk(
                        content="",
                        additional_kwargs={"reasoning_content": "需要工具"},
                    ),
                    {"langgraph_node": "model"},
                ),
            ),
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
        ]
    )
    events = []

    stream_agent_response(
        agent,
        {"messages": [{"role": "user", "content": "问题"}]},
        config={"configurable": {"thread_id": "test"}},
        emit_text=lambda text: events.append(("text", text)),
        emit_status=lambda status: events.append(("status", status)),
        emit_thinking=lambda thinking: events.append(("thinking", thinking)),
        emit_thinking_done=lambda: events.append(("done", "")),
        show_thinking=True,
    )

    assert events == [
        ("thinking", "需要工具"),
        ("done", ""),
        ("status", "正在调用工具：web_search"),
    ]


def test_stream_agent_response_flattens_tuple_interrupts():
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
    agent = StreamingAgent([("updates", {"__interrupt__": (interrupt,)})])

    result = stream_agent_response(
        agent,
        {"messages": [{"role": "user", "content": "search"}]},
        config={"configurable": {"thread_id": "test"}},
        emit_text=lambda text: None,
        emit_status=lambda status: None,
        emit_thinking=lambda thinking: None,
        show_thinking=False,
    )

    assert result.interrupts == [interrupt]
