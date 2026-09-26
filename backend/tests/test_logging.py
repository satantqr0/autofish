import json
import logging

from app.core.logging import JsonFormatter, redact_log_text


def test_redact_log_text_masks_common_credentials():
    source = (
        "password=plain-secret token:abc123456789 Bearer header.payload.signature "
        "api_key=sk-examplecredential123 eyJhbGciOiJIUzI1NiJ9.payload.signature"
    )

    redacted = redact_log_text(source)

    for secret in (
        "plain-secret",
        "abc123456789",
        "header.payload.signature",
        "examplecredential123",
    ):
        assert secret not in redacted
    assert redacted.count("[REDACTED]") >= 4


def test_json_formatter_preserves_redacted_exception_trace():
    formatter = JsonFormatter()
    try:
        raise RuntimeError("authorization=super-secret-value")
    except RuntimeError:
        record = logging.LogRecord(
            name="autofish.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="request failed token=another-secret-value",
            args=(),
            exc_info=__import__("sys").exc_info(),
        )

    payload = json.loads(formatter.format(record))
    assert "another-secret-value" not in payload["message"]
    assert "super-secret-value" not in payload["exception"]
    assert "RuntimeError" in payload["exception"]
