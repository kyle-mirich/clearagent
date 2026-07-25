# Reference

This page summarizes the public surfaces that new users and contributors need
most often.

## Python API

Import the main authoring helpers from `clearagent`:

```python
from clearagent import create_agent, tool
```

### `create_agent`

`create_agent` returns an `Agent`.

Common arguments:

- `name`: stable agent name used in traces and reports
- `model`: model URI in `provider:model` format
- `system_prompt`: optional system instruction
- `tools`: optional list of `@tool`-decorated Python callables
- `trace`: whether tracing is enabled by default
- `trace_db_path`: SQLite path for trace data
- `trace_store`: optional implementation of the public `TraceStore` protocol;
  `SQLiteTraceStore` is the bundled default
- `max_turns`: maximum model/tool loop iterations
- `temperature`: provider temperature value
- `provider`: optional custom provider, useful for tests
- `response_format`: optional Pydantic model or provider response-format object

`response_format` can be a Pydantic model, a `ResponseFormat`, a raw JSON
schema mapping, or a mapping with `name`, `schema`, and optional `strict`.
When using the `schema` form, `schema` itself must be a mapping.
When a structured format is requested, the final provider response must include
JSON text that validates against the schema. Missing text, invalid JSON, or a
schema mismatch raises a `ValueError` and marks the traced run as failed.
The same validation runs after a streamed response is joined.

### `@tool`

`@tool` attaches an OpenAI-compatible function schema to a Python function. The
schema is derived from:

- function name
- docstring
- argument type hints
- required arguments

Supported JSON type mapping includes common scalar types, lists, dictionaries,
enums, literals, optional values, defaults, and string fallback.

Tool contracts can validate expected tool argument and output behavior in
normal tests:

```python
from clearagent.contracts import ToolContractCase, validate_tool_contract

result = validate_tool_contract(
    lookup_order,
    ToolContractCase(
        name="lookup shipped order",
        arguments={"order_id": "A123"},
        expected={"order_id": "A123", "status": "shipped"},
    ),
)
assert result.passed
```

### `FakeProvider`

`FakeProvider` is available from `clearagent.providers.base` for deterministic
tests and examples. It accepts queued `ProviderResponse` objects or exceptions
and records completed requests.

### Runtime Results

`Agent.run(...)` returns `clearagent.types.RunResult`, containing the final
`output`, optional `run_id` and trace path, tool-call records, merged usage and
cost, latency, and validated `structured_output`.

Parsed provider tool calls use `clearagent.providers.base.ToolCall`. Its
`provider_data` mapping preserves opaque provider metadata needed for a later
turn, such as Google thought signatures; applications should pass it through
without interpreting it.

`Agent.stream_text(...)` yields text chunks. When an agent has tools, ClearAgent
runs the bounded tool loop and yields its final output as one chunk.

### `AgentGraph`

Import `AgentGraph` from `clearagent.graph`. It executes a bounded linear chain
of registered agents under one trace run:

```python
from clearagent.graph import AgentGraph

flow = (
    AgentGraph("review_flow")
    .add_node(planner)
    .add_node(writer)
    .add_edge("planner", "writer")
    .set_entrypoint("planner")
)
result = flow.run("Review this proposal.", max_nodes=2)
```

Graphs reject missing or unknown entrypoints, unknown edge targets, cycles,
invalid bounds, and flows that exceed `max_nodes`.

### Evals And Storage

- `clearagent.evals.EvalSuite.from_yaml(path)` loads a validated YAML suite.
- `clearagent.evals.EvalRunner(agent).run_suite(suite)` executes and persists
  results.
- `clearagent.storage.TraceStore` is the persistence protocol accepted by
  agents and graphs.
- `clearagent.storage.SQLiteTraceStore` is the bundled local implementation.

Custom trace stores must implement the complete `TraceStore` protocol. Chat
session persistence remains separate in `clearagent.chat.ChatStore`.

## CLI

The installed console script is `clearagent`.

