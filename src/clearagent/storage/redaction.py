import re
from typing import Any

SECRET_REDACTED = "[REDACTED]"

# Keys are normalized before comparison (casefolded, separators stripped), so
# "API-Key", "api_key", and "apiKey" all resolve to the same entry below.
_SECRET_KEY_TOKENS = frozenset(
    {
        "apikey",
        "xapikey",
        "xgoogapikey",
        "googleapikey",
        "openaiapikey",
        "anthropicapikey",
        "authorization",
        "proxyauthorization",
        "password",
        "passwd",
        "secret",
        "clientsecret",
        "secretkey",
        "privatekey",
        "token",
        "accesstoken",
        "refreshtoken",
        "idtoken",
        "authtoken",
        "bearer",
        "credential",
        "credentials",
    }
)

# Conservative value-shape patterns for well-known credential formats that can
# appear inside free-form content (chat messages, tool results, error text).
_SECRET_VALUE_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"\b(?:sk|rk)-[A-Za-z0-9_-]{16,}",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"\bgh[pousr]_[A-Za-z0-9]{20,}",
        r"\bxox[baprs]-[A-Za-z0-9-]{10,}",
        r"\bAIza[0-9A-Za-z_\-]{35}",
    )
)


def _normalize_key(key: Any) -> str:
    return str(key).lower().replace("-", "").replace("_", "").replace(" ", "")


def _scrub_value(value: str) -> str:
    for pattern in _SECRET_VALUE_PATTERNS:
        value = pattern.sub(SECRET_REDACTED, value)
    return value


def is_secret_key(key: Any) -> bool:
    return _normalize_key(key) in _SECRET_KEY_TOKENS


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if is_secret_key(key):
                redacted[key] = SECRET_REDACTED
            else:
                redacted[key] = redact(item)
        return redacted
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _scrub_value(value)
    return value
