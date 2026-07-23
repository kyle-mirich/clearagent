# ClearAgent Codex Phase Plan

This is a planning artifact and may describe phased or future behavior. For
current implemented commands and APIs, see [Reference](../reference.md).

## Purpose

This is the execution plan to hand off to Codex alongside the ClearAgent PRD.

The PRD defines the product. This plan defines the implementation order, test checkpoints, and exact boundaries for each Codex pass.

## Core instruction for Codex

Do not implement the whole PRD at once.

Implement ClearAgent in small phases. Each phase must have tests. Do not proceed to the next phase until the current phase passes its checkpoint.

For now, all provider behavior must be tested with mocked OpenAI-compatible responses. Do not call real OpenAI, Anthropic, Google, OpenRouter, or local model APIs yet. Live API testing will happen later when an API key is provided.

## Product wedge to preserve

ClearAgent is an eval-first Python agent framework with:

1. SQLite traces on by default.
2. Exact provider request snapshots saved before each model call.
3. Turn-level replay points.
4. Final-output-first eval suites.
5. pytest integration.
6. Optional Promptfoo export.
7. OpenAI-compatible provider shape as the MVP provider path.
8. Simple single-node and multi-node agent creation.

Do not turn ClearAgent into a full LangChain, LangGraph, CrewAI, or Microsoft Agent Framework clone.

## Global engineering rules

1. Use `uv`, not pip, in all commands and docs.
2. Target Python 3.14.
3. Keep tracing on by default.
4. Save the exact provider request object before the provider call.
5. Use mocked OpenAI-compatible responses for all tests in this plan.
6. Do not require real API keys in tests.
7. Do not make network calls in unit tests.
8. Keep eval checks final-output-first by default.
9. Use SQLite for local trace storage.
10. Keep Promptfoo optional.
11. Add tests for every phase.
12. If tests fail, stop and fix before continuing.

## Desired final workflow

```bash
uv sync --all-extras --dev
uv run pytest
uv run clearagent init
uv run clearagent eval examples.customer_support.agent:agent examples/customer_support/evals/smoke.yaml
uv run clearagent trace list
uv run clearagent request <run_id> --turn 0
```

Later, with a real API key:

```bash
OPENAI_API_KEY=... uv run clearagent run examples.customer_support.agent:agent "Where is order A123?"
```

Promptfoo remains optional:

```bash
uv run clearagent promptfoo export examples.customer_support.agent:agent examples/customer_support/evals/safety.yaml --out promptfooconfig.yaml
npx promptfoo eval -c promptfooconfig.yaml
```

# Phase 0: Scaffold audit

## Goal

Understand the current scaffold and produce an implementation map before modifying code.

## Codex prompt

```text
Audit the current ClearAgent scaffold. Do not modify files yet. Summarize the current package structure, existing tests, existing CLI commands, provider abstractions, eval support, storage support, and gaps relative to the PRD. Then recommend the smallest safe first implementation pass.
```

## Tasks

1. Inspect `pyproject.toml`.
2. Inspect `.python-version`.
3. Inspect `src/clearagent`.
4. Inspect `examples`.
5. Inspect `tests`.
6. Inspect existing CLI commands.
7. Identify already implemented PRD features.
8. Identify missing PRD features.
9. Identify risky areas that need careful refactoring.

## Testing checkpoint

No tests required because no files should change.

## Acceptance criteria

1. Codex produces a concise audit summary.
2. Codex lists the exact files it expects to modify in Phase 1.
3. Codex does not modify code.

## Do not proceed unless

The current scaffold structure is understood and the first code phase is clearly identified.

# Phase 1: uv-native Python 3.14 baseline

## Goal

Make the project cleanly installable and testable with uv and Python 3.14.

## Codex prompt

```text
Implement Phase 1 only. Make the project uv-native and Python 3.14 aligned. Update pyproject.toml, .python-version, README setup commands, .gitignore, and CI if present. Do not change runtime architecture. Then run the test checkpoint.
```

## Tasks

1. Ensure `.python-version` contains:

   ```text
   3.14
   ```
2. Ensure `pyproject.toml` has:

   ```toml
   requires-python = ">=3.14"
   ```
3. Ensure runtime dependencies are minimal:

   1. `pydantic`
   2. `pyyaml`
   3. `typer`
   4. `rich`
   5. `httpx`
   6. `jsonschema`
