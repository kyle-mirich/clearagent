#!/usr/bin/env bash
set -euo pipefail

# This CI-safe campaign mutates promotion admission, where an undetected boolean
# or threshold mutation could activate a weak agent. Expand `only_mutate`
# deliberately after adding dedicated module coverage.
mutation_source_root="$(pwd)/mutants/src"
mkdir -p "$mutation_source_root"
PYTHONPATH="${mutation_source_root}${PYTHONPATH:+:${PYTHONPATH}}" \
  uv run mutmut run --max-children=1
uv run mutmut results
uv run mutmut export-cicd-stats
uv run python - <<'PY'
import json
from pathlib import Path

stats = json.loads(Path("mutants/mutmut-cicd-stats.json").read_text())
survived = int(stats.get("survived", 0))
if survived:
    raise SystemExit(f"Mutation gate failed: {survived} mutant(s) survived.")
print(f"Mutation gate passed: {stats.get('killed', 0)} killed, {stats.get('total', 0)} total.")
PY
