from typing import Any, Protocol

from clearagent.providers.base import ProviderRequest, ProviderResponse


class TraceStore(Protocol):
    """Persistence contract used by agent and graph execution."""

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

    def save_model_request(
        self, *, run_id: str, turn_id: str, request: ProviderRequest
    ) -> str: ...

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

    def get_turns(self, run_id: str) -> list[dict[str, Any]]: ...
