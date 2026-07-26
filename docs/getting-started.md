# Getting Started

ClearAgent is a local-first, eval-first Python library. The shortest useful
feedback loop is:

```text
define an agent -> run it -> inspect its SQLite trace -> run or generate an eval
```

## Install In An External Project

Use Python 3.14 or newer and follow the canonical
[installation and first traced eval](install.md#install-as-a-dependency). The
pre-release path installs from the public GitHub repository; the same page says
when to use a PyPI dependency and how to install provider extras.

## Complete The Offline Feedback Loop

Follow [First Traced Eval](install.md#first-traced-eval) to create `agent.py` and
`smoke.yaml`. The example uses `FakeProvider`, so it is deterministic and needs
no provider credentials.

Run the agent, locate the trace, and run the eval:

```bash
uv run python agent.py
uv run clearagent trace list
uv run clearagent eval agent:agent smoke.yaml
```

Direct runs and eval cases write to `.clearagent/traces.sqlite` by default. The
agent script prints its trace path and run ID; `trace list` shows the same run in
the local database.

## Inspect Or Promote A Trace

Copy a run ID from `trace list` and inspect the run or turn it into a starter
eval:

```bash
uv run clearagent trace show <run_id>
uv run clearagent trace turns <run_id>
uv run clearagent request <run_id> --turn 0
uv run clearagent trace-to-eval <run_id> --out generated.yaml
```

The generated YAML is a draft based on the observed input and output. Review its
checks before adding it to regression coverage.

## Configure A Live Provider

After the offline loop works, set only the API key for the provider you use and
replace the fake provider in `agent.py` with the normal model-backed agent
configuration. For example:

```bash
export OPENAI_API_KEY=...
```

```python
from clearagent import create_agent

agent = create_agent(
    name="support_agent",
    model="openai:gpt-4.1-mini",
    system_prompt="Help users with order status.",
)
```

See [Providers](providers.md) for OpenRouter, local, Ollama, Anthropic, and Google
model URIs and credentials.

## Optional Local Config

Create `.clearagent/config.toml` when you want CLI tracing settings shared by
`run`, `eval`, and `chat`:

```bash
uv run clearagent init
```

The command creates the file only when it is absent. Review it before deciding
whether the project should commit those shared settings. Direct Python API
calls continue to use values passed to `create_agent`.

## Contributor Setup

The following commands are only for a checkout of the ClearAgent repository,
not for an application that depends on the package:

```bash
uv sync --locked --all-extras --dev
./scripts/check.sh
uv run python examples/customer_support/agent.py
uv run clearagent eval examples.customer_support.agent:agent examples/customer_support/evals/smoke.yaml
```

The quality gate runs deterministic non-live tests with at least 90% package
line coverage, then Ruff, mypy, and the documentation checker.

## Next Steps

- Read [Core Concepts](core-concepts.md) for the mental model.
- Add richer checks with [Evals](evals.md).
- Wire suites into tests with [Pytest](pytest.md).
- Inspect and replay requests with [Tracing](tracing.md).
- Use the loopback-only browser client with [Chat](chat.md).
