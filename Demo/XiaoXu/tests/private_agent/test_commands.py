from datetime import datetime, timezone
import json
import re

from private_agent.agent_factory import build_tools, create_resources
from private_agent.commands import handle_command
from private_agent.config import AppSettings
from private_agent.runtime import RuntimeState, RuntimeStatus
from private_agent.security import PermissionPolicy, RiskLevel, ToolPermission
from private_agent.tools.registry import ToolRegistry


def make_context():
    return RuntimeState(), AppSettings(), PermissionPolicy()


def test_help_returns_v0_command_list():
    runtime, settings, policy = make_context()

    response = handle_command("/help", runtime, settings, policy)

    assert response.handled is True
    assert "/status" in response.message
    assert "/tools" in response.message
    assert "/exit" in response.message


def test_status_reports_idle_runtime():
    runtime, settings, policy = make_context()

    response = handle_command("/status", runtime, settings, policy)

    assert '"status": "idle"' in response.message
    assert '"awaiting_approval": false' in response.message


def test_usage_reports_statistics_without_any_limit(tmp_path):
    settings = AppSettings(
        run_dir=tmp_path,
        enable_summarization_middleware=False,
    )
    runtime = RuntimeState()
    resources = create_resources(settings, PermissionPolicy(), runtime)
    usage_date = datetime.now(timezone.utc).date().isoformat()
    resources.usage.record(
        user_id=settings.user_id,
        model_calls=2,
        tool_calls=3,
        input_tokens=120_000,
        output_tokens=4_000,
        usage_date=usage_date,
    )

    response = handle_command(
        "/usage",
        runtime,
        settings,
        PermissionPolicy(),
        resources.registry,
    )
    payload = json.loads(response.message)

    assert payload == {
        "usage_date": usage_date,
        "usage": {
            "model_calls": 2,
            "tool_calls": 3,
            "input_tokens": 120_000,
            "output_tokens": 4_000,
            "total_tokens": 124_000,
        },
    }


def test_exit_command_stops_runtime():
    runtime, settings, policy = make_context()

    response = handle_command("/exit", runtime, settings, policy)

    assert response.should_exit is True
    assert runtime.status == RuntimeStatus.STOPPING


def test_unknown_slash_command_is_handled_locally():
    runtime, settings, policy = make_context()

    response = handle_command("/rewind", runtime, settings, policy)

    assert response.handled is True
    assert "Unknown command" in response.message


def test_natural_language_is_not_handled_by_command_parser():
    runtime, settings, policy = make_context()

    response = handle_command("hello", runtime, settings, policy)

    assert response.handled is False


class DeletingCheckpointer:
    def __init__(self):
        self.deleted = []

    def delete_thread(self, thread_id):
        self.deleted.append(thread_id)


def test_clear_deletes_checkpoint_and_compact_updates_runtime_marker():
    runtime, settings, policy = make_context()
    checkpointer = DeletingCheckpointer()

    clear_response = handle_command(
        "/clear",
        runtime,
        settings,
        policy,
        checkpointer=checkpointer,
    )
    compact_response = handle_command("/compact", runtime, settings, policy)

    assert clear_response.handled is True
    assert clear_response.message == "Conversation cleared."
    assert checkpointer.deleted == [runtime.thread_id]
    assert compact_response.handled is True
    assert runtime.clear_requests == 1
    assert runtime.compaction_requests == 1


def test_clear_fails_closed_without_deletable_checkpointer():
    runtime, settings, policy = make_context()

    response = handle_command("/clear", runtime, settings, policy)

    assert "was not cleared" in response.message
    assert runtime.clear_requests == 0


def test_tools_lists_registered_permissions():
    runtime, settings, policy = make_context()
    registry = ToolRegistry()
    registry.register(
        lambda: "ok",
        ToolPermission(
            name="demo_tool",
            risk=RiskLevel.READ_SAFE,
            requires_approval=False,
        ),
    )

    response = handle_command("/tools", runtime, settings, policy, registry)

    assert "demo_tool" in response.message
    assert "risk=read_safe" in response.message
    assert "read_files=" not in response.message
    assert "write_files=" not in response.message


def test_model_command_opens_menu_without_subcommands():
    runtime, settings, policy = make_context()

    response = handle_command("/model", runtime, settings, policy)
    subcommand_response = handle_command("/model list", runtime, settings, policy)

    assert response.handled is True
    assert response.action == "model_menu"
    assert subcommand_response.handled is True
    assert subcommand_response.action is None
    assert "请输入 /model" in subcommand_response.message


def test_thinking_command_opens_menu_without_subcommands():
    runtime, settings, policy = make_context()

    response = handle_command("/thinking", runtime, settings, policy)
    subcommand_response = handle_command("/thinking on", runtime, settings, policy)

    assert response.handled is True
    assert response.action == "thinking_menu"
    assert subcommand_response.handled is True
    assert subcommand_response.action is None
    assert "请输入 /thinking" in subcommand_response.message


def test_memory_commands_cover_explicit_lifecycle(tmp_path):
    runtime = RuntimeState(thread_id="memory-command-thread")
    settings = AppSettings(
        run_dir=tmp_path,
        enable_summarization_middleware=False,
    )
    policy = PermissionPolicy()
    resources = create_resources(settings, policy, runtime)
    build_tools(resources)

    added = handle_command(
        "/memory add 我喜欢乌龙茶",
        runtime,
        settings,
        policy,
        resources.registry,
    )
    memory_id = re.search(r"memory_id=(mem_[0-9a-f]{32})", added.message).group(1)
    listed = handle_command(
        "/memory list",
        runtime,
        settings,
        policy,
        resources.registry,
    )
    searched = handle_command(
        "/memory search 乌龙茶",
        runtime,
        settings,
        policy,
        resources.registry,
    )
    updated = handle_command(
        f"/memory update {memory_id} 我喜欢茉莉乌龙茶",
        runtime,
        settings,
        policy,
        resources.registry,
    )
    deleted = handle_command(
        f"/memory delete {memory_id}",
        runtime,
        settings,
        policy,
        resources.registry,
    )

    assert "已保存长期记忆" in added.message
    assert memory_id in listed.message
    assert "乌龙茶" in searched.message
    assert "已更新长期记忆" in updated.message
    assert "已删除长期记忆" in deleted.message


def test_memory_command_rejects_invalid_shapes(tmp_path):
    runtime = RuntimeState()
    settings = AppSettings(
        run_dir=tmp_path,
        enable_summarization_middleware=False,
    )
    policy = PermissionPolicy()
    resources = create_resources(settings, policy, runtime)
    build_tools(resources)

    assert "Usage:" in handle_command(
        "/memory add",
        runtime,
        settings,
        policy,
        resources.registry,
    ).message
    assert "must be an integer" in handle_command(
        "/memory list nope",
        runtime,
        settings,
        policy,
        resources.registry,
    ).message
    assert "Unknown /memory subcommand" in handle_command(
        "/memory export",
        runtime,
        settings,
        policy,
        resources.registry,
    ).message
