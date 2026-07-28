import time
from typing import Any

from clearagent.messages import Message, dump_messages
from clearagent.providers.base import ProviderResponse, Usage
from clearagent.storage.protocol import TraceStore


def error_payload(exc: Exception) -> dict[str, str]:
    return {"type": exc.__class__.__name__, "message": str(exc)}


def latency_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


class TraceLifecycle:
    def __init__(
        self,
        store: TraceStore | None,
        run_id: str | None,
        *,
        own_run: bool,
        run_started: float,
    ):
        self.store = store
        self.run_id = run_id
        self.own_run = own_run
        self.run_started = run_started

    def save_model_response(
        self,
        model_call_id: str | None,
        *,
        response: ProviderResponse | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        if self.store is not None and model_call_id:
            self.store.save_model_response(
                model_call_id=model_call_id,
                response=response,
                error=error,
            )

    def end_turn(
        self,
        turn_id: str | None,
        *,
        output_messages: list[dict[str, Any]],
        final_output: str | None = None,
        status: str = "ok",
        error: dict[str, Any] | None = None,
        turn_started: float | None = None,
    ) -> None:
        if self.store is not None and turn_id:
            self.store.end_turn(
                turn_id=turn_id,
                output_messages=output_messages,
                final_output=final_output,
                status=status,
                error=error,
                latency_ms=latency_ms(turn_started) if turn_started is not None else None,
            )

    def end_run(
        self,
        *,
        final_output: str | None = None,
        status: str = "ok",
        error: dict[str, Any] | None = None,
        end_run: bool = True,
        usage: Usage | None = None,
    ) -> None:
        if self.store is not None and self.run_id and self.own_run and end_run:
            self.store.end_run(
                self.run_id,
                final_output=final_output,
                status=status,
                error=error,
                latency_ms=latency_ms(self.run_started),
                prompt_tokens=usage.prompt_tokens if usage else None,
                completion_tokens=usage.completion_tokens if usage else None,
                cost_usd=usage.cost_usd if usage else None,
            )

    def record_model_error(
        self,
        *,
        model_call_id: str | None,
        turn_id: str | None,
        messages: list[Message],
        turn_started: float,
        exc: Exception,
        usage: Usage | None = None,
    ) -> dict[str, str]:
        error = error_payload(exc)
        self.save_model_response(model_call_id, error=error)
        self.end_turn(
            turn_id,
            output_messages=dump_messages(messages),
            status="error",
            error=error,
            turn_started=turn_started,
        )
        self.end_run(status="error", error=error, usage=usage)
        return error

    def record_tool_error(
        self,
        *,
        tool_call_id: str | None,
        turn_id: str | None,
        messages: list[Message],
        turn_started: float,
        exc: Exception,
        usage: Usage | None = None,
    ) -> dict[str, str]:
        error = error_payload(exc)
        if self.store is not None and tool_call_id:
            self.store.end_tool_call(tool_call_id, status="error", error=error)
        self.end_turn(
            turn_id,
            output_messages=dump_messages(messages),
            status="error",
            error=error,
            turn_started=turn_started,
        )
        self.end_run(status="error", error=error, usage=usage)
        return error
