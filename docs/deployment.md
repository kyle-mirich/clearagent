# GitHub And CI

ClearAgent is maintained in the public
[`kyle-mirich/clearagent`](https://github.com/kyle-mirich/clearagent)
repository.

## Continuous Integration

The `CI` GitHub Actions workflow runs on every push and pull request using
Python 3.14. It executes the same local gate as contributors:

```bash
uv run bash scripts/check.sh
```

## Required Secrets

No secrets are required for CI.

Optional secrets for manual or scheduled live checks:

- `OPENROUTER_API_KEY`
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `GEMINI_API_KEY` or `GOOGLE_API_KEY`

The live OpenRouter eval is skipped unless both `OPENROUTER_API_KEY` and
`CLEARAGENT_RUN_LIVE=1` are present.

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

Local Markdown links are checked by:

```bash
uv run python scripts/check_docs_links.py
```

GitHub Pages is optional. If enabled, publish the `docs/` directory or a static
site generated from these Markdown files.
