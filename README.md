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

Continue with the canonical
[First Traced Eval](https://github.com/kyle-mirich/clearagent/blob/main/docs/install.md#first-traced-eval).
It supplies copy-pastable `agent.py` and `smoke.yaml` files, uses a deterministic
provider with no API key, records a local trace, and runs the first eval. The
same installation page covers live provider keys and optional extras.

Release maintainers can follow the
[publishing checklist](https://github.com/kyle-mirich/clearagent/blob/main/docs/publishing.md).

## Project Structure

- `src/clearagent/` - installable runtime, providers, tracing, evals, CLI, and
  local chat backend
- `tests/` - deterministic unit and integration coverage; live tests are
  explicitly opt-in
- `examples/` - runnable agents, graph flows, and eval suites
- `docs/` - curated guides, architecture notes, and API/CLI reference
- `scripts/check.sh` - the same 90%-coverage quality gate used by CI

The paid provider compatibility suite is separate from that gate. Contributors
can follow the bounded opt-in workflow in
[Live Provider Compatibility](https://github.com/kyle-mirich/clearagent/blob/main/docs/live-provider-compatibility.md).

## Contributor Setup

These commands are for a checkout of this repository, not for applications that
depend on ClearAgent:

```bash
uv sync --locked --all-extras --dev
./scripts/check.sh
```

The gate runs deterministic non-live tests with at least 90% package line
coverage, followed by Ruff, mypy, and documentation checks. CI runs the same
gate on Python 3.14.

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
