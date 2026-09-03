from pathlib import Path
from typing import Any

from pydantic import BaseModel


class RunResult(BaseModel):
    output: str
    run_id: str | None
    trace_db_path: str | Path | None
    tool_calls: list[dict[str, Any]]
    usage: Any = None
    latency_ms: int
    cost_usd: float = 0.0
    structured_output: Any = None