```bash
uv run clearagent init
uv run clearagent run <agent_module:object> "input text"
uv run clearagent run <agent_module:object> "input text" --no-trace
uv run clearagent chat <agent_module:object>
uv run clearagent chat <agent_module:object> --allow-settings-mutation
uv run clearagent eval <agent_module:object> <suite.yaml>
uv run clearagent trace list
uv run clearagent trace show <run_id>
uv run clearagent trace turns <run_id>
uv run clearagent request <run_id> --turn 0
uv run clearagent replay-request <run_id> --turn 0 --out request.json
uv run clearagent replay <run_id> --turn 0
uv run clearagent diff <run_id> --turn 0
uv run clearagent trace-to-eval <run_id> --out generated.yaml
uv run clearagent trace-report <run_id> --out report.md
uv run clearagent iterate <agent_module:object> <suite.yaml> --model openai:gpt-4.1-mini --temperature 0.0
uv run clearagent promptfoo export <agent_module:object> <suite.yaml> promptfooconfig.yaml
uv run clearagent promptfoo target <agent_module:object> .clearagent/promptfoo_target.py
uv run clearagent baseline save <suite_run_id> --name v1
uv run clearagent baseline compare <baseline_name> <suite_run_id>
```

`agent_module:object` is imported from the current working directory. For
example:

```bash
uv run clearagent eval examples.customer_support.agent:agent examples/customer_support/evals/smoke.yaml
```

The object path must include both sides of the colon. For example,
`examples.customer_support.agent:agent` imports the `agent` object from the
`examples.customer_support.agent` module. ClearAgent reports a parameter error
if the object path is malformed, the module cannot be imported, or the named
object is missing.

## Eval Suite Format

Eval suites are YAML mappings with a `name`, optional `type`, optional
`description`, optional `defaults`, optional `matrix`, and a list of `cases`.
`defaults` and `matrix` must be mappings. Matrix `models` and `temperatures`
must be lists when present.

```yaml
name: smoke
type: output
description: Customer support smoke suite.
cases:
  - name: shipped order
    input: Where is order A123?
    checks:
      - contains: shipped
```

Cases may also carry optional `expected`, `reference_notes`, and `split` fields
for interoperable datasets. The local deterministic checks do not require them.

Available output check names:

- `contains`
- `contains_any`
- `not_contains`
- `regex`
- `equals`
- `json_schema`
- `refuses`
- `expected_tools`
- `forbidden_tools`
- `latency_under_ms`
- `cost_under`

`cost_under` passes only when the provider reports a monetary cost. Providers
that return token counts without a cost produce a failed check with a clear
"cost is unavailable" message; ClearAgent never treats an unknown cost as zero.

Available trace-aware check names:

- `structured_output`
- `trace_provider`
- `max_turns`
- `called_tool`
- `not_called_tool`

## Providers

Supported model URI providers:

- `openai`
- `openrouter`
- `local`
- `ollama`
- `anthropic`
- `google`

OpenAI uses the native Responses API. OpenRouter, local, and Ollama use the
OpenAI-compatible Chat Completions adapter. Anthropic and Google use native
provider adapters.

Local OpenAI-compatible servers can use `local:<model>` for the default
`http://localhost:8000/v1` endpoint, or
`local:<base-url>?model=<model>` for an explicit endpoint. Ollama uses
`ollama:<model>` with the default `http://localhost:11434/v1` endpoint. Local
URL model URIs require a non-empty `model` query value. Local and Ollama
providers do not read `OPENAI_API_KEY`.

## Chat App Factory

`create_chat_app(agent)` serves the local browser chat backend and packaged
trace viewer. Runtime settings mutation is disabled by default. Optional safety
controls:

- `allow_settings_mutation=True` enables `PUT /api/settings`.
- `settings_admin_token="..."` requires `X-ClearAgent-Admin-Token` for runtime
  settings changes.

Trace viewer endpoints:

- `GET /api/traces` returns recent local trace summaries for the packaged visual
  trace viewer.
- `GET /api/triage/runs/{run_id}` returns a local trace triage payload with run
  data, related rows, grouped timeline steps, parsed model/tool JSON, detected
  failures, and a Markdown report.

Successful `POST /api/sessions/{session_id}/messages` streams include a
`trace` Server-Sent Event with `{"run_id": "..."}` before `[DONE]` when a trace
run was recorded.

## Local Files

Default local runtime paths:

- `.clearagent/config.toml`
- `.clearagent/traces.sqlite`
- `.clearagent/chat.sqlite`
- `.clearagent/promptfoo_target.py`

Do not commit local trace databases or generated runtime files.

## Examples

- `examples/customer_support/agent.py` - fake-provider tool-calling agent
- `examples/customer_support/evals/smoke.yaml` - output eval suite
- `examples/customer_support/evals/safety.yaml` - safety-oriented eval suite
- `examples/multinode/flow.py` - linear `AgentGraph` flow
- `examples/openrouter_chat/agent.py` - live OpenRouter chat agent
