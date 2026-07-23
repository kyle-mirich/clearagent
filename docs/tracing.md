# Tracing

Tracing is on by default and writes to `.clearagent/traces.sqlite`.

Each run stores:

- a `runs` row with the final output
- one `turns` row per model iteration
- one `model_calls` row per provider request
- `tool_calls` rows when tools execute

Secrets in request bodies and headers are redacted before persistence.

```bash
uv run clearagent trace list
uv run clearagent trace show <run_id>
uv run clearagent trace turns <run_id>
uv run clearagent request <run_id> --turn 0
uv run clearagent replay-request <run_id> --turn 0 --out request.json
uv run clearagent replay <run_id> --turn 0
uv run clearagent diff <run_id> --turn 0
uv run clearagent trace-report <run_id> --out report.md
uv run clearagent trace-to-eval <run_id> --out generated.yaml
```

`request` prints the saved request snapshot. `replay-request` exports the saved
request snapshot. They do not rebuild a request from current code or call a
provider.

`replay` reruns the stored provider request with fresh credentials from the
environment. `diff` reruns the request and compares output, finish reason, and
usage against the stored response.

If a run or turn does not have a stored model request, or a stored request or
response snapshot is malformed, these commands fail with a clear parameter
error instead of falling through to a raw exception.
`diff` also requires the original turn to have a stored model response, so
failed original provider calls cannot be diffed until there is a response to
compare against.

`trace-report` exports a Markdown report with the run input, final output,
turns, model calls, and tool calls. It is intended for pull requests, debugging
notes, and incident reviews.

`trace-to-eval` writes a starter eval suite from a completed run so useful
observed behavior can become regression coverage.

## Visual Trace Viewer

The packaged local browser client includes a **Traces** mode for debugging runs
without leaving the chat app:

```bash
uv run clearagent chat examples.customer_support.agent:agent
```

Open the browser client and select **Traces**. The viewer lists recent runs from
the agent's local trace database with agent name, graph name when present,
status, start time, input preview, final output preview, turn count, and tool
call count.

Opening a run shows the execution timeline. For multi-agent graph runs, each
turn shows the recorded `node_name`, so planner/writer or other graph nodes are
visible in order. Each turn includes user input, final output, status, latency,
model calls, tool calls, collapsible request/response/usage JSON panes, and copy
buttons for JSON, run IDs, and the Markdown report.

The viewer is read-only and local-first. It uses the same
`.clearagent/traces.sqlite` data as the trace CLI and does not upload traces or
add external observability services.

## Related Docs

- [Chat Backend](chat.md)
- [Architecture](architecture.md)
- [Reference](reference.md)
