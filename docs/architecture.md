# Architecture

ClearAgent Engine is a public, local-first backend core for building,
evaluating, and improving prompt-based agents.

## Engine and its consumers

There is one implementation of the engine: this package. Downstream
applications consume it as a Git-installed package pinned to a full
40-character commit SHA and extend engine `Settings` by subclassing for their
own deployment configuration. No sync/export step is needed.

The dependency runs in one direction only. Consumers import the engine; the
engine imports nothing from its consumers and must never learn a consumer's
package name, routes, schemas, or deployment settings.

| Fact | Value |
| --- | --- |
| Consumed as | Git-installed package pinned to a full 40-character commit SHA |
| Extension mechanism | Consumers subclass `clearagent.config.Settings` for deployment policy |
| Prohibited | Vendored engine tree, namespace shim, editable sibling path dependency, export/sync script |

Consumers build their own FastAPI applications. They do not mount or import
`clearagent.app.create_app`; the engine's HTTP surface exists for engine
adopters running the engine directly.

### Releasing an adoptable commit

1. Land and verify the change: `uv run ruff check src tests`,
   `uv run python -m mypy src`, `uv run pytest -q`, `uv build`.
2. Merge to the default branch and confirm the commit is reachable from a
   branch or tag that will be retained. An unreachable SHA still resolves until
   GitHub garbage-collects it, so a pin to a branch that is deleted after a
   squash merge will break downstream builds later, not immediately.
3. Record the full 40-character SHA for adopters.

Prefer tagging the adopted commit so adopters can pin a release instead of a
raw SHA. Downstream, replace the SHA in the `clearagent` dependency,
regenerate the lockfile, and re-verify.

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

| Concern | Engine owner | Downstream owner |
| --- | --- | --- |
| Agent runtime, tools, structured output | `agent.py`, `runtime/` | — |
| Providers and model URIs | `runtime/providers/` | — |
| Build loop, datasets, judges, GEPA, admission | `builds/` | — |
| Build/run/version/event persistence | `store.py` | — |
| Trace persistence, redaction, replay, reports | `storage/`, `replay.py`, `reports.py` | — |
| Engine settings | `config.py` | subclassed downstream |
| Engine CLI | `command.py` | separate downstream CLI |
| Engine HTTP surface | `app.py` | separate downstream app |
| Product routes, requests, and responses | — | downstream |
| Identity, tokens, signatures | — | downstream |
| Deployment policy: TTL, cleanup, quotas | — | downstream |
| Background workers, leases, admission limits | — | downstream |
| Document and website ingestion | — | downstream |
| Chunking, embeddings, vector storage | — | downstream |
| Grounded chat, citations, chat judges | — | downstream |
| Browser application | — | downstream |
| Deployment and CI | — | downstream |

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
  tuple on `create_run`). Consumers supply only the numbers. Keep that
  direction: mechanism here, deployment policy there.
- **Trace storage is SQLite-only.** `storage/` provides `SQLiteTraceStore` and
  the `TraceStore` protocol; there is no PostgreSQL trace store. `store.py`
  supports both SQLite and PostgreSQL, so an adopter running PostgreSQL keeps
  build records there and traces in SQLite unless they implement `TraceStore`.
- **Stream screening primitives stay generic.** The engine
  exports `response_has_meta_leakage` and `clean_runtime_response` from
  `runtime/contracts.py`, but `/api/v1/invoke/stream` in `app.py` emits raw
  deltas. Sentence-buffered screening using those primitives lives downstream.
- **Payload validators bound schema sizes.** The validators in `models.py`
  reject oversized schemas and tool definitions. The limits themselves are
  generic engine constants.
