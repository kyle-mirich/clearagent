# Documentation Guide

ClearAgent docs are curated Markdown pages that can be published as a
documentation website. They should teach the project through concepts, examples,
and workflows instead of mirroring docstrings.

## Source Of Truth

Use the current repository state as the source:

- `README.md` for the project pitch and first link into docs
- `AGENTS.md` for the task-to-code/test/docs map and repository invariants
- `docs/site.md` for the docs table of contents
- `docs/install.md` for the canonical external-project installation and
  first-use walkthrough
- `docs/publishing.md` for package release checks
- `src/clearagent/` for public APIs and behavior
- `examples/` for runnable examples
- `tests/` for supported edge cases and expected behavior
- `pyproject.toml` for supported Python version, dependencies, and console
  scripts

Docstrings can help explain tool schemas and public APIs, but they are not the
public documentation source of truth.

## When To Update Docs

Update docs in the same change when you modify:

- public Python APIs
- CLI commands, flags, or output
- provider model URI behavior
- eval suite format or checks
- trace storage, redaction, request replay, or default paths
- chat backend endpoints or storage behavior
- pytest helper behavior or plugin options
- examples, setup commands, or contributor workflows

If a behavior change does not need a docs change, call that out in the change
summary.

## Page Format

Use this structure for new docs pages:

```markdown
# Page Title

One short paragraph explaining what the page helps readers do.

## When To Use This

Describe the reader goal.

## Example

Show a runnable command or Python snippet when possible.

## How It Works

Explain the behavior using repo terms.

## Related Docs

- Related page: `other-page.md`
```

Keep headings short and stable. Use relative links so the same Markdown works in
GitHub and static docs sites.

## Agent Maintenance Workflow

When a Codex agent updates docs:

1. Scan `README.md`, `docs/site.md`, relevant existing docs, related code,
   examples, tests, and `pyproject.toml`.
2. Use the repository map in `AGENTS.md` to identify the focused tests and all
   reader-facing pages affected by the change.
3. Decide whether to update an existing page or add a focused new page.
4. Keep `docs/site.md` in learning-path order. Every `docs/*.md` page must be
   linked from that index.
5. Prefer examples that already exist in `examples/` or tests and import public
   values from the package entry points documented in `reference.md`.
6. Document only implemented commands and behavior. Label commands that create
   databases, configuration, reports, or other output files.
7. Run `uv run python scripts/check_docs_links.py` for Markdown changes. It
   validates local paths, local heading anchors, and the docs index.
8. Run `./scripts/check.sh` for repo-wide or public-behavior changes when
   practical.

## Website Readiness Checklist

- The page has one `#` title.
- Links are relative, point to existing files, and use valid heading anchors.
- Commands use `uv`.
- Code examples match current public APIs.
- No page relies on generated docstring dumps.
- New pages are linked from `docs/site.md`.
- The canonical copy-paste quickstart remains in `docs/install.md`; other pages
  link to it instead of maintaining a second copy.
- README points readers toward the docs entrypoint.
- README links that need to work on PyPI use absolute GitHub URLs.
