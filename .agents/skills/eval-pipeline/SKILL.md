---
name: clearagent-eval-pipeline
description: Safely change the ClearAgent GEPA and evaluation pipeline while preserving honest holdout admission.
---

# Eval Pipeline

Use this skill when changing `src/clearagent/builds/` or evaluation tests.

- Keep GEPA optimization on train/validation data only.
- Treat holdout data as untouched final evidence; never use it to tune or rank
  candidates before the admission decision.
- Preserve weighted rubric scoring, required-behavior gates, deterministic
  leakage checks, bounded model calls, token/cost accounting, and redacted
  telemetry.
- Test candidate acceptance, rejection, incumbent retention, malformed judge
  output, empty/undersized splits, and cancellation/error persistence.
- Use deterministic mode for local tests and keep live provider tests explicitly
  opt-in.

Prefer one readable orchestration path and native `gepa.optimize_anything` over
new generic optimizer abstractions.
