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

SQLite runtime files are local artifacts and should not be committed. The
project `.gitignore` excludes `.clearagent/*.sqlite`.
