from collections.abc import Mapping
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from pydantic import BaseModel, ConfigDict, Field, SkipValidation, field_validator
from pydantic.json_schema import SkipJsonSchema

from clearagent.providers.base import Usage
from clearagent.storage.protocol import TraceStore


class ExecutedToolCall(TypedDict):
    """A tool invocation completed during an agent run."""

    name: str
    arguments: NotRequired[dict[str, Any]]
    result: NotRequired[Any]


class RunResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    output: str
    run_id: str | None
    trace_db_path: str | Path | None
    trace_store: SkipJsonSchema[SkipValidation[TraceStore | None]] = Field(
        default=None,
        exclude=True,
        repr=False,
    )
    tool_calls: list[ExecutedToolCall]
    usage: Usage | None = None
    latency_ms: int
    cost_usd: float | None = None
    structured_output: Any = None

    @field_validator("usage", mode="before")
    @classmethod
    def reject_unknown_usage_fields(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        supported = {
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "cost_usd",
            "cost",
        }
        unknown = set(value) - supported
        if unknown:
            fields = ", ".join(sorted((repr(field) for field in unknown)))
            raise ValueError(f"usage contains unsupported fields: {fields}")
        return value
