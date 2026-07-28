# Evals

Eval suites are YAML files focused on final output checks.

```yaml
name: smoke
type: output
cases:
  - name: shipped order
    input: Where is order A123?
    checks:
      - contains: shipped
      - not_contains: cancelled
```

Every suite must include a string `name` and one or more `cases`. Each case must
include string `name` and `input` fields plus one or more deterministic
`checks`, and case names must be unique within the suite. Optional `defaults`
and `matrix` sections must be mappings, and matrix `models` and `temperatures`
values must be lists when present. ClearAgent rejects empty suites and cases
without checks before running the agent, so a vacuous eval cannot pass or make
a provider call.

Run a suite:

```bash
uv run clearagent eval examples.customer_support.agent:agent examples/customer_support/evals/smoke.yaml
```

Each eval case runs the agent, writes a normal trace run, evaluates checks
against the final output or trace data, and records the eval result through the
agent's `TraceStore`. SQLite is the default; an injected store is used for the
entire eval flow.

Add `--json` to emit the complete `EvalReport` for automation. The command
still exits 1 when any case fails, after writing the JSON report.

## Trace-to-Eval Generation

Promote observed behavior into regression coverage with `trace-to-eval`:

```bash
uv run clearagent trace-to-eval <run_id> --out generated.yaml
```

The generated suite uses the trace run input as the eval input and adds a
starter `contains` check for the recorded final output. Edit the generated YAML
before committing it; the command is meant to create a useful first draft, not a
perfect long-term assertion.

## Output Checks

Supported output checks include:

- `contains`
- `contains_any`
- `not_contains`
- `regex`
- `equals`
- `json_schema`
- `refuses`
- `expected_tools`
- `forbidden_tools`
- `latency_under_ms`
- `cost_under`

Use output checks first. Tool, latency, and trace-aware checks are available
when the final answer alone is not enough to catch a regression.

`cost_under` requires the provider response to include a monetary cost. If only
token usage is available, the check fails as unavailable instead of assuming a
zero-dollar run.

Invalid `regex` patterns fail that check with an error message instead of
aborting the suite. The `regex` operand must be a YAML string; `refuses` and
`structured_output` operands must be YAML booleans.

`contains_any`, `expected_tools`, and `forbidden_tools` expect YAML lists. If a
suite passes a scalar value instead, ClearAgent fails that check with a clear
validation message.

Malformed operands for other checks likewise fail that case without aborting
the suite, and suite records are finalized even when setup fails unexpectedly.
Trace-aware checks fail explicitly when the result has no trace or its recorded
run cannot be found; absence of trace evidence never counts as a passing check.

Eval suites can also define a model and temperature matrix:

```yaml
name: model_matrix
type: output
matrix:
  models:
    - openrouter:openai/gpt-4o-mini
    - openrouter:anthropic/claude-sonnet-4.5
  temperatures: [0.0, 0.2]
cases:
  - name: shipped order
    input: Where is order A123?
    checks:
      - contains: shipped
      - trace_provider: openrouter
      - max_turns: 2
```

If `models` is omitted, ClearAgent runs the temperature variants against the
agent's current model and current provider. This preserves custom and fake
providers. Python callers can pass an explicit `provider_factory` to
`EvalRunner` when they want a new provider for every matrix variant.

For quick prompt, model, or temperature experiments, use `iterate`:

```bash
uv run clearagent iterate examples.customer_support.agent:agent examples/customer_support/evals/smoke.yaml \
  --model openai:gpt-4.1-mini \
  --temperature 0.0 \
  --temperature 0.7
```

`iterate` prints JSON with one summary per variant, including pass/fail counts,
pass rate, case-level checks, and trace run IDs. It restores the agent's
original model, provider, and temperature after the run.

Trace-aware checks include:

- `trace_provider`
- `max_turns`
- `called_tool`
- `not_called_tool`
- `structured_output`

They read from the exact store retained on the case's `RunResult`, so they work
with custom `TraceStore` implementations without reopening SQLite.

## Baselines

Suite runs can be saved as baselines and compared later:

```bash
uv run clearagent baseline save <suite_run_id> --name v1
uv run clearagent baseline compare v1 <suite_run_id> --json
```

Baseline comparison reports regressions and improvements by eval case name.
The JSON form also includes unchanged passes and failures. A successful
comparison exits zero even when its `regressions` list is non-empty, so CI
policy should inspect that field.
For matrix runs, ClearAgent persists each result's canonical variant data and
adds it to the reported identity, for example
`shipped order [variant={"model":"openai:gpt-4.1-mini","temperature":0.2}]`.
This keeps results for the same case distinct across models and temperatures;
non-matrix case names remain unchanged.
Missing suite runs, comparison runs, baseline names, or malformed stored
baseline records fail with a clear parameter error.
Baseline comparison also requires the saved baseline and current suite run to
share the same suite name, suite type, agent name, model, and case set.

## Related Docs

- [Tracing](tracing.md)
- [Pytest](pytest.md)
- [Reference](reference.md)
