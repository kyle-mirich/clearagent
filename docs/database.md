# Database

ClearAgent uses local SQLite files for runtime data. There is no external
database service required.

## Trace Store

Default path:

```text
.clearagent/traces.sqlite
```

The trace store records:

- runs
- turns
- model calls
- tool calls
- eval suite runs
- eval case results
- baselines

Provider requests are saved before the model call. Secrets in headers and
request bodies are redacted before persistence.

`SQLiteTraceStore` implements the public `TraceStore` read/write protocol. A
custom implementation supplied to an agent is used by runtime execution,
graphs, eval persistence, trace-aware checks, reports, and the agent-backed chat
trace viewer. See [Architecture](architecture.md#storage-boundary) for the
boundary; standalone `--trace-db` CLI inspection remains SQLite-specific.

## Chat Store

Default path:

```text
.clearagent/chat.sqlite
```

The chat store records:

- chat sessions
- chat messages

This store is for the local chat backend and browser client.

## Schema Upgrades

Both stores use `PRAGMA user_version` and add missing columns during
initialization. This keeps existing local databases usable as the schema evolves.

## Git Hygiene

SQLite runtime files and their `-wal` and `-shm` sidecars are local artifacts
and should not be committed. `.clearagent/config.toml` is different: it is an
optional project configuration created by `clearagent init` and may be tracked
after its shared settings are reviewed.
