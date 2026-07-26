#!/usr/bin/env bash
set -euo pipefail
unset PYTEST_ADDOPTS PYTEST_PLUGINS

coverage_report="$(mktemp "${TMPDIR:-/tmp}/clearagent-coverage.XXXXXX")"
trap 'rm -f "$coverage_report"' EXIT

uv run python scripts/check_test_policy.py
uv run coverage erase --rcfile=/dev/null
uv run coverage run --rcfile=/dev/null --branch --source=clearagent -m pytest -p scripts.pytest_gate_plugin -c pyproject.toml --strict-config --strict-markers --disable-socket --allow-unix-socket
uv run coverage report --rcfile=/dev/null --fail-under=95
uv run coverage json --rcfile=/dev/null -o "$coverage_report"
uv run python scripts/check_changed_coverage.py "$coverage_report"
uv run ruff check --config pyproject.toml --no-fix --no-respect-gitignore .
uv run python -m mypy --config-file pyproject.toml src
uv run python scripts/check_docs_links.py
uv run python scripts/check_distribution.py