4. Ensure dev dependencies include:

   1. `pytest`
   2. `pytest-cov`
   3. `ruff`
   4. `mypy`
5. Ensure the CLI entrypoint exists:

   ```toml
   [project.scripts]
   clearagent = "clearagent.cli:app"
   ```
6. Add pytest plugin entrypoint if the package already has a placeholder plugin module:

   ```toml
   [project.entry-points.pytest11]
   clearagent = "clearagent.pytest_plugin.plugin"
   ```
7. Add `.gitignore` entries:

   ```text
   .clearagent/*.sqlite
   .clearagent/traces/
   .clearagent/reports/
   .env
   ```
8. Update README setup commands to use `uv`, not `pip`.
9. If GitHub Actions exists, use uv there too.

## Testing checkpoint

Run:

```bash
uv sync --all-extras --dev
uv run pytest
```

## Acceptance criteria

1. `uv sync --all-extras --dev` succeeds.
2. `uv run pytest` succeeds.
3. No docs tell the user to use pip for normal setup.
4. Python 3.14 is explicit.

## Do not proceed unless

The package installs and the existing tests pass under uv.

# Phase 2: Core types and provider request snapshot design

## Goal

Create the types and provider interface required to save exact provider requests before API calls.

## Codex prompt

```text
Implement Phase 2 only. Add core provider request/response types, model URI parsing, provider registry skeleton, and a fake provider for tests. The provider interface must separate build_request(...) from complete(...). Do not implement SQLite yet. Do not make network calls.
```

## Tasks

1. Add or update `src/clearagent/providers/model_uri.py`.
2. Parse model strings:

   1. `openai:gpt-4.1-mini`
   2. `anthropic:claude-sonnet-4-5`
   3. `google:gemini-2.5-flash`
   4. `openrouter:anthropic/claude-sonnet-4.5`
3. Add or update `src/clearagent/providers/base.py`.
4. Define `ProviderRequest` with:

   1. `provider`
   2. `model`
   3. `api_shape`
   4. `body`
   5. `endpoint`
   6. `headers_snapshot`
5. Define `ProviderResponse` with:

   1. `provider`
   2. `model`
   3. `raw`
   4. `output_text`
   5. `tool_calls`
   6. `usage`
   7. `finish_reason`
6. Define provider interface:

   ```python
   request = provider.build_request(...)
   response = provider.complete(request)
   ```
7. Add fake provider for deterministic tests.
8. Add provider registry skeleton.

## Testing checkpoint

Run:

```bash
uv run pytest tests/unit/test_model_uri.py tests/unit/test_provider_base.py
uv run pytest
```

## Required tests

1. Valid OpenAI URI parses.
2. Valid Anthropic URI parses.
3. Valid Google URI parses.
4. Valid OpenRouter URI parses.
5. Invalid URI raises a clear error.
6. Fake provider can build a provider request without credentials.
7. Fake provider can return a mocked provider response.
8. `build_request` does not perform a network call.

## Acceptance criteria

1. Provider requests are inspectable before completion.
2. No real API key is required.
3. No network call is made in tests.
4. All tests pass.

## Do not proceed unless

The provider abstraction supports exact request snapshotting.

# Phase 3: OpenAI-compatible provider with mocked responses

## Goal

Implement the MVP provider adapter around the OpenAI Chat Completions-compatible request shape.

## Codex prompt

```text
Implement Phase 3 only. Add the OpenAI-compatible provider adapter. It must build exact Chat Completions-compatible request bodies. Tests must use mocked responses only and must not call real APIs. Do not add SQLite yet.
```

## Tasks

1. Add or update `src/clearagent/providers/openai_compatible.py`.
2. Implement `OpenAICompatibleProvider.build_request`.
3. Implement `OpenAICompatibleProvider.complete`, but test it using mocked HTTP responses.
4. Support:

   1. `model`
   2. `messages`
   3. `tools`
   4. `tool_choice`
   5. `temperature`
   6. `max_tokens`
   7. `extra` params
5. Build request body in OpenAI-compatible shape:

   ```python
   {
       "model": "gpt-4.1-mini",
       "messages": [...],
       "tools": [...],
       "tool_choice": "auto",
       "temperature": 0.0,
   }
   ```
6. Building a request must not require an API key.
7. Completing a request may require API key config, but tests should mock it.
8. Parse mocked final text response.
9. Parse mocked tool call response.
10. Parse mocked usage info.

