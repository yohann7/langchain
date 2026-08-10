import json

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langgraph.checkpoint.memory import InMemorySaver

from private_agent.agent_factory import (
    SYSTEM_PROMPT,
    _normalize_knowledge_bases,
    build_tools,
    create_resources,
    create_private_agent,
    init_model,
)
from private_agent.config import AppSettings
from private_agent.commands import handle_command
from private_agent.knowledge.schemas import KnowledgeStatusResponse
from private_agent.runtime import RuntimeState
from private_agent.security import PermissionDecision, PermissionPolicy, RiskLevel


class ToolBindableFakeChatModel(FakeListChatModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self


def test_create_private_agent_exposes_only_supported_cli_tools(tmp_path):
    settings = AppSettings(
        run_dir=tmp_path,
        enable_summarization_middleware=False,
    )
    runtime = RuntimeState()
    policy = PermissionPolicy()
    model = ToolBindableFakeChatModel(responses=["你好，我是 V1 Agent"])

    agent, resources = create_private_agent(settings, policy, runtime, model=model)

    assert agent is not None
    names = resources.registry.names()
    assert names == [
        "activate_skill",
        "forget_memory",
        "get_knowledge_status",
        "list_memories",
        "read_skill_resource",
        "remember_memory",
        "search_knowledge",
        "search_memories",
        "update_memory",
        "web_search",
    ]
    assert resources.memory is not None
    assert resources.knowledge is not None


def test_create_resources_scans_skill_metadata_without_loading_instructions(tmp_path):
    skill_dir = tmp_path / "skills" / "knowledge-research"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: knowledge-research\n"
        "description: Search knowledge with citations.\n"
        "---\n"
        "SECRET FULL INSTRUCTIONS\n",
        encoding="utf-8",
    )
    settings = AppSettings(
        run_dir=tmp_path / "runtime",
        skills_dir=tmp_path / "skills",
        enable_summarization_middleware=False,
    )

    _agent, resources = create_private_agent(
        settings,
        PermissionPolicy(),
        RuntimeState(),
        model=ToolBindableFakeChatModel(responses=["ok"]),
    )

    metadata = resources.skills.list()
    assert [skill.name for skill in metadata] == ["knowledge-research"]
    assert not hasattr(metadata[0], "instructions")
    activated = resources.registry.get("activate_skill").func(
        name="knowledge-research"
    )
    assert "SECRET FULL INSTRUCTIONS" in activated


def test_private_agent_can_answer_with_fake_model(tmp_path):
    settings = AppSettings(
        run_dir=tmp_path,
        enable_summarization_middleware=False,
    )
    runtime = RuntimeState()
    policy = PermissionPolicy()
    model = ToolBindableFakeChatModel(responses=["你好，我是 V1 Agent"])

    agent, _resources = create_private_agent(settings, policy, runtime, model=model)
    result = agent.invoke(
        {"messages": [{"role": "user", "content": "你好"}]},
        config={"configurable": {"thread_id": "test"}},
    )

    assert result["messages"][-1].content == "你好，我是 V1 Agent"


def test_private_agent_accepts_injected_checkpointer(tmp_path):
    settings = AppSettings(
        run_dir=tmp_path,
        enable_summarization_middleware=False,
    )
    checkpointer = InMemorySaver()
    model = ToolBindableFakeChatModel(responses=["ok"])

    agent, _resources = create_private_agent(
        settings,
        PermissionPolicy(),
        RuntimeState(),
        model=model,
        checkpointer=checkpointer,
    )

    # Strict msgpack mode may return an allowlist-hardened copy during graph
    # compilation; the injected persistence strategy must still be preserved.
    assert isinstance(agent.checkpointer, InMemorySaver)


def test_clear_command_removes_real_langgraph_thread_state(tmp_path):
    settings = AppSettings(
        run_dir=tmp_path,
        enable_summarization_middleware=False,
    )
    runtime = RuntimeState(thread_id="thread-to-clear")
    policy = PermissionPolicy()
    checkpointer = InMemorySaver()
    agent, resources = create_private_agent(
        settings,
        policy,
        runtime,
        model=ToolBindableFakeChatModel(responses=["remembered"]),
        checkpointer=checkpointer,
    )
    config = {"configurable": {"thread_id": runtime.thread_id}}
    agent.invoke(
        {"messages": [{"role": "user", "content": "remember this"}]},
        config=config,
    )
    long_term = resources.memory.remember("长期记忆不应被 /clear 删除")
    assert checkpointer.get_tuple(config) is not None

    response = handle_command(
        "/clear",
        runtime,
        settings,
        policy,
        resources.registry,
        checkpointer=agent.checkpointer,
    )

    assert response.message == "Conversation cleared."
    assert checkpointer.get_tuple(config) is None
    assert resources.memory.list() == [long_term]


