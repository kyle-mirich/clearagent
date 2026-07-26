import time
from collections.abc import Callable
import json
from pathlib import Path
from typing import Any

from jsonschema import ValidationError as JSONSchemaValidationError
from jsonschema import validate

from clearagent.messages import Message, dump_messages, normalize_messages
from clearagent.providers.base import (
    Provider,
    ProviderResponse,
    ResponseFormat,
    ResponseFormatInput,
    Usage,
    normalize_response_format,
)
from clearagent.providers.model_uri import parse_model_uri
from clearagent.serialization import json_safe, stringify
from clearagent.storage.sqlite import DEFAULT_TRACE_DB, SQLiteTraceStore
from clearagent.storage.protocol import TraceStore
from clearagent.trace_lifecycle import TraceLifecycle, latency_ms
from clearagent.tool import tool_name, validate_tool_arguments
from clearagent.types import RunResult


class MaxTurnsExceeded(RuntimeError):
    """Raised when an agent does not produce a final response within its turn limit."""

    pass


class Agent:
    """Run one provider-backed agent with optional tools, tracing, and structured output."""

    def __init__(
        self,
        *,
        name: str,
        model: str,
        provider: Provider,
        system_prompt: str | None = None,
        tools: list[Callable[..., Any]] | None = None,
        trace: bool = True,
        trace_db_path: str | Path = DEFAULT_TRACE_DB,
        trace_store: TraceStore | None = None,
        max_turns: int = 8,
        temperature: float | None = None,
        response_format: ResponseFormatInput = None,
    ):
        self.name = name
        self.model = model
        self.provider = provider
        self.system_prompt = system_prompt
        self.tools = tools or []
        self.trace = trace
        self.trace_db_path = Path(trace_db_path)
        self.trace_store = trace_store
        self.max_turns = max_turns
        self.temperature = temperature
        self.response_format = normalize_response_format(response_format)

    def _store(self) -> TraceStore:
        return self.trace_store or SQLiteTraceStore(self.trace_db_path)

    def run(
        self,
        input: str | list[Message],
        *,
        trace: bool | None = None,
        run_id: str | None = None,
        trace_store: TraceStore | None = None,
        node_name: str | None = None,
        graph_name: str | None = None,
        end_run: bool = True,
        turn_index_offset: int = 0,
        extra: dict[str, Any] | None = None,
    ) -> RunResult:
        """Run the bounded model/tool loop and return the final result."""
        started = time.monotonic()
        should_trace = self.trace if trace is None else trace
        store = trace_store or (self._store() if should_trace else None)
        own_run = run_id is None
        root_input = input if isinstance(input, str) else repr(input)
        if store and run_id is None:
            run_id = store.start_run(agent_name=self.name, root_input=root_input, graph_name=graph_name)
        trace_lifecycle = TraceLifecycle(store, run_id, own_run=own_run, run_started=started)

        messages = normalize_messages(self.system_prompt, input)
        all_tool_calls: list[dict[str, Any]] = []
        usage = None
        node = node_name or self.name

        for turn_index in range(self.max_turns):
            persisted_turn_index = turn_index_offset + turn_index
            turn_started = time.monotonic()
            turn_id = None
            if store and run_id:
                turn_id = store.start_turn(
                    run_id=run_id,
                    turn_index=persisted_turn_index,
                    node_name=node,
                    input_messages=dump_messages(messages),
                )
            model_call_id = None
            try:
                request = self.provider.build_request(
                    model=_request_model_name(self.model),
                    messages=messages,
                    tools=self.tools,
                    tool_choice="auto" if self.tools else None,
                    temperature=self.temperature,
                    max_tokens=None,
                    extra=extra or {},
                    response_format=self.response_format,
                )
                if store and run_id and turn_id:
                    model_call_id = store.save_model_request(
                        run_id=run_id, turn_id=turn_id, request=request
                    )
                response = self.provider.complete(request)
                if not response.tool_calls:
                    _apply_structured_output(response, self.response_format)
            except Exception as exc:
                trace_lifecycle.record_model_error(
                    model_call_id=model_call_id,
                    turn_id=turn_id,
                    messages=messages,
                    turn_started=turn_started,
                    exc=exc,
                    usage=usage,
                )
                raise
            trace_lifecycle.save_model_response(model_call_id, response=response)
            usage = merge_usage(usage, response.usage)
            messages.append(_assistant_message(response))

            if response.tool_calls:
                for call in response.tool_calls:
                    tool_call_id = None
                    if store and run_id and turn_id:
                        tool_call_id = store.start_tool_call(
                            run_id=run_id,
                            turn_id=turn_id,
                            tool_name=call.name,
                            args=call.arguments,
                        )
                    try:
                        tool_fn = self._find_tool(call.name)
                        validated_arguments = validate_tool_arguments(tool_fn, call.arguments)
                        result = tool_fn(**validated_arguments)
                    except Exception as exc:
                        trace_lifecycle.record_tool_error(
                            tool_call_id=tool_call_id,
                            turn_id=turn_id,
                            messages=messages,
                            turn_started=turn_started,
                            exc=exc,
                            usage=usage,
                        )
                        raise
                    safe_result = json_safe(result)
                    if store and tool_call_id:
                        store.end_tool_call(tool_call_id, result=safe_result)
                    all_tool_calls.append(
                        {"name": call.name, "arguments": call.arguments, "result": safe_result}
                    )
                    messages.append(
                        Message(
                            role="tool",
                            content=stringify(result),
                            tool_call_id=call.id,
                            name=call.name,
                        )
                    )
                trace_lifecycle.end_turn(
                    turn_id,
                    output_messages=dump_messages(messages),
                    turn_started=turn_started,
                )
                continue

            output = response.output_text or ""
            run_latency_ms = latency_ms(started)
            trace_lifecycle.end_turn(
                turn_id,
                output_messages=dump_messages(messages),
                final_output=output,
                turn_started=turn_started,
            )
            trace_lifecycle.end_run(final_output=output, end_run=end_run, usage=usage)
            return RunResult(
                output=output,
                run_id=run_id,
                trace_db_path=self.trace_db_path if should_trace else None,
                tool_calls=all_tool_calls,
                usage=usage,
                cost_usd=usage.cost_usd if usage else None,
                latency_ms=run_latency_ms,
                structured_output=response.structured_output,
            )

        error = {"type": "MaxTurnsExceeded", "message": f"Exceeded {self.max_turns} turns."}
        trace_lifecycle.end_run(status="error", error=error, usage=usage)
        raise MaxTurnsExceeded(error["message"])

    def stream_text(
        self,
        input: str | list[Message],
        *,
        trace: bool | None = None,
        run_id: str | None = None,
        trace_store: TraceStore | None = None,
        node_name: str | None = None,
        graph_name: str | None = None,
        extra: dict[str, Any] | None = None,
    ):
        """Yield provider text chunks while recording the run when tracing is enabled.

        Agents with tools use the normal bounded tool loop and yield the final
        output as one chunk because tool execution requires complete responses.
        """
        if self.tools:
            run_extra = dict(extra or {})
            run_extra.pop("stream", None)
            result = self.run(
                input,
                trace=trace,
                run_id=run_id,
                trace_store=trace_store,
                node_name=node_name,
                graph_name=graph_name,
                extra=run_extra,
            )
            yield result.output
            return

        started = time.monotonic()
        should_trace = self.trace if trace is None else trace
        store = trace_store or (self._store() if should_trace else None)
        own_run = run_id is None
        root_input = input if isinstance(input, str) else repr(input)
        if store and run_id is None:
            run_id = store.start_run(agent_name=self.name, root_input=root_input, graph_name=graph_name)
        trace_lifecycle = TraceLifecycle(store, run_id, own_run=own_run, run_started=started)

        messages = normalize_messages(self.system_prompt, input)
        node = node_name or self.name
        turn_started = time.monotonic()
        turn_id = None
        if store and run_id:
            turn_id = store.start_turn(
                run_id=run_id,
                turn_index=0,
                node_name=node,
                input_messages=dump_messages(messages),
            )
        model_call_id = None
        chunks: list[str] = []
        try:
            request = self.provider.build_request(
                model=_request_model_name(self.model),
                messages=messages,
                tools=[],
                tool_choice=None,
                temperature=self.temperature,
                max_tokens=None,
                extra=extra or {},
                response_format=self.response_format,
            )
            if store and run_id and turn_id:
                model_call_id = store.save_model_request(
                    run_id=run_id, turn_id=turn_id, request=request
                )
            for chunk in self.provider.stream_text(request):
                chunks.append(chunk)
                yield chunk
            output = "".join(chunks)
            streamed_response = ProviderResponse(
                provider=request.provider,
                model=request.model,
                raw={"streamed": True},
                output_text=output,
            )
            _apply_structured_output(streamed_response, self.response_format)
        except GeneratorExit:
            trace_lifecycle.record_model_error(
                model_call_id=model_call_id,
                turn_id=turn_id,
                messages=messages,
                turn_started=turn_started,
                exc=RuntimeError("stream consumer closed before completion"),
            )
            raise
        except Exception as exc:
            trace_lifecycle.record_model_error(
                model_call_id=model_call_id,
                turn_id=turn_id,
                messages=messages,
                turn_started=turn_started,
                exc=exc,
            )
            raise

        messages.append(Message(role="assistant", content=output))
        trace_lifecycle.save_model_response(
            model_call_id,
            response=streamed_response,
        )
        trace_lifecycle.end_turn(
            turn_id,
            output_messages=dump_messages(messages),
            final_output=output,
            turn_started=turn_started,
        )
        trace_lifecycle.end_run(final_output=output)

    def _find_tool(self, name: str) -> Callable[..., Any]:
        for fn in self.tools:
            if tool_name(fn) == name:
                return fn
        raise KeyError(f"No tool named {name!r} is registered.")


