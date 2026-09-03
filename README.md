# ClearAgent Engine

ClearAgent Engine is an open-source backend for building prompt-based agents
on LangGraph, evaluating them, optimizing their instructions with GEPA, and
recording the evidence locally.

This repository intentionally contains no web application, document-ingestion
workflow, multi-tenant chat contracts, or deployment-specific project routes.
It exposes a small generic FastAPI surface and a Python/CLI interface for the
engine itself.

## What it provides

- **LangGraph runtime** — agents execute as a state graph with model and tool
  nodes, bounded tool loops, structured output, and trace lifecycle handling.
- **LangChain providers** — OpenAI, Anthropic, Google, OpenRouter-compatible,
  local, and Ollama model URIs share one provider interface.
- **Eval-first builds** — generate train/validation/holdout cases, run weighted
  judges and deterministic checks, optimize prompts with native GEPA, and admit
  only candidates that clear holdout quality gates.
- **Local evidence** — persist redacted runs, turns, model calls, tool calls,
  build events, candidate versions, and promotion decisions. Build records
  (`clearagent.store.Store`) use SQLite or PostgreSQL; detailed provider traces
  (`clearagent.storage.SQLiteTraceStore`) are SQLite-only.
- **Deterministic mode** — exercise the complete build loop without provider
  calls or API keys.

## Quick start

### What

ClearAgent is a Python 3.11+ engine for building an agent from a goal, testing
it, improving its instructions, and keeping the best version.

Install the first GitHub release with `uv`:

```bash
uv add "clearagent @ git+https://github.com/kyle-mirich/clearagent.git@v0.1.0"
```

### Why

The `v0.1.0` tag keeps the install reproducible and avoids the unrelated
package currently using the `clearagent` name on PyPI. ClearAgent is alpha, so
install a release tag or a full commit SHA instead of tracking `main`.

### How

Run a complete offline build—no API key or provider account required:

```bash
uv run clearagent build \
  "Build a release notes summarizer for changelog entries." \
  --deterministic \
  --export prompt.md
```

To contribute from this checkout:

```bash
uv sync --locked --dev
```

The Python API exposes the same build engine:

```python
from clearagent import Build, Settings, PlanningRequest

engine = Build(Settings(deterministic_mode=True))
plan = engine.plan(PlanningRequest(goal="Build a release notes summarizer."))
```

See [examples/summarizer.py](examples/summarizer.py) for a complete local run.

## CLI

```bash
clearagent build "What the agent should do" [--level quick|standard|deep]
clearagent eval "The goal" --instruction "The prompt to score"
clearagent serve --port 8000
```

`build` plans the task, generates and validates an evaluation set, scores the
seed, runs GEPA, verifies the candidate on holdout cases, and reports the
selected version.

## FastAPI

`clearagent serve` exposes only generic engine operations:

| Route | Purpose |
| --- | --- |
| `GET /healthz` | Liveness |
| `GET /readyz` | Local configuration snapshot |
| `POST /api/v1/invoke` | Invoke an agent and return its result |
| `POST /api/v1/invoke/stream` | Stream text deltas as server-sent events |

```python
from clearagent.app import create_app

app = create_app()
```

## Scope

This repository is the canonical engine implementation. Downstream
applications install this package at a pinned Git commit and add their own
product API, ingestion, retrieval, chat, frontend, authentication, and
deployment configuration. Those concerns are deliberately kept out of this
repository: the engine stays generic, local-first, and testable without
credentials or network access.

Put a change in the engine when all of the following hold:

- It can be expressed without HTTP request/response schemas, without a user
  identity, and without a deployment-specific service endpoint.
- A second consumer could plausibly use it from Python or the CLI alone.
- It can be tested deterministically, with no provider credentials and no
  network.

Keep a change downstream when any of the following hold:

- It names a product route, a product request/response schema, or a browser
  field.
- It depends on authentication, tenancy scoping, quotas, TTLs, or cleanup policy.
- It requires a vector store, an embedding service, or a hosting platform.
- It fetches, parses, chunks, or indexes external documents.

Engine `Settings` owns local engine defaults only. Deployment policy is added
by subclassing it downstream, never by adding fields here.

See [docs/architecture.md](docs/architecture.md) for the module map, the seam
map, and known boundary observations.

## Status

Alpha. The engine interface, CLI, and minimal HTTP routes may still evolve.
