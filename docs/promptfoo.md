# Promptfoo

Promptfoo support is optional. ClearAgent can export a starter config and Python
target script, but Promptfoo is not a core dependency.

```bash
uv run clearagent promptfoo export examples.customer_support.agent:agent examples/customer_support/evals/smoke.yaml promptfooconfig.yaml
uv run clearagent promptfoo target examples.customer_support.agent:agent .clearagent/promptfoo_target.py
```

The export command writes a Promptfoo config that calls the generated Python
target script. Both commands require the agent path to use `module:object`
format. Promptfoo itself is not installed as a ClearAgent dependency.

Current export support maps `contains`, `not_contains`, `equals`,
`contains_any`, `regex`, and `refuses`. If a suite uses other ClearAgent check
types, export fails with a clear error instead of dropping those checks
silently. Export also validates the expected value shapes for supported checks
such as list-valued `contains_any`, boolean `refuses`, and valid regex syntax.
Exported Promptfoo tests keep the ClearAgent eval case name as the Promptfoo
test description.

## Related Docs

- [Evals](evals.md)
- [Reference](reference.md)
