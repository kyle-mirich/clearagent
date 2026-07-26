# GitHub And CI

ClearAgent is maintained in the public
[`kyle-mirich/clearagent`](https://github.com/kyle-mirich/clearagent)
repository.

## Continuous Integration

The `CI` GitHub Actions workflow runs for every pull request, merge-queue group,
and push to `main`. Its stable `quality` job uses Python 3.14 on Ubuntu, installs
the locked dependency graph and Chromium, and executes the same local gate as
contributors:

```bash
uv run bash scripts/check.sh
```

The gate includes strict offline pytest configuration, real browser interaction,
at least 95% combined line/branch coverage, at least 90% combined coverage for
each touched product file, and complete coverage of changed executable lines and
branches. Changed coverage exclusions, skip/xfail escapes, broad network access,
and static-client changes without a browser-test change fail the gate. Ruff,
mypy, documentation links, and built-distribution verification run afterward.
Checkout history is available to compare the change with its proposed merge
base.

A separate `package-smoke` matrix runs on Ubuntu, macOS, and Windows. Each job
builds an sdist and wheel in a temporary directory, inspects metadata, entry
points, Python modules, and bundled chat assets, runs Twine checks, installs the
base wheel outside the checkout, exercises the public imports, CLI, fake-
provider tools and SQLite trace, serves the installed chat assets, and verifies
the installed pytest extra. Superseded runs for the same pull request or ref
are cancelled.

## Required Secrets

No secrets are required for CI.

Optional secrets for manual or scheduled live checks:

- `OPENROUTER_API_KEY`
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `GEMINI_API_KEY` or `GOOGLE_API_KEY`

The bounded live suite is never part of CI and refuses to run without
`CLEARAGENT_LIVE_TESTS=1`. Missing provider credentials are reported without
making requests. See [Live Provider Compatibility](live-provider-compatibility.md)
for the exact command, targets, and request limits.

## Package Build And PyPI

The full gate validates release artifacts automatically. Run the same package
stage directly with:

```bash
uv run python scripts/check_distribution.py
```

The project metadata lives in `pyproject.toml`. See [Publishing](publishing.md)
for the PyPI checklist, wheel inspection, dry run, and token-safe upload
commands.

## Documentation

The docs are Markdown files under `docs/` and render directly on GitHub. The
entrypoint is [site.md](site.md).

Local Markdown links are checked by:

```bash
uv run python scripts/check_docs_links.py
```

GitHub Pages is optional. If enabled, publish the `docs/` directory or a static
site generated from these Markdown files.
