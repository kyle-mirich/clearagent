# Live Provider Compatibility

ClearAgent's live compatibility suite is a contributor-only, explicitly paid
check. Normal pytest runs and `scripts/check.sh` never invoke provider APIs;
they replay sanitized recordings instead.

## Capability Inventory

Provider-dependent runtime behavior includes request construction, message and
system-instruction mapping, response parsing, tool-call round trips, streaming,
structured-output request mapping, usage reporting, and normalized provider
errors. Agent creation, Python tool execution, SQLite trace persistence,
serialization and redaction, deterministic eval checks and aggregation,
trace-to-eval generation, baseline comparison, graphs, reports, replay, pytest
helpers, and the chat backend are provider-agnostic once a provider response is
available.

The open-source package does not expose an LLM-as-judge API or held-out hosted
promotion workflow. Those rows are `unsupported`, not silently skipped. The
live suite exercises the public deterministic evaluator, trace-to-eval
promotion, and baseline comparison quality gate instead.

## Credentials And Opt-In

Set credentials in the environment or in an ignored `.env` file. Never pass
their values on the command line or commit the file.

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `GEMINI_API_KEY` or `GOOGLE_API_KEY`
- `OPENROUTER_API_KEY`

The command refuses to make a request unless `CLEARAGENT_LIVE_TESTS=1` is set:

```bash
CLEARAGENT_LIVE_TESTS=1 uv run python scripts/live_provider_compatibility.py
```

Use `--env-file .env` to load an ignored file. Use `--provider openai` to run a
single provider while diagnosing a failure; the option is repeatable.

## Provider And Cost Boundary

The suite makes exactly four requests per available provider: one basic agent
response, two requests for the tool/eval round trip, and one streamed response.
Every request has a 96-output-token ceiling and the runner permits no retries.
The hard cap is therefore four requests and at most 384 generated tokens per
provider, excluding provider-accounted reasoning tokens.

Treat USD 0.10 per provider per run as the administrative stop boundary. Check
current provider pricing before refreshing recordings and do not run if these
bounds could exceed it. Most provider responses report tokens but not monetary
cost, so the script enforces request and output bounds rather than claiming an
unavailable price. The recorded OpenRouter non-stream calls reported
`$0.00012139`; the other three providers did not report a monetary cost.

The targets recorded on 2026-07-25 are:

| Provider | Exact model | Requests | Recorded non-stream tokens | Result |
| --- | --- | ---: | ---: | --- |
| OpenAI | `gpt-5.6-luna` | 4/4 | 259 | pass |
| Anthropic | `claude-sonnet-5` | 4/4 | 1,167 | pass |
| Google GenAI | `gemini-3.5-flash-lite` | 4/4 | 241 | pass |
| OpenRouter | `xiaomi/mimo-v2.5` | 4/4 | 751 | pass |

`gemini-3.5-flash-lite` was selected from Google's live model catalog as a
currently supported inexpensive general-purpose `generateContent` model.
OpenAI uses its canonical Responses API. OpenRouter, local servers, and Ollama
continue to use the OpenAI-compatible Chat Completions adapter.

## Compatibility Matrix

`pass` means the checked-in recording contains successful live evidence.
`skipped` is reserved for a missing credential or deliberately unrun
capability; `unsupported` means ClearAgent does not claim the feature; `failed`
means a request was attempted and did not meet the contract.

| Capability | OpenAI | Anthropic | Google | OpenRouter |
| --- | --- | --- | --- | --- |
| Basic agent and deterministic response | pass | pass | pass | pass |
| System instruction and `list[Message]` input | pass | pass | pass | pass |
| Tool definition, call, result, final response | pass | pass | pass | pass |
| Streaming | pass | pass | pass | pass |
| Trace capture and serialization | pass | pass | pass | pass |
| Fixture dataset, evaluator checks, aggregate | pass | pass | pass | pass |
| Trace-to-eval promotion | pass | pass | pass | pass |
| Baseline comparison quality gate | pass | pass | pass | pass |
| Structured-output request mapping | skipped | skipped | skipped | skipped |
| LLM-as-judge / hosted held-out promotion | unsupported | unsupported | unsupported | unsupported |

Structured-output mapping remains covered by mocked offline tests; it was kept
out of this minimum paid call plan. Missing credentials are reported as
`skipped` with the required variable names and zero requests. Malformed
responses, HTTP/provider failures, malformed tool arguments, and invalid stream
events are exercised with mocked provider responses so error coverage does not
spend money.

## Record Or Refresh Fixtures

Refreshing is intentional and writes sanitized JSON to
`tests/fixtures/live_provider_recordings/`:

```bash
CLEARAGENT_LIVE_TESTS=1 uv run python scripts/live_provider_compatibility.py \
  --env-file .env --record
```

The recorder redacts credential headers, normalizes response/run/tool IDs and
timestamps, checks the serialized payload against every configured credential,
and refuses to write if a secret is found. Each provider file includes the
provider, exact requested and actual model, API shape, recording timestamp,
request bounds, realistic raw response structures, tool traces, evaluator
report, generated eval YAML, and baseline result. `run-summary.json` is the
machine-readable compatibility matrix.

Review all five JSON files and update the dated tables in this page after an
intentional refresh. A requested-model rejection must remain recorded as
`failed` with the provider's catalog-supported alternative; never edit the
model silently.

## Offline Fixture Suite

Run the recordings without credentials or network access:

```bash
uv run pytest tests/unit/test_live_provider_compatibility.py
```

These tests feed recorded raw responses through each provider parser, validate
serialized traces and tool calls, reconstruct the public `EvalReport`, parse
the promoted YAML, check the baseline result, enforce request metadata, and
scan for common secret patterns. They prove fixture usability, not that a model
is still available today.

When a live run fails, first distinguish the target availability status from a
capability failure in `run-summary.json`. An availability failure should name a
supported alternative without substituting it invisibly. A capability failure
usually indicates provider API drift: preserve the sanitized error, add or
adjust an offline regression test, fix the adapter if appropriate, refresh only
that provider, then update the matrix and run the full local gate.

## 2026-07-25 Completion Audit

The final recording command used each exact model in the table and passed at
four requests per provider. No requested model was unavailable and no
alternative was substituted. Before that final run, bounded diagnostic runs
exposed OpenAI's need for the canonical Responses API and two Google REST
round-trip requirements: tool results use the `user` role, and Gemini 3 thought
signatures must be returned with their original function-call parts. The
corresponding adapter fixes are covered offline.

Across implementation and final verification, generation requests totaled 31:
11 OpenAI, 4 Anthropic, 12 Google, and 4 OpenRouter. Four additional model-list
catalog requests established exact availability and selected the Google model.
No invocation exceeded its per-provider cap and no retry was automatic. Only
OpenRouter reported monetary cost (`$0.00012139` for its recorded non-stream
calls); the other providers returned token usage without USD amounts.

Commands used for final evidence:

```bash
CLEARAGENT_LIVE_TESTS=1 uv run python scripts/live_provider_compatibility.py \
  --env-file .env --record
uv run pytest tests/unit/test_live_provider_compatibility.py
uv run bash scripts/check.sh
uv build
```

Remaining limitations are explicit: structured-output mapping is mocked but
was not included in the paid live call plan; LLM-as-judge and hosted held-out
promotion are not public ClearAgent features; streamed traces currently retain
chunks and joined output but not provider token/cost usage; and Anthropic,
Google, and OpenAI did not report USD cost through these response shapes.
