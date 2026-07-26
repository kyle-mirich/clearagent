# GitHub And CI

ClearAgent is maintained in the public
[`kyle-mirich/clearagent`](https://github.com/kyle-mirich/clearagent)
repository.

## Continuous Integration

The `CI` GitHub Actions workflow runs on every push and pull request using
Python 3.14. The test job installs from the checked lockfile and executes the
same offline gate as contributors:

```bash
./scripts/check.sh
```

A separate wheel smoke job builds the wheel, installs only its base
dependencies into a fresh environment, checks the public imports and bundled
chat assets, and invokes CLI help.

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

Validate release artifacts locally with:

```bash
uv build
```

The project metadata lives in `pyproject.toml`. See [Publishing](publishing.md)
for the PyPI checklist, wheel inspection, dry run, and token-safe upload
commands.

## Documentation

The docs are Markdown files under `docs/` and render directly on GitHub. The
entrypoint is [site.md](site.md).

Local Markdown file targets and heading anchors are checked by the following
command. It also fails when a page under `docs/` is not listed from `site.md`:

```bash
uv run python scripts/check_docs_links.py
```

GitHub Pages is optional. If enabled, publish the `docs/` directory or a static
site generated from these Markdown files.
