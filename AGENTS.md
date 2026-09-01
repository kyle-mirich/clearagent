# ClearAgent Engine Instructions

This repository is the public, local-first ClearAgent engine. Keep Studio-only
HTTP contracts, source ingestion, grounded product chat, frontend code, and
deployment secrets out of it.

This repository is the canonical implementation, not a generated projection.
Private Studio installs this package at a pinned Git commit under a distinct
`clearagent_studio` namespace. Keep engine settings/models free of Studio HTTP
schemas and hosted configuration. Preserve the top-level consumer imports.

## Setup

Use Python 3.11 or newer and `uv`:

```bash
uv sync --locked --dev
```

Do not commit `.env`, credentials, local databases, build artifacts, or traces.

## Verification

Run the focused checks while iterating:

```bash
uv run ruff check src tests
uv run python -m mypy src
uv run pytest -q
uv build
```

The default tests are deterministic and must not require provider credentials or
external network access. Add regression coverage for changed runtime, CLI, HTTP,
provider, persistence, or evaluation behavior.

## Repository map

- `src/clearagent/agent.py` — LangGraph-backed agent runtime.
- `src/clearagent/graph/` — bounded linear graph composition.
- `src/clearagent/builds/` — planning, datasets, judges, GEPA, and promotion.
- `src/clearagent/runtime/` — messages, tools, structured output, and providers.
- `src/clearagent/storage/` — redacted trace persistence.
- `src/clearagent/store.py` — build/run/version persistence.
- `src/clearagent/app.py` — minimal generic FastAPI delivery.
- `src/clearagent/command.py` — generic engine CLI.
- `tests/` — deterministic runtime, build, trace, CLI, and HTTP contracts.

Keep the public interface small and deep: accept dependencies at seams, preserve
credential redaction, keep holdout cases out of optimization, and make failures
observable without leaking secrets.
