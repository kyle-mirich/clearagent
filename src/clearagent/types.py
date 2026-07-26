from pathlib import Path
from typing import Any, NotRequired, TypedDict

from pydantic import BaseModel, ConfigDict, Field, SkipValidation
from pydantic.json_schema import SkipJsonSchema

from clearagent.providers.base import Usage
from clearagent.storage.protocol import TraceStore


class ExecutedToolCall(TypedDict):
    """A tool invocation completed during an agent run."""

    name: str
    arguments: NotRequired[dict[str, Any]]
    result: NotRequired[Any]


class RunResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

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
