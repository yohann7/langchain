from uuid import uuid4

from langchain_core.messages import HumanMessage

from private_agent.agent.usage_callback import SummaryUsageCallback


def test_failed_summary_records_estimated_input_and_error_status():
    recorded = []
    callback = SummaryUsageCallback(
        lambda input_tokens, output_tokens, **details: recorded.append(
            (input_tokens, output_tokens, details)
        )
    )
    run_id = uuid4()
    callback.on_chat_model_start(
        {},
        [[HumanMessage(content="summarize this conversation")]],
        run_id=run_id,
        metadata={"lc_source": "summarization"},
    )

    callback.on_llm_error(RuntimeError("offline"), run_id=run_id)

    assert len(recorded) == 1
    assert recorded[0][0] > 0
    assert recorded[0][1] == 0
    assert recorded[0][2] == {
        "purpose": "summarization",
        "status": "error",
        "error_type": "RuntimeError",
    }