def test_wecom_chat_profile_exposes_only_low_risk_tools(tmp_path):
    settings = AppSettings(
        run_dir=tmp_path,
        enable_summarization_middleware=False,
    )
    model = ToolBindableFakeChatModel(responses=["ok"])

    _agent, resources = create_private_agent(
        settings,
        PermissionPolicy(),
        RuntimeState(),
        model=model,
        tool_profile="wecom_chat",
    )

    assert resources.registry.names() == [
        "activate_skill",
        "forget_memory",
        "get_knowledge_status",
        "list_memories",
        "read_skill_resource",
        "remember_memory",
        "search_knowledge",
        "search_memories",
        "update_memory",
    ]


def test_web_search_requires_approval_by_default(tmp_path):
    settings = AppSettings(run_dir=tmp_path, enable_summarization_middleware=False)
    runtime = RuntimeState()
    policy = PermissionPolicy()
    model = ToolBindableFakeChatModel(responses=["ok"])

    _agent, resources = create_private_agent(settings, policy, runtime, model=model)
    permission = resources.registry.get("web_search").permission
    result = policy.decision_for(permission)

    assert result.decision == PermissionDecision.ASK


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
    tool = next(
        item
        for item in build_tools(resources)
        if item.name == "get_knowledge_status"
    )

    raw_result = tool.invoke({})
    result = json.loads(raw_result)

    assert tool.args == {}
    assert captured == {"user_id": "current-user"}
    assert result["sqlite"]["total_documents"] == 10
    assert "tool_usage:knowledge_search" not in raw_result
    permission = resources.registry.get("get_knowledge_status").permission
    assert permission.risk == RiskLevel.READ_SAFE
    assert permission.requires_approval is False
    assert permission.uses_network is True
    assert resources.runtime.search_turn_state is None


def test_system_prompt_requires_web_search_for_factual_relationship_questions():
    assert "人物、组织、概念、事件之间的关系" in SYSTEM_PROMPT
    assert "先调用 web_search" in SYSTEM_PROMPT
    assert "[web_search：<方式>, knowledge_search:<方式>]" in SYSTEM_PROMPT
    assert "未调用才填写 None" in SYSTEM_PROMPT
    assert "SearXNG失败" not in SYSTEM_PROMPT


def test_system_prompt_routes_knowledge_status_questions_to_status_tool():
    assert "get_knowledge_status" in SYSTEM_PROMPT
    assert "文档数量" in SYSTEM_PROMPT
    assert "分块数量" in SYSTEM_PROMPT


def test_system_prompt_requires_rag_search_to_converge():
    assert "不得在同一轮用相同参数重复检索" in SYSTEM_PROMPT
    assert "不得逐段复制或倾倒检索片段" in SYSTEM_PROMPT
    assert "成功但没有结果" in SYSTEM_PROMPT
    assert "remaining_queries 大于零" in SYSTEM_PROMPT
    assert "针对仍缺失的事实" in SYSTEM_PROMPT
    assert "证据足够后立即停止" in SYSTEM_PROMPT


def test_system_prompt_forbids_automatic_memory_writes():
    assert "长期记忆只允许显式操作" in SYSTEM_PROMPT
    assert "不得从普通对话、工具结果、知识库结果或推断中自动保存记忆" in SYSTEM_PROMPT
    assert "/clear 只清理当前会话 checkpoint，不会删除长期记忆" in SYSTEM_PROMPT


def test_normalize_knowledge_bases_accepts_provider_string_formats():
    assert _normalize_knowledge_bases('["personal"]') == ["personal"]
    assert _normalize_knowledge_bases("personal") == ["personal"]
    assert _normalize_knowledge_bases("personal,work") == ["personal", "work"]
    assert _normalize_knowledge_bases(["personal"]) == ["personal"]


def test_init_model_uses_model_manager_active_model(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.example")
    captured = {}

    def fake_init_openai_compatible_chat_model(**kwargs):
        captured.update(kwargs)
        return kwargs

    monkeypatch.setattr(
        "private_agent.models.manager.init_openai_compatible_chat_model",
        fake_init_openai_compatible_chat_model,
    )
    settings = AppSettings(
        run_dir=tmp_path,
        active_model="deepseek.deepseek-v4-flash",
        enable_summarization_middleware=False,
    )

    model = init_model(settings)

    assert model == captured
    assert captured["model"] == "deepseek-v4-flash"
    assert captured["api_key"] == "deepseek-key"
