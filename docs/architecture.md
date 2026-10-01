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

The five generation/evaluation roles default to `openai:gpt-6-luna`; explicit
settings and environment model overrides are preserved. The OpenAI provider
sets reasoning effort to `none` for GPT-6 Luna so sampling parameters and
function tools retain their supported behavior. Other model families keep their
own reasoning defaults.

The engine's important quality sequence is:

```text
goal -> task spec -> generated train/validation/holdout cases
     -> seed evaluation -> GEPA optimization on train/validation
     -> holdout evaluation -> quality admission -> selected version
```

Holdout cases are evaluated after optimization and do not tune GEPA. Provider
requests and responses are redacted before trace persistence; deterministic mode
uses templates and local judging so the loop can run without credentials.

Generated input/expected pairs must be distinct across all splits; generation
fails rather than renaming duplicate cases. Fixed leakage checks inspect string
values throughout structured outputs, including nested fields. Current promotion
records required-behavior and pass-rate evidence but does not enforce absolute
quality floors (`thresholds_enforced=false`); an optimized candidate must improve
on the seed's holdout score and the incumbent, when present. Otherwise the
incumbent is retained, or the seed is selected for a first build.

Provider generation options apply to synchronous, asynchronous, and text-stream
requests. Native tool binding translates schemas and named tool selection for
each provider. Intermediate tool-call turns are not validated as final structured
answers. Missing provider token usage remains unknown; reported zero usage is
distinct from missing usage, and provider costs are retained only when reported.

The HTTP stream adapter adds the system instruction once and closes abandoned
agent iterators after any in-flight provider read finishes. A late provider error
cannot change a canceled run into a failed run: the failure transition updates
only rows still in queued/running state.

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

## Store connection lifecycle

`Store(database_url, postgres_pool_size=0)` preserves direct PostgreSQL
connections and SQLite behavior. A positive size creates a pool owned by that
Store, with zero minimum connections and the supplied maximum. The pool uses
a five-second acquisition timeout, five-second connect timeout, sixty-second
idle limit, and a health check before borrowing. Limits are per Store, not
global across processes.

`Store.connect()` commits on success and rolls back failed pooled transactions
before returning the connection. The ten-second statement timeout uses
`SET LOCAL`, so it resets at the transaction boundary. Initialization failures
during schema migration close the pool. Owners must call `Store.close()` at
shutdown after finishing database work. Closing a pool is idempotent and
prevents subsequent borrowing. With no pool, `close()` is a no-op.

Pooling requires `psycopg-pool>=3.2,<4`, installed as a runtime dependency. It
is selected only through the Store constructor; engine Settings and the CLI
do not enable it. Detailed traces still use `SQLiteTraceStore`.

Direct connections close even when SQLite pragmas or PostgreSQL timeout setup
fail. A failed rollback does not replace the original setup/transaction error.

## Build provider ownership

Each build completion and tool evaluation owns the provider it constructs and
invokes its optional `close()` after use. Canceling a sync-only provider's async
fallback returns promptly, while its real worker keeps the provider open until
the call finishes. The canceled retry coroutine starts no further attempts;
late worker errors are retrieved and cleanup runs once. Existing SDK retries
inside that worker may still finish and retain their budget reservation.

The OpenAI and Anthropic adapters share native SDK transports between models;
per-case cleanup does not close those shared clients. Custom closeable providers
retain their explicit ownership contract.

## Model-call usage provenance

Build `model_call_completed` events include a unique `call_id`, `usage_known`, and
nullable `reported_cost_usd`. The ID follows a completed logical provider call;
separate empty-answer attempts receive separate IDs. Consumers can deduplicate
an event copied or delivered more than once. `usage_known=false` means legacy
numeric token defaults are not measured usage. `reported_cost_usd` is populated
only from a finite, nonnegative numeric provider `cost`/`total_cost`; it remains
unknown when absent. Existing `estimated_cost_usd` values serve budget accounting
and may use fallback prices, so consumers must not call them measured spend.
The callback remains best effort. Failed transport attempts that produce no
completion response are outside this event's coverage.

`Build.execute(store, run_id, on_model_call=callback)` exposes the same observer
seam as planning. The pipeline wraps this callback so telemetry failures do not
fail execution. This lets consumers measure native build planning calls directly
without trusting planner events copied from client-supplied plans. Fixture
replay is tagged `usage_source=replay`; recorded token/cost data never becomes
newly measured provider usage, while simulated budget accounting is preserved.

## Pre-attempt build budget

`PreflightBudget(limits, request_bound)` accepts finite `BudgetLimits` and a
callback returning `RequestBudget` for the fully built provider request.
`RequestBudget.max_model_calls` is positive; token and dollar bounds are
nonnegative. All three are aggregate upper bounds for one provider invocation,
including every possible internal SDK retry. The callback must include provider
defaults and any adapter-generated structured schemas missing from the generic
request body, output limits, input framing, and applicable billing modifiers.
It runs synchronously outside the reservation lock and may run concurrently;
callers must provide a thread-safe callback.
Unknown bounds reject before invocation. Dollar admission uses exact rational
addition of the supplied decimal values, independent of ambient numeric context.

The guard reserves atomically before every outer sync/async retry; empty-answer
and JSON-repair loops re-enter it. Instrumented build tool providers reserve for
completion and streaming. A `Build(..., preflight_budget=guard)` preserves that
same guard through planning and profile execution. All reservations remain
charged against admission after success, transport error, timeout, cancellation,
or unknown usage. Canceled thread-backed calls may continue; their reservations
remain. No response-driven refunds are inferred. Synthetic generation propagates
budget rejection and cancels queued tasks rather than degrading coverage.

This guard supplements the separate post-response `BudgetTracker`; it does not
change default profile policy or generic cost estimates. The post-response
tracker retains consumed resources even when its limit is crossed. Preflight
enforcement is conditional on trustworthy caller bounds and covers the lifetime
of one in-process guard, not provider invoices or durable quotas. Consumers own
cross-process admission, general runtime/chat calls, and external tool spending.
