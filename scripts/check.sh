#!/usr/bin/env bash
set -euo pipefail

coverage_report="$(mktemp "${TMPDIR:-/tmp}/clearagent-coverage.XXXXXX")"
trap 'rm -f "$coverage_report"' EXIT

uv run python scripts/check_test_policy.py
uv run coverage erase --rcfile=/dev/null
uv run coverage run --rcfile=/dev/null --branch --source=clearagent -m pytest --strict-config --strict-markers
uv run coverage report --rcfile=/dev/null --fail-under=95
uv run coverage json --rcfile=/dev/null -o "$coverage_report"
uv run python scripts/check_changed_coverage.py "$coverage_report"
uv run ruff check .
uv run python -m mypy src
uv run python scripts/check_docs_links.py
uv run python scripts/check_distribution.py
