# ClearAgent Engine

ClearAgent Engine is the open-source backend behind the private ClearAgent
Studio product. It builds prompt-based agents on LangGraph, evaluates them,
optimizes their instructions with GEPA, and records the evidence locally.

This repository intentionally contains no Studio web application, source-ingestion
workflow, hosted chat contracts, or product-facing project routes. It exposes a
small generic FastAPI surface and a Python/CLI interface for the engine itself.

## What it provides

- **LangGraph runtime** — agents execute as a state graph with model and tool
  nodes, bounded tool loops, structured output, and trace lifecycle handling.
- **LangChain providers** — OpenAI, Anthropic, Google, OpenRouter-compatible,
  local, and Ollama model URIs share one provider interface.
- **Eval-first builds** — generate train/validation/holdout cases, run weighted
  judges and deterministic checks, optimize prompts with native GEPA, and admit
  only candidates that clear holdout quality gates.
- **Local evidence** — persist redacted runs, turns, model calls, tool calls,
  build events, candidate versions, and promotion decisions in SQLite or
  PostgreSQL.
- **Deterministic mode** — exercise the complete build loop without provider
  calls or API keys.

## Quick start

Use Python 3.14 and `uv`:

From another project, install this engine as a dependency (pin a full commit or
release in production):

```bash
uv add "clearagent @ git+https://github.com/kyle-mirich/clearagent.git"
```

For contributors in this checkout:

```bash
uv sync --locked --dev
uv run clearagent build \
  "Build a release notes summarizer for changelog entries." \
  --deterministic \
  --export prompt.md
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

## Scope boundary

This repository is the canonical engine implementation. The private Studio
product installs a pinned engine commit and adds its own product API,
source ingestion, retrieval, grounded chat, frontend, authentication, and
deployment configuration. Those concerns are deliberately kept out of this
repository.

Studio uses a separate `clearagent_studio` namespace and extends engine settings
with hosted configuration. Engine changes are made here once, not copied back
and forth. Generic build records and stored agent configurations belong here;
Studio's HTTP request/response schemas do not.

See [docs/architecture.md](docs/architecture.md) for the module map and the
engine-to-product seam.

## Status

Alpha. The engine interface, CLI, and minimal HTTP routes may still evolve.
