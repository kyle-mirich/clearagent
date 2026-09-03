# Test Instructions

Tests in this repository verify the public engine.

- Prefer deterministic fakes and local temporary databases.
- Keep live provider checks explicitly opt-in.
- Test observable output, events, persisted records, errors, and HTTP responses.
- Changes to GEPA, evaluation, holdout selection, or promotion need regression
  coverage for both the passing and rejecting paths.
- Run the full local commands from the repository `AGENTS.md` before handoff.
