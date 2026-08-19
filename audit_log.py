import json
from typing import Any


SENSITIVE_KEYS = {
    "access_token",
    "authorization",
    "body",
    "client_secret",
    "content",
    "graph_response",
    "password",
    "temporary_password",
    "token",
}


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in SENSITIVE_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def log_event(event: str, **fields: Any) -> None:
    """Emit structured diagnostics without request, prompt, result or secret dumps."""
    payload = {"event": event, **redact(fields)}
    print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)
