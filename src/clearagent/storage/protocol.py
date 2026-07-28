from typing import Any, Protocol, TypedDict, cast, runtime_checkable

from clearagent.providers.base import ProviderRequest, ProviderResponse


class TraceRun(TypedDict):
    """Canonical row returned by trace run read operations."""

    id: str
    agent_name: str
    graph_name: str | None
    root_input: str
    final_output: str | None
    status: str
    started_at: str
    ended_at: str | None
    total_latency_ms: int | None
    total_prompt_tokens: int | None
    total_completion_tokens: int | None
    total_cost_usd: float | None
    metadata_json: str


class TraceTurn(TypedDict):
    """Canonical row returned by trace turn read operations."""

    id: str
    run_id: str
    turn_index: int
    node_name: str
    input_messages_json: str
    output_messages_json: str
    final_output: str | None
    status: str
    started_at: str
    ended_at: str | None
    latency_ms: int | None
    error_json: str | None


class ModelCallRecord(TypedDict):
    """Canonical row returned by model-call read operations."""

    id: str
    run_id: str
    turn_id: str
    provider: str
    model: str
    api_shape: str
    endpoint: str | None
    request_json: str
    response_json: str | None
    usage_json: str | None
    status: str
    started_at: str
    ended_at: str | None
    latency_ms: int | None
    error_json: str | None


class ToolCallRecord(TypedDict):
    """Canonical row returned by tool-call read operations."""

    id: str
    run_id: str
    turn_id: str
    tool_name: str
    args_json: str
    result_json: str | None
    status: str
    started_at: str
    ended_at: str | None
    latency_ms: int | None
    error_json: str | None


class EvalCaseResultRecord(TypedDict):
    """Canonical row returned by eval-result read operations."""

    id: str
    suite_run_id: str
    run_id: str
    suite_name: str
    case_name: str
    input: str
    final_output: str | None
    passed: bool | int
    checks_json: str
    variant_json: str
    failure_json: str | None
    latency_ms: int | None
    cost_usd: float | None


@runtime_checkable
class TraceStore(Protocol):
    """Persistence contract used by runtime, eval, and trace inspection flows.

    Implementations own both sides of the trace boundary: recording execution
    data and reading it back for eval checks, reports, and local debugging.
    """

    def start_run(
        self,
        *,
        agent_name: str,
        root_input: str,
        graph_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str: ...

    def end_run(
        self,
        run_id: str,
        *,
        final_output: str | None = None,
        status: str = "ok",
        latency_ms: int | None = None,
        error: dict[str, Any] | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        cost_usd: float | None = None,
    ) -> None: ...

    def start_turn(
        self,
        *,
        run_id: str,
        turn_index: int,
        node_name: str,
        input_messages: list[dict[str, Any]],
    ) -> str: ...

    def end_turn(
        self,
        *,
        turn_id: str,
        output_messages: list[dict[str, Any]],
        final_output: str | None = None,
        status: str = "ok",
        latency_ms: int | None = None,
        error: dict[str, Any] | None = None,
    ) -> None: ...

    def save_model_request(self, *, run_id: str, turn_id: str, request: ProviderRequest) -> str: ...

    def save_model_response(
        self,
        *,
        model_call_id: str,
        response: ProviderResponse | None = None,
        error: dict[str, Any] | None = None,
    ) -> None: ...

    def start_tool_call(
        self, *, run_id: str, turn_id: str, tool_name: str, args: dict[str, Any]
    ) -> str: ...

    def end_tool_call(
        self,
        tool_call_id: str,
        *,
        result: Any = None,
        status: str = "ok",
        error: dict[str, Any] | None = None,
    ) -> None: ...

    def get_turns(self, run_id: str) -> list[TraceTurn]: ...

    def list_runs(self) -> list[TraceRun]: ...

    def get_run(self, run_id: str) -> TraceRun | None: ...

    def get_model_call_for_turn(self, run_id: str, turn_index: int) -> ModelCallRecord | None: ...

    def list_tool_calls(self, run_id: str) -> list[ToolCallRecord]: ...

    def list_model_calls(self, run_id: str) -> list[ModelCallRecord]: ...

    def get_latest_run_for_agent(self, agent_name: str) -> TraceRun | None: ...

    def start_eval_suite_run(
        self,
        *,
        suite_name: str,
        suite_type: str,
        agent_name: str,
        model: str,
    ) -> str: ...

    def end_eval_suite_run(
        self,
        suite_run_id: str,
        *,
        passed: int,
        failed: int,
        skipped: int = 0,
        status: str | None = None,
        error: dict[str, Any] | None = None,
    ) -> None: ...

    def save_eval_case_result(
        self,
        *,
        suite_run_id: str,
        run_id: str,
        suite_name: str,
        case_name: str,
        input: str,
        final_output: str,
        passed: bool,
        checks: list[dict[str, Any]],
        latency_ms: int | None,
        cost_usd: float | None,
        variant: dict[str, Any] | None = None,
    ) -> str: ...

    def list_eval_case_results(self, suite_run_id: str) -> list[EvalCaseResultRecord]: ...


def require_complete_trace_store(store: object) -> TraceStore:
    """Return a structurally complete store or raise a useful contract error."""
    required_methods = {
        name
        for name, member in vars(TraceStore).items()
        if not name.startswith("_") and callable(member)
    }
    missing = sorted(name for name in required_methods if not callable(getattr(store, name, None)))
    if missing:
        raise TypeError(
            "trace_store must implement the complete TraceStore protocol; "
            f"missing: {', '.join(missing)}"
        )
    return cast(TraceStore, store)
