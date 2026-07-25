# Publishing

This page is the release checklist for publishing ClearAgent as a Python
package.

## When To Use This

Use this before uploading a release to PyPI or TestPyPI. The goal is to verify
that a user can install the package, import `clearagent`, run the CLI, and read
useful metadata on the package index.

## Proposed First Release

- Package version: `0.1.0`
- Git tag: `v0.1.0`
- Maturity: first alpha, represented by the `Development Status :: 3 - Alpha`
  classifier and the project documentation

`0.1.0` is intentionally the exact release version, not the PEP 440 prerelease
identifier `0.1.0a1`. Do not create the tag, publish artifacts, or create a
GitHub release until the maintainer explicitly approves the release.

## Preflight

1. Confirm `pyproject.toml` contains version `0.1.0` and the Alpha classifier.
2. Confirm `CHANGELOG.md` contains only verified user-visible behavior.
3. Check that `README.md` renders as a useful PyPI project description.
4. Check that support, security, license, and contribution policies are current.
5. Check that `docs/site.md` links to every public workflow doc.
6. Run the full local gate:

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
uv run python -m zipfile -l dist/clearagent-<version>-py3-none-any.whl
```

## Fresh-Environment Smoke Test

Before any upload, install the wheel into a new environment outside the
repository and verify the public imports and console entry point:

```bash
CLEARAGENT_WHEEL="$(pwd)/dist/clearagent-0.1.0-py3-none-any.whl"
CLEARAGENT_SMOKE_DIR="$(mktemp -d)"
uv venv --python 3.14 "$CLEARAGENT_SMOKE_DIR/.venv"
uv pip install --python "$CLEARAGENT_SMOKE_DIR/.venv/bin/python" "$CLEARAGENT_WHEEL"
"$CLEARAGENT_SMOKE_DIR/.venv/bin/python" -c "from clearagent import create_agent, tool; print(create_agent, tool)"
"$CLEARAGENT_SMOKE_DIR/.venv/bin/clearagent" --help
```

Also run the offline fake-provider path in [Installation](install.md) against
that installed wheel and confirm it writes a SQLite trace and passes its eval.

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

Publishing is an approval-gated maintainer action. The preflight, build,
fresh-environment smoke test, and dry run do not authorize an upload.

Publish with a PyPI token in the environment:

```bash
UV_PUBLISH_TOKEN=... uv publish
```

Do not commit tokens or paste real tokens into docs, issues, or examples.

## Post-Publish Smoke Test

From a fresh project, install and smoke test the release:

```bash
uv init --bare --python 3.14 clearagent-smoke
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
