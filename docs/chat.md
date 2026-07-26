# Chat Backend

ClearAgent can serve any `Agent` through a small FastAPI app with SQLite-backed
chat sessions, streamed text responses, and a local visual trace viewer.

```bash
uv run clearagent chat examples.customer_support.agent:agent
```

The command loads `.env` before importing the agent module, so provider keys such
as `OPENROUTER_API_KEY` can live in the project `.env` file.

For a live OpenRouter-backed demo:

```bash
uv run clearagent chat examples.openrouter_chat.agent:agent
```

## API

- `POST /api/sessions` creates a chat session.
- `GET /api/sessions` lists recent sessions.
- `GET /api/sessions/{session_id}` returns one session.
- `GET /api/sessions/{session_id}/messages` returns persisted messages.
- `POST /api/sessions/{session_id}/messages` accepts `{"content": "..."}` and
  streams the assistant response as `text/event-stream` Server-Sent Events.
  Successful responses include a `trace` SSE event with the created `run_id`
  before `[DONE]` when tracing is enabled.
- `GET /api/traces` returns recent trace run summaries for the visual viewer.
- `GET /api/triage/runs/{run_id}` returns a local failure-triage payload for a
  trace run, including run rows, turns, model calls, tool calls, detected
  failures, grouped timeline steps, parsed request/response/tool JSON, and the
  same Markdown report produced by `trace-report`.

Messages are stored in `.clearagent/chat.sqlite` by default. The model request is
built from the agent system prompt plus the persisted user/assistant messages for
the selected session.

Persisted chat messages use the roles `user`, `assistant`, and `system`.
`ChatStore.add_message` rejects any other role before writing to SQLite, so a
bad caller cannot leave an unreadable row behind.

New sessions default to the title `New chat`. Explicit session titles and
first-user-message titles are whitespace-normalized before they are persisted.
Session lists are ordered by most recent message activity, including when
multiple updates occur within the same timestamp second.

## Shape

This is intentionally a small subset of the LangGraph/LangServe pattern:

- a session is the thread/conversation container
- session messages are short-term chat memory
- the run endpoint streams model output
- SQLite is the local persistence layer

The frontend can be a React/Vite app or any browser client that can call these
HTTP endpoints and read a streaming response body.

## Trace Viewer

The packaged browser client has a **Traces** mode next to Chat. It is a local
debugging surface for recent `.clearagent/traces.sqlite` runs:

- scan recent runs by status, agent, graph, input preview, and output preview
- open an agent or graph run as an ordered turn timeline
- inspect graph node names, model calls, tool calls, tool arguments, and tool
  results inline
- expand model request, response, usage, and tool JSON
- copy run IDs, JSON panes, and the Markdown trace report

The viewer is read-only. Missing run IDs return `404`, and malformed trace JSON
is surfaced as a detected failure in the triage payload where possible.

## Safety Boundary

The chat backend is a local development surface. The CLI binds to `127.0.0.1`
and rejects non-loopback hosts because the session and trace APIs are not a
hosted authentication boundary.

Runtime settings mutation is disabled by default. Enable it explicitly from the
CLI only when needed:

```bash
uv run clearagent chat examples.customer_support.agent:agent --allow-settings-mutation
```

If you embed `create_chat_app` in another server, opt in explicitly:

```python
app = create_chat_app(agent, allow_settings_mutation=True)
```

An embedded app can also require an admin token for `PUT /api/settings`:

```python
app = create_chat_app(
    agent,
    allow_settings_mutation=True,
    settings_admin_token="change-me",
)
```

Clients then send the token in `X-ClearAgent-Admin-Token`.

Invalid `PUT /api/settings` payloads return `400` without mutating the active
runtime settings or the bound agent configuration.
