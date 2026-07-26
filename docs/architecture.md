# Architecture

ClearAgent keeps the runtime small and local-first:

```text
create_agent -> Agent.run -> provider.build_request
                         -> TraceStore.save_model_request
                         -> provider.complete
                         -> TraceStore.save_model_response
```

The provider request object is built before the model call and persisted before
completion. This is the core invariant that makes request replay possible.

## Runtime Flow

1. `create_agent` builds an `Agent` with a provider, model URI, tools, tracing
   defaults, response format, and either an injected trace store or the default
   SQLite path.
2. `Agent.run` normalizes the system prompt and user input into messages.
3. The provider builds a provider-shaped request object without making the model
   call.
4. If tracing is enabled, `TraceStore.save_model_request` persists the redacted
   request snapshot.
5. The provider completes the request.
6. The response, tool calls, structured output, turn output, and final run output
   are saved through the same store.
7. `RunResult.trace_store` retains that exact store for trace-aware checks and
   other in-process readers. The field is excluded from Pydantic serialization.

## Storage Boundary

Trace storage is local SQLite by default. Agent and graph execution depend on
the public `TraceStore` protocol, so applications can provide another backend
without changing runtime code. The contract includes both write operations and
the read/eval operations needed to inspect runs, turns, model calls, tool calls,
and eval results.

The same injected store flows through an agent run, graph run, `EvalRunner`,
trace-aware eval checks, reports, and the agent-backed chat trace endpoints.
Code in those flows must not infer a SQLite database from a path and silently
replace the supplied store. A `RunResult` that has no store can still use its
SQLite `trace_db_path` as a compatibility fallback for trace-aware checks.

Standalone CLI inspection commands such as `trace list` and `trace show` remain
file-oriented SQLite tools. Their `--trace-db` option selects the file; it does
not discover an application-defined store.

The bundled SQLite store owns run, turn, model call, tool call, eval result, and
baseline rows. Provider adapters do not write to persistence directly; they
only build and complete provider requests. Chat sessions and messages remain in
the separate `ChatStore` even when chat trace inspection uses an injected
`TraceStore`.

## Provider Boundary

Native OpenAI Responses, Anthropic Messages, native Google GenAI, and
OpenAI-compatible Chat Completions adapters expose the same internal provider
interface while preserving their request shapes in saved traces. That keeps
replay and request inspection exact without making the agent runtime
provider-specific.

## Graph Boundary

`AgentGraph` is intentionally narrow. It runs a linear sequence of `Agent`
nodes, passes each node output to the next node, and shares a trace run ID across
the graph so each node appears as a turn in the same run. Cycles and unknown
targets are rejected before provider calls, and `max_nodes` supplies an explicit
execution bound.

## Related Docs

- [Core Concepts](core-concepts.md)
- [Tracing](tracing.md)
- [Reference](reference.md)