## Mock response examples

### Final text response

```json
{
  "id": "chatcmpl_mock_final",
  "object": "chat.completion",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Order A123 has shipped and arrives Friday."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 100,
    "completion_tokens": 20,
    "total_tokens": 120
  }
}
```

### Tool call response

```json
{
  "id": "chatcmpl_mock_tool",
  "object": "chat.completion",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": null,
        "tool_calls": [
          {
            "id": "call_lookup_order",
            "type": "function",
            "function": {
              "name": "lookup_order",
              "arguments": "{\"order_id\": \"A123\"}"
            }
          }
        ]
      },
      "finish_reason": "tool_calls"
    }
  ],
  "usage": {
    "prompt_tokens": 90,
    "completion_tokens": 10,
    "total_tokens": 100
  }
}
```

## Testing checkpoint

Run:

```bash
uv run pytest tests/unit/test_openai_compatible_provider.py
uv run pytest
```

## Required tests

1. Build request without tools.
2. Build request with tools.
3. Build request with `tool_choice="auto"`.
4. Build request with extra params.
5. Building request does not need API key.
6. Mock final text response parses correctly.
7. Mock tool call response parses correctly.
8. Usage parses correctly.
9. No test performs a network call.

## Acceptance criteria

1. OpenAI-compatible request bodies are exact and stable.
2. Mocked responses parse correctly.
3. No real API calls happen.
4. All tests pass.

## Do not proceed unless

The OpenAI-compatible provider can be fully tested with mocks.

# Phase 4: SQLite trace store

## Goal

Add local SQLite tracing with schema, migrations, redaction, and retrieval methods.

## Codex prompt

```text
Implement Phase 4 only. Add SQLite trace storage. Tracing is on by default later, but this phase only implements the storage layer. Add migrations, schema creation, redaction, and storage/retrieval methods. Use temp SQLite files in tests.
```

## Tasks

1. Add `src/clearagent/storage/sqlite.py`.
2. Add `src/clearagent/storage/redaction.py`.
3. Add schema creation or migration support.
4. Create tables:

   1. `runs`
   2. `turns`
   3. `model_calls`
   4. `tool_calls`
   5. `eval_suite_runs`
   6. `eval_case_results`
   7. `baselines`
5. Add indexes for run and turn lookup.
6. Implement:

   1. `start_run`
   2. `end_run`
   3. `start_turn`
   4. `end_turn`
   5. `save_model_request`
   6. `save_model_response`
   7. `start_tool_call`
   8. `end_tool_call`
   9. `list_runs`
   10. `get_run`
   11. `get_turns`
   12. `get_model_call_for_turn`
7. Implement redaction for common secret keys.
8. Ensure `.clearagent` directory is created automatically when using default DB path.

## Testing checkpoint

Run:

```bash
uv run pytest tests/unit/test_sqlite_trace_store.py
uv run pytest
```

## Required tests

1. DB initializes.
2. Migrations are idempotent.
3. Run lifecycle works.
4. Turn lifecycle works.
5. Model request JSON is saved exactly, except redacted secrets.
6. Model response links to model request.
7. Tool call links to turn.
8. `get_model_call_for_turn(run_id, turn_index)` returns the right row.
9. Redaction redacts `authorization`, `api_key`, `token`, `secret`, and `password` keys.
10. Tests use temporary SQLite paths.

## Acceptance criteria

1. SQLite storage works without external services.
2. Exact provider request body can be persisted.
3. Turn-level retrieval works.
4. All tests pass.

## Do not proceed unless

The trace store can reliably save and retrieve model requests by run and turn.

# Phase 5: Agent runtime with tracing on by default

## Goal

Wire the provider abstraction and SQLite trace store into `Agent.run`.

## Codex prompt

```text
Implement Phase 5 only. Update Agent.run so tracing is on by default and every run captures run, turn, model request, model response, tool call, and final output data. Use fake providers and mocked OpenAI-compatible responses only. Do not call real APIs.
```

## Tasks

1. Update `Agent` to accept:

   1. `trace=True`
   2. `trace_db_path=".clearagent/traces.sqlite"`
   3. `trace_store=None`
   4. `max_turns`
