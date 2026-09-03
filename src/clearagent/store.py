from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import json
import sqlite3
import time
from typing import Any, Iterator
from uuid import uuid4

from clearagent.models import EventRecord, FeedbackKind, FeedbackRecord, ProjectRecord, RunRecord


POSTGRES_RUN_ADMISSION_LOCK_ID = int.from_bytes(b"CLAGRUNS", byteorder="big")


class ActiveRunCapacityError(RuntimeError):
    def __init__(self, scope: str):
        super().__init__(f"Active run capacity reached for {scope}")
        self.scope = scope


class RunRateLimitError(RuntimeError):
    def __init__(self, scope: str, retry_after: int):
        super().__init__(f"Run rate limit reached for {scope}")
        self.scope = scope
        self.retry_after = retry_after


SQLITE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  name TEXT NOT NULL,
  goal TEXT NOT NULL,
  status TEXT NOT NULL,
  promoted_agent_version_id TEXT,
  settings_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_projects_owner_id ON projects(owner_id);

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  owner_id TEXT NOT NULL,
  status TEXT NOT NULL,
  stage TEXT NOT NULL,
  progress REAL NOT NULL,
  budget_profile TEXT NOT NULL,
  seed INTEGER NOT NULL,
  dataset_size INTEGER,
  run_config_json TEXT NOT NULL,
  task_spec_json TEXT,
  dataset_json TEXT,
  best_agent_version_id TEXT,
  baseline_validation_score REAL,
  best_validation_score REAL,
  baseline_test_score REAL,
  optimized_test_score REAL,
  promotion_decision_json TEXT,
  error_json TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  completed_at TEXT,
  FOREIGN KEY(project_id) REFERENCES projects(id)
);
CREATE INDEX IF NOT EXISTS idx_runs_owner_id ON runs(owner_id);
CREATE INDEX IF NOT EXISTS idx_runs_project_id ON runs(project_id);

CREATE TABLE IF NOT EXISTS run_idempotency (
  owner_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  key TEXT NOT NULL,
  run_id TEXT NOT NULL,
  PRIMARY KEY(owner_id, project_id, key)
);

