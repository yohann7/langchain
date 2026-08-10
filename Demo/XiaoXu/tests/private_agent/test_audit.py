import json

from private_agent.audit import AuditLogger, redact_text


def test_redact_text_hides_common_sensitive_values():
    text = "email a@example.com phone 15641685664 key sk-1234567890abcdef"

    redacted = redact_text(text)

    assert "a@example.com" not in redacted
    assert "15641685664" not in redacted
    assert "sk-1234567890abcdef" not in redacted
    assert "[REDACTED_EMAIL]" in redacted
    assert "[REDACTED_PHONE]" in redacted
    assert "[REDACTED_API_KEY]" in redacted


def test_audit_logger_writes_redacted_jsonl(tmp_path):
    log_path = tmp_path / "audit.jsonl"
    logger = AuditLogger(log_path)

    logger.record(
        "tool_call",
        {"recipient": "a@example.com", "api_key": "sk-1234567890abcdef"},
    )

    line = log_path.read_text(encoding="utf-8").strip()
    entry = json.loads(line)
    assert entry["event_type"] == "tool_call"
    assert entry["payload"]["recipient"] == "[REDACTED_EMAIL]"
    assert entry["payload"]["api_key"] == "[REDACTED_API_KEY]"
