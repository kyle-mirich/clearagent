from contextlib import contextmanager
import json
import sqlite3
import time
from pathlib import Path
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

from clearagent.providers.base import ProviderRequest, ProviderResponse
from clearagent.serialization import json_safe
from clearagent.storage.redaction import redact

DEFAULT_TRACE_DB = Path(".clearagent/traces.sqlite")
TRACE_SCHEMA_VERSION = 2

TRACE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  agent_name TEXT NOT NULL,
  graph_name TEXT,
  root_input TEXT NOT NULL,
  final_output TEXT,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  total_latency_ms INTEGER,
  total_prompt_tokens INTEGER,
  total_completion_tokens INTEGER,
  total_cost_usd REAL,
  metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  turn_index INTEGER NOT NULL,
  node_name TEXT NOT NULL,
  input_messages_json TEXT NOT NULL,
  output_messages_json TEXT NOT NULL,
  final_output TEXT,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  latency_ms INTEGER,
  error_json TEXT,
  FOREIGN KEY(run_id) REFERENCES runs(id)
);
CREATE TABLE IF NOT EXISTS model_calls (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  turn_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  api_shape TEXT NOT NULL,
  endpoint TEXT,
  request_json TEXT NOT NULL,
  response_json TEXT,
  usage_json TEXT,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  latency_ms INTEGER,
  error_json TEXT,
  FOREIGN KEY(run_id) REFERENCES runs(id),
  FOREIGN KEY(turn_id) REFERENCES turns(id)
);
CREATE TABLE IF NOT EXISTS tool_calls (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  turn_id TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  args_json TEXT NOT NULL,
  result_json TEXT,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  latency_ms INTEGER,
  error_json TEXT,
  FOREIGN KEY(run_id) REFERENCES runs(id),
  FOREIGN KEY(turn_id) REFERENCES turns(id)
);
CREATE TABLE IF NOT EXISTS eval_suite_runs (
  id TEXT PRIMARY KEY,
  suite_name TEXT NOT NULL,
  suite_type TEXT NOT NULL,
  agent_name TEXT NOT NULL,
  model TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  status TEXT NOT NULL,
  passed INTEGER NOT NULL,
  failed INTEGER NOT NULL,
  skipped INTEGER NOT NULL,
  metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS eval_case_results (
  id TEXT PRIMARY KEY,
  suite_run_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  suite_name TEXT NOT NULL,
  case_name TEXT NOT NULL,
  input TEXT NOT NULL,
  final_output TEXT,
  passed INTEGER NOT NULL,
  checks_json TEXT NOT NULL,
  variant_json TEXT NOT NULL DEFAULT '{}',
  failure_json TEXT,
  latency_ms INTEGER,
  cost_usd REAL,
  FOREIGN KEY(suite_run_id) REFERENCES eval_suite_runs(id),
  FOREIGN KEY(run_id) REFERENCES runs(id)
);
CREATE TABLE IF NOT EXISTS baselines (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  suite_name TEXT NOT NULL,
  agent_name TEXT NOT NULL,
  model TEXT NOT NULL,
  created_at TEXT NOT NULL,
  results_json TEXT NOT NULL,
  metadata_json TEXT NOT NULL
);
"""

TRACE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_turns_run_id ON turns(run_id);
CREATE INDEX IF NOT EXISTS idx_turns_run_turn_index ON turns(run_id, turn_index);
CREATE INDEX IF NOT EXISTS idx_model_calls_run_id ON model_calls(run_id);
CREATE INDEX IF NOT EXISTS idx_model_calls_turn_id ON model_calls(turn_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_run_id ON tool_calls(run_id);
CREATE INDEX IF NOT EXISTS idx_eval_results_suite_run_id ON eval_case_results(suite_run_id);
CREATE INDEX IF NOT EXISTS idx_eval_results_run_id ON eval_case_results(run_id);
"""

TRACE_COLUMNS = {
    "runs": {
        "id": "TEXT PRIMARY KEY",
        "agent_name": "TEXT NOT NULL DEFAULT ''",
        "graph_name": "TEXT",
        "root_input": "TEXT NOT NULL DEFAULT ''",
        "final_output": "TEXT",
        "status": "TEXT NOT NULL DEFAULT 'running'",
        "started_at": "TEXT NOT NULL DEFAULT ''",
        "ended_at": "TEXT",
        "total_latency_ms": "INTEGER",
        "total_prompt_tokens": "INTEGER",
        "total_completion_tokens": "INTEGER",
        "total_cost_usd": "REAL",
        "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
    },
    "turns": {
        "id": "TEXT PRIMARY KEY",
        "run_id": "TEXT NOT NULL DEFAULT ''",
        "turn_index": "INTEGER NOT NULL DEFAULT 0",
        "node_name": "TEXT NOT NULL DEFAULT ''",
        "input_messages_json": "TEXT NOT NULL DEFAULT '[]'",
        "output_messages_json": "TEXT NOT NULL DEFAULT '[]'",
        "final_output": "TEXT",
        "status": "TEXT NOT NULL DEFAULT 'running'",
        "started_at": "TEXT NOT NULL DEFAULT ''",
        "ended_at": "TEXT",
        "latency_ms": "INTEGER",
        "error_json": "TEXT",
    },
    "model_calls": {
        "id": "TEXT PRIMARY KEY",
        "run_id": "TEXT NOT NULL DEFAULT ''",
        "turn_id": "TEXT NOT NULL DEFAULT ''",
        "provider": "TEXT NOT NULL DEFAULT ''",
        "model": "TEXT NOT NULL DEFAULT ''",
        "api_shape": "TEXT NOT NULL DEFAULT ''",
        "endpoint": "TEXT",
        "request_json": "TEXT NOT NULL DEFAULT '{}'",
        "response_json": "TEXT",
        "usage_json": "TEXT",
        "status": "TEXT NOT NULL DEFAULT 'running'",
        "started_at": "TEXT NOT NULL DEFAULT ''",
        "ended_at": "TEXT",
        "latency_ms": "INTEGER",
        "error_json": "TEXT",
    },
    "tool_calls": {
        "id": "TEXT PRIMARY KEY",
        "run_id": "TEXT NOT NULL DEFAULT ''",
        "turn_id": "TEXT NOT NULL DEFAULT ''",
        "tool_name": "TEXT NOT NULL DEFAULT ''",
        "args_json": "TEXT NOT NULL DEFAULT '{}'",
        "result_json": "TEXT",
        "status": "TEXT NOT NULL DEFAULT 'running'",
        "started_at": "TEXT NOT NULL DEFAULT ''",
        "ended_at": "TEXT",
        "latency_ms": "INTEGER",
        "error_json": "TEXT",
    },
    "eval_suite_runs": {
        "id": "TEXT PRIMARY KEY",
        "suite_name": "TEXT NOT NULL DEFAULT ''",
        "suite_type": "TEXT NOT NULL DEFAULT 'output'",
        "agent_name": "TEXT NOT NULL DEFAULT ''",
        "model": "TEXT NOT NULL DEFAULT ''",
        "started_at": "TEXT NOT NULL DEFAULT ''",
        "ended_at": "TEXT",
        "status": "TEXT NOT NULL DEFAULT 'running'",
        "passed": "INTEGER NOT NULL DEFAULT 0",
        "failed": "INTEGER NOT NULL DEFAULT 0",
        "skipped": "INTEGER NOT NULL DEFAULT 0",
        "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
    },
    "eval_case_results": {
        "id": "TEXT PRIMARY KEY",
        "suite_run_id": "TEXT NOT NULL DEFAULT ''",
        "run_id": "TEXT NOT NULL DEFAULT ''",
        "suite_name": "TEXT NOT NULL DEFAULT ''",
        "case_name": "TEXT NOT NULL DEFAULT ''",
        "input": "TEXT NOT NULL DEFAULT ''",
        "final_output": "TEXT",
        "passed": "INTEGER NOT NULL DEFAULT 0",
        "checks_json": "TEXT NOT NULL DEFAULT '[]'",
        "variant_json": "TEXT NOT NULL DEFAULT '{}'",
        "failure_json": "TEXT",
        "latency_ms": "INTEGER",
        "cost_usd": "REAL",
    },
    "baselines": {
        "id": "TEXT PRIMARY KEY",
        "name": "TEXT NOT NULL DEFAULT ''",
        "suite_name": "TEXT NOT NULL DEFAULT ''",
        "agent_name": "TEXT NOT NULL DEFAULT ''",
        "model": "TEXT NOT NULL DEFAULT ''",
        "created_at": "TEXT NOT NULL DEFAULT ''",
        "results_json": "TEXT NOT NULL DEFAULT '{}'",
        "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
    },
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class SQLiteTraceStore:
    """SQLite implementation of the public trace persistence protocol."""

    def __init__(self, path: str | Path = DEFAULT_TRACE_DB):
        self.path = Path(path)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(TRACE_SCHEMA_SQL)
            _ensure_columns(db, TRACE_COLUMNS)
            db.executescript(TRACE_INDEX_SQL)
            db.execute(f"PRAGMA user_version = {TRACE_SCHEMA_VERSION}")

    def start_run(
        self,
        *,
        agent_name: str,
        root_input: str,
        graph_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        run_id = _id("run")
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO runs
                (id, agent_name, graph_name, root_input, status, started_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, agent_name, graph_name, root_input, "running", _now(), json.dumps(metadata or {})),
            )
        return run_id

    def end_run(
        self,
        run_id: str,
        *,
        final_output: str | None = None,
        status: str = "ok",
        latency_ms: int | None = None,
        error: dict[str, Any] | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        cost_usd: float | None = None,
    ) -> None:
        with self.connect() as db:
            row = db.execute("SELECT metadata_json FROM runs WHERE id=?", (run_id,)).fetchone()
            metadata = json.loads(row["metadata_json"]) if row and row["metadata_json"] else {}
            if error:
                metadata["error"] = json_safe(error)
            db.execute(
                """
                UPDATE runs SET final_output=?, status=?, ended_at=?, total_latency_ms=?,
                    total_prompt_tokens=?, total_completion_tokens=?, total_cost_usd=?, metadata_json=?
                WHERE id=?
                """,
                (
                    final_output,
                    status,
                    _now(),
                    latency_ms,
                    prompt_tokens,
                    completion_tokens,
                    cost_usd,
                    json.dumps(metadata),
                    run_id,
                ),
            )

    def start_turn(
        self,
        *,
        run_id: str,
        turn_index: int,
        node_name: str,
        input_messages: list[dict[str, Any]],
    ) -> str:
        turn_id = _id("turn")
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO turns
                (id, run_id, turn_index, node_name, input_messages_json, output_messages_json, status, started_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (turn_id, run_id, turn_index, node_name, json.dumps(input_messages), "[]", "running", _now()),
            )
        return turn_id

    def end_turn(
        self,
        *,
        turn_id: str,
        output_messages: list[dict[str, Any]],
        final_output: str | None = None,
        status: str = "ok",
        latency_ms: int | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as db:
            db.execute(
                """
                UPDATE turns
                SET output_messages_json=?, final_output=?, status=?, ended_at=?, latency_ms=?, error_json=?
                WHERE id=?
                """,
                (
                    json.dumps(output_messages),
                    final_output,
                    status,
                    _now(),
                    latency_ms,
                    json.dumps(error) if error else None,
                    turn_id,
                ),
            )

    def save_model_request(self, *, run_id: str, turn_id: str, request: ProviderRequest) -> str:
        model_call_id = _id("model_call")
        payload = redact(request.model_dump())
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO model_calls
                (id, run_id, turn_id, provider, model, api_shape, endpoint, request_json, status, started_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    model_call_id,
                    run_id,
                    turn_id,
                    request.provider,
                    request.model,
                    request.api_shape,
                    request.endpoint,
                    json.dumps(payload),
                    "running",
                    _now(),
                ),
            )
        return model_call_id

    def save_model_response(
        self,
        *,
        model_call_id: str,
        response: ProviderResponse | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        status = "error" if error else "ok"
        with self.connect() as db:
            db.execute(
                """
                UPDATE model_calls
                SET response_json=?, usage_json=?, status=?, ended_at=?, error_json=?
                WHERE id=?
                """,
                (
                    json.dumps(redact(response.model_dump())) if response else None,
                    json.dumps(response.usage.model_dump()) if response and response.usage else None,
                    status,
                    _now(),
                    json.dumps(redact(error)) if error else None,
                    model_call_id,
                ),
            )

    def start_tool_call(
        self, *, run_id: str, turn_id: str, tool_name: str, args: dict[str, Any]
    ) -> str:
        tool_call_id = _id("tool_call")
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO tool_calls
                (id, run_id, turn_id, tool_name, args_json, status, started_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tool_call_id,
                    run_id,
                    turn_id,
                    tool_name,
                    json.dumps(redact(args)),
                    "running",
                    _now(),
                ),
            )
        return tool_call_id

    def end_tool_call(
        self,
        tool_call_id: str,
        *,
        result: Any = None,
        status: str = "ok",
        error: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as db:
            db.execute(
                """
                UPDATE tool_calls SET result_json=?, status=?, ended_at=?, error_json=?
                WHERE id=?
                """,
                (
                    json.dumps(redact(json_safe(result))),
                    status,
                    _now(),
                    json.dumps(redact(error)) if error else None,
                    tool_call_id,
                ),
            )

    def list_runs(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM runs ORDER BY started_at DESC").fetchall()
        return [dict(row) for row in rows]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def get_turns(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM turns WHERE run_id=? ORDER BY turn_index", (run_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def get_model_call_for_turn(self, run_id: str, turn_index: int) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT model_calls.* FROM model_calls
                JOIN turns ON turns.id = model_calls.turn_id
                WHERE turns.run_id=? AND turns.turn_index=?
                ORDER BY model_calls.started_at LIMIT 1
                """,
                (run_id, turn_index),
            ).fetchone()
        return dict(row) if row else None

    def list_tool_calls(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM tool_calls WHERE run_id=?", (run_id,)).fetchall()
        return [dict(row) for row in rows]

    def list_model_calls(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM model_calls WHERE run_id=?", (run_id,)).fetchall()
        return [dict(row) for row in rows]

    def start_eval_suite_run(self, *, suite_name: str, suite_type: str, agent_name: str, model: str) -> str:
        suite_run_id = _id("suite_run")
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO eval_suite_runs
                (id, suite_name, suite_type, agent_name, model, started_at, status, passed, failed, skipped, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (suite_run_id, suite_name, suite_type, agent_name, model, _now(), "running", 0, 0, 0, "{}"),
            )
        return suite_run_id

    def end_eval_suite_run(
        self,
        suite_run_id: str,
        *,
        passed: int,
        failed: int,
        skipped: int = 0,
        status: str | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        metadata = json.dumps({"error": error} if error else {})
        with self.connect() as db:
            db.execute(
                """
                UPDATE eval_suite_runs
                SET ended_at=?, status=?, passed=?, failed=?, skipped=?, metadata_json=?
                WHERE id=?
                """,
                (
                    _now(),
                    status or ("ok" if failed == 0 else "failed"),
                    passed,
                    failed,
                    skipped,
                    metadata,
                    suite_run_id,
                ),
            )

    def save_eval_case_result(
        self,
        *,
        suite_run_id: str,
        run_id: str,
        suite_name: str,
        case_name: str,
        input: str,
        final_output: str,
        passed: bool,
        checks: list[dict[str, Any]],
        latency_ms: int | None,
        cost_usd: float | None,
        variant: dict[str, Any] | None = None,
    ) -> str:
        result_id = _id("case_result")
        failures = [check for check in checks if not check.get("passed")]
        variant_json = json.dumps(variant or {}, sort_keys=True, separators=(",", ":"))
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO eval_case_results
                (id, suite_run_id, run_id, suite_name, case_name, input, final_output,
                 passed, checks_json, variant_json, failure_json, latency_ms, cost_usd)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result_id,
                    suite_run_id,
                    run_id,
                    suite_name,
                    case_name,
                    input,
                    final_output,
                    1 if passed else 0,
                    json.dumps(checks),
                    variant_json,
                    json.dumps(failures) if failures else None,
                    latency_ms,
                    cost_usd,
                ),
            )
        return result_id

    def list_eval_case_results(self, suite_run_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM eval_case_results WHERE suite_run_id=?", (suite_run_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def get_latest_run_for_agent(self, agent_name: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM runs WHERE agent_name=? ORDER BY rowid DESC LIMIT 1",
                (agent_name,),
            ).fetchone()
        return dict(row) if row else None


def _ensure_columns(
    db: sqlite3.Connection,
    table_columns: dict[str, dict[str, str]],
) -> None:
    for table_name, columns in table_columns.items():
        existing = {
            row["name"]
            for row in db.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        for column_name, definition in columns.items():
            if column_name not in existing:
                db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