CREATE TABLE IF NOT EXISTS agent_versions (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  version_number INTEGER NOT NULL,
  kind TEXT NOT NULL,
  instruction_text TEXT NOT NULL,
  state_json TEXT NOT NULL,
  validation_metrics_json TEXT NOT NULL,
  test_metrics_json TEXT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_versions_project_id ON agent_versions(project_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_versions_project_version_number
  ON agent_versions(project_id, version_number);

CREATE TABLE IF NOT EXISTS run_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  stage TEXT NOT NULL,
  message TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(run_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_events_run_id ON run_events(run_id, sequence);

CREATE TABLE IF NOT EXISTS agent_feedback (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  version_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  input TEXT NOT NULL,
  feedback TEXT NOT NULL,
  corrected_output_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(project_id) REFERENCES projects(id),
  FOREIGN KEY(version_id) REFERENCES agent_versions(id)
);
CREATE INDEX IF NOT EXISTS idx_feedback_project_version ON agent_feedback(project_id, version_id, created_at);

CREATE TABLE IF NOT EXISTS worker_leases (
  name TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  expires_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS api_rate_limits (
  rate_key TEXT NOT NULL,
  bucket TEXT NOT NULL,
  window_start INTEGER NOT NULL,
  request_count INTEGER NOT NULL,
  PRIMARY KEY(rate_key, bucket, window_start)
);
CREATE INDEX IF NOT EXISTS idx_rate_limits_window ON api_rate_limits(window_start);
"""

POSTGRES_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  name TEXT NOT NULL,
  goal TEXT NOT NULL,
  status TEXT NOT NULL,
  promoted_agent_version_id TEXT,
  settings_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_projects_owner_id ON projects(owner_id);

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_id TEXT NOT NULL,
  status TEXT NOT NULL,
  stage TEXT NOT NULL,
  progress DOUBLE PRECISION NOT NULL,
  budget_profile TEXT NOT NULL,
  seed INTEGER NOT NULL,
  dataset_size INTEGER,
  run_config_json TEXT NOT NULL,
  task_spec_json TEXT,
  dataset_json TEXT,
  best_agent_version_id TEXT,
  baseline_validation_score DOUBLE PRECISION,
  best_validation_score DOUBLE PRECISION,
  baseline_test_score DOUBLE PRECISION,
  optimized_test_score DOUBLE PRECISION,
  promotion_decision_json TEXT,
  error_json TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_owner_id ON runs(owner_id);
CREATE INDEX IF NOT EXISTS idx_runs_project_id ON runs(project_id);

CREATE TABLE IF NOT EXISTS run_idempotency (
  owner_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  key TEXT NOT NULL,
  run_id TEXT NOT NULL,
  PRIMARY KEY(owner_id, project_id, key)
);

CREATE TABLE IF NOT EXISTS agent_versions (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  version_number INTEGER NOT NULL,
  kind TEXT NOT NULL,
  instruction_text TEXT NOT NULL,
  state_json TEXT NOT NULL,
  validation_metrics_json TEXT NOT NULL,
  test_metrics_json TEXT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_versions_project_id ON agent_versions(project_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_versions_project_version_number
  ON agent_versions(project_id, version_number);

CREATE TABLE IF NOT EXISTS run_events (
  id BIGSERIAL PRIMARY KEY,
  run_id TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  stage TEXT NOT NULL,
  message TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(run_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_events_run_id ON run_events(run_id, sequence);

CREATE TABLE IF NOT EXISTS agent_feedback (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  version_id TEXT NOT NULL REFERENCES agent_versions(id),
  kind TEXT NOT NULL,
  input TEXT NOT NULL,
  feedback TEXT NOT NULL,
  corrected_output_json TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_project_version ON agent_feedback(project_id, version_id, created_at);

CREATE TABLE IF NOT EXISTS worker_leases (
  name TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  expires_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_rate_limits (
  rate_key TEXT NOT NULL,
  bucket TEXT NOT NULL,
  window_start BIGINT NOT NULL,
  request_count INTEGER NOT NULL,
  PRIMARY KEY(rate_key, bucket, window_start)
);
CREATE INDEX IF NOT EXISTS idx_rate_limits_window ON api_rate_limits(window_start);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def _loads(value: str | None, fallback: Any) -> Any:
    return json.loads(value) if value else fallback


def _sqlite_path(database_url: str) -> Path:
    if database_url.startswith("sqlite:///"):
        return Path(database_url.removeprefix("sqlite:///"))
    if database_url.startswith("sqlite://"):
        return Path(database_url.removeprefix("sqlite://"))
    raise ValueError("The SQLite adapter requires a sqlite:/// DATABASE_URL.")


class _Database:
    def __init__(self, connection: Any, *, dialect: str):
        self.connection = connection
        self.dialect = dialect

    def execute(self, sql: str, params: Any = None) -> Any:
        return self.connection.execute(self._sql(sql), params or ())

    def executescript(self, sql: str) -> None:
        statements = (statement.strip() for statement in sql.split(";"))
        if self.dialect == "sqlite":
            for statement in statements:
                if statement:
                    self.connection.execute(statement)
            return
        with self.connection.cursor() as cursor:
            for statement in statements:
                if statement:
                    cursor.execute(statement)

    def _sql(self, sql: str) -> str:
        if self.dialect == "postgres":
            return sql.replace("?", "%s")
        return sql


class Store:
    def __init__(self, database_url: str, *, auto_migrate: bool = True):
        self.database_url = database_url
        self.dialect = (
            "postgres" if database_url.startswith(("postgres://", "postgresql://")) else "sqlite"
        )
        self.path = _sqlite_path(database_url) if self.dialect == "sqlite" else None
        if auto_migrate:
            self.initialize()

    @contextmanager
    def connect(self) -> Iterator[_Database]:
        if self.dialect == "sqlite":
            assert self.path is not None
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection: Any = sqlite3.connect(self.path)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            # Concurrent API requests and pipeline event writers share this
            # database; without a busy timeout they fail fast with
            # "database is locked" instead of waiting their turn.
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA journal_mode = WAL")
        else:
            from psycopg import connect
            from psycopg.rows import dict_row

            connection = connect(
                self.database_url,
                row_factory=dict_row,
                connect_timeout=5,
            )
            # Configure the timeout after connecting. Pooled providers such as
            # Neon reject arbitrary PostgreSQL startup ``options``, while a
            # transaction-local setting works with both pooled and direct
            # endpoints and is reset automatically after this unit of work.
            connection.execute("SET LOCAL statement_timeout = 10000")
        try:
            yield _Database(connection, dialect=self.dialect)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        schema = SQLITE_SCHEMA_SQL if self.dialect == "sqlite" else POSTGRES_SCHEMA_SQL
        with self.connect() as db:
            db.executescript(schema)

    def ping(self) -> None:
        with self.connect() as db:
            db.execute("SELECT 1")

    def cleanup_expired(self, *, ttl_seconds: int) -> dict[str, int]:
        if ttl_seconds <= 0:
            return {
                "projects": 0,
                "runs": 0,
                "agent_versions": 0,
                "agent_feedback": 0,
                "run_events": 0,
                "run_idempotency": 0,
                "api_rate_limits": 0,
            }
        cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - ttl_seconds))
        with self.connect() as db:
            event_cursor = db.execute(
                """
                DELETE FROM run_events
                WHERE run_id IN (
                  SELECT id FROM runs
                  WHERE status IN ('completed', 'failed', 'canceled')
                    AND completed_at IS NOT NULL AND completed_at < ?
                )
                """,
                (cutoff,),
            )
            feedback_cursor = db.execute(
                """
                DELETE FROM agent_feedback
                WHERE version_id IN (
                  SELECT agent_versions.id
                  FROM agent_versions
                  JOIN runs ON runs.id = agent_versions.run_id
                  WHERE runs.status IN ('completed', 'failed', 'canceled')
                    AND runs.completed_at IS NOT NULL AND runs.completed_at < ?
                )
                """,
                (cutoff,),
            )
            version_cursor = db.execute(
                """
                DELETE FROM agent_versions
                WHERE run_id IN (
                  SELECT id FROM runs
                  WHERE status IN ('completed', 'failed', 'canceled')
                    AND completed_at IS NOT NULL AND completed_at < ?
                )
                """,
                (cutoff,),
            )
            idempotency_cursor = db.execute(
                """
                DELETE FROM run_idempotency
                WHERE run_id IN (
                  SELECT id FROM runs
                  WHERE status IN ('completed', 'failed', 'canceled')
                    AND completed_at IS NOT NULL AND completed_at < ?
                )
                """,
                (cutoff,),
            )
            run_cursor = db.execute(
                """
                DELETE FROM runs
                WHERE status IN ('completed', 'failed', 'canceled')
                  AND completed_at IS NOT NULL AND completed_at < ?
                """,
                (cutoff,),
            )
            project_cursor = db.execute(
                """
                DELETE FROM projects
                WHERE created_at < ?
                  AND NOT EXISTS (SELECT 1 FROM runs WHERE runs.project_id = projects.id)
                """,
                (cutoff,),
            )
            rate_limit_cursor = db.execute(
                "DELETE FROM api_rate_limits WHERE window_start < ?",
                (int(time.time()) - max(ttl_seconds, 86_400),),
            )
        return {
            "projects": max(project_cursor.rowcount, 0),
            "runs": max(run_cursor.rowcount, 0),
            "agent_versions": max(version_cursor.rowcount, 0),
            "agent_feedback": max(feedback_cursor.rowcount, 0),
            "run_events": max(event_cursor.rowcount, 0),
            "run_idempotency": max(idempotency_cursor.rowcount, 0),
            "api_rate_limits": max(rate_limit_cursor.rowcount, 0),
        }

    def acquire_worker_lease(
        self,
        *,
        name: str,
        owner_id: str,
        ttl_seconds: int,
    ) -> bool:
        now = int(time.time())
        expires_at = now + ttl_seconds
        with self.connect() as db:
            cursor = db.execute(
                """
                INSERT INTO worker_leases (name, owner_id, expires_at)
                VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                  owner_id=excluded.owner_id,
                  expires_at=excluded.expires_at
                WHERE worker_leases.owner_id=excluded.owner_id
                   OR worker_leases.expires_at < ?
                """,
                (name, owner_id, expires_at, now),
            )
        return cursor.rowcount == 1

    def release_worker_lease(self, *, name: str, owner_id: str) -> None:
        with self.connect() as db:
            db.execute(
                "DELETE FROM worker_leases WHERE name=? AND owner_id=?",
                (name, owner_id),
            )

    def fail_interrupted_runs(self) -> list[str]:
        now = _now()
        with self.connect() as db:
            rows = db.execute("SELECT id FROM runs WHERE status='running'").fetchall()
            run_ids = [str(row["id"]) for row in rows]
            if run_ids:
                db.execute(
                    """
                    UPDATE runs
                    SET status='failed', stage='failed', completed_at=?,
                        error_json=?
                    WHERE status='running'
                    """,
                    (
                        now,
                        _json(
                            {
                                "type": "WorkerInterrupted",
                                "message": "The build worker restarted before this run finished. Retry the build.",
                            }
                        ),
                    ),
                )
        for run_id in run_ids:
            self.add_event(
                run_id=run_id,
                event_type="run_failed",
                stage="failed",
                message="Build worker restarted before the run finished. The build can be retried safely.",
            )
        return run_ids

    def claim_next_queued_run(self) -> RunRecord | None:
        now = _now()
        with self.connect() as db:
            row = db.execute(
                "SELECT id FROM runs WHERE status='queued' ORDER BY created_at, id LIMIT 1"
            ).fetchone()
            if not row:
                return None
            cursor = db.execute(
                """
                UPDATE runs
                SET status='running', stage='planning', progress=0.01, started_at=?
                WHERE id=? AND status='queued'
                """,
                (now, row["id"]),
            )
            if cursor.rowcount != 1:
                return None
            run_id = str(row["id"])
        return self.get_run(run_id)

    def count_active_runs(self, *, owner_id: str | None = None) -> int:
        with self.connect() as db:
            if owner_id is None:
                row = db.execute(
                    "SELECT COUNT(*) AS count FROM runs WHERE status IN ('queued', 'running')"
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT COUNT(*) AS count FROM runs WHERE owner_id=? AND status IN ('queued', 'running')",
                    (owner_id,),
                ).fetchone()
        return int(row["count"])

    def consume_rate_limit(
        self,
        *,
        rate_key: str,
        bucket: str,
        limit: int,
        window_seconds: int,
    ) -> tuple[bool, int]:
        now = int(time.time())
        with self.connect() as db:
            return _consume_rate_limit(
                db,
                rate_key=rate_key,
                bucket=bucket,
                limit=limit,
                window_seconds=window_seconds,
                now=now,
            )

    def run_stream_snapshot(
        self,
        run_id: str,
        *,
        after: int,
        owner_id: str,
    ) -> tuple[list[EventRecord], RunRecord]:
        with self.connect() as db:
            run_row = db.execute(
                "SELECT * FROM runs WHERE id=? AND owner_id=?",
                (run_id, owner_id),
            ).fetchone()
            if not run_row:
                raise KeyError(run_id)
            event_rows = db.execute(
                """
                SELECT * FROM run_events
                WHERE run_id=? AND sequence>? ORDER BY sequence
                """,
                (run_id, after),
            ).fetchall()
        events = [
            EventRecord(
                sequence=row["sequence"],
                type=row["event_type"],
                stage=row["stage"],
                message=row["message"],
                timestamp=row["created_at"],
                payload=_loads(row["payload_json"], {}),
            )
            for row in event_rows
        ]
        return events, _run_record(run_row)

    def create_project(
        self, *, owner_id: str, goal: str, name: str | None, settings: dict[str, Any]
    ) -> ProjectRecord:
        now = _now()
        project_id = _id("proj")
        display_name = name or _name_from_goal(goal)
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO projects
                (id, owner_id, name, goal, status, settings_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (project_id, owner_id, display_name, goal, "active", _json(settings), now, now),
            )
        return self.get_project(project_id, owner_id=owner_id)

    def get_project(self, project_id: str, *, owner_id: str) -> ProjectRecord:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM projects WHERE id=? AND owner_id=?", (project_id, owner_id)
            ).fetchone()
        if not row:
            raise KeyError(project_id)
        return _project_record(row)

    def list_projects(self, *, owner_id: str) -> list[ProjectRecord]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM projects WHERE owner_id=? ORDER BY updated_at DESC, id",
                (owner_id,),
            ).fetchall()
        return [_project_record(row) for row in rows]

    def create_run(
        self,
        *,
        owner_id: str,
        project_id: str,
        idempotency_key: str,
        budget_profile: str,
        seed: int,
        dataset_size: int | None = None,
        run_config: dict[str, Any] | None = None,
        owner_active_limit: int | None = None,
        global_active_limit: int | None = None,
        rate_limits: tuple[tuple[str, str, int, int], ...] = (),
    ) -> tuple[RunRecord, bool]:
        with self.connect() as db:
            if self.dialect == "sqlite":
                db.execute("BEGIN IMMEDIATE")
            elif owner_active_limit is not None or global_active_limit is not None or rate_limits:
                db.execute(
                    "SELECT pg_advisory_xact_lock(?)",
                    (POSTGRES_RUN_ADMISSION_LOCK_ID,),
                )
            project_lock = " FOR UPDATE" if self.dialect == "postgres" else ""
            project = db.execute(
                f"SELECT id FROM projects WHERE id=? AND owner_id=?{project_lock}",
                (project_id, owner_id),
            ).fetchone()
            if not project:
                raise KeyError(project_id)
            existing = db.execute(
                """
                SELECT runs.*
                FROM run_idempotency
                JOIN runs ON runs.id=run_idempotency.run_id
                WHERE run_idempotency.owner_id=?
                  AND run_idempotency.project_id=?
                  AND run_idempotency.key=?
                """,
                (owner_id, project_id, idempotency_key),
            ).fetchone()
            if existing:
                return _run_record(existing), False
            if owner_active_limit is not None:
                owner_active = db.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM runs
                    WHERE owner_id=? AND status IN ('queued', 'running')
                    """,
                    (owner_id,),
                ).fetchone()
                if int(owner_active["count"]) >= owner_active_limit:
                    raise ActiveRunCapacityError("owner")
            if global_active_limit is not None:
                global_active = db.execute(
                    "SELECT COUNT(*) AS count FROM runs WHERE status IN ('queued', 'running')"
                ).fetchone()
                if int(global_active["count"]) >= global_active_limit:
                    raise ActiveRunCapacityError("global")
            now_epoch = int(time.time())
            for rate_key, bucket, limit, window_seconds in rate_limits:
                allowed, retry_after = _consume_rate_limit(
                    db,
                    rate_key=rate_key,
                    bucket=bucket,
                    limit=limit,
                    window_seconds=window_seconds,
                    now=now_epoch,
                )
                if not allowed:
                    scope = "global" if rate_key == "global" else "session"
                    raise RunRateLimitError(scope, retry_after)
            run_id = _id("run")
            now = _now()
            db.execute(
                """
                INSERT INTO runs
                (id, project_id, owner_id, status, stage, progress, budget_profile, seed, dataset_size, run_config_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    project_id,
                    owner_id,
                    "queued",
                    "queued",
                    0.0,
                    budget_profile,
                    seed,
                    dataset_size,
                    _json(run_config or {}),
                    now,
                ),
            )
            db.execute(
                """
                INSERT INTO run_idempotency (owner_id, project_id, key, run_id)
                VALUES (?, ?, ?, ?)
                """,
                (owner_id, project_id, idempotency_key, run_id),
            )
            # Publish the first event in the same transaction as the queue row.
            # Otherwise a durable worker can claim the committed run before the
            # request thread records ``run_queued``, reversing event order or
            # racing both writers for sequence 1.
            db.execute(
                """
                INSERT INTO run_events
                (run_id, sequence, event_type, stage, message, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    1,
                    "run_queued",
                    "queued",
                    "Run queued.",
                    _json({"dataset_size": dataset_size}),
                    now,
                ),
            )
        return self.get_run(run_id, owner_id=owner_id), True

    def get_idempotent_run(
        self,
        *,
        owner_id: str,
        project_id: str,
        idempotency_key: str,
    ) -> RunRecord | None:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT runs.*
                FROM run_idempotency
                JOIN runs ON runs.id=run_idempotency.run_id
                WHERE run_idempotency.owner_id=?
                  AND run_idempotency.project_id=?
                  AND run_idempotency.key=?
                """,
                (owner_id, project_id, idempotency_key),
            ).fetchone()
        return _run_record(row) if row else None

    def get_run(self, run_id: str, *, owner_id: str | None = None) -> RunRecord:
        with self.connect() as db:
            if owner_id:
                row = db.execute(
                    "SELECT * FROM runs WHERE id=? AND owner_id=?", (run_id, owner_id)
                ).fetchone()
            else:
                row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise KeyError(run_id)
        return _run_record(row)

    def update_run(self, run_id: str, **fields: Any) -> RunRecord:
        if not fields:
            return self.get_run(run_id)
        column_map = {
            "status": "status",
            "stage": "stage",
            "progress": "progress",
            "task_spec": "task_spec_json",
            "dataset": "dataset_json",
            "best_agent_version_id": "best_agent_version_id",
            "baseline_validation_score": "baseline_validation_score",
            "best_validation_score": "best_validation_score",
            "baseline_test_score": "baseline_test_score",
            "optimized_test_score": "optimized_test_score",
            "promotion_decision": "promotion_decision_json",
            "error": "error_json",
            "started_at": "started_at",
            "completed_at": "completed_at",
        }
        assignments = []
        values = []
        for name, value in fields.items():
            column = column_map[name]
            assignments.append(f"{column}=?")
            if name in {"task_spec", "dataset", "promotion_decision", "error"}:
                values.append(_json(value) if value is not None else None)
            else:
                values.append(value)
        values.append(run_id)
        with self.connect() as db:
            db.execute(f"UPDATE runs SET {', '.join(assignments)} WHERE id=?", values)
        return self.get_run(run_id)

    def add_event(
        self,
        *,
        run_id: str,
        event_type: str,
        stage: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> EventRecord:
        with self.connect() as db:
            if self.dialect == "sqlite":
                db.execute("BEGIN IMMEDIATE")
                run = db.execute("SELECT id FROM runs WHERE id=?", (run_id,)).fetchone()
            else:
                # The run row is a stable per-run sequencing lock. PostgreSQL
                # event writers for different runs remain fully concurrent.
                run = db.execute(
                    "SELECT id FROM runs WHERE id=? FOR UPDATE",
                    (run_id,),
                ).fetchone()
            if not run:
                raise KeyError(run_id)
            row = db.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM run_events WHERE run_id=?",
                (run_id,),
            ).fetchone()
            sequence = int(row["next_sequence"])
            now = _now()
            db.execute(
                """
                INSERT INTO run_events
                (run_id, sequence, event_type, stage, message, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, sequence, event_type, stage, message, _json(payload or {}), now),
            )
        return EventRecord(
            sequence=sequence,
            type=event_type,
            stage=stage,
            message=message,
            timestamp=now,
            payload=payload or {},
        )

    def list_events(self, run_id: str, *, after: int = 0) -> list[EventRecord]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT * FROM run_events
                WHERE run_id=? AND sequence>? ORDER BY sequence
                """,
                (run_id, after),
            ).fetchall()
        return [
            EventRecord(
                sequence=row["sequence"],
                type=row["event_type"],
                stage=row["stage"],
                message=row["message"],
                timestamp=row["created_at"],
                payload=_loads(row["payload_json"], {}),
            )
            for row in rows
        ]

    def create_agent_version(
        self,
        *,
        project_id: str,
        run_id: str,
        version_number: int | None = None,
        kind: str,
        instruction_text: str,
        state: dict[str, Any],
        validation_metrics: dict[str, Any],
        test_metrics: dict[str, Any] | None = None,
        status: str = "candidate",
    ) -> str:
        version_id = _id("ver")
        with self.connect() as db:
            if self.dialect == "sqlite":
                db.execute("BEGIN IMMEDIATE")
            project_lock = " FOR UPDATE" if self.dialect == "postgres" else ""
            project = db.execute(
                f"SELECT id FROM projects WHERE id=?{project_lock}",
                (project_id,),
            ).fetchone()
            if not project:
                raise KeyError(project_id)
            resolved_version_number = version_number
            if resolved_version_number is None:
                next_number = db.execute(
                    """
                    SELECT COALESCE(MAX(version_number), -1) + 1 AS version_number
                    FROM agent_versions
                    WHERE project_id=?
                    """,
                    (project_id,),
                ).fetchone()
                resolved_version_number = int(next_number["version_number"])
            db.execute(
                """
                INSERT INTO agent_versions
                (id, project_id, run_id, version_number, kind, instruction_text, state_json,
                 validation_metrics_json, test_metrics_json, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    project_id,
                    run_id,
                    resolved_version_number,
                    kind,
                    instruction_text,
                    _json(state),
                    _json(validation_metrics),
                    _json(test_metrics) if test_metrics else None,
                    status,
                    _now(),
                ),
            )
        return version_id

    def promote_version(self, *, project_id: str, owner_id: str, version_id: str) -> None:
        now = _now()
        with self.connect() as db:
            db.execute(
                "UPDATE agent_versions SET status='candidate' WHERE project_id=? AND status='promoted'",
                (project_id,),
            )
            db.execute(
                """
                UPDATE projects
                SET promoted_agent_version_id=?, updated_at=?
                WHERE id=? AND owner_id=?
                """,
                (version_id, now, project_id, owner_id),
            )
            db.execute("UPDATE agent_versions SET status='promoted' WHERE id=?", (version_id,))

    def update_agent_version_test_metrics(
        self,
        version_id: str,
        test_metrics: dict[str, Any],
    ) -> None:
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE agent_versions SET test_metrics_json=? WHERE id=?",
                (_json(test_metrics), version_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Agent version not found: {version_id}")

    def get_agent_version(self, *, version_id: str, owner_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT agent_versions.*
                FROM agent_versions
                JOIN projects ON projects.id = agent_versions.project_id
                WHERE agent_versions.id=? AND projects.owner_id=?
                """,
                (version_id, owner_id),
            ).fetchone()
        return _version_dict(row) if row else None

    def list_agent_versions(self, *, run_id: str, owner_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT agent_versions.*
                FROM agent_versions
                JOIN projects ON projects.id = agent_versions.project_id
                WHERE agent_versions.run_id=? AND projects.owner_id=?
                ORDER BY agent_versions.version_number
                """,
                (run_id, owner_id),
            ).fetchall()
        return [_version_dict(row) for row in rows]

    def get_promoted_version(self, *, project_id: str, owner_id: str) -> dict[str, Any] | None:
        project = self.get_project(project_id, owner_id=owner_id)
        if not project.promoted_agent_version_id:
            return None
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM agent_versions WHERE id=?", (project.promoted_agent_version_id,)
            ).fetchone()
        return _version_dict(row) if row else None

    def add_feedback(
        self,
        *,
        project_id: str,
        owner_id: str,
        version_id: str,
        kind: FeedbackKind,
        input: str,
        feedback: str,
        corrected_output: dict[str, Any] | None,
    ) -> FeedbackRecord:
        self.get_project(project_id, owner_id=owner_id)
        version = self.get_agent_version(version_id=version_id, owner_id=owner_id)
        if version is None or version["project_id"] != project_id:
            raise KeyError(version_id)
        feedback_id = _id("feedback")
        created_at = _now()
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO agent_feedback
                (id, project_id, version_id, kind, input, feedback, corrected_output_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    feedback_id,
                    project_id,
                    version_id,
                    kind,
                    input,
                    feedback,
                    _json(corrected_output) if corrected_output is not None else None,
                    created_at,
                ),
            )
        return FeedbackRecord(
            id=feedback_id,
            project_id=project_id,
            version_id=version_id,
            kind=kind,
            input=input,
            feedback=feedback,
            corrected_output=corrected_output,
            created_at=created_at,
        )

    def list_feedback(self, *, project_id: str, owner_id: str) -> list[FeedbackRecord]:
        self.get_project(project_id, owner_id=owner_id)
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM agent_feedback WHERE project_id=? ORDER BY created_at, id",
                (project_id,),
            ).fetchall()
        return [
            FeedbackRecord(
                id=row["id"],
                project_id=row["project_id"],
                version_id=row["version_id"],
                kind=row["kind"],
                input=row["input"],
                feedback=row["feedback"],
                corrected_output=_loads(row["corrected_output_json"], None),
                created_at=row["created_at"],
            )
            for row in rows
        ]


