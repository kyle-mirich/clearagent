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

This gate already builds and inspects both distributions and runs fresh base-
wheel and pytest-extra smoke tests outside the repository.

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

Run a credential-free metadata check against both artifacts:

```bash
uv run python -m twine check dist/*
```

For the authoritative repeatable artifact check, use:

```bash
uv run python scripts/check_distribution.py
```

That command builds into a fresh temporary directory, rejects missing or extra
artifacts, inspects both archives, runs Twine, and performs the external
installation checks. It does not contact an upload endpoint or require PyPI
credentials.

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

## Optional Publish Dry Run

Maintainers can also ask `uv` to check the files it would select for an upload:

```bash
uv publish --dry-run
```

The command does not upload the files, but it still performs publishing
authentication. Outside a trusted-publishing environment, `uv` tries to obtain
an OIDC token when no token, username/password, or keyring credentials are
available. It can check the artifacts and then report a trusted-publishing
failure; depending on the installed `uv` version, that authentication failure
may also produce a nonzero exit status. Therefore, do not treat
`uv publish --dry-run` as a required passing local gate without credentials.

Use the credential-free `twine check` command above for a local gate that can
pass without publishing access. Preserve `uv publish --dry-run` as an optional
maintainer check when credentials are configured or trusted publishing is
available.

## Publish

Publishing is an approval-gated maintainer action. The preflight, build,
artifact check, fresh-environment smoke test, and optional dry run do not
authorize an upload.

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