2. Ensure `agent.run(..., trace=None)` uses the agent default.
3. Start run at beginning.
4. For each model iteration:

   1. start turn
   2. build provider request
   3. save provider request before completion
   4. complete provider call using fake/mocked provider
   5. save provider response
   6. execute tools if needed
   7. save tool calls
   8. end turn
5. End run with final output.
6. Save errors for provider failure, tool failure, and max turns exceeded.
7. Return `RunResult` with:

   1. `output`
   2. `run_id`
   3. `trace_db_path`
   4. `tool_calls`
   5. `usage`
   6. `latency_ms`
8. Ensure tracing can be disabled with `trace=False`.

## Testing checkpoint

Run:

```bash
uv run pytest tests/integration/test_agent_tracing.py
uv run pytest
```

## Required tests

1. Simple no-tool run creates:

   1. one run row
   2. one turn row
   3. one model call row
   4. final output
2. Tool-using run creates:

   1. one run row
   2. multiple turn rows
   3. multiple model call rows
   4. at least one tool call row
3. Provider request is saved before provider response.
4. Failed mocked provider call still saves request row.
5. `trace=False` creates no DB rows.
6. Default tracing creates `.clearagent/traces.sqlite`.
7. Every turn has a `turn_index`.
8. Every turn has `input_messages_json` and `output_messages_json`.

## Acceptance criteria

1. Tracing is on by default.
2. Exact provider requests are saved for each turn.
3. Turn-level replay data exists.
4. No real API calls happen.
5. All tests pass.

## Do not proceed unless

A mocked agent run creates complete trace rows and request snapshots.

# Phase 6: Trace CLI and replay-request export

## Goal

Expose saved traces and exact provider requests through the CLI.

## Codex prompt

```text
Implement Phase 6 only. Add CLI commands for trace list, trace show, trace turns, request, and replay-request. These commands should read from SQLite and print/export stored data. Do not re-run model calls.
```

## Tasks

1. Add or update CLI commands:

   1. `clearagent trace list`
   2. `clearagent trace show <run_id>`
   3. `clearagent trace turns <run_id>`
   4. `clearagent request <run_id> --turn 0`
   5. `clearagent replay-request <run_id> --turn 0 --out request.json`
2. Support `--trace-db` flag.
3. Print readable Rich tables for list/show commands.
4. Print JSON for request commands.
5. Do not call providers from these commands.

## Testing checkpoint

Run:

```bash
uv run pytest tests/integration/test_trace_cli.py
uv run pytest
```

## Required tests

1. `trace list` shows stored run.
2. `trace show` shows run summary.
3. `trace turns` shows turn indexes.
4. `request <run_id> --turn 0` prints exact request JSON.
5. `replay-request` writes exact request JSON to file.
6. Missing run ID returns clear error.
7. Missing turn index returns clear error.

## Acceptance criteria

1. Developers can inspect saved traces without code.
2. Developers can export exact provider requests.
3. No provider call is made by trace commands.
4. All tests pass.

## Do not proceed unless

The stored provider request can be recovered exactly from CLI.

# Phase 7: Eval suite parser and final-output checks

## Goal

Implement YAML eval suites and deterministic final-output-first checks.

## Codex prompt

```text
Implement Phase 7 only. Add YAML eval suite parsing and final-output-first checks. Keep tool checks optional. Do not add pytest integration yet. Use fake/mocked agents in tests.
```

## Tasks

1. Add or update:

   1. `src/clearagent/evals/suite.py`
   2. `src/clearagent/evals/case.py`
   3. `src/clearagent/evals/checks.py`
2. Parse suite fields:

   1. `name`
   2. `type`
   3. `description`
   4. `defaults`
   5. `cases`
3. Parse case fields:

   1. `name`
   2. `input`
   3. `tags`
   4. `checks`
4. Implement checks:

   1. `contains`
   2. `contains_any`
   3. `not_contains`
   4. `regex`
   5. `equals`
   6. `json_schema`
   7. `refuses`
   8. `expected_tools`
   9. `forbidden_tools`
   10. `latency_under_ms`
   11. `cost_under`
5. Ensure checks return structured results, not just booleans.

## Testing checkpoint

Run:

```bash
uv run pytest tests/unit/test_eval_suite.py tests/unit/test_eval_checks.py
uv run pytest
```

## Required tests

