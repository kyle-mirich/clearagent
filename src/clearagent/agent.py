import time
from collections.abc import Callable
import json
import logging
from pathlib import Path
from typing import Any, TypedDict

from jsonschema import ValidationError as JSONSchemaValidationError
from jsonschema import validate
from langgraph.graph import END, START, StateGraph

from clearagent.runtime.messages import Message, dump_messages, normalize_messages
from clearagent.runtime.providers.base import (
    Provider,
    ProviderResponse,
    ResponseFormat,
    ResponseFormatInput,
    Usage,
    normalize_response_format,
)
from clearagent.runtime.providers.model_uri import parse_model_uri
from clearagent.storage.sqlite import DEFAULT_TRACE_DB, SQLiteTraceStore
from clearagent.storage.protocol import TraceStore
from clearagent.trace_lifecycle import TraceLifecycle, latency_ms
from clearagent.runtime.tools import tool_name, validate_tool_arguments
from clearagent.runtime.types import RunResult


class MaxTurnsExceeded(RuntimeError):
    pass


class _AgentState(TypedDict):
    messages: list[Message]
    turn_index: int


logger = logging.getLogger(__name__)

ROOT_INPUT_PREVIEW_CHARS = 2_000

_TRACE_STORE_CACHE: dict[str, TraceStore] = {}


def _trace_write(action: str, call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    # Observability must never fail otherwise valid product work: a broken or
    # locked trace store degrades to a warning instead of raising into the run.
    try:
        return call(*args, **kwargs)
    except Exception:
        logger.warning("Trace write %s failed; continuing without it", action, exc_info=True)
        return None


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content)
    except (TypeError, ValueError):
        return str(content)


def _root_input_preview(input: str | list[Message]) -> str:
    # Persist only conversation content, never the system prompt: root_input
    # feeds shareable trace reports and must not leak hidden instructions.
    if isinstance(input, str):
        text = input
    else:
        parts = [
            _message_text(message.content)
            for message in input
            if message.role != "system" and message.content
        ]
        text = "\n".join(parts)
    if len(text) > ROOT_INPUT_PREVIEW_CHARS:
        return text[: ROOT_INPUT_PREVIEW_CHARS - 1].rstrip() + "…"
    return text


