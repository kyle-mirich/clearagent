# ClearAgent Power Features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add compact MVPs for five high-leverage ClearAgent features: trace-to-eval generation, failure triage, eval iteration, readable reports, and tool contract validation.

**Architecture:** Keep the features local-first and file-oriented. New helpers live in small modules under `clearagent.evals`, `clearagent.reports`, and `clearagent.contracts`; the CLI exposes them without requiring hosted services.

**Tech Stack:** Python 3.14, Typer, SQLite trace store, YAML, Markdown, pytest, FastAPI chat backend.

---

### Task 1: Trace-to-eval generation

**Files:**
- Create: `src/clearagent/evals/generate.py`
- Modify: `src/clearagent/cli.py`
- Test: `tests/unit/test_eval_generation.py`
- Docs: `docs/evals.md`, `docs/reference.md`, `docs/site.md`

- [ ] Write a failing unit test that creates a trace run and expects a YAML eval case.
- [ ] Run the focused test and verify it fails because the module does not exist.
- [ ] Implement `generate_eval_case_from_trace()` and `clearagent eval generate`.
- [ ] Run the focused test and CLI smoke coverage.

### Task 2: Eval iteration runner

**Files:**
- Create: `src/clearagent/evals/iteration.py`
- Modify: `src/clearagent/cli.py`
- Test: `tests/unit/test_eval_iteration.py`
- Docs: `docs/evals.md`, `docs/reference.md`

- [ ] Write a failing test for running an eval suite across multiple models or temperatures and summarizing pass rate.
- [ ] Run the focused test and verify it fails because the module does not exist.
- [ ] Implement the small iteration helper and `clearagent iterate`.
- [ ] Run focused tests.

### Task 3: Trace reports and failure dashboard

**Files:**
- Create: `src/clearagent/reports.py`
- Modify: `src/clearagent/cli.py`, `src/clearagent/chat/app.py`
- Test: `tests/unit/test_reports.py`, `tests/integration/test_chat_app.py`
- Docs: `docs/tracing.md`, `docs/chat.md`, `docs/reference.md`

- [ ] Write failing tests for Markdown trace reports and `/api/triage/runs/{run_id}`.
- [ ] Run the focused tests and verify they fail.
- [ ] Implement report rendering from stored run, turn, model, and tool rows.
- [ ] Expose the CLI and chat API surfaces.
- [ ] Run focused tests.

### Task 4: Tool contract validation

**Files:**
- Create: `src/clearagent/contracts.py`
- Modify: `src/clearagent/__init__.py`
- Test: `tests/unit/test_contracts.py`
- Docs: `docs/reference.md`, `docs/core-concepts.md`

- [ ] Write failing tests for valid and invalid tool contract examples.
- [ ] Run focused tests and verify they fail.
- [ ] Implement `validate_tool_contract()` and `tool_contract_cases()`.
- [ ] Run focused tests.

### Task 5: Docs and broad verification

**Files:**
- Modify: `README.md`, `docs/site.md`

- [ ] Update reader-facing docs for all new public behavior.
- [ ] Run `uv run pytest` or the focused set if runtime is constrained.
- [ ] Run docs link tests.