def _consume_rate_limit(
    db: _Database,
    *,
    rate_key: str,
    bucket: str,
    limit: int,
    window_seconds: int,
    now: int,
) -> tuple[bool, int]:
    window_start = now - (now % window_seconds)
    row = db.execute(
        """
        INSERT INTO api_rate_limits (rate_key, bucket, window_start, request_count)
        VALUES (?, ?, ?, 1)
        ON CONFLICT(rate_key, bucket, window_start)
        DO UPDATE SET request_count=api_rate_limits.request_count + 1
        RETURNING request_count
        """,
        (rate_key, bucket, window_start),
    ).fetchone()
    count = int(row["request_count"])
    return count <= limit, window_start + window_seconds - now


def _project_record(row: sqlite3.Row) -> ProjectRecord:
    return ProjectRecord(
        id=row["id"],
        owner_id=row["owner_id"],
        name=row["name"],
        goal=row["goal"],
        status=row["status"],
        settings=_loads(row["settings_json"], {}),
        promoted_agent_version_id=row["promoted_agent_version_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _run_record(row: sqlite3.Row) -> RunRecord:
    return RunRecord(
        id=row["id"],
        project_id=row["project_id"],
        owner_id=row["owner_id"],
        status=row["status"],
        stage=row["stage"],
        progress=row["progress"],
        budget_profile=row["budget_profile"],
        seed=row["seed"],
        dataset_size=row["dataset_size"],
        run_config=_loads(row["run_config_json"], {}),
        task_spec=_loads(row["task_spec_json"], None),
        dataset=_loads(row["dataset_json"], None),
        best_agent_version_id=row["best_agent_version_id"],
        baseline_validation_score=row["baseline_validation_score"],
        best_validation_score=row["best_validation_score"],
        baseline_test_score=row["baseline_test_score"],
        optimized_test_score=row["optimized_test_score"],
        promotion_decision=_loads(row["promotion_decision_json"], None),
        error=_loads(row["error_json"], None),
        created_at=row["created_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


def _version_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "run_id": row["run_id"],
        "version_number": row["version_number"],
        "kind": row["kind"],
        "instruction_text": row["instruction_text"],
        "state": _loads(row["state_json"], {}),
        "validation_metrics": _loads(row["validation_metrics_json"], {}),
        "test_metrics": _loads(row["test_metrics_json"], None),
        "status": row["status"],
        "created_at": row["created_at"],
    }


def _name_from_goal(goal: str) -> str:
    words = " ".join(goal.split()).split(" ")[:6]
    return " ".join(words).rstrip(".") or "Untitled project"
