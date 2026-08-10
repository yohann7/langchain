"""LangChain agent construction for V1."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import (
    HumanInTheLoopMiddleware,
    ModelCallLimitMiddleware,
    PIIMiddleware,
    SummarizationMiddleware,
    ToolCallLimitMiddleware,
    ToolRetryMiddleware,
)
from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import tool

from private_agent.agent.governance import (
    ModelUsageMiddleware,
    ToolExecutionGateway,
    ToolExecutionMiddleware,
)
from private_agent.agent.middleware import SearchPolicyMiddleware
from private_agent.agent.profiles import TOOL_PROFILE_ALLOWLISTS, ToolProfile
from private_agent.agent.prompts import SYSTEM_PROMPT, build_system_prompt
from private_agent.agent.usage_callback import attach_summary_usage_callback
from private_agent.config import AppSettings
from private_agent.core.capabilities import CapabilityPolicy
from private_agent.core.identity import current_conversation_type, current_user_id
from private_agent.knowledge.client import KnowledgeClient
from private_agent.knowledge.formatter import format_prompt_text
from private_agent.memory import MemoryRecord, MemoryService
from private_agent.models import ModelManager
from private_agent.persistence.audit import DatabaseAuditLogger
from private_agent.persistence.checkpoint import create_sqlite_checkpointer
from private_agent.persistence.database import XiaoXuDatabase
from private_agent.persistence.memories import MemoryStore
from private_agent.persistence.usage import DailyUsageStore
from private_agent.runtime import RuntimeState, current_runtime_state
from private_agent.search import SearchBatch, SearchCoordinator, SearchKind
from private_agent.search.context import (
    current_knowledge_search_config,
    current_prepared_search,
    current_web_search_config,
)
from private_agent.security import PermissionPolicy, RiskLevel, ToolPermission
from private_agent.skills.loader import SkillLoader
from private_agent.skills.registry import SkillRegistry
from private_agent.tools.knowledge.search_knowledge import search_knowledge
from private_agent.tools.knowledge.get_knowledge_status import (
    get_knowledge_status as get_knowledge_status_result,
)
from private_agent.tools.registry import ToolRegistry
from private_agent.tool_usage import append_tool_usage_marker
from private_agent.tools.search_tools import (
    format_web_search_result,
    web_search_result,
)

@dataclass
class AgentResources:
    """Resources shared by V1 tools and tests."""

    settings: AppSettings
    policy: PermissionPolicy
    runtime: RuntimeState
    audit: DatabaseAuditLogger
    knowledge: KnowledgeClient
    capabilities: CapabilityPolicy
    database: XiaoXuDatabase
    skill_loader: SkillLoader
    skills: SkillRegistry
    registry: ToolRegistry
    usage: DailyUsageStore
    gateway: ToolExecutionGateway
    memory: MemoryService


def create_resources(
    settings: AppSettings,
    policy: PermissionPolicy,
    runtime: RuntimeState,
) -> AgentResources:
    """Create stores and a registry for V1."""

    run_dir = settings.run_dir.expanduser().resolve(strict=False)
    database = XiaoXuDatabase(
        settings.resolve_in_run_dir(settings.sqlite_database_path)
    )
    user_id_provider = lambda: current_user_id(settings.user_id)
    audit = DatabaseAuditLogger(database, user_id_provider)
    knowledge = KnowledgeClient(
        base_url=settings.knowledge_api_url,
        token=settings.knowledge_api_token or "",
    )
    skill_loader = SkillLoader(
        settings.skills_dir,
        max_frontmatter_bytes=settings.skill_max_frontmatter_bytes,
        max_instructions_bytes=settings.skill_max_instructions_bytes,
        max_resource_bytes=settings.skill_max_resource_bytes,
    )
    skills = SkillRegistry(skill_loader.scan())
    registry = ToolRegistry()
    usage = DailyUsageStore(database)
    gateway = ToolExecutionGateway(
        registry=registry,
        policy=policy,
        runtime=runtime,
        audit=audit,
        usage=usage,
        default_user_id=settings.user_id,
    )
    memory = MemoryService(
        store=MemoryStore(database),
        user_id_provider=user_id_provider,
        conversation_type_provider=current_conversation_type,
        thread_id_provider=lambda: current_runtime_state(runtime).thread_id,
        audit=audit,
        max_content_bytes=settings.memory_max_content_bytes,
        max_items_per_user=settings.memory_max_items_per_user,
        max_results=settings.memory_max_results,
        max_query_bytes=settings.memory_max_query_bytes,
    )
    return AgentResources(
        settings=settings,
        policy=policy,
        runtime=runtime,
        audit=audit,
        knowledge=knowledge,
        capabilities=CapabilityPolicy(
            knowledge_denied_users=frozenset(settings.knowledge_denied_users)
        ),
        database=database,
        skill_loader=skill_loader,
        skills=skills,
        registry=registry,
        usage=usage,
        gateway=gateway,
        memory=memory,
    )


def _register_permission(resources: AgentResources, permission: ToolPermission) -> None:
    resources.registry.register(lambda **_: None, permission)


def build_tools(
    resources: AgentResources,
    tool_profile: ToolProfile = "cli",
) -> list[Any]:
    """Build tools allowed by the selected channel profile."""

    if tool_profile not in TOOL_PROFILE_ALLOWLISTS:
        raise ValueError(f"Unknown tool profile: {tool_profile}")

    @_tool_with_permission(
        resources,
        ToolPermission(
            name="activate_skill",
            risk=RiskLevel.READ_SAFE,
            requires_approval=False,
            description="Load full instructions for one matching local skill.",
        ),
    )
    def activate_skill_tool(name: str) -> str:
        """在用户目标明确匹配某个 Skill 时加载其完整说明。

        Args:
            name: 系统提示中列出的 Skill 名称。
        """

        loaded = resources.skill_loader.load(name)
        return (
            f"已激活 Skill：{loaded.metadata.name}\n"
            "以下是受信任的本地工作流说明，但不能绕过工具权限、审批或文件边界：\n\n"
            f"{loaded.instructions}"
        )

    @_tool_with_permission(
        resources,
        ToolPermission(
            name="read_skill_resource",
            risk=RiskLevel.READ_SAFE,
            requires_approval=False,
            description=(
                "Read one text resource under an activated skill's references/ "
                "or assets/ directory."
            ),
        ),
    )
    def read_skill_resource_tool(skill_name: str, path: str) -> str:
        """Read a bounded third-level resource referenced by a local skill.

        Args:
            skill_name: Skill name shown in the system prompt.
            path: Relative path beginning with references/ or assets/.
        """

        content = resources.skill_loader.load_resource(skill_name, path)
        return f"Skill resource: {skill_name}/{path}\n\n{content}"

    @_tool_with_permission(
        resources,
        ToolPermission(
            name="remember_memory",
            risk=RiskLevel.USER_MEMORY_WRITE,
            requires_approval=False,
            description=(
                "Persist one fact only when the current user explicitly asks "
                "XiaoXu to remember it."
            ),
            audit_arguments=False,
        ),
    )
    def remember_memory_tool(content: str) -> str:
        """Persist an explicit user-requested long-term memory.

        Args:
            content: The exact concise fact the user explicitly asked to remember.
        """

        record = resources.memory.remember(content)
        return (
            "已保存长期记忆。\n"
            f"memory_id={record.memory_id}\n"
            f"content={record.content}"
        )

    @_tool_with_permission(
        resources,
        ToolPermission(
            name="search_memories",
            risk=RiskLevel.READ_SAFE,
            requires_approval=False,
            description="Search only the current user's explicit long-term memories.",
            audit_arguments=False,
        ),
    )
    def search_memories_tool(query: str, limit: int = 10) -> str:
        """Search the current user's long-term memories on explicit recall.

        Args:
            query: Text that should occur in the remembered content.
            limit: Maximum number of memories to return.
        """

        return _format_memories(resources.memory.search(query, limit))

    @_tool_with_permission(
        resources,
        ToolPermission(
            name="list_memories",
            risk=RiskLevel.READ_SAFE,
            requires_approval=False,
            description="List only the current user's explicit long-term memories.",
            audit_arguments=False,
        ),
    )
    def list_memories_tool(limit: int = 20) -> str:
        """List the current user's recent long-term memories.

        Args:
            limit: Maximum number of memories to return.
        """

        return _format_memories(resources.memory.list(limit))

    @_tool_with_permission(
        resources,
        ToolPermission(
            name="update_memory",
            risk=RiskLevel.USER_MEMORY_WRITE,
            requires_approval=False,
            description=(
                "Update one current-user memory only after an explicit user request."
            ),
            audit_arguments=False,
        ),
    )
    def update_memory_tool(memory_id: str, content: str) -> str:
        """Update one explicit long-term memory owned by the current user.

        Args:
            memory_id: The memory id previously returned by a memory tool.
            content: The replacement content explicitly requested by the user.
        """

        record = resources.memory.update(memory_id, content)
        return (
            "已更新长期记忆。\n"
            f"memory_id={record.memory_id}\n"
            f"content={record.content}"
        )

    @_tool_with_permission(
        resources,
        ToolPermission(
            name="forget_memory",
            risk=RiskLevel.USER_MEMORY_WRITE,
            requires_approval=False,
            description=(
                "Permanently delete one current-user memory only after an "
                "explicit user request."
            ),
            audit_arguments=False,
        ),
    )
    def forget_memory_tool(memory_id: str) -> str:
        """Delete one explicit long-term memory owned by the current user.

        Args:
            memory_id: The memory id previously returned by a memory tool.
        """

        resources.memory.forget(memory_id)
        return f"已删除长期记忆。memory_id={memory_id}"

    @_tool_with_permission(
        resources,
        ToolPermission(
            name="web_search",
            risk=RiskLevel.NETWORK_READ,
            requires_approval=True,
            uses_network=True,
            description=(
                "Search one focused web query under the active configuration. Only add a "
                "different query for a specifically missing fact; stop when evidence "
                "is sufficient and remaining_queries permits it. SearXNG backend "
                "attempts remain one logical query."
            ),
        ),
    )
    def web_search_tool(query: str) -> str:
        """联网搜索资料。

        Args:
            query: 一个简洁、具体的查询；不要用无意义改写绕过重复限制。
        """

        prepared = current_prepared_search(SearchKind.WEB)
        config = current_web_search_config()
        coordinator = _search_coordinator(resources)
        outcome = web_search_result(
            prepared.query,
            resources.settings.tavily_api_key_env,
            config=config,
            searxng_url=resources.settings.searxng_url,
        )
        if not outcome.available:
            coordinator.fail(prepared, outcome.error_code or "backend unavailable")
            return append_tool_usage_marker(
                json.dumps(
                    {
                        "error": {
                            "code": "SEARCH_BACKEND_UNAVAILABLE",
                            "message": outcome.message or "Web search is unavailable.",
                        },
                        "query_index": prepared.query_index,
                        "new_results": 0,
                        "total_unique_results": len(
                            coordinator.state.seen_web
                        ),
                        "duplicate_results": 0,
                        "updated_results": 0,
                        "remaining_queries": 0,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "web_search",
                outcome.backend,
            )

        batch = coordinator.accept_web(prepared, outcome.results)
        sections: list[str] = []
        if batch.items or outcome.answer:
            result: dict[str, Any] = {"results": batch.items}
            if outcome.answer:
                result["answer"] = outcome.answer
            sections.append(format_web_search_result(result))
        else:
            sections.append("本次网页查询没有返回新的唯一证据。")
        sections.extend(_format_source_updates(batch))
        sections.append(_format_search_progress(batch))
        return append_tool_usage_marker(
            "\n\n".join(sections),
            "web_search",
            outcome.backend,
        )

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

    @_tool_with_permission(
        resources,
        ToolPermission(
            name="search_knowledge",
            risk=RiskLevel.READ_SAFE,
            requires_approval=False,
            uses_network=True,
            description=(
                "Hybrid-search authorized local knowledge under the active configuration. "
                "Only revise an empty result when remaining_queries permits it."
            ),
        ),
    )
    def search_knowledge_tool(
        query: str,
        knowledge_bases: list[str] | str | None = None,
        limit: int | None = None,
    ) -> str:
        """使用语义向量和 BM25 混合检索本地知识库。

        Args:
            query: 一个简洁、具体的问题或关键词；额外查询必须针对缺失事实。
            knowledge_bases: 可选知识库名称或 ID 列表；省略时检索全部授权知识库。
            limit: 可选返回片段数量；省略时使用当前配置默认值，显式值不得超过当前配置上限。
        """

        prepared = current_prepared_search(SearchKind.KNOWLEDGE)
        config = current_knowledge_search_config()
        coordinator = _search_coordinator(resources)
        effective_limit = (
            config.default_results_per_query if limit is None else limit
        )
        result = search_knowledge(
            query=prepared.query,
            user_id=current_user_id(resources.settings.user_id),
            knowledge_bases=_normalize_knowledge_bases(knowledge_bases),
            limit=effective_limit,
            timeout_seconds=config.request_timeout_seconds,
            client=resources.knowledge,
            capabilities=resources.capabilities,
        )
        if result.get("error"):
            backend_error = result.get("error")
            coordinator.fail(prepared, str(backend_error))
            result["error"] = {
                "code": "SEARCH_BACKEND_UNAVAILABLE",
                "message": "Knowledge search is unavailable for this turn.",
                "backend_error": backend_error,
            }
            result["query_index"] = prepared.query_index
            result["new_results"] = 0
            result["total_unique_results"] = len(
                coordinator.state.seen_knowledge
            )
            result["duplicate_results"] = 0
            result["updated_results"] = 0
            result["remaining_queries"] = 0
            return format_prompt_text(result)

        raw_hits = result.get("hits", [])
        hits = (
            [dict(item) for item in raw_hits if isinstance(item, dict)]
            if isinstance(raw_hits, list)
            else []
        )
        batch = coordinator.accept_knowledge(prepared, hits)
        result["hits"] = batch.items
        result["sources"] = [
            {
                "doc_id": hit.get("doc_id", ""),
                "chunk_id": hit.get("chunk_id", ""),
                "document_name": hit.get("document_name", ""),
                "location": hit.get("location", ""),
                "knowledge_base": hit.get("knowledge_base", ""),
            }
            for hit in batch.items
        ]
        return f"{format_prompt_text(result)}\n\n{_format_search_progress(batch)}"

    all_tools = [
        activate_skill_tool,
        read_skill_resource_tool,
        remember_memory_tool,
        search_memories_tool,
        list_memories_tool,
        update_memory_tool,
        forget_memory_tool,
        web_search_tool,
        get_knowledge_status_tool,
        search_knowledge_tool,
    ]
    allowlist = TOOL_PROFILE_ALLOWLISTS[tool_profile]
    if allowlist is None:
        return all_tools

    for name in tuple(resources.registry.names()):
        if name not in allowlist:
            resources.registry.remove(name)
    return [registered_tool for registered_tool in all_tools if registered_tool.name in allowlist]


def _tool_with_permission(resources: AgentResources, permission: ToolPermission):
    """Register permission metadata and convert a function into a LangChain tool."""

    def decorator(func):
        resources.registry.register(func, permission)
        return tool(permission.name, parse_docstring=True)(func)

    return decorator


def _normalize_knowledge_bases(
    knowledge_bases: list[str] | str | None,
) -> list[str] | None:
    if knowledge_bases is None:
        return None
    if isinstance(knowledge_bases, list):
        return [str(value).strip() for value in knowledge_bases if str(value).strip()]

    value = knowledge_bases.strip()
    if not value:
        return None
    if value.startswith("["):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    return [item.strip() for item in value.split(",") if item.strip()]


def _format_memories(records: list[MemoryRecord]) -> str:
    if not records:
        return "当前用户没有匹配的长期记忆。"
    rows = [f"找到 {len(records)} 条当前用户的长期记忆："]
    rows.extend(
        f"- [{record.memory_id}] {record.content}"
        for record in records
    )
    return "\n".join(rows)


def _search_coordinator(resources: AgentResources) -> SearchCoordinator:
    runtime = current_runtime_state(resources.runtime)
    if runtime.search_turn_state is None:
        raise RuntimeError("search turn state is not initialized")
    return SearchCoordinator(runtime.search_turn_state)


def _format_search_progress(batch: SearchBatch) -> str:
    progress = batch.progress
    return "SEARCH_PROGRESS " + json.dumps(
        {
            "query_index": progress.query_index,
            "new_results": progress.new_results,
            "total_unique_results": progress.total_unique_results,
            "duplicate_results": progress.duplicate_results,
            "updated_results": progress.updated_results,
            "remaining_queries": progress.remaining_queries,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _format_source_updates(batch: SearchBatch) -> list[str]:
    return [
        "SOURCE_UPDATE "
        f"{item.get('source_index', '?')}: {item.get('title', 'Untitled')}\n"
        f"URL: {item.get('url', '')}\n{item.get('content', '')}"
        for item in batch.updates
    ]


def build_middleware(
    settings: AppSettings,
    resources: AgentResources,
    *,
    model: str | BaseChatModel | None = None,
) -> list[Any]:
    """Build V1 middleware stack."""

    interrupt_on = {
        permission.name: {
            "allowed_decisions": ["approve", "reject", "edit"],
            "description": permission.description or permission.name,
        }
        for permission in resources.registry.list_permissions()
        if resources.policy.decision_for(permission).decision.value == "ask"
    }
    middleware: list[Any] = [
        HumanInTheLoopMiddleware(
            interrupt_on=interrupt_on,
            description_prefix="工具执行需要审批",
        ),
        ModelCallLimitMiddleware(run_limit=settings.max_model_calls_per_run, exit_behavior="end"),
        SearchPolicyMiddleware(
            runtime=resources.runtime,
            audit=resources.audit,
            web_config_path=settings.web_search_config_path,
            knowledge_config_path=settings.knowledge_search_config_path,
        ),
        ToolCallLimitMiddleware(run_limit=settings.max_tool_calls_per_run, exit_behavior="continue"),
        ToolRetryMiddleware(max_retries=2, on_failure="continue"),
    ]
    if settings.enable_pii_middleware:
        middleware.extend(
            [
                PIIMiddleware("email", strategy="redact", apply_to_input=True),
                PIIMiddleware("credit_card", strategy="mask", apply_to_input=True),
                PIIMiddleware("url", strategy="hash", apply_to_input=True),
                PIIMiddleware("ip", strategy="block", apply_to_input=True),
            ]
        )
    if settings.enable_summarization_middleware:
        summary_model = settings.summarization_model_name or model
        if summary_model is not None and summary_model != "not-configured":
            summarization = SummarizationMiddleware(
                model=summary_model,
                trigger=("tokens", settings.summarization_trigger_tokens),
                keep=("tokens", settings.summarization_keep_tokens),
            )
            attach_summary_usage_callback(
                summarization.model,
                resources.gateway.record_model_usage,
            )
            middleware.append(summarization)
    # Governance is intentionally innermost: HITL and limits can stop a call
    # before it is counted, while ToolRetry records every actual execution
    # attempt separately.
    middleware.extend(
        [
            ModelUsageMiddleware(resources.gateway),
            ToolExecutionMiddleware(resources.gateway),
        ]
    )
    return middleware


def init_model(
    settings: AppSettings,
    *,
    database: XiaoXuDatabase | None = None,
) -> str | BaseChatModel:
    """Initialize the configured model or return a model string."""

    model_manager = ModelManager(settings, database=database)
    if model_manager.has_active_model():
        return model_manager.build_chat_model()

    if settings.model_name == "not-configured":
        raise ValueError(
            "Model is not configured. Use /model to select a model or set PRIVATE_AGENT_MODEL_NAME."
        )
    api_key = os.getenv(settings.model_api_key_env) if settings.model_api_key_env else None
    if settings.model_provider or api_key or settings.model_base_url:
        kwargs: dict[str, Any] = {"model": settings.model_name}
        if settings.model_provider:
            kwargs["model_provider"] = settings.model_provider
        if api_key:
            kwargs["api_key"] = api_key
        if settings.model_base_url:
            kwargs["base_url"] = settings.model_base_url
        return init_chat_model(**kwargs)
    return settings.model_name


def create_private_agent(
    settings: AppSettings,
    policy: PermissionPolicy,
    runtime: RuntimeState,
    model: str | BaseChatModel | None = None,
    *,
    checkpointer: Any | None = None,
    tool_profile: ToolProfile = "cli",
):
    """Create the agent graph plus resources for one channel profile."""

    resources = create_resources(settings, policy, runtime)
    tools = build_tools(resources, tool_profile=tool_profile)
    selected_model = (
        model
        if model is not None
        else init_model(settings, database=resources.database)
    )
    selected_checkpointer = (
        checkpointer
        if checkpointer is not None
        else create_sqlite_checkpointer(resources.database)
    )
    agent = create_agent(
        model=selected_model,
        tools=tools,
        system_prompt=build_system_prompt(resources.skills.list()),
        middleware=build_middleware(settings, resources, model=selected_model),
        checkpointer=selected_checkpointer,
    )
    return agent, resources
