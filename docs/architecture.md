# Architecture

ClearAgent Engine is the public backend core consumed by the private ClearAgent
Studio product.

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

The private product adds planning UI, hosted project routes, file and website
ingestion, retrieval, grounded chat, authentication, and its frontend around the
engine. The public repository does not expose those product contracts.

The engine's important quality sequence is:

```text
goal -> task spec -> generated train/validation/holdout cases
     -> seed evaluation -> GEPA optimization on train/validation
     -> holdout evaluation -> quality admission -> selected version
```

Holdout cases are evaluated after optimization and do not tune GEPA. Provider
requests and responses are redacted before trace persistence; deterministic mode
uses templates and local judging so the loop can run without credentials.
