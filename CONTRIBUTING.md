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

Install the locked development environment from the repository root:

```bash
uv sync --locked --all-extras --dev
```

## Verification

Run the full local gate before opening a change:

```bash
./scripts/check.sh
```

The gate runs deterministic non-live tests, requires at least 90% package line
coverage, then runs Ruff lint and formatting checks, mypy, and documentation
checks for local files, anchors, and indexed pages.

Focused tests are useful while iterating:

```bash
uv run pytest tests/unit/test_tool_schema.py
uv run pytest tests/integration/test_agent_tracing.py
uv run ruff check .
uv run python -m mypy src
uv run python scripts/check_docs_links.py
```

Tests must be deterministic by default. Provider-backed live tests belong
behind the explicit `CLEARAGENT_LIVE_TESTS=1` flag. Follow the bounded commands
and fixture-review process in
[Live Provider Compatibility](docs/live-provider-compatibility.md).
Do not enable live tests only because a key happens to be present, and never
print a credential in test output.

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
- the output of `./scripts/check.sh`

Do not commit API keys, `.env` files, local databases, generated reports, or
package artifacts. By contributing, you agree that your contribution is
licensed under the repository's [MIT License](LICENSE).

## Commands That Write Files

Several useful commands create local state:

- `clearagent init` creates `.clearagent/config.toml` if it is absent. This is
  optional for contributors. Review and commit it only when the project should
  share those settings.
- Agent runs and evals create `.clearagent/traces.sqlite` by default; chat also
  creates `.clearagent/chat.sqlite`. SQLite may create `-wal` and `-shm`
  sidecars.
- `trace-to-eval`, `trace-report`, `replay-request`, and Promptfoo commands
  write the paths passed to their output arguments.
- `uv build` writes `dist/`; tests and checks may update tool caches.

Use `tmp_path` in tests and explicit temporary paths for exploratory runs. Keep
SQLite files and sidecars, generated reports and evals, Promptfoo targets,
package artifacts, and `.env` files out of git. A generated eval or report
becomes hand-authored project material only after it is reviewed and moved to
an intentional tracked location.
