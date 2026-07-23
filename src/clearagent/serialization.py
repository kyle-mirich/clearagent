from dataclasses import asdict, is_dataclass
from enum import Enum
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def json_safe(value: Any) -> Any:
    """Return a value that can be persisted as JSON without changing tool execution."""
    return json.loads(json.dumps(value, default=_json_default))


def stringify(value: Any) -> str:
    """Return tool output as compact JSON text while preserving existing strings."""
    if isinstance(value, str):
        return value
    return json.dumps(json_safe(value), ensure_ascii=False, separators=(",", ":"))


def _json_default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, (Path, Enum)):
        return str(value.value if isinstance(value, Enum) else value)
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    return str(value)
