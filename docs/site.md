# ClearAgent Documentation

This page is the website-ready table of contents for ClearAgent. It is organized
as a learning path for new users first, then as a reference for contributors and
maintainers.

## Start Here

- [Installation](install.md) - use the canonical copy-paste walkthrough to
  install ClearAgent as a dependency and complete the external-project agent,
  trace, and eval path without an API key.
- [Getting Started](getting-started.md) - understand the first traced-eval loop,
  then configure a live provider or a repository checkout.
- [Product Scope](product-scope.md) - the public-library boundary and the
  near-term maintenance policy.
- [Core Concepts](core-concepts.md) - understand agents, tools, providers,
  structured outputs, traces, eval suites, graph flows, and chat sessions.
- [Support Status](status.md) - see which surfaces are stable, young, local-only,
  or optional.

## Guides

- [Evals](evals.md) - write YAML eval suites and run them through the CLI.
- [Tracing](tracing.md) - inspect saved runs, turns, model requests, visual trace
  timelines, replay, and request diffs.
- [Pytest](pytest.md) - run ClearAgent eval suites from normal pytest tests.
- [Providers](providers.md) - choose model URIs and understand provider request
  shapes.
- [Live Provider Compatibility](live-provider-compatibility.md) - run the
  bounded opt-in provider suite and refresh sanitized recordings.
- [Chat Backend](chat.md) - serve an agent through the FastAPI chat backend,
  browser client, and trace viewer.
- [Promptfoo](promptfoo.md) - export optional Promptfoo configs and target
  scripts.
- [Application Flows](flows.md) - understand the main run, eval, replay, and chat
  flows.
- [Database](database.md) - understand local SQLite trace and chat storage.
- [GitHub Deployment](deployment.md) - publish the repo, run CI, and validate
  docs and package builds.
- [Publishing](publishing.md) - build, inspect, dry-run, and publish Python
  package artifacts.

## Reference

- [Reference](reference.md) - public Python APIs, CLI commands, eval checks,
  trace paths, providers, and example modules.

## Internals

- [Architecture](architecture.md) - the runtime flow and trace persistence
  invariant.

## Contributing

- [Documentation Guide](contributing-docs.md) - how agents and contributors
  should keep docs accurate as code changes.
- [Contributing](../CONTRIBUTING.md) - development setup, quality checks, and
  pull request expectations.
- [Support](../SUPPORT.md) - where to ask questions and how to report bugs.
- [Security Policy](../SECURITY.md) - supported versions and private
  vulnerability reporting.
- [Code of Conduct](../CODE_OF_CONDUCT.md) - expected behavior in project
  spaces.

## Maintenance Rule

Every public behavior change should include a docs decision: update an existing
page, add a new page and link it here, or explicitly note why no docs changed.
The documentation checker validates local files, heading anchors, and that each
page under `docs/` appears in this index.
