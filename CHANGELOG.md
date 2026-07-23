# Changelog

All notable changes to ClearAgent will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.1.0 - Unreleased

- Initial local-first agent runtime with SQLite traces.
- Added provider request snapshots, replay, eval suites, pytest integration,
  Promptfoo export, native Anthropic and Google adapters, structured outputs,
  graph flows, and a local chat backend.
- Added PyPI-ready package metadata, installation docs, and publishing
  checklist.
- Hardened provider streaming, trace/eval lifecycle finalization, graph bounds,
  typed tool serialization, usage aggregation, and local chat safety defaults.
- Added the public `TraceStore` protocol and canonical optional eval dataset
  fields while keeping hosted planning and optimization outside the MIT package.
- Added contributor, support, conduct, and security policies plus a 90% minimum
  package coverage requirement in CI.
- Expanded reader-facing API, architecture, eval, chat, and support-status
  documentation.
