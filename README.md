# ClearAgent

[![CI](https://github.com/kyle-mirich/clearagent/actions/workflows/ci.yml/badge.svg)](https://github.com/kyle-mirich/clearagent/actions/workflows/ci.yml)
[![Python 3.14](https://img.shields.io/badge/python-3.14-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/kyle-mirich/clearagent/blob/main/LICENSE)

ClearAgent is a local-first, eval-first Python library for building and testing
small agents.

It is built for developers who want plain Python tools, native and OpenAI-compatible provider
support, automatic local SQLite traces, exact provider request snapshots,
turn-level replay, YAML eval suites, trace-to-eval generation, structured
outputs, and pytest integration.

It is not a general-purpose graph framework, observability SaaS, or deployment
platform.

The product is deliberately narrow: define an agent in ordinary Python, inspect
the local trace, turn observed behavior into a repeatable eval, and replay or
compare changes. Read the
[Product Scope](https://github.com/kyle-mirich/clearagent/blob/main/docs/product-scope.md)
for the public-library boundary and the capabilities reserved for ClearAgent
Studio.

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

Use Python 3.14 or newer and
[`uv`](https://docs.astral.sh/uv/getting-started/installation/). This pre-release
path creates a project and installs ClearAgent from its public GitHub repository
without assuming that a PyPI release exists:

```bash
uv init --bare --python 3.14 clearagent-quickstart
cd clearagent-quickstart
uv add "clearagent @ git+https://github.com/kyle-mirich/clearagent.git"
```

Create `agent.py` with a deterministic provider so the first run needs no API
key:

```python
from clearagent import create_agent, tool
from clearagent.providers.base import FakeProvider, ProviderResponse, ToolCall


@tool
def lookup_order(order_id: str) -> dict:
    """Look up an order."""
    return {"order_id": order_id, "status": "shipped", "eta": "Friday"}


agent = create_agent(
    name="support_agent",
    model="openai:gpt-4.1-mini",
    system_prompt="Help users with order status.",
    tools=[lookup_order],
    provider=FakeProvider(
        [
            ProviderResponse.fake_tool_call(
                ToolCall(
                    id="call_lookup_order",
                    name="lookup_order",
                    arguments={"order_id": "A123"},
                )
            ),
            ProviderResponse.fake_text("Order A123 has shipped and arrives Friday."),
        ]
    ),
)


if __name__ == "__main__":
    result = agent.run("Where is order A123?")
    print(result.output)
    print(f"trace: {result.trace_db_path}")
    print(f"run_id: {result.run_id}")
```

Run the agent and locate its local SQLite trace:

```bash
uv run python agent.py
uv run clearagent trace list
```

Create `smoke.yaml`:

```yaml
name: quickstart
cases:
  - name: shipped order
    input: Where is order A123?
    checks:
      - contains: shipped
      - contains: Friday
```

Run the eval. It imports a fresh `agent` object, executes the case, and records
another local trace:

```bash
uv run clearagent eval agent:agent smoke.yaml
```

Copy a run ID from `trace list` to turn any observed trace into a starter eval:

```bash
uv run clearagent trace-to-eval <run_id> --out generated.yaml
```

See [Installation](https://github.com/kyle-mirich/clearagent/blob/main/docs/install.md)
for live provider keys and optional extras. Release maintainers can follow the
[publishing checklist](https://github.com/kyle-mirich/clearagent/blob/main/docs/publishing.md).

## Project Structure

- `src/clearagent/` - installable runtime, providers, tracing, evals, CLI, and
  local chat backend
- `tests/` - deterministic unit and integration coverage; live tests are
  explicitly opt-in
- `examples/` - runnable agents, graph flows, and eval suites
- `docs/` - curated guides, architecture notes, and API/CLI reference
- `scripts/check.sh` - the same branch-coverage, browser, docs, type, and built-
  distribution quality gate used by CI

Pytest blocks external sockets by default. The paid provider compatibility
suite is a separate, explicitly opted-in script outside that gate. Contributors
can follow the bounded workflow in
[Live Provider Compatibility](https://github.com/kyle-mirich/clearagent/blob/main/docs/live-provider-compatibility.md).

## Contributor Setup

These commands are for a checkout of this repository, not for applications that
depend on ClearAgent:

```bash
uv sync --locked --all-extras --dev
uv run playwright install chromium
uv run bash scripts/check.sh
```

The gate runs unit, integration, and real Chromium tests; requires at least 95%
combined line/branch coverage, 90% per touched product file, and complete line
and branch coverage for changed executable code. It rejects coverage or static-
analysis suppressions, skipped/xfail/deselected outcomes, collection and config
overrides, broad test networking, and static-client changes without a browser-
test change; then runs Ruff, mypy, documentation links, and a fresh built-wheel
smoke test. CI runs that gate on Python 3.14 and repeats distribution smoke tests
on Ubuntu, macOS, and Windows.

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
