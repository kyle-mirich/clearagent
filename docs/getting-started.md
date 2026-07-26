# Getting Started

ClearAgent is a local-first, eval-first Python library. The shortest useful
feedback loop is:

```text
define an agent -> run it -> inspect its SQLite trace -> run or generate an eval
```

## Install In An External Project

Use Python 3.14 or newer and `uv`:

```bash
uv init --bare --python 3.14 clearagent-quickstart
cd clearagent-quickstart
uv add "clearagent @ git+https://github.com/kyle-mirich/clearagent.git"
```

The documented pre-release install uses the public GitHub repository. Replace
it with `uv add clearagent` only after the intended release is visible on PyPI.

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
    model="openai:gpt-5.6-terra",
    system_prompt="Help users with order status.",
)
```

ClearAgent omits `temperature` unless you set it explicitly, allowing each
provider and model to apply its supported default. Pass `temperature=...` only
when the selected model supports the value you need.

See [Providers](providers.md) for OpenRouter, local, Ollama, Anthropic, and Google
model URIs and credentials.

## Optional Local Config

Create `.clearagent/config.toml` when you want CLI tracing settings shared by
`run`, `eval`, and `chat`:

```bash
uv run clearagent init
```

Direct Python API calls continue to use values passed to `create_agent`.

## Contributor Setup

The following commands are only for a checkout of the ClearAgent repository,
not for an application that depends on the package:

```bash
uv sync --all-extras --dev
uv run bash scripts/check.sh
uv run python examples/customer_support/agent.py
uv run clearagent eval examples.customer_support.agent:agent examples/customer_support/evals/smoke.yaml
```

The quality gate runs the complete deterministic test suite with at least 95%
combined line/branch coverage, at least 90% combined coverage for every touched
product file, and complete coverage for changed executable lines and their
branch outcomes. It then runs Ruff, mypy, documentation links, and a fresh
built-distribution smoke test outside the repository.

## Next Steps

- Read [Core Concepts](core-concepts.md) for the mental model.
- Add richer checks with [Evals](evals.md).
- Wire suites into tests with [Pytest](pytest.md).
- Inspect and replay requests with [Tracing](tracing.md).
- Use the loopback-only browser client with [Chat](chat.md).
