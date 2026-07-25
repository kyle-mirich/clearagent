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
  reports, replay, and response diffs.
- Run YAML eval suites, generate starter evals from traces, save baselines, and
  integrate evals with pytest.
- Use OpenAI-compatible, Anthropic, and Google provider adapters, plus a
  deterministic fake provider for tests and offline examples.
- Discover current OpenAI, Anthropic, and OpenRouter model catalogs in local
  chat, with offline fallbacks that include GPT-5.6 and Claude Opus 5.
- Debug through the CLI or the bundled loopback-only local chat and trace
  viewer.

Hosted workspaces, natural-language agent generation, dataset or judge
generation, prompt optimization, deployment, billing, and collaboration are not
part of this package.
