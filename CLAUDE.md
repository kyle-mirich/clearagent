# Claude Code Instructions

Read the nearest `AGENTS.md` before changing files. This is the public
ClearAgent Engine repository, not the private Studio product.

- Keep product-facing routes, hosted chat/source workflows, frontend code,
  deployment configuration, and credentials out of this repository.
- Use Python 3.14 and `uv`; keep the default test path offline and deterministic.
- Preserve the small public FastAPI surface: health, readiness, invoke, and SSE.
- Treat `builds/` as the eval-first engine: train/validation cases may guide
  GEPA, while holdout cases are final admission evidence only.
- Run `uv run ruff check src tests`, `uv run python -m mypy src`, `uv run pytest -q`,
  and `uv build` before handoff.

Useful repository skills live under `.agents/skills/`. Load the narrowest skill
that matches the task before editing.
