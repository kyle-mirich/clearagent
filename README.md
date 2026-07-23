# ClearAgent

[![CI](https://github.com/kyle-mirich/clearagent/actions/workflows/ci.yml/badge.svg)](https://github.com/kyle-mirich/clearagent/actions/workflows/ci.yml)
[![Python 3.14](https://img.shields.io/badge/python-3.14-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

ClearAgent is a tiny eval-first Python agent framework.

It is built for developers who want plain Python tools, OpenAI-compatible provider
support, automatic local SQLite traces, exact provider request snapshots,
turn-level replay, YAML eval suites, trace-to-eval generation, structured
outputs, and pytest integration.

It is not a general-purpose graph framework, observability SaaS, or deployment
platform.

ClearAgent is currently an alpha project. The stable local core is tested and
documented, while newer provider and chat surfaces are labeled in the
[support matrix](https://github.com/kyle-mirich/clearagent/blob/main/docs/status.md).

## Why ClearAgent

Agent behavior is difficult to improve when requests, tool calls, and failures
are invisible. ClearAgent keeps that feedback loop local and explicit:

- define agents and typed tools in plain Python
- save redacted provider requests and every execution turn to SQLite
- turn observed traces into repeatable YAML eval cases
- run deterministic evals through the CLI or normal pytest tests
- replay stored requests and compare responses while using fresh credentials
- inspect runs through reports or the bundled local chat and trace viewer

## Open Source And Studio

This MIT package is the complete local developer core: agents, tools, provider
adapters, structured output, bounded linear graphs, evals, pytest helpers,
SQLite traces, replay, reports, and the local debugging chat app.

ClearAgent Studio is a separate product layer for natural-language agent
planning, document ingestion, synthetic datasets, generated judges, prompt
optimization, held-out promotion, hosted projects, authentication, and managed
storage. Those hosted and optimization features are intentionally not bundled
in this package.

## Quickstart

Install the current package directly from the public GitHub repository:

```bash
uv add git+https://github.com/kyle-mirich/clearagent.git
```

The package is not on PyPI yet. Release maintainers can follow the documented
[publishing checklist](https://github.com/kyle-mirich/clearagent/blob/main/docs/publishing.md).

Use Python 3.14 or newer.

Contributor setup from this repository:

```bash
uv sync --all-extras --dev
uv run pytest
uv run clearagent init
uv run clearagent chat examples.customer_support.agent:agent
```

Run the full local quality gate with:

```bash
uv run bash scripts/check.sh
```

The gate runs the full test suite with source coverage, requires at least 90%
line coverage for `clearagent`, then runs Ruff, mypy, and documentation-link
checks. CI runs the same gate on Python 3.14.

```python
from clearagent import create_agent, tool


@tool
def lookup_order(order_id: str) -> dict:
    return {"order_id": order_id, "status": "shipped", "eta": "Friday"}

agent = create_agent(
    name="support_agent",
    model="openai:gpt-4.1-mini",
    system_prompt="Help users with order status.",
    tools=[lookup_order],
)
```

Structured outputs can be requested with a Pydantic model:

```python
from pydantic import BaseModel

class TicketLabel(BaseModel):
    label: str
    confidence: float

agent = create_agent(
    name="classifier",
    model="openrouter:openai/gpt-4o-mini",
    response_format=TicketLabel,
)
```

ClearAgent maps structured outputs to each provider's native request shape where
available and validates the parsed output before returning it as
`result.structured_output`. Streaming runs validate the joined response before
the trace is marked successful.

Turn an observed trace into a regression test and export a readable report:

```bash
uv run clearagent trace-to-eval <run_id> --out generated.yaml
uv run clearagent trace-report <run_id> --out report.md
```

The packaged local browser client also includes a read-only **Traces** mode for
debugging recent runs, graph node turns, model requests, and tool call results.

## Project Structure

- `src/clearagent/` - installable runtime, providers, tracing, evals, CLI, and
  local chat backend
- `tests/` - deterministic unit and integration coverage; live tests are
  explicitly opt-in
- `examples/` - runnable agents, graph flows, and eval suites
- `docs/` - curated guides, architecture notes, and API/CLI reference
- `scripts/check.sh` - the same 90%-coverage quality gate used by CI

## Documentation

Start with the [documentation index](https://github.com/kyle-mirich/clearagent/blob/main/docs/site.md). It is organized as a
website-ready learning path:

- [Installation](https://github.com/kyle-mirich/clearagent/blob/main/docs/install.md)
- [Getting Started](https://github.com/kyle-mirich/clearagent/blob/main/docs/getting-started.md)
- [Core Concepts](https://github.com/kyle-mirich/clearagent/blob/main/docs/core-concepts.md)
- [Support Status](https://github.com/kyle-mirich/clearagent/blob/main/docs/status.md)
- [Guides and Reference](https://github.com/kyle-mirich/clearagent/blob/main/docs/site.md)
- [Publishing](https://github.com/kyle-mirich/clearagent/blob/main/docs/publishing.md)
- [Documentation Guide](https://github.com/kyle-mirich/clearagent/blob/main/docs/contributing-docs.md)

## Chat backend

ClearAgent includes a FastAPI chat backend for running an agent from a browser
client. It stores chat sessions and messages in local SQLite and streams model
tokens from OpenAI-compatible providers such as OpenRouter.

See [docs/chat.md](https://github.com/kyle-mirich/clearagent/blob/main/docs/chat.md).

## Community And Maintenance

- [Contributing](https://github.com/kyle-mirich/clearagent/blob/main/CONTRIBUTING.md)
- [Code of Conduct](https://github.com/kyle-mirich/clearagent/blob/main/CODE_OF_CONDUCT.md)
- [Security Policy](https://github.com/kyle-mirich/clearagent/blob/main/SECURITY.md)
- [Support](https://github.com/kyle-mirich/clearagent/blob/main/SUPPORT.md)
- [Changelog](https://github.com/kyle-mirich/clearagent/blob/main/CHANGELOG.md)
- [MIT License](https://github.com/kyle-mirich/clearagent/blob/main/LICENSE)