1. Valid YAML parses.
2. Invalid YAML raises clear error.
3. `contains` passes and fails.
4. `contains_any` passes and fails.
5. `not_contains` passes and fails.
6. `regex` passes and fails.
7. `equals` passes and fails.
8. `json_schema` passes and fails.
9. `refuses` passes on refusal-like text.
10. `expected_tools` passes and fails.
11. `forbidden_tools` passes and fails.
12. Cost and latency checks pass and fail.

## Acceptance criteria

1. Eval suites can be represented as typed Python objects.
2. Checks produce readable failure details.
3. Final-output checks are the primary path.
4. All tests pass.

## Do not proceed unless

Eval suite parsing and check execution are stable.

# Phase 8: Eval runner and eval CLI

## Goal

Run eval suites against agents, save eval results to SQLite, and print reports.

## Codex prompt

```text
Implement Phase 8 only. Add EvalRunner and CLI eval commands. Each eval case should run the agent, save a normal trace run, evaluate final output, and save eval results to SQLite. Use mocked providers only.
```

## Tasks

1. Add or update `src/clearagent/evals/runner.py`.
2. Add or update `src/clearagent/evals/report.py`.
3. Implement `EvalRunner(agent).run_suite(suite)`.
4. For each eval case:

   1. run agent
   2. get final output
   3. run checks
   4. save trace run
   5. save eval case result
5. Add CLI:

   1. `clearagent eval <agent_path> <suite_path>`
   2. `clearagent eval all` if config supports discovery
6. Print report summary.
7. On failures, include:

   1. suite name
   2. case name
   3. input
   4. final output
   5. failed checks
   6. run_id
   7. trace DB path

## Testing checkpoint

Run:

```bash
uv run pytest tests/integration/test_eval_runner.py tests/integration/test_eval_cli.py
uv run pytest
```

## Required tests

1. Passing suite returns success.
2. Failing suite returns failure.
3. Each eval case creates a trace run.
4. Eval suite run is saved in SQLite.
5. Eval case results are saved in SQLite.
6. CLI output includes failure details.
7. CLI exit code is nonzero on failed evals.
8. CLI uses mocked provider only.

## Acceptance criteria

1. Eval suites can be run from CLI.
2. Eval results are linked to trace runs.
3. Failures are readable.
4. No real API calls happen.
5. All tests pass.

## Do not proceed unless

An eval failure points to a traceable run ID.

# Phase 9: pytest integration

## Goal

Make ClearAgent evals runnable as normal pytest tests.

## Codex prompt

```text
Implement Phase 9 only. Add the pytest integration. Start with a simple helper function assert_eval_suite_passes(agent, suite_path). Then add pytest markers and CLI options if practical. Do not implement custom test collection yet unless it is straightforward.
```

## Tasks

1. Add `src/clearagent/pytest_plugin/plugin.py`.
2. Add helper:

   ```python
   assert_eval_suite_passes(agent, suite_path, *, trace_db_path=None)
   ```
3. Add pytest options:

   1. `--clearagent-trace-db`
   2. `--clearagent-no-trace`
   3. `--clearagent-model` if easy
4. Register markers:

   1. `clearagent`
   2. `clearagent_suite`
   3. `clearagent_live_model`
5. Ensure failures raise normal `AssertionError` with readable detail.
6. Add docs example.

## Testing checkpoint

Run:

```bash
uv run pytest tests/integration/test_pytest_integration.py
uv run pytest
```

## Required tests

1. Passing suite passes pytest helper.
2. Failing suite raises `AssertionError`.
3. Error message includes suite name.
4. Error message includes case name.
5. Error message includes failed check.
6. Error message includes run ID.
7. Error message includes trace DB path.
8. `--clearagent-no-trace` disables tracing if tested through pytester.

## Acceptance criteria

1. Users can run ClearAgent evals through pytest.
2. CI systems see normal pytest pass/fail results.
3. Failed evals are easy to debug.
4. All tests pass.

## Do not proceed unless

ClearAgent evals can be used in a standard pytest file.

# Phase 10: Single-node `create_agent` ergonomics

## Goal

Make the primary API feel as easy as LangGraph/LangChain `create_agent`, but smaller and eval-first.

## Codex prompt

```text
Implement Phase 10 only. Improve create_agent ergonomics while preserving the existing tested runtime. The API should let users create a single-node agent with model string, system prompt, tools, tracing config, and max turns. Do not add graph features yet.
```

## Tasks

