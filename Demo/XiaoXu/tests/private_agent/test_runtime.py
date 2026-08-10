from private_agent.runtime import RuntimeState, RuntimeStatus
from private_agent.search import SearchTurnState


def test_runtime_default_status_is_idle():
    runtime = RuntimeState()

    assert runtime.status == RuntimeStatus.IDLE
    assert runtime.snapshot()["status"] == "idle"
    assert runtime.snapshot()["is_busy"] is False


def test_exit_state_is_stopping():
    runtime = RuntimeState()

    runtime.stop()

    assert runtime.status == RuntimeStatus.STOPPING


def test_usage_counters_track_local_counts():
    runtime = RuntimeState()

    runtime.record_model_call(input_tokens=10, output_tokens=5)
    runtime.record_tool_call()

    usage = runtime.snapshot()["usage"]
    assert usage["model_calls"] == 1
    assert usage["tool_calls"] == 1
    assert usage["total_tokens"] == 15


def test_search_turn_state_is_explicitly_replaced_between_user_turns():
    runtime = RuntimeState()

    first = runtime.begin_search_turn()
    second = runtime.begin_search_turn()

    assert isinstance(first, SearchTurnState)
    assert second is runtime.search_turn_state
    assert second is not first
    assert second.knowledge_query_count == 0
    assert second.web_query_count == 0


def test_search_state_survives_approval_resume_until_turn_is_explicitly_ended():
    runtime = RuntimeState()
    state = runtime.begin_search_turn()

    runtime.start_task()
    runtime.wait_for_approval()
    runtime.start_task()
    runtime.finish_task()

    assert runtime.search_turn_state is state
    runtime.end_search_turn()
    assert runtime.search_turn_state is None
