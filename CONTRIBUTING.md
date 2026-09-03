# Contributing

ClearAgent Engine favors small interfaces, deterministic tests, and documentation
that matches shipped behavior. Keep product-specific code out of this
repository.

## Setup

```bash
uv sync --locked --dev
```

## Verification

Before opening a pull request, run:

```bash
uv run ruff check src tests
uv run python -m mypy src
uv run pytest -q
uv build
```

The admission mutation campaign lives here with the engine:
`uv run bash scripts/mutation.sh`. Downstream integration tests run against
the pinned engine dependency.

Tests must not require live provider credentials or external network access.
Provider-backed behavior should use deterministic fakes or sanitized fixtures.

Do not commit API keys, `.env` files, local databases, generated traces, or
package artifacts. Changes to public CLI, HTTP, provider, persistence, or build
behavior need observable contract tests and matching README/documentation.