1. Add or update `src/clearagent/create.py`.
2. Export `create_agent` from `clearagent.__init__`.
3. Support:

   1. `name`
   2. `model`
   3. `system_prompt`
   4. `tools`
   5. `trace`
   6. `trace_db_path`
   7. `max_turns`
   8. `temperature`
4. Resolve provider from model URI.
5. Use OpenAI-compatible provider by default for:

   1. `openai:*`
   2. `openrouter:*`
6. For now, use fake provider in tests.
7. Add README example.

## Testing checkpoint

Run:

```bash
uv run pytest tests/unit/test_create_agent.py tests/integration/test_agent_tracing.py
uv run pytest
```

## Required tests

1. `create_agent` returns an Agent.
2. Agent has correct name.
3. Agent resolves provider from model URI.
4. Agent tracing is on by default.
5. Agent can run with mocked provider.
6. Agent can run with tools.

## Acceptance criteria

1. Simple agent creation is clean.
2. Existing runtime tests still pass.
3. No real API calls happen.

## Do not proceed unless

The main user-facing API is stable.

# Phase 11: Minimal multi-node graph

## Goal

Support simple multi-node agents without becoming a full graph framework.

## Codex prompt

```text
Implement Phase 11 only. Add a minimal AgentGraph that supports linear node flows and simple conditional routing. It must reuse Agent.run internals where practical and preserve shared trace run IDs with node names. Use mocked providers only.
```

## Tasks

1. Add `src/clearagent/graph/graph.py`.
2. Add `src/clearagent/graph/node.py` if needed.
3. Support:

   1. `.add_node(agent)`
   2. `.add_edge(from_node, to_node)`
   3. `.set_entrypoint(node_name)`
   4. `.run(input)`
4. Store graph name in run row.
5. Store node name in every turn.
6. Support linear flows first.
7. Optional: support conditional routing function.
8. Add example in `examples/multinode`.

## Testing checkpoint

Run:

```bash
uv run pytest tests/integration/test_agent_graph.py
uv run pytest
```

## Required tests

1. Two-node graph runs from planner to writer.
2. Graph creates one shared run ID.
3. Turns include node names.
4. Final graph output is saved to run row.
5. Trace CLI can show graph node turns.
6. No real API calls happen.

## Acceptance criteria

1. Multi-node flow works.
2. Trace data is clear and replayable by node/turn.
3. The graph API remains minimal.
4. All tests pass.

## Do not proceed unless

Multi-node trace output is understandable.

# Phase 12: Baselines and regression comparison

## Goal

Allow eval suite results to be saved and compared across prompt/model changes.

## Codex prompt

```text
Implement Phase 12 only. Add baseline save and compare for eval suite runs. This should compare case-level pass/fail results and report regressions and improvements. Do not add model matrix yet.
```

## Tasks

1. Add `src/clearagent/evals/baseline.py`.
2. Implement save baseline from suite run.
3. Implement compare current suite run to baseline.
4. Add CLI:

   1. `clearagent baseline save <suite_run_id> --name v1`
   2. `clearagent baseline compare <baseline_name> <suite_run_id>`
5. Report:

   1. unchanged passes
   2. unchanged failures
   3. regressions
   4. improvements
6. Store baselines in SQLite.

## Testing checkpoint

Run:

```bash
uv run pytest tests/integration/test_baselines.py
uv run pytest
```

## Required tests

1. Save baseline.
2. Compare identical run to baseline.
3. Detect regression.
4. Detect improvement.
5. Missing baseline returns clear error.

## Acceptance criteria

1. Regression checks work from stored eval results.
2. CLI reports regressions clearly.
3. All tests pass.

## Do not proceed unless

A prompt/model change can be evaluated against a baseline.

# Phase 13: Promptfoo optional adapter

## Goal

Export ClearAgent eval suites to Promptfoo without making Promptfoo a core dependency.

## Codex prompt

```text
Implement Phase 13 only. Add optional Promptfoo export support. Do not add Promptfoo as a required dependency. Generate a promptfooconfig.yaml and a Python target script that calls a ClearAgent agent. Add tests that inspect generated files only. Do not run Promptfoo in tests.
```

## Tasks

1. Add `src/clearagent/evals/promptfoo_export.py`.
2. Add CLI group:

   1. `clearagent promptfoo export`
   2. `clearagent promptfoo target`
3. Export ClearAgent suite cases into Promptfoo tests.
4. Generate Python target script.
5. Include run ID and trace DB path in returned metadata.
6. Document that Promptfoo requires Node.js and separate install.

