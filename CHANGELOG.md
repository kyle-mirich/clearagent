# Changelog

## 0.1.0 - 2026-08-31

- Rebuilt the public repository around the LangGraph agent runtime.
- Added native GEPA prompt optimization with train, validation, and holdout
  evaluation splits.
- Added generated evaluation cases, weighted LLM judges, deterministic checks,
  quality admission, promotion decisions, and redacted local traces.
- Added a generic CLI and minimal FastAPI invoke/stream surface.
- Added deterministic end-to-end build execution for offline development.
- Removed the bundled web frontend and product-facing HTTP contracts; the
  public repository is now engine-only.
