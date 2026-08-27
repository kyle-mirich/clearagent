from typing import Any, Protocol

from clearagent.runtime.providers.base import ProviderRequest, ProviderResponse


class TraceStore(Protocol):
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

    def list_runs(self) -> list[dict[str, Any]]: ...

    def get_run(self, run_id: str) -> dict[str, Any] | None: ...

    def get_turns(self, run_id: str) -> list[dict[str, Any]]: ...

    def get_model_call_for_turn(self, run_id: str, turn_index: int) -> dict[str, Any] | None: ...

    def list_tool_calls(self, run_id: str) -> list[dict[str, Any]]: ...

    def list_model_calls(self, run_id: str) -> list[dict[str, Any]]: ...

    def start_eval_suite_run(
        self, *, suite_name: str, suite_type: str, agent_name: str, model: str
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
    ) -> str: ...

    def list_eval_case_results(self, suite_run_id: str) -> list[dict[str, Any]]: ...

    def get_latest_run_for_agent(self, agent_name: str) -> dict[str, Any] | None: ...
