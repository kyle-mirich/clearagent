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
uv sync --locked --all-extras --dev
uv run playwright install chromium
```

The Chromium install is required by the executable local-chat browser test. On
Debian/Ubuntu CI hosts, use `uv run playwright install --with-deps chromium` to
install its system dependencies as well.

## Verification

Run the full local gate before opening a change. It is mandatory for every pull
request, including changes to tests, documentation, packaging, or CI:

```bash
uv run bash scripts/check.sh
```

The gate runs the complete deterministic unit, integration, and Chromium test
suite; a 95% global combined coverage floor; a 90% floor for every touched
product file; complete changed-line and changed-branch coverage; static and type
checks; documentation validation; and a built-wheel smoke test outside the
repository. It also rejects changed coverage exclusions, skip/xfail escapes,
broad test networking, and static-client changes without a browser-test change.
A change is not ready to merge unless the entire gate passes.

Focused tests are useful while iterating:

```bash
uv run pytest tests/unit/test_tool_schema.py
uv run ruff check .
uv run python -m mypy src
```

Focused checks never replace the full gate. Each behavior change must include
tests that would detect the old or broken behavior, cover its successful path,
and cover relevant failures, boundaries, and persisted state. Bug fixes require
an exact regression test. Assertions should verify observable results such as
outputs, errors, HTTP or provider payloads, traces, database state, files, or
rendered interactions rather than implementation details.

Package metadata, package data, public imports, and entry-point changes must be
tested through the built wheel in an isolated temporary environment. Browser
client changes require executable interaction coverage; serving the asset or
searching its source is not sufficient.

Tests must be deterministic by default. Provider-backed live tests belong
behind the explicit `CLEARAGENT_LIVE_TESTS=1` flag. Follow the bounded commands
and fixture-review process in
[Live Provider Compatibility](docs/live-provider-compatibility.md).
Credential-free provider contract and error tests are always required; live
checks supplement rather than replace them. Pytest blocks external sockets;
tests that need a local server must allow only the exact loopback host.

## Documentation And Public APIs

Public behavior changes must update curated docs in the same pull request. Add
or update docstrings for public Python APIs, but do not use generated docstring
dumps as a replacement for reader-oriented guides. Check
[docs/site.md](docs/site.md) when adding a new workflow or concept. See the
[documentation guide](docs/contributing-docs.md) for the full policy.

## Pull Requests

Keep pull requests reviewable and include:

- the problem and intended behavior
- tests for successful, failure, boundary, and regression paths as applicable
- documentation updates, or a short explanation of why none are needed
- the output of `uv run bash scripts/check.sh`

Do not weaken tests, lower coverage thresholds, add skips or xfails, or widen
analysis ignores merely to make a pull request pass. If the full gate cannot
run or a required behavior cannot be tested, describe the specific blocker and
do not present the change as safe to merge.

Do not commit API keys, `.env` files, local databases, generated reports, or
package artifacts. By contributing, you agree that your contribution is
licensed under the repository's [MIT License](LICENSE).

## Local Runtime Files

Local traces, chat sessions, generated Promptfoo targets, and `.env` files
should stay out of git. The project `.gitignore` excludes `.clearagent/*.sqlite`
and `.env`.
