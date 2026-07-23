# Publishing

This page is the release checklist for publishing ClearAgent as a Python
package.

## When To Use This

Use this before uploading a release to PyPI or TestPyPI. The goal is to verify
that a user can install the package, import `clearagent`, run the CLI, and read
useful metadata on the package index.

## Preflight

1. Confirm the version in `pyproject.toml` is the intended release version.
2. Update `CHANGELOG.md` for user-visible changes.
3. Check that `README.md` renders as a useful PyPI project description.
4. Check that `docs/site.md` links to any new public workflow docs.
5. Run the full local gate:

```bash
uv run bash scripts/check.sh
```

## Build Artifacts

Build the source distribution and wheel:

```bash
uv build
```

Confirm both files are present:

```bash
ls dist/
```

When package data changes, inspect the wheel. For example, the chat browser
client should include `clearagent/chat/static/index.html`,
`clearagent/chat/static/styles.css`, and `clearagent/chat/static/app.js`:

```bash
python -m zipfile -l dist/clearagent-<version>-py3-none-any.whl
```

## Dry Run

Run a no-upload publish check:

```bash
uv publish --dry-run
```

This validates the files selected for publishing without sending them to an
index. Outside a trusted-publishing CI environment, `uv` may print a warning
about missing credentials or an OIDC token during the dry run. That warning is
expected as long as the artifact checks still run and the command exits
successfully.

## Publish

Publish with a PyPI token in the environment:

```bash
UV_PUBLISH_TOKEN=... uv publish
```

Do not commit tokens or paste real tokens into docs, issues, or examples.

## Post-Publish Smoke Test

From a fresh project, install and smoke test the release:

```bash
uv init clearagent-smoke
cd clearagent-smoke
uv add clearagent
uv run python -c "from clearagent import create_agent, tool; print(create_agent); print(tool)"
uv run clearagent --help
```

For provider-backed manual testing, set the relevant API key and run a small
agent from [Installation](install.md).

## Related Docs

- [Installation](install.md)
- [Deployment](deployment.md)
- [Reference](reference.md)
