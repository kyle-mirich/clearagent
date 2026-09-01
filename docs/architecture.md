# Architecture

ClearAgent Engine is the public backend core consumed by the private ClearAgent
Studio product.

## Engine and Studio

There is one implementation of the shared engine: this package. Studio depends
on a pinned public commit, owns the `clearagent_studio` namespace, and subclasses
engine `Settings` for its hosted configuration. No sync/export step is needed.

The dependency runs in one direction only. Studio imports the engine; the engine
imports nothing from Studio and must never learn the product's package name,
routes, schemas, or hosted settings.

| Fact | Value |
| --- | --- |
| Consumed as | Git-installed package pinned to a full 40-character commit SHA |
| Pinned in | Studio `pyproject.toml` and `uv.lock`; the Studio Docker image clones the public repository at that SHA |
| Engine namespaces Studio imports | `clearagent.builds`, `clearagent.builds.budgets`, `clearagent.builds.optimization`, `clearagent.builds.pipeline`, `clearagent.builds.planner`, `clearagent.builds.quality`, `clearagent.builds.task_spec`, `clearagent.config`, `clearagent.models`, `clearagent.runtime`, `clearagent.runtime.providers`, `clearagent.store` |
| Extension mechanism | Studio `Settings` subclasses `clearagent.config.Settings` |
| Prohibited | Vendored engine tree, namespace shim, editable sibling path dependency, export/sync script |

Studio builds its own FastAPI application. It does not mount or import
`clearagent.app.create_app`; the engine's HTTP surface exists for engine
adopters, not for the product.

### Updating the pinned commit

Engine side, in this repository:

1. Land and verify the change: `uv run ruff check src tests`,
   `uv run python -m mypy src`, `uv run pytest -q`, `uv build`.
2. Merge to the default branch and confirm the commit is reachable from a
   branch or tag that will be retained. An unreachable SHA still resolves until
   GitHub garbage-collects it, so a pin to a branch that is deleted after a
   squash merge will break Studio builds later, not immediately.
3. Record the full 40-character SHA Studio should adopt.

Studio side, in the private repository:

4. Replace the SHA in the `clearagent` dependency in `pyproject.toml`.
5. Regenerate the lockfile: `uv lock`.
6. Rebuild and verify: product tests, the PostgreSQL suite, and the frontend
   gates.

Prefer tagging the adopted commit so adopters can pin a release instead of a
raw SHA.

## Module map

| Module | Responsibility |
| --- | --- |
| `agent.py` | Runs a bounded model/tool loop through LangGraph `StateGraph`. |
| `graph/` | Composes multiple agents into a terminating linear graph. |
| `builds/optimization.py` | Thin native GEPA adapter and progress callbacks. |
| `builds/pipeline.py` | Planning, synthetic cases, execution, judges, holdout admission, and export. |
| `runtime/` | Provider-neutral messages, tools, schemas, and LangChain adapters. |
| `storage/` | Redacted SQLite trace protocol and lifecycle persistence. |
| `store.py` | Build projects, runs, versions, events, leases, and rate-limit state. |
| `app.py` | Generic health, invoke, and server-sent-event delivery only. |
| `command.py` | Local `build`, `eval`, and `serve` commands. |

## Seam map

| Concern | Engine owner | Studio owner |
| --- | --- | --- |
| Agent runtime, tools, structured output | `agent.py`, `runtime/` | — |
| Providers and model URIs | `runtime/providers/` | — |
| Build loop, datasets, judges, GEPA, admission | `builds/` | — |
| Build/run/version/event persistence | `store.py` | — |
| Trace persistence, redaction, replay, reports | `storage/`, `replay.py`, `reports.py` | — |
| Engine settings | `config.py` | subclasses it |
| Engine CLI | `command.py` | separate product CLI |
| Engine HTTP surface | `app.py` | separate product app |
| Product routes, requests, and responses | — | `app.py`, `models.py` |
| Owner identity, tokens, signatures | — | `dependencies.py` |
| Hosted policy: TTL, cleanup, quotas | — | `config.py` |
| Background workers, leases, admission | — | `app.py` |
| Document and website ingestion | — | `sources.py` |
| Chunking, embeddings, vector storage | — | `knowledge.py` |
| Grounded chat, citations, chat judges | — | `chat/` |
| Browser application | — | `web/` |
| Deployment and CI | — | `railway.json`, `Dockerfile`, `web/vercel.json` |

## Quality sequence

The engine's important quality sequence is:

```text
goal -> task spec -> generated train/validation/holdout cases
     -> seed evaluation -> GEPA optimization on train/validation
     -> holdout evaluation -> quality admission -> selected version
```

Holdout cases are evaluated after optimization and do not tune GEPA. Provider
requests and responses are redacted before trace persistence; deterministic mode
uses templates and local judging so the loop can run without credentials.

## Known boundary observations

These are current facts about the seam, not design goals. They are recorded so
adopters and contributors are not surprised by them.

- **Owner scoping is in the engine schema.** `owner_id` is a required column on
  `projects`, `runs`, `run_idempotency`, and `worker_leases` in `store.py`,
  appears on `ProjectRecord` and `RunRecord` in `models.py`, and is a required
  keyword on most `Store` methods and on `Build.report`, `Build.export`,
  `Build.load_agent`, and `Build.list_agents`. The engine CLI passes the constant
  `owner_id="cli"`. The column is tenancy-neutral in principle, but an engine
  adopter inherits it and must supply a value.
- **Rate limiting is split by mechanism and policy.** `store.py` owns the
  `api_rate_limits` table, `consume_rate_limit`, and active-run capacity
  admission (`owner_active_limit`, `global_active_limit`, and a `rate_limits`
  tuple on `create_run`). Studio supplies only the numbers. Keep that direction:
  mechanism here, hosted policy there.
- **Trace storage is SQLite-only.** `storage/` provides `SQLiteTraceStore` and
  the `TraceStore` protocol; there is no PostgreSQL trace store. `store.py`
  supports both SQLite and PostgreSQL, so an adopter running PostgreSQL keeps
  build records there and traces in SQLite unless they implement `TraceStore`.
- **Stream screening is applied in Studio, not in the engine.** The engine
  exports `response_has_meta_leakage` and `clean_runtime_response` from
  `runtime/contracts.py`, but `/api/v1/invoke/stream` in `app.py` emits raw
  deltas. The sentence-buffered screener that uses those primitives lives in the
  Studio chat module.
- **`Settings` error strings mention hosted limits.** The payload validators in
  `models.py` reject oversized schemas with the message "exceeds the hosted
  payload limit". The limits themselves are generic; only the wording refers to
  a hosted deployment.
