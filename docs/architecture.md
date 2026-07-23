# Architecture

ClearAgent keeps the runtime small and local-first:

```text
create_agent -> Agent.run -> provider.build_request
                         -> SQLiteTraceStore.save_model_request
                         -> provider.complete
                         -> SQLiteTraceStore.save_model_response
```

The provider request object is built before the model call and persisted before
completion. This is the core invariant that makes request replay possible.

## Runtime Flow

1. `create_agent` builds an `Agent` with a provider, model URI, tools, tracing
   defaults, response format, and a SQLite trace store path.
2. `Agent.run` normalizes the system prompt and user input into messages.
3. The provider builds a provider-shaped request object without making the model
   call.
4. If tracing is enabled, `SQLiteTraceStore.save_model_request` persists the
   redacted request snapshot.
5. The provider completes the request.
6. The response, tool calls, structured output, turn output, and final run output
   are saved back to SQLite.

## Storage Boundary

Trace storage is local SQLite by default. Agent and graph execution depend on
the public `TraceStore` protocol, so applications can provide another backend
without changing the runtime. The bundled SQLite store owns run, turn, model
call, tool call, eval result, and baseline rows. Provider adapters do not write
to the database directly; they only build and complete provider requests.

## Provider Boundary

OpenAI-compatible, Anthropic, and Google adapters expose the same internal
provider interface while preserving their native request shapes in saved traces.
That keeps replay and request inspection exact without making the agent runtime
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
