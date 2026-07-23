# Application Flows

This page summarizes the main ClearAgent workflows.

## Agent Run

1. User code creates an agent with `create_agent`.
2. `Agent.run` normalizes the system prompt and input into messages.
3. The provider builds a request object.
4. The trace store saves the redacted request snapshot.
5. The provider completes the request.
6. ClearAgent executes requested tools when present.
7. The final output and trace rows are saved.

## Eval Run

1. `clearagent eval` loads an agent and YAML suite.
2. Each case runs the agent.
3. Checks evaluate the final output or trace data.
4. Results are persisted in SQLite.
5. The CLI reports pass/fail counts and exits non-zero when any case fails.

## Request Replay

1. `clearagent request` reads a stored request snapshot.
2. `clearagent replay-request` exports the snapshot to JSON.
3. `clearagent replay` reruns the stored request with fresh credentials.
4. `clearagent diff` compares the rerun response with the stored response.

## Chat Backend

1. `clearagent chat` starts a FastAPI app on `127.0.0.1` by default.
2. The browser client creates or selects a chat session.
3. User messages are persisted to local SQLite.
4. The agent streams assistant text as server-sent events.
5. Assistant messages are persisted after the stream completes.
