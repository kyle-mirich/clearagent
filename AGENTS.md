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
- run `uv run bash scripts/check.sh` when practical
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
- Run `uv run bash scripts/check.sh` for broad verification when practical.
