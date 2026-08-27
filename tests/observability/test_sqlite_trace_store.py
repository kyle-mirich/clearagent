import json
import sqlite3

from clearagent.runtime.providers.base import ProviderRequest, ProviderResponse
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
    persisted = json.loads(row["request_json"])
    # Credential material must never be persisted: headers are dropped in
    # favor of a boolean marker (replay re-authenticates in memory).
    assert "headers_snapshot" not in persisted
    assert persisted["has_auth"] is True
    assert persisted["body"]["api_key"] == "[REDACTED]"
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

    assert version == 1
    assert run["graph_name"] == "graph"
    assert run["metadata_json"] == "{}"
    assert turn["ended_at"] is not None
    assert turn["latency_ms"] is None


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


def test_redaction_redacts_common_secret_keys():
    value = {
        "authorization": "Bearer x",
        "api_key": "x",
        "token": "x",
        "secret": "x",
        "password": "x",
        "nested": [{"x-api-key": "x"}],
    }

    assert redact(value) == {
        "authorization": "[REDACTED]",
        "api_key": "[REDACTED]",
        "token": "[REDACTED]",
        "secret": "[REDACTED]",
        "password": "[REDACTED]",
        "nested": [{"x-api-key": "[REDACTED]"}],
    }


def test_redaction_normalizes_key_variants_and_google_headers():
    value = {
        "X-Goog-Api-Key": "raw-google-key",
        "API-KEY": "x",
        "apiKey": "x",
        "access_token": "x",
        "client_secret": "x",
    }

    assert set(redact(value).values()) == {"[REDACTED]"}


def test_redaction_scrubs_secret_shaped_values_in_content():
    content = (
        "my key is sk-proj-abcdefghij0123456789 please keep it safe "
        "and also AIzaSyA1234567890abcdefghijklmnopqrstuv plus ghp_abcdef0123456789ABCDEFGHIJ"
    )

    assert "sk-proj-" not in redact(content)
    assert "AIza" not in redact(content)
    assert "ghp_" not in redact(content)
    assert "[REDACTED]" in redact(content)


def test_trace_store_redacts_run_and_turn_content(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    store = SQLiteTraceStore(db_path)

    run_id = store.start_run(
        agent_name="support",
        root_input="use sk-proj-abcdefghij0123456789 as the key",
    )
    turn_id = store.start_turn(
        run_id=run_id,
        turn_index=0,
        node_name="support",
        input_messages=[{"role": "user", "content": "key: sk-proj-abcdefghij0123456789"}],
    )
    store.end_turn(
        turn_id=turn_id,
        output_messages=[{"role": "assistant", "content": "stored sk-proj-abcdefghij0123456789"}],
        final_output="done",
    )
    store.end_turn(turn_id=turn_id, output_messages=[], final_output=None)

    assert "sk-proj-" not in store.get_run(run_id)["root_input"]
    turn_row = store.get_turns(run_id)[0]
    assert "sk-proj-" not in turn_row["input_messages_json"]
    assert "sk-proj-" not in turn_row["output_messages_json"]


def test_trace_lifecycle_survives_broken_store(tmp_path, caplog):
    class BrokenStore:
        def start_run(self, **kwargs):
            raise sqlite3.OperationalError("database is locked")

        def save_model_response(self, **kwargs):
            raise sqlite3.OperationalError("database is locked")

        def end_turn(self, **kwargs):
            raise sqlite3.OperationalError("database is locked")

        def end_run(self, *args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

        def end_tool_call(self, *args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

    from clearagent.trace_lifecycle import TraceLifecycle

    lifecycle = TraceLifecycle(BrokenStore(), "run_1", own_run=True, run_started=0.0)
    with caplog.at_level("WARNING"):
        lifecycle.save_model_response("call_1", error={"type": "X", "message": "x"})
        lifecycle.end_turn("turn_1", output_messages=[])
        lifecycle.end_run(final_output="x")
        error = lifecycle.record_model_error(
            model_call_id="call_1",
            turn_id="turn_1",
            messages=[],
            turn_started=0.0,
            exc=ValueError("the real error"),
        )

    assert error == {"type": "ValueError", "message": "the real error"}
    assert "Trace write" in caplog.text
