# Agent Instructions

## Documentation

ClearAgent is an open source project, so behavior changes must keep the
reader-facing docs current.

When changing public behavior, update documentation in the same change. This
includes changes to:

- public Python APIs such as `create_agent`, `@tool`, providers, evals, tracing,
  graph flows, chat, pytest helpers, or structured outputs
- CLI commands, flags, output shape, or config files
- example agents, eval suites, provider setup, or trace storage behavior
- contributor workflows, test commands, or project structure

Use curated Markdown as the source of truth for public docs. Do not dump
docstrings into the docs as a substitute for explaining concepts and workflows.
Docstrings can inform reference material, but docs should be written for people
learning the repo.

Before finishing a change, check whether `docs/site.md` still points readers to
the right pages. If a new concept or workflow is added, either update an
existing page or add a focused page and link it from `docs/site.md`.

## Testing And Main-Branch Safety

Treat `main` as releasable. Every change requires verification proportionate to
what it can break. Changes that can affect runtime behavior, persistence,
provider wire formats, public APIs, CLI or HTTP output, browser assets,
examples, packaging, installation, or documented commands must include
automated tests that exercise the changed contract. A focused test passing is
not completion; the full repository gate must pass.

For every changed behavior:

- add or update a test that would detect the previous or broken behavior
- cover the successful path and each relevant failure, rejection, boundary,
  and persisted-state path introduced or changed
- add a regression test that reproduces the exact failure for every bug fix
- assert observable contracts such as return values, exceptions, exit status,
  HTTP payloads, provider request bodies, traces, database rows, files, or
  rendered interactions; merely asserting that a helper was called or that
  source text exists is not enough
- preserve and test existing public behavior, including malformed, legacy, and
  backward-compatible inputs when applicable
- put isolated logic tests in `tests/unit/` and cross-component behavior in
  `tests/integration/`; use temporary directories and deterministic fake or
  mocked providers

Provider changes require credential-free tests for the applicable exact
request shape, response and usage parsing, tool and structured-output round
trips, streaming, non-success responses, malformed payloads, and normalized
errors. Sanitized live recordings may supplement those tests. Paid live tests
must remain explicitly opt-in, bounded, and secret-safe; they never replace
required offline CI coverage.

Changes to `src/clearagent/chat/static/` must be exercised by an executable
browser or DOM test that covers the changed interaction and its failure state.
Serving an asset or searching its source for function names is not sufficient.

Changes to package metadata, dependencies, package data, static assets, public
imports, or entry points must build both the sdist and wheel and test the built
wheel from a temporary environment outside the repository. The smoke test must
verify public imports, `clearagent --help`, bundled chat assets, and a fake
provider run that writes a SQLite trace.

Documentation changes must keep public commands and examples executable and
must pass the documentation checker. Public behavior changes require matching
reader-facing documentation in the same change.

Tests must be deterministic and must not depend on real credentials, external
network access, user home-directory state, wall-clock timing, or test order
unless those dependencies are explicitly isolated. Do not delete or weaken a
test, lower a coverage threshold, add a skip or xfail, or widen an ignore merely
to make a change pass. Any justified replacement must retain or strengthen the
same contract coverage.

Changes to CI, test configuration, fixtures, or `scripts/check.sh` must include
evidence that the gate still rejects a deliberately failing case; a successful
run alone does not prove that a gate works.

Before finishing any change, run:

```bash
uv run bash scripts/check.sh
```

Focused checks are useful during development but never replace the full gate.
If the full gate cannot run or a required behavior cannot be tested, report the
specific blocker and do not claim the change is safe to merge.

## Library Consumer Experience

Treat ClearAgent as an installable library, not only as this repository. Public
docs should make the external-project path obvious:

- show how to install the package as a dependency before showing contributor
  setup commands
- keep examples copy-pastable in a fresh project, with imports from
  `clearagent`
- label repo-only commands such as `uv sync --all-extras --dev` as contributor
  setup
- document provider API keys, optional extras, local runtime files, and the
  expected Python version near the first install instructions
- keep `README.md` useful on PyPI by avoiding docs links that only work from a
  checked-out repository
- keep `docs/reference.md` aligned with the public API and CLI surfaces

When changing package metadata, bundled files, entry points, optional
dependencies, or release workflow, update the installation/publishing docs in
the same change and verify the package can build.

## Packaging And Release Readiness

Before claiming the project is ready to publish or consume as a package:

- run `uv build` and confirm both sdist and wheel are produced
- inspect the wheel when package data changes, especially chat static assets
- run `uv run bash scripts/check.sh` and require it to pass
- keep `pyproject.toml` project URLs valid for PyPI
- keep release instructions token-safe; never document or commit real publish
  tokens

## Project Conventions

- Use `uv` for setup, tests, and command examples.
- Target Python 3.14.
- Keep tracing local-first and SQLite-backed unless a change explicitly alters
  that design.
- Avoid documenting commands that are not implemented in this repo.
- Prefer runnable examples backed by `examples/` or tests.
- Run `uv run bash scripts/check.sh` and require it to pass before declaring a
  change complete or safe to merge.
