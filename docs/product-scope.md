# Product Scope

## Decision

ClearAgent is an eval-first, local-first Python library for developers who
need to observe, test, and improve a small agent without adopting a hosted
agent platform.

The public product is the development feedback loop:

```text
define agent -> capture local trace -> turn behavior into an eval -> replay or
compare a change -> keep the result in ordinary Python tests
```

That loop is the reason to choose ClearAgent. It should stay easier to inspect
and adopt than a general orchestration framework or a hosted observability
product.

## What ClearAgent includes

- Plain-Python agents, typed tools, and structured outputs.
- OpenAI-compatible, Anthropic, and Gemini provider adapters.
- Redacted local SQLite traces, request replay, reports, and diffs.
- YAML eval suites, baselines, iteration summaries, and pytest integration.
- A deliberately bounded linear graph and local-only chat/debugging surfaces.

## What ClearAgent does not include

ClearAgent is not a general agent builder, hosted workspace, deployment
platform, or team collaboration product. In particular, it does not include:

- Natural-language planning or generated agent architectures.
- Source ingestion, synthetic datasets, generated judges, or prompt
  optimization.
- Hosted projects, authentication, managed storage, billing, or production
  agent hosting.
- Visual workflow composition, general-purpose or dynamic multi-agent
  orchestration, or an integration marketplace. The bundled `AgentGraph`
  remains a bounded linear flow.

Those managed workflow capabilities belong to ClearAgent Studio. Keeping the
boundary explicit lets the open-source package remain useful on its own while
leaving room for a distinct paid product.

## Near-term maintenance policy

Until the package has external users and release feedback, prioritize:

1. Reliable package releases and a short path from installation to a first
   traced eval.
2. Documentation and examples that demonstrate the local feedback loop.
3. Fixes to the stable core and provider compatibility.

Do not add new graph, workflow, hosting, or builder features merely to match
larger agent frameworks. A proposed addition should make the core loop more
reliable or easier for a new developer to adopt.
