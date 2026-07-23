#!/usr/bin/env bash
set -euo pipefail

uv run coverage erase
uv run coverage run --source=clearagent -m pytest
uv run coverage report --fail-under=90
uv run ruff check .
uv run python -m mypy src
uv run python scripts/check_docs_links.py