## Testing checkpoint

Run:

```bash
uv run pytest tests/unit/test_promptfoo_export.py
uv run pytest
```

## Required tests

1. Export creates valid YAML.
2. Export includes test cases.
3. Export maps simple checks to Promptfoo assertions where possible.
4. Target script imports the requested agent path.
5. Promptfoo is not required to run unit tests.

## Acceptance criteria

1. Promptfoo integration is optional.
2. Generated config is usable as a starting point.
3. Core package does not depend on Promptfoo.
4. All tests pass.

## Do not proceed unless

Promptfoo remains a complement, not a replacement for ClearAgent evals.

# Phase 14: Documentation and examples pass

## Goal

Make the project understandable and impressive as an open-source repo.

## Codex prompt

```text
Implement Phase 14 only. Update README, docs, examples, and comments to match the implemented features. Do not add new runtime features. Ensure all documented commands work with mocked/local examples unless clearly marked as live-provider commands.
```

## Tasks

1. Update README with:

   1. positioning
   2. quickstart
   3. create_agent example
   4. eval suite example
   5. tracing example
   6. pytest example
   7. Promptfoo optional example
2. Add docs:

   1. `docs/tracing.md`
   2. `docs/evals.md`
   3. `docs/providers.md`
   4. `docs/pytest.md`
   5. `docs/promptfoo.md`
3. Ensure examples use mocked or fake providers by default.
4. Add comments showing where live API keys can be used later.
5. Add architecture diagram in markdown text if useful.

## Testing checkpoint

Run:

```bash
uv run pytest
uv run clearagent --help
```

If examples are executable:

```bash
uv run python examples/customer_support/agent.py
```

## Acceptance criteria

1. Docs match implementation.
2. README is clear about the product wedge.
3. Examples run without real API keys unless marked live.
4. All tests pass.

## Do not proceed unless

A new contributor can understand what ClearAgent is and run local tests.

# Final full test checkpoint

Run this before handing back to the user:

```bash
uv sync --all-extras --dev
uv run pytest
uv run ruff check .
uv run mypy src || true
uv run clearagent --help
```

If mypy is not clean yet, report the issues and whether they are acceptable for MVP.

# Mocking policy for the whole plan

Until the user provides real API keys, all model behavior must be mocked.

Allowed:

1. Fake provider classes.
2. Mocked `httpx` responses.
3. Deterministic canned OpenAI-compatible JSON responses.
4. Unit tests asserting request shapes.
5. Integration tests using fake providers.

Not allowed yet:

1. Real OpenAI API calls.
2. Real Anthropic API calls.
3. Real Google API calls.
4. Real OpenRouter API calls.
5. Tests that require network access.
6. Tests that require API keys.

# Live API testing plan for later

When a real API key is provided, add a separate live test phase:

```bash
OPENAI_API_KEY=... uv run pytest -m live_model
```

Live tests should be marked:

```python
@pytest.mark.live_model
```

Live tests should not run by default in CI.

# Suggested first Codex message

Use this to start:

```text
You are implementing ClearAgent from the PRD and phase plan. Start with Phase 0 only. Audit the scaffold and do not modify files. Summarize the current structure, existing tests, and gaps. Then propose the smallest safe Phase 1 changes. Remember: all provider behavior must be mocked for now. Do not make live API calls.
```

# Suggested second Codex message

After Phase 0 is complete:

```text
Proceed with Phase 1 only. Make the project uv-native and Python 3.14 aligned. Update only packaging, setup docs, .python-version, .gitignore, and CI if present. Run `uv sync --all-extras --dev` and `uv run pytest`. Stop and report if either fails.
```

# Stop conditions for Codex

Codex should stop and report instead of continuing if:

1. uv dependency resolution fails.
2. pytest fails after a phase.
3. A refactor requires changing more than three major modules unexpectedly.
4. A test would require a real API key.
5. A feature requires network access.
6. A design choice conflicts with the PRD.
7. A schema migration would break existing tests.
8. It is unclear whether a feature belongs in MVP.

# Final reminder

The correct MVP is not a huge agent framework.

The correct MVP is:

```text
create_agent + mocked OpenAI-compatible provider + SQLite traces on by default + exact request snapshots + turn replay + YAML eval suites + pytest helper
```

Everything else is secondary.
