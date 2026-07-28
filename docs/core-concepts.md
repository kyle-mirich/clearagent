# Core Concepts

ClearAgent keeps the main mental model small:

```text
Agent = model + system prompt + tools + eval suites + trace store
```

The project is intentionally not a full orchestration framework. It focuses on
plain Python agents, local evals, exact provider request snapshots, and traces
that can be inspected without a hosted service.

## Agents

Create an agent with `create_agent`:

```python
from clearagent import create_agent

agent = create_agent(
    name="support_agent",
    model="openai:gpt-4.1-mini",
    system_prompt="Help users with order status.",
)
```

`Agent.run(input)` returns a `RunResult` with the final output, trace run ID,
the store used for tracing, an SQLite path when applicable, tool calls, typed
token usage, latency, and optional structured output. Token usage is aggregated
across every model/tool turn. Monetary cost remains `None` unless the provider
reports it. The in-process store handle is excluded when the result is
serialized.

## Tools

Tools are normal Python functions decorated with `@tool`:

```python
from clearagent import tool


@tool
def lookup_order(order_id: str) -> dict:
    """Look up an order."""
    return {"order_id": order_id, "status": "shipped"}
```

ClearAgent derives a function schema from the function name, type hints, and
docstring. The provider can request tool calls, ClearAgent executes the matching
Python function, and the tool result is added back into the model conversation.
Typed values such as Pydantic models are converted to a JSON-safe form for both
the trace and the provider conversation.

## Providers

Model strings use `provider:model` format, such as:

- `openai:gpt-4.1-mini`
- `openrouter:anthropic/claude-sonnet-4.5`
- `anthropic:claude-sonnet-4-5`
- `google:gemini-2.5-flash`

OpenAI model URIs use the native Responses API. OpenRouter, local, and Ollama
model URIs use the OpenAI-compatible Chat Completions adapter. Anthropic and
Google model URIs use native request/response shapes. Tests and examples can
use `FakeProvider` to avoid network calls.

## Structured Outputs

Pass a Pydantic model through `response_format` to request provider-native
structured output where available:

```python
from pydantic import BaseModel

from clearagent import create_agent


class TicketLabel(BaseModel):
    label: str
    confidence: float


agent = create_agent(
    name="classifier",
    model="openrouter:openai/gpt-4o-mini",
    response_format=TicketLabel,
)
```

The parsed value is returned as `result.structured_output` and is available to
eval checks. If the final model response does not include JSON text, includes
invalid JSON, or does not match the requested schema, the run raises a
`ValueError` and records the run as failed in the local trace database.

## Traces

Tracing is on by default. Each run stores:

- a run row with the final output
- turn rows for each model iteration
- model call rows with the exact request object saved before completion
- tool call rows when tools execute

The important invariant is that the provider request is built and persisted
before the model call. That makes `clearagent request` and
`clearagent replay-request` read from stored request data instead of
reconstructing a request later.

SQLite at `.clearagent/traces.sqlite` is the default implementation. An
injected `TraceStore` is carried through agents, graphs, evals, trace-aware
checks, reports, and agent-backed chat trace inspection.

Completed traces can also be exported as Markdown reports or promoted into
starter eval suites:

```bash
uv run clearagent trace-report <run_id> --out report.md
uv run clearagent trace-to-eval <run_id> --out generated.yaml
```

## Evals

Eval suites are YAML files. Each case gives the agent an input and checks the
final output:

```yaml
name: smoke
type: output
cases:
  - name: shipped order
    input: Where is order A123?
    checks:
      - contains: shipped
      - contains: Friday
```

Run a suite with:

```bash
uv run clearagent eval examples.customer_support.agent:agent examples/customer_support/evals/smoke.yaml
```

## Pytest Integration

Use `assert_eval_suite_passes` to make evals part of a normal pytest suite:

```python
from clearagent.pytest_plugin import assert_eval_suite_passes
from examples.customer_support.agent import agent


def test_smoke_suite():
    assert_eval_suite_passes(agent, "examples/customer_support/evals/smoke.yaml")
```

## Graph Flows

`AgentGraph` supports a small linear multi-node flow. Each node is an `Agent`.
The output of one node becomes the input to the next, and the graph shares one
trace run ID across nodes.

Cycles and unknown nodes are rejected before execution. `AgentGraph.run` also
accepts `trace`, `trace_store`, and `max_nodes` overrides and preserves combined
tool calls, usage, cost, and the final node's structured output.

See `examples/multinode/flow.py` for the current pattern.

## Chat Sessions

The chat backend wraps an agent in a FastAPI app. It stores sessions and
messages in local SQLite and streams model text back to a browser client.

See [Chat Backend](chat.md) for endpoints and storage details.
