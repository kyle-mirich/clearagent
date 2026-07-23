from typing import Any

SECRET_KEYS = {
    "api_key",
    "authorization",
    "x-api-key",
    "openai_api_key",
    "anthropic_api_key",
    "google_api_key",
    "password",
    "secret",
    "token",
}


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if key.lower() in SECRET_KEYS:
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact(item)
        return redacted
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value
