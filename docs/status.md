# Support Status

This page separates the stable core from local or experimental surfaces.

## Stable Core

- `create_agent`
- `@tool`
- `FakeProvider` for tests and examples
- SQLite trace storage
- `clearagent run`
- `clearagent eval`
- `clearagent trace list/show/turns`
- `clearagent request`
- `clearagent replay-request`
- `clearagent trace-to-eval`
- `clearagent trace-report`
- pytest integration through `assert_eval_suite_passes`

These surfaces are covered by the main test suite and are the safest APIs to
use from external projects.

## Supported But Young

- Native Anthropic provider adapter
- Native Google Gemini provider adapter
- Structured outputs
- Eval matrix runs
- Eval iteration summaries
- Baseline save and compare
- `clearagent replay`
- `clearagent diff`
- Tool contract helpers

These features are implemented and tested, but provider behavior can still vary
across live models and API releases.

## Local Development Surfaces

- FastAPI chat backend
- Packaged browser chat client
- Runtime model/settings mutation
- Local trace triage API

The chat backend is designed for local development. The CLI rejects non-loopback
hosts, and runtime settings mutation is disabled unless explicitly enabled.
Applications embedding the FastAPI factory are responsible for any broader
authentication, authorization, rate limits, and deployment controls.

## ClearAgent Studio Boundary

Natural-language planning, source ingestion, synthetic data, generated judges,
prompt optimization, held-out promotion, hosted projects, authentication, and
managed storage belong to the separate ClearAgent Studio product. The MIT
package remains a complete local SDK and does not include those hosted workflow
implementations.

## Optional Integrations

- Promptfoo export
- Live OpenRouter eval smoke test

Live tests require both `OPENROUTER_API_KEY` and `CLEARAGENT_RUN_LIVE=1`.
Promptfoo is not installed as a core dependency.
