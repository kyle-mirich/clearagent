# Installation

This page shows how to use ClearAgent from another Python project and how to
set up this repository for development.

## Requirements

- Python 3.14 or newer
- `uv` for the documented commands

ClearAgent uses these default local runtime paths when the corresponding
features run:

- `.clearagent/config.toml`
- `.clearagent/traces.sqlite`
- `.clearagent/chat.sqlite`

Do not commit local `.clearagent/*.sqlite` files or their `-wal` and `-shm`
sidecars.

## Install As A Dependency

Before a ClearAgent release is visible on PyPI, start a fresh application
project and add the current package directly from GitHub:

```bash
uv init --bare --python 3.14 clearagent-quickstart
cd clearagent-quickstart
uv add "clearagent @ git+https://github.com/kyle-mirich/clearagent.git"
```

For an existing project that uses `pip`, install the same Git dependency with:

```bash
python -m pip install "clearagent @ git+https://github.com/kyle-mirich/clearagent.git"
```

Only replace the Git dependency with `uv add clearagent` or `pip install
clearagent` after the intended release is visible on PyPI. Maintainers can
follow the [Publishing](publishing.md) checklist.

## Provider Setup

Core native OpenAI Responses, OpenRouter, local, Ollama, Anthropic, and Google
adapters use `httpx`, which is installed by the base package. Set only the API
keys for the providers you use:

```bash
export OPENAI_API_KEY=...
export OPENROUTER_API_KEY=...
export ANTHROPIC_API_KEY=...
export GEMINI_API_KEY=...
```

`GOOGLE_API_KEY` is also accepted for Google Gemini models.

Optional extras install provider SDKs for applications that want those SDKs in
the same environment:

```bash
uv add "clearagent[openai] @ git+https://github.com/kyle-mirich/clearagent.git"
uv add "clearagent[anthropic] @ git+https://github.com/kyle-mirich/clearagent.git"
uv add "clearagent[google] @ git+https://github.com/kyle-mirich/clearagent.git"
uv add "clearagent[all] @ git+https://github.com/kyle-mirich/clearagent.git"
```

## First Traced Eval

This offline path proves the installed package before you configure a live
provider. Create `agent.py` in the new project:

```python
from clearagent import create_agent, tool
from clearagent.providers import FakeProvider, ProviderResponse, ToolCall


@tool
def lookup_order(order_id: str) -> dict:
    """Look up an order."""
    return {"order_id": order_id, "status": "shipped", "eta": "Friday"}


agent = create_agent(
    name="support_agent",
    model="openai:gpt-4.1-mini",
    system_prompt="Help users with order status.",
    tools=[lookup_order],
    provider=FakeProvider(
        [
            ProviderResponse.fake_tool_call(
                ToolCall(
                    id="call_lookup_order",
                    name="lookup_order",
                    arguments={"order_id": "A123"},
                )
            ),
            ProviderResponse.fake_text("Order A123 has shipped and arrives Friday."),
        ]
    ),
)


if __name__ == "__main__":
    result = agent.run("Where is order A123?")
    print(result.output)
    print(f"trace: {result.trace_db_path}")
    print(f"run_id: {result.run_id}")
```

Run the agent, confirm that `.clearagent/traces.sqlite` exists, and list the
recorded run:

```bash
uv run python agent.py
test -f .clearagent/traces.sqlite
uv run clearagent trace list
```

Create `smoke.yaml`:

```yaml
name: quickstart
cases:
  - name: shipped order
    input: Where is order A123?
    checks:
      - contains: shipped
      - contains: Friday
```

Run the eval. The CLI imports a fresh `agent` object, so the deterministic
provider queue starts full again:

```bash
uv run clearagent eval agent:agent smoke.yaml
```

The command prints `1 passed, 0 failed` and writes another run to the same local
trace database. To promote an observed run into a starter eval instead, copy its
ID from `trace list` and run:

```bash
uv run clearagent trace-to-eval <run_id> --out generated.yaml
```

## Repository Development Setup

When working on ClearAgent itself, install all extras and development tools from
the repository root:

```bash
uv sync --locked --all-extras --dev
./scripts/check.sh
```

Project configuration is optional. Run `uv run clearagent init` only when you
want to create `.clearagent/config.toml`, then review that file before deciding
whether its tracing settings should be shared in version control.

Build package artifacts locally with:

```bash
uv build
```

## Related Docs

- [Getting Started](getting-started.md)
- [Providers](providers.md)
- [Reference](reference.md)
- [Publishing](publishing.md)
