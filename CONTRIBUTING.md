# Contributing

Thanks for improving ClearAgent. The project favors focused changes, explicit
tests, and documentation that matches shipped behavior.

For usage questions, start with [SUPPORT.md](SUPPORT.md). Report security
issues privately using [SECURITY.md](SECURITY.md), not a public issue.

## Before You Start

- Search existing issues and pull requests before opening a duplicate.
- Open an issue before a broad API redesign so scope and compatibility can be
  discussed early.
- Keep hosted planning, optimization, authentication, and managed storage out
  of this MIT repository; see [docs/status.md](docs/status.md).

ClearAgent is developed with `uv` and targets Python 3.14.

## Setup

```bash
uv sync --all-extras --dev
```

## Verification

Run the full local gate before opening a change:

```bash
uv run bash scripts/check.sh
```

The gate runs the complete test suite, requires at least 90% package line
coverage, then runs Ruff, mypy, and documentation-link checks.

Focused tests are useful while iterating:

```bash
uv run pytest tests/unit/test_tool_schema.py
uv run ruff check .
uv run python -m mypy src
```

Tests must be deterministic by default. Provider-backed live tests belong
behind the explicit `CLEARAGENT_LIVE_TESTS=1` flag. Follow the bounded commands
and fixture-review process in
[Live Provider Compatibility](docs/live-provider-compatibility.md).

## Documentation And Public APIs

Public behavior changes must update curated docs in the same pull request. Add
or update docstrings for public Python APIs, but do not use generated docstring
dumps as a replacement for reader-oriented guides. Check
[docs/site.md](docs/site.md) when adding a new workflow or concept. See the
[documentation guide](docs/contributing-docs.md) for the full policy.

## Pull Requests

Keep pull requests reviewable and include:

- the problem and intended behavior
- tests for successful and failure paths
- documentation updates, or a short explanation of why none are needed
- the output of `uv run bash scripts/check.sh`

Do not commit API keys, `.env` files, local databases, generated reports, or
package artifacts. By contributing, you agree that your contribution is
licensed under the repository's [MIT License](LICENSE).

## Local Runtime Files

Local traces, chat sessions, generated Promptfoo targets, and `.env` files
should stay out of git. The project `.gitignore` excludes `.clearagent/*.sqlite`
and `.env`.
