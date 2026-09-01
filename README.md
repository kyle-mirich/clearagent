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
  build events, candidate versions, and promotion decisions. Build records
  (`clearagent.store.Store`) use SQLite or PostgreSQL; detailed provider traces
  (`clearagent.storage.SQLiteTraceStore`) are SQLite-only.
- **Deterministic mode** — exercise the complete build loop without provider
  calls or API keys.

## Quick start

Use Python 3.14 and `uv`:

From another project, install this engine as a dependency. Pin a full commit
SHA, the way Studio does, so builds stay reproducible:

```bash
uv add "clearagent @ git+https://github.com/kyle-mirich/clearagent.git@<full commit sha>"
```

An unpinned install tracks the default branch and will change under you:

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

This repository is the canonical engine implementation. The private ClearAgent
Studio product is a separate repository that installs this package at a pinned
Git commit and adds its own product API, source ingestion, retrieval, grounded
chat, frontend, authentication, and deployment configuration. Those concerns
are deliberately kept out of this repository.

Nothing here is a projection of Studio and nothing is copied back from it.
Engine changes are made once, in this repository, and adopted by Studio through
the pin.

### How Studio consumes this package

Studio declares this repository as an ordinary Git-installed dependency pinned
to an exact commit:

```toml
"clearagent @ git+https://github.com/kyle-mirich/clearagent.git@<full commit sha>"
```

The pin is exhaustive. Both `pyproject.toml` and `uv.lock` carry the SHA, and
the Studio Docker image clones the public repository at that SHA. There is no
vendored engine tree, no namespace shim, and no export or sync script. Studio
imports the engine directly (`from clearagent.builds import Build`,
`from clearagent.store import Store`, `from clearagent.models import
PlanningRequest, SavedAgentConfig`) and subclasses `clearagent.config.Settings`
for its hosted fields, so engine defaults have exactly one owner.

A commit pin is used instead of a version range because the engine is alpha.
The pin makes a Studio deployment reproducible and turns every engine change
into an explicit, reviewable decision instead of a silent upgrade.

### Updating the pin

Engine side, in this repository:

1. Land and verify the change: `uv run ruff check src tests`,
   `uv run python -m mypy src`, `uv run pytest -q`, `uv build`.
2. Merge to the default branch and confirm the commit is reachable from a
   branch or tag that will be retained. An unreachable SHA still resolves
   until GitHub garbage-collects it, which makes builds fail long after the
   merge.
3. Record the full 40-character SHA Studio should adopt.

Studio side, in the private repository:

4. Replace the SHA in the `clearagent` dependency in `pyproject.toml`.
5. Regenerate the lockfile: `uv lock`.
6. Rebuild and verify: product tests, the PostgreSQL suite, and the frontend
   gates.

Never substitute an editable sibling path dependency for the pin. That makes
Studio builds depend on an uncommitted checkout.

### Capability matrix

Everything in the Engine column is in this repository. Everything in the
Studio column lives in the private product repository.

| Capability | Engine (`clearagent`) | Studio (`clearagent_studio`) |
| --- | --- | --- |
| LangGraph agent runtime: bounded model/tool loop, structured output | owned — `agent.py` | reuses |
| Linear multi-agent graph composition | owned — `graph/` | not used |
| Model URIs: openai, openrouter, anthropic, google, local, ollama | owned — `runtime/providers/model_uri.py` | reuses |
| LangChain provider adapters | owned — `runtime/providers/langchain_provider.py` | reuses |
| Deterministic provider for offline runs | owned — `runtime/providers/base.py` | reuses |
| Tool decorator and tool schema | owned — `runtime/tools.py` | reuses |
| Prompt/response meta-leakage screening primitives | owned — `runtime/contracts.py` | reuses |
| Build engine: plan, execute, report, export, load, list | owned — `builds/module.py` | reuses |
| Planning, task spec, Agent PRD, quality contract | owned — `builds/planner.py`, `task_spec.py`, `quality.py` | reuses |
| Synthetic train/validation/holdout case generation | owned — `builds/datasets.py` | reuses |
| Weighted LLM judges and deterministic checks | owned — `builds/scoring.py` | reuses |
| Native GEPA prompt optimization | owned — `builds/optimization.py` | reuses |
| Holdout admission and promotion decision | owned — `builds/admission.py` | reuses |
| Budget profiles and metric call budgets | owned — `builds/budgets.py` | reuses |
| Build/run/version/event persistence, SQLite and PostgreSQL | owned — `store.py` | reuses |
| Worker leases and run capacity admission (mechanism) | owned — `store.py` | supplies the limits |
| Credential redaction and run trace persistence, SQLite | owned — `storage/` | reuses |
| Trace replay and trace reports | owned — `replay.py`, `reports.py` | reuses |
| Generic HTTP: `/healthz`, `/readyz`, `/api/v1/invoke`, `/api/v1/invoke/stream` | owned — `app.py` | not used |
| Generic CLI: `build`, `eval`, `serve` | owned — `command.py` | not used |
| Product HTTP API: projects, plans, agents, runs, versions, feedback, playground, export, `/version` | absent | owned — `app.py` |
| Document and website ingestion with SSRF pinning | absent | owned — `sources.py` |
| Chunking, embeddings, Qdrant and in-memory retrieval | absent | owned — `knowledge.py` |
| Grounded chat: retrieval tool, citations, judges, SSE streaming | absent | owned — `chat/module.py` |
| Authentication, bearer tokens, HMAC owner signature, owner scoping | absent | owned — `dependencies.py` |
| Hosted policy: environment, TTL, cleanup, quotas, Qdrant, embeddings | absent | owned — `config.py` |
| Background build worker, job lifecycle, and hosted limit values | absent | owned — `app.py` |
| Browser application | absent | owned — `web/` |
| Deployment: Docker/Railway API, Vercel frontend, CI | absent | owned — `railway.json`, `Dockerfile`, `web/vercel.json` |
| Product CLI | absent | owned — `command.py` |

Studio does not use the engine's `create_app`; it builds its own FastAPI
application and calls the engine's Python API. The engine's HTTP surface exists
for engine adopters, not for the product.

### Where does a change belong?

Apply these rules in order. When a change matches both an engine rule and a
Studio rule, keep it in Studio until a second consumer needs it.

Put it in the engine when all of the following hold:

- It can be expressed without HTTP request/response schemas, without a user
  identity, and without a hosted service endpoint.
- A second consumer could plausibly use it from Python or the CLI alone.
- It can be tested deterministically, with no provider credentials and no
  network.

Keep it in Studio when any of the following hold:

- It names a product route, a product request/response schema, or a browser
  field.
- It depends on authentication, owner scoping, quotas, TTLs, or cleanup policy.
- It requires Qdrant, an embedding service, Vercel, Railway, or a signed cookie.
- It fetches, parses, chunks, or indexes external documents.

Assignments already in force:

- Generic build records, versions, events, feedback, and saved agent
  configurations belong in `src/clearagent/models.py` and `store.py`.
- Studio HTTP schemas belong in `src/clearagent_studio/models.py`. They may
  import engine types such as `FeedbackKind` and `RequestModel`; the engine
  never imports theirs.
- Engine `Settings` owns local engine defaults only. Hosted policy is added by
  subclassing it in Studio, never by adding fields here.
- Meta-leakage screening is engine-owned. Applying it to a product stream is
  Studio-owned.

See [docs/architecture.md](docs/architecture.md) for the module map, the seam
map, and known boundary observations.

## Status

Alpha. The engine interface, CLI, and minimal HTTP routes may still evolve.
