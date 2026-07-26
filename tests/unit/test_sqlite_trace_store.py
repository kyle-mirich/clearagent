import json
import sqlite3

import pytest

from clearagent.providers.base import ProviderRequest, ProviderResponse
from clearagent.storage.redaction import redact
from clearagent.storage.sqlite import SQLiteTraceStore


def test_trace_store_lifecycle_and_model_call_retrieval(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    store = SQLiteTraceStore(db_path)

    run_id = store.start_run(agent_name="support", root_input="Where is A123?")
    turn_id = store.start_turn(
        run_id=run_id,
        turn_index=0,
        node_name="support",
        input_messages=[{"role": "user", "content": "Where is A123?"}],
    )
    request = ProviderRequest(
        provider="openai",
        model="gpt-4.1-mini",
        api_shape="openai_chat_completions",
        endpoint="https://api.openai.com/v1/chat/completions",
        headers_snapshot={"authorization": "Bearer secret"},
        body={"model": "gpt-4.1-mini", "api_key": "secret", "messages": []},
    )
    call_id = store.save_model_request(run_id=run_id, turn_id=turn_id, request=request)
    store.save_model_response(
        model_call_id=call_id,
        response=ProviderResponse.fake_text("shipped"),
    )
    store.end_turn(
        turn_id=turn_id,
        output_messages=[{"role": "assistant", "content": "shipped"}],
        final_output="shipped",
    )
    store.end_run(run_id, final_output="shipped")

    row = store.get_model_call_for_turn(run_id, 0)

    assert row is not None
    assert json.loads(row["request_json"])["headers_snapshot"]["authorization"] == "[REDACTED]"
    assert json.loads(row["request_json"])["body"]["api_key"] == "[REDACTED]"
    assert store.get_run(run_id)["final_output"] == "shipped"
    assert store.get_turns(run_id)[0]["turn_index"] == 0


def test_migrations_are_idempotent(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    SQLiteTraceStore(db_path).initialize()
    SQLiteTraceStore(db_path).initialize()

    assert db_path.exists()


def test_trace_store_upgrades_legacy_schema_with_missing_columns(tmp_path):
    db_path = tmp_path / "legacy-traces.sqlite"
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            CREATE TABLE runs (
              id TEXT PRIMARY KEY,
              agent_name TEXT NOT NULL,
              root_input TEXT NOT NULL,
              final_output TEXT,
              status TEXT NOT NULL,
              started_at TEXT NOT NULL
            );
            CREATE TABLE turns (
              id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              turn_index INTEGER NOT NULL,
              node_name TEXT NOT NULL,
              input_messages_json TEXT NOT NULL,
              output_messages_json TEXT NOT NULL,
              status TEXT NOT NULL,
              started_at TEXT NOT NULL
            );
            PRAGMA user_version = 0;
            """
        )

    store = SQLiteTraceStore(db_path)
    run_id = store.start_run(agent_name="support", root_input="hello", graph_name="graph")
    turn_id = store.start_turn(
        run_id=run_id,
        turn_index=0,
        node_name="support",
        input_messages=[{"role": "user", "content": "hello"}],
    )
    store.end_turn(turn_id=turn_id, output_messages=[], final_output="done")
    store.end_run(run_id, final_output="done", latency_ms=12)

    with store.connect() as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        run = db.execute("SELECT graph_name, metadata_json FROM runs WHERE id=?", (run_id,)).fetchone()
        turn = db.execute("SELECT ended_at, latency_ms FROM turns WHERE id=?", (turn_id,)).fetchone()

    assert version == 2
    assert run["graph_name"] == "graph"
    assert run["metadata_json"] == "{}"
    assert turn["ended_at"] is not None
    assert turn["latency_ms"] is None


def test_trace_store_adds_variant_column_to_legacy_eval_results(tmp_path):
    db_path = tmp_path / "legacy-evals.sqlite"
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            CREATE TABLE eval_case_results (
              id TEXT PRIMARY KEY,
              suite_run_id TEXT NOT NULL,
              run_id TEXT NOT NULL,
              suite_name TEXT NOT NULL,
              case_name TEXT NOT NULL,
              input TEXT NOT NULL,
              final_output TEXT,
              passed INTEGER NOT NULL,
              checks_json TEXT NOT NULL,
              failure_json TEXT,
              latency_ms INTEGER,
              cost_usd REAL
            );
            INSERT INTO eval_case_results
              (id, suite_run_id, run_id, suite_name, case_name, input, final_output,
               passed, checks_json, failure_json, latency_ms, cost_usd)
            VALUES
              ('case_result_legacy', 'suite_run_legacy', 'run_legacy', 'smoke',
               'shipped', 'Where is A123?', 'shipped', 1, '[]', NULL, 1, 0.0);
            PRAGMA user_version = 1;
            """
        )

    store = SQLiteTraceStore(db_path)

    with store.connect() as db:
        row = db.execute(
            "SELECT variant_json FROM eval_case_results WHERE id='case_result_legacy'"
        ).fetchone()
        version = db.execute("PRAGMA user_version").fetchone()[0]

    assert row["variant_json"] == "{}"
    assert version == 2


def test_trace_store_closes_connections_after_context_exit(tmp_path):
    store = SQLiteTraceStore(tmp_path / "traces.sqlite")

    with store.connect() as db:
        db.execute("SELECT 1")

    try:
        db.execute("SELECT 1")
    except sqlite3.ProgrammingError as exc:
        assert "closed" in str(exc)
    else:
        raise AssertionError("connection should be closed")


def test_trace_store_rolls_back_failed_transactions(tmp_path):
    store = SQLiteTraceStore(tmp_path / "traces.sqlite")

    with pytest.raises(RuntimeError, match="abort transaction"):
        with store.connect() as db:
            db.execute(
                """
                INSERT INTO runs
                (id, agent_name, root_input, status, started_at, metadata_json)
                VALUES ('run_rollback', 'agent', 'temporary', 'running', 'now', '{}')
                """
            )
            raise RuntimeError("abort transaction")

    assert store.get_run("run_rollback") is None


def test_end_run_preserves_start_metadata_and_adds_error(tmp_path):
    store = SQLiteTraceStore(tmp_path / "traces.sqlite")
    run_id = store.start_run(
        agent_name="support",
        root_input="hello",
        metadata={"tenant": "alpha"},
    )

    store.end_run(run_id, status="error", error={"type": "RuntimeError", "message": "boom"})

    metadata = json.loads(store.get_run(run_id)["metadata_json"])
    assert metadata["tenant"] == "alpha"
    assert metadata["error"]["message"] == "boom"


def test_redaction_redacts_common_secret_keys():
    value = {
        "authorization": "Bearer x",
        "api_key": "x",
        "token": "x",
        "secret": "x",
        "password": "x",
        "Access_Token": "x",
        "anthropic_api_key": "x",
        "client_secret": "x",
        "Cookie": "session=x",
        "google_api_key": "x",
        "id_token": "x",
        "openai_api_key": "x",
        "private_key": "x",
        "proxy-authorization": "x",
        "refresh_token": "x",
        "set-cookie": "session=x",
        "x-auth-token": "x",
        "nested": [{"x-api-key": "x"}],
    }

    assert redact(value) == {
        "authorization": "[REDACTED]",
        "api_key": "[REDACTED]",
        "token": "[REDACTED]",
        "secret": "[REDACTED]",
        "password": "[REDACTED]",
        "Access_Token": "[REDACTED]",
        "anthropic_api_key": "[REDACTED]",
        "client_secret": "[REDACTED]",
        "Cookie": "[REDACTED]",
        "google_api_key": "[REDACTED]",
        "id_token": "[REDACTED]",
        "openai_api_key": "[REDACTED]",
        "private_key": "[REDACTED]",
        "proxy-authorization": "[REDACTED]",
        "refresh_token": "[REDACTED]",
        "set-cookie": "[REDACTED]",
        "x-auth-token": "[REDACTED]",
        "nested": [{"x-api-key": "[REDACTED]"}],
    }
