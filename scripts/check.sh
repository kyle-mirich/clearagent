#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"
cd "$repo_root"

coverage_file="$(mktemp "${TMPDIR:-/tmp}/clearagent-coverage.XXXXXX")"
trap 'rm -f -- "$coverage_file"' EXIT
export COVERAGE_FILE="$coverage_file"

uv_run=(uv run --locked --no-sync)

"${uv_run[@]}" coverage erase
"${uv_run[@]}" coverage run --source=clearagent -m pytest -m "not live"
"${uv_run[@]}" coverage report --show-missing --fail-under=90
"${uv_run[@]}" ruff check .
"${uv_run[@]}" ruff format --check .
"${uv_run[@]}" python -m mypy src
"${uv_run[@]}" python scripts/check_docs_links.py
