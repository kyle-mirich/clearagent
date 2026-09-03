---
name: clearagent-release-check
description: Verify a ClearAgent Engine release candidate and its public package surface.
---

# Release Check

Use this skill before tagging or publishing the public engine.

Run:

```bash
uv sync --locked --dev
uv run ruff check src tests
uv run python -m mypy src
uv run pytest -q
uv build
```

Then verify that the distribution version matches `clearagent.__version__`, the
wheel contains no frontend or product-only modules, the FastAPI app has
only generic engine routes, and the README examples execute in deterministic
mode. Never publish from a checkout containing credentials or product-specific
artifacts.
