import json
import logging
import re
import sys
from datetime import UTC, datetime

_NAMED_SECRET = re.compile(
    r"(?i)\b(api[_ -]?key|authorization|cookie|password|passwd|secret|token)"
    r"(\s*[:=]\s*)([^\s,;}]+)"
)
_BEARER_SECRET = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_PROVIDER_KEY = re.compile(r"\bsk-[A-Za-z0-9._-]{8,}")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")


def redact_log_text(value):
    text = str(value)
    text = _NAMED_SECRET.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", text)
    text = _BEARER_SECRET.sub("Bearer [REDACTED]", text)
    text = _PROVIDER_KEY.sub("sk-[REDACTED]", text)
    return _JWT.sub("[JWT REDACTED]", text)


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_log_text(record.getMessage()),
        }
        correlation_id = getattr(record, "correlation_id", None)
        if correlation_id:
            payload["correlation_id"] = correlation_id
        if record.exc_info:
            payload["exception"] = redact_log_text(self.formatException(record.exc_info))
        if record.stack_info:
            payload["stack"] = redact_log_text(self.formatStack(record.stack_info))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level):
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