def _assistant_message(response: ProviderResponse) -> Message:
    metadata: dict[str, Any] = {}
    if response.tool_calls:
        metadata["tool_calls"] = []
        for call in response.tool_calls:
            serialized_call = {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            if call.provider_data:
                serialized_call["provider_data"] = call.provider_data
            metadata["tool_calls"].append(serialized_call)
        openai_output = response.raw.get("output")
        if isinstance(openai_output, list):
            metadata["openai_responses_output"] = openai_output
        anthropic_content = response.raw.get("content")
        if isinstance(anthropic_content, list):
            metadata["anthropic_content"] = anthropic_content
    return Message(role="assistant", content=response.output_text, metadata=metadata)


def _request_model_name(model_uri: str) -> str:
    try:
        return parse_model_uri(model_uri).model
    except ValueError:
        return model_uri.split(":", 1)[1] if ":" in model_uri else model_uri


def _apply_structured_output(
    response: ProviderResponse, response_format: ResponseFormat | None
) -> None:
    if response_format is None:
        return
    if response.output_text is None:
        if response.tool_calls:
            return
        raise ValueError(
            f"structured output response did not include text for {response_format.name!r}."
        )
    if response.output_text == "":
        raise ValueError(
            f"structured output response did not include text for {response_format.name!r}."
        )
    try:
        parsed = json.loads(response.output_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid structured output JSON for {response_format.name!r}: {exc}") from exc
    try:
        validate(parsed, response_format.json_schema)
    except JSONSchemaValidationError as exc:
        raise ValueError(
            f"Structured output did not match schema for {response_format.name!r}: {exc.message}"
        ) from exc
    response.structured_output = parsed


def merge_usage(current: Usage | None, incoming: Usage | None) -> Usage | None:
    if incoming is None:
        return current
    if current is None:
        return incoming.model_copy()
    cost_usd = (
        current.cost_usd + incoming.cost_usd
        if current.cost_usd is not None and incoming.cost_usd is not None
        else None
    )
    return Usage(
        prompt_tokens=current.prompt_tokens + incoming.prompt_tokens,
        completion_tokens=current.completion_tokens + incoming.completion_tokens,
        total_tokens=current.total_tokens + incoming.total_tokens,
        cost_usd=cost_usd,
    )
