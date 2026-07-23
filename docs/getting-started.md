# Getting Started

ClearAgent is a small eval-first Python agent framework. It is built for local
development with automatic SQLite traces, replayable provider requests, YAML
eval suites, structured outputs, and pytest integration.

## Prerequisites

- Python 3.14
- `uv`

## Install As A Library

In an application that uses ClearAgent, install the current package from the
public GitHub repository:

```bash
uv add git+https://github.com/kyle-mirich/clearagent.git
```

The package is not on PyPI yet. See [Publishing](publishing.md) for the release
checklist.

See [Installation](install.md) for provider keys, optional extras, and the
external-project smoke test.

## Install This Repository

From the repository root:

```bash
uv sync --all-extras --dev
```

Run the full local quality gate:

```bash
uv run bash scripts/check.sh
```

This command requires at least 90% line coverage across the `clearagent`
package in addition to passing tests, Ruff, mypy, and documentation-link checks.

## Initialize Local Config

Create `.clearagent/config.toml`:

```bash
uv run clearagent init
```

Local runtime files are written under `.clearagent/`. Trace data defaults to
`.clearagent/traces.sqlite`, and chat sessions default to
`.clearagent/chat.sqlite`.

The CLI reads `[tracing].enabled` and `[tracing].db_path` from this file for
`run`, `eval`, and `chat`. Direct Python API calls continue to use the values
passed to `create_agent`.

## Create An Agent

```python
from clearagent import create_agent, tool


@tool
def lookup_order(order_id: str) -> dict:
    """Look up an order."""
    return {"order_id": order_id, "status": "shipped", "eta": "Friday"}


agent = create_agent(
    name="support_agent",
    model="openai:gpt-4.1-mini",
    system_prompt="Help users with order status and refund questions.",
    tools=[lookup_order],
)
```

The `@tool` decorator converts type hints and the function docstring into an
OpenAI-compatible tool schema.

## Request Structured Output

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

ClearAgent maps the schema to the selected provider request shape and validates
the parsed output before returning it as `result.structured_output`.

## Run An Example

The customer support example uses a fake provider, so it does not need an API
key:

```bash
uv run python examples/customer_support/agent.py
```

Run the same agent through an eval suite:

```bash
uv run clearagent eval examples.customer_support.agent:agent examples/customer_support/evals/smoke.yaml
```

## Inspect A Trace

After running an agent or eval, inspect saved runs:

```bash
uv run clearagent trace list
uv run clearagent trace show <run_id>
uv run clearagent trace turns <run_id>
uv run clearagent request <run_id> --turn 0
```

Use `replay-request` to export the exact saved provider request for a turn:

```bash
uv run clearagent replay-request <run_id> --turn 0 --out request.json
```

## Start The Chat Backend

Serve an agent through the FastAPI chat backend:

```bash
uv run clearagent chat examples.customer_support.agent:agent
```

For a live OpenRouter-backed chat demo, set `OPENROUTER_API_KEY` in `.env` and
run:

```bash
uv run clearagent chat examples.openrouter_chat.agent:agent
```

## Next Steps

- Read [Installation](install.md) for dependency setup in another project.
- Read [Core Concepts](core-concepts.md) for the mental model.
- Add an eval suite with [Evals](evals.md).
- Wire evals into tests with [Pytest](pytest.md).
- Inspect request snapshots with [Tracing](tracing.md).
