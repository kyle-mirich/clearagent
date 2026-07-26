# Changelog

All notable changes to ClearAgent will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.1.0 - Unreleased

First alpha of the MIT-licensed, Python 3.14, local-first and eval-first
ClearAgent library.

- Define agents and typed tools in plain Python, including validated structured
  outputs and bounded linear flows.
- Record local SQLite traces with provider request snapshots, tool calls,
  reports, replay, response diffs, and deterministic serialization for
  unordered set-valued tool results.
- Run YAML eval suites, generate starter evals from traces, save baselines, and
  integrate evals with pytest.
- Keep matrix variants distinct in stored eval results and baseline comparisons,
  preserve custom providers for temperature-only matrices, and reject empty or
  check-free evals, malformed check operands, and missing trace evidence without
  false-positive passes.
- Use native OpenAI Responses, Anthropic Messages, and Google GenAI provider
  adapters, plus OpenAI-compatible OpenRouter/local adapters and a deterministic
  fake provider for tests and offline examples.
- Preserve OpenAI reasoning output and Anthropic thinking content across tool
  loops, omit temperature by default, and expose current OpenAI and Anthropic
  models in the local chat fallback catalog without removing older choices.
- Verify hosted provider behavior through a bounded opt-in live suite and
  sanitized recordings that replay in the default offline tests.
- Debug through the CLI or the bundled loopback-only local chat and trace
  viewer, with each streamed response linked only to the trace created by that
  request and sessions ordered by their actual recent activity. Invalid project
  and Promptfoo inputs produce actionable CLI parameter errors.

Hosted workspaces, natural-language agent generation, dataset or judge
generation, prompt optimization, deployment, billing, and collaboration are not
part of this package.
