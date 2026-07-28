#!/usr/bin/env bash
set -euo pipefail
unset PYTEST_ADDOPTS PYTEST_PLUGINS

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"
cd "$repo_root"

coverage_data="$(mktemp "${TMPDIR:-/tmp}/clearagent-coverage-data.XXXXXX")"
coverage_report="$(mktemp "${TMPDIR:-/tmp}/clearagent-coverage-report.XXXXXX")"
trap 'rm -f -- "$coverage_data" "$coverage_report"' EXIT
export COVERAGE_FILE="$coverage_data"

uv_run=(uv run --locked --no-sync)

"${uv_run[@]}" python scripts/check_test_policy.py
"${uv_run[@]}" coverage erase --rcfile=/dev/null
"${uv_run[@]}" coverage run --rcfile=/dev/null --branch --source=clearagent -m pytest -p scripts.pytest_gate_plugin -c pyproject.toml --strict-config --strict-markers --disable-socket --allow-unix-socket
"${uv_run[@]}" coverage report --rcfile=/dev/null --show-missing --fail-under=95
"${uv_run[@]}" coverage json --rcfile=/dev/null -o "$coverage_report"
"${uv_run[@]}" python scripts/check_changed_coverage.py "$coverage_report"
"${uv_run[@]}" ruff check --config pyproject.toml --no-fix --no-respect-gitignore .
"${uv_run[@]}" ruff format --check --config pyproject.toml .
"${uv_run[@]}" python -m mypy --config-file pyproject.toml src
"${uv_run[@]}" python scripts/check_docs_links.py
"${uv_run[@]}" python scripts/check_distribution.py
