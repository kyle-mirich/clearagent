# Installation

This page shows how to use ClearAgent from another Python project and how to
set up this repository for development.

## Requirements

- Python 3.14 or newer
- `uv` for the documented commands

ClearAgent writes local runtime files under `.clearagent/` by default:

- `.clearagent/config.toml`
- `.clearagent/traces.sqlite`
- `.clearagent/chat.sqlite`

Do not commit local `.clearagent/*.sqlite` files.

## Install As A Dependency

Add the current ClearAgent package to another project directly from GitHub:

```bash
uv add git+https://github.com/kyle-mirich/clearagent.git
```

With `pip`, use:

```bash
python -m pip install "clearagent @ git+https://github.com/kyle-mirich/clearagent.git"
```

The package is not on PyPI yet. See [Publishing](publishing.md) for the release
checklist.

## Provider Setup

Core OpenAI-compatible, OpenRouter, local, Ollama, Anthropic, and Google
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
uv add "clearagent[openai]"
uv add "clearagent[anthropic]"
uv add "clearagent[google]"
uv add "clearagent[all]"
```

## First Agent

Create `support_agent.py` in your project:

```python
from clearagent import create_agent, tool


@tool
def lookup_order(order_id: str) -> dict:
    """Look up an order."""
    return {"order_id": order_id, "status": "shipped", "eta": "Friday"}


agent = create_agent(
    name="support_agent",
    model="openai:gpt-4.1-mini",
    system_prompt="Help users with order status and refund questions.",
    tools=[lookup_order],
)


if __name__ == "__main__":
    result = agent.run("Where is order A123?")
    print(result.output)
```

Run it directly:

```bash
uv run python support_agent.py
```

Or through the installed CLI:

```bash
uv run clearagent run support_agent:agent "Where is order A123?"
```

## Repository Development Setup

When working on ClearAgent itself, install all extras and development tools from
the repository root:

```bash
uv sync --all-extras --dev
uv run clearagent init
uv run bash scripts/check.sh
```

Build package artifacts locally with:

```bash
uv build
```

## Related Docs

- [Getting Started](getting-started.md)
- [Providers](providers.md)
- [Reference](reference.md)
- [Publishing](publishing.md)