class Agent:
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
        max_tokens: int | None = None,
        temperature: float | None = 0.0,
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
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.response_format = normalize_response_format(response_format)

    def _store(self) -> TraceStore:
        if self.trace_store:
            return self.trace_store
        # Reuse one store per trace DB path: re-running schema DDL and column
        # migrations on every call adds contention under concurrent runs.
        cache_key = str(Path(self.trace_db_path))
        store = _TRACE_STORE_CACHE.get(cache_key)
        if store is None:
            store = SQLiteTraceStore(cache_key)
            _TRACE_STORE_CACHE[cache_key] = store
        return store

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
        started = time.monotonic()
        should_trace = self.trace if trace is None else trace
        store = trace_store or (self._store() if should_trace else None)
        own_run = run_id is None
        root_input = _root_input_preview(input)
        if store and run_id is None:
            run_id = _trace_write(
                "start_run",
                store.start_run,
                agent_name=self.name,
                root_input=root_input,
                graph_name=graph_name,
            )
        trace_lifecycle = TraceLifecycle(store, run_id, own_run=own_run, run_started=started)

        messages = normalize_messages(self.system_prompt, input)
        # Per-run context lives in this closure, never on the Agent instance:
        # one Agent may serve concurrent graph runs.
        ctx: dict[str, Any] = {
            "usage": Usage(),
            "tool_calls": [],
            "pending_tool_calls": [],
            "turn_id": None,
            "turn_started": None,
            "result": None,
        }
        execution_graph = self._execution_graph(
            store=store,
            run_id=run_id,
            trace_lifecycle=trace_lifecycle,
            node=node_name or self.name,
            turn_index_offset=turn_index_offset,
            end_run=end_run,
            extra=extra or {},
            started=started,
            should_trace=should_trace,
            ctx=ctx,
        )
        final_state = execution_graph.invoke(
            {"messages": messages, "turn_index": 0},
            config={"recursion_limit": self.max_turns * 2 + 4},
        )
        _ = final_state
        result: RunResult = ctx["result"]
        return result

    def _execution_graph(
        self,
        *,
        store: TraceStore | None,
        run_id: str | None,
        trace_lifecycle: TraceLifecycle,
        node: str,
        turn_index_offset: int,
        end_run: bool,
        extra: dict[str, Any],
        started: float,
        should_trace: bool,
        ctx: dict[str, Any],
    ):
        def model_node(state: _AgentState) -> _AgentState:
            turn_index = state["turn_index"]
            messages = list(state["messages"])
            if turn_index >= self.max_turns:
                error = {"type": "MaxTurnsExceeded", "message": f"Exceeded {self.max_turns} turns."}
                trace_lifecycle.end_run(status="error", error=error)
                raise MaxTurnsExceeded(error["message"])

            ctx["turn_started"] = time.monotonic()
            turn_id = None
            if store and run_id:
                turn_id = _trace_write(
                    "start_turn",
                    store.start_turn,
                    run_id=run_id,
                    turn_index=turn_index_offset + turn_index,
                    node_name=node,
                    input_messages=dump_messages(messages),
                )
            ctx["turn_id"] = turn_id
            request = self.provider.build_request(
                model=_request_model_name(self.model),
                messages=messages,
                tools=self.tools,
                tool_choice="auto" if self.tools else None,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                extra=extra,
                response_format=self.response_format,
            )
            model_call_id = None
            if store and run_id and turn_id:
                model_call_id = _trace_write(
                    "save_model_request",
                    store.save_model_request,
                    run_id=run_id,
                    turn_id=turn_id,
                    request=request,
                )
            try:
                response = self.provider.complete(request)
                _apply_structured_output(response, self.response_format)
            except Exception as exc:
                trace_lifecycle.record_model_error(
                    model_call_id=model_call_id,
                    turn_id=turn_id,
                    messages=messages,
                    turn_started=ctx["turn_started"],
                    exc=exc,
                )
                raise
            trace_lifecycle.save_model_response(model_call_id, response=response)
            if response.usage:
                # Accumulate across turns so multi-turn tool runs report total
                # spend instead of just the final turn's tokens.
                usage: Usage = ctx["usage"]
                ctx["usage"] = Usage(
                    prompt_tokens=usage.prompt_tokens + response.usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens + response.usage.completion_tokens,
                    total_tokens=usage.total_tokens + response.usage.total_tokens,
                )
            messages.append(_assistant_message(response))

            if response.tool_calls:
                ctx["pending_tool_calls"] = response.tool_calls
                return {"messages": messages, "turn_index": turn_index + 1}

            output = response.output_text or ""
            trace_lifecycle.end_turn(
                turn_id,
                output_messages=dump_messages(messages),
                final_output=output,
                turn_started=ctx["turn_started"],
            )
            trace_lifecycle.end_run(final_output=output, end_run=end_run)
            ctx["result"] = RunResult(
                output=output,
                run_id=run_id,
                trace_db_path=self.trace_db_path if should_trace else None,
                tool_calls=ctx["tool_calls"],
                usage=ctx["usage"],
                latency_ms=latency_ms(started),
                structured_output=response.structured_output,
            )
            return {"messages": messages, "turn_index": turn_index + 1}

        def tools_node(state: _AgentState) -> _AgentState:
            messages = list(state["messages"])
            for call in ctx["pending_tool_calls"]:
                tool_call_id = None
                if store and run_id and ctx["turn_id"]:
                    tool_call_id = _trace_write(
                        "start_tool_call",
                        store.start_tool_call,
                        run_id=run_id,
                        turn_id=ctx["turn_id"],
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
                        turn_id=ctx["turn_id"],
                        messages=messages,
                        turn_started=ctx["turn_started"],
                        exc=exc,
                    )
                    raise
                if store and tool_call_id:
                    _trace_write("end_tool_call", store.end_tool_call, tool_call_id, result=result)
                ctx["tool_calls"].append({"name": call.name, "arguments": call.arguments, "result": result})
                messages.append(
                    Message(
                        role="tool",
                        content=_stringify_tool_result(result),
                        tool_call_id=call.id,
                        name=call.name,
                    )
                )
            ctx["pending_tool_calls"] = []
            trace_lifecycle.end_turn(
                ctx["turn_id"],
                output_messages=dump_messages(messages),
                turn_started=ctx["turn_started"],
            )
            return {"messages": messages, "turn_index": state["turn_index"]}

        def route_after_model(state: _AgentState) -> str:
            _ = state
            if ctx["result"] is not None:
                return END
            return "tools"

        builder = StateGraph(_AgentState)
        builder.add_node("model", model_node)
        builder.add_node("tools", tools_node)
        builder.add_edge(START, "model")
        builder.add_conditional_edges("model", route_after_model, {"tools": "tools", END: END})
        builder.add_edge("tools", "model")
        return builder.compile()

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
        root_input = _root_input_preview(input)
        if store and run_id is None:
            run_id = _trace_write(
                "start_run",
                store.start_run,
                agent_name=self.name,
                root_input=root_input,
                graph_name=graph_name,
            )
        trace_lifecycle = TraceLifecycle(store, run_id, own_run=own_run, run_started=started)

        messages = normalize_messages(self.system_prompt, input)
        node = node_name or self.name
        turn_started = time.monotonic()
        turn_id = None
        if store and run_id:
            turn_id = _trace_write(
                "start_turn",
                store.start_turn,
                run_id=run_id,
                turn_index=0,
                node_name=node,
                input_messages=dump_messages(messages),
            )
        request = self.provider.build_request(
            model=_request_model_name(self.model),
            messages=messages,
            tools=[],
            tool_choice=None,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            extra=extra or {},
            response_format=self.response_format,
        )
        model_call_id = None
        if store and run_id and turn_id:
            model_call_id = _trace_write(
                "save_model_request",
                store.save_model_request,
                run_id=run_id,
                turn_id=turn_id,
                request=request,
            )

        chunks: list[str] = []
        try:
            for chunk in self.provider.stream_text(request):
                chunks.append(chunk)
                yield chunk
        except GeneratorExit:
            # Consumer abandoned the stream: close out trace state instead of
            # leaving the run and turn rows stuck in "running" forever.
            partial = "".join(chunks)
            messages.append(Message(role="assistant", content=partial))
            abandoned = {
                "type": "StreamAbandoned",
                "message": "Consumer disconnected before the stream finished.",
            }
            trace_lifecycle.save_model_response(
                model_call_id,
                response=ProviderResponse(
                    provider=request.provider,
                    model=request.model,
                    raw={"streamed": True, "abandoned": True},
                    output_text=partial,
                ),
            )
            trace_lifecycle.end_turn(
                turn_id,
                output_messages=dump_messages(messages),
                final_output=partial,
                status="aborted",
                error=abandoned,
                turn_started=turn_started,
            )
            trace_lifecycle.end_run(final_output=partial, status="aborted", error=abandoned)
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

        output = "".join(chunks)
        messages.append(Message(role="assistant", content=output))
        trace_lifecycle.save_model_response(
            model_call_id,
            response=ProviderResponse(
                provider=request.provider,
                model=request.model,
                raw={"streamed": True},
                output_text=output,
            ),
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
    metadata = {}
    if response.tool_calls:
        metadata["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in response.tool_calls
        ]
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


def _stringify_tool_result(result: Any) -> str:
    if isinstance(result, str):
        return result
    return str(result)
