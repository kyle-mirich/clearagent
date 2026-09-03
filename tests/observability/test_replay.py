import pytest

from clearagent.create import create_agent
from clearagent.runtime.providers.base import FakeProvider, ProviderError, ProviderResponse
from clearagent.replay import diff_model_call, replay_model_call
from clearagent.storage.sqlite import SQLiteTraceStore


def test_replay_model_call_reruns_stored_request_with_provider(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    first_provider = FakeProvider([ProviderResponse.fake_text("old answer")])
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=first_provider,
        trace_db_path=db_path,
    )
    result = agent.run("hello")
    replay_provider = FakeProvider([ProviderResponse.fake_text("new answer")])

    replayed = replay_model_call(db_path, result.run_id, turn=0, provider=replay_provider)

    assert replayed.output_text == "new answer"
    assert replay_provider.completed_requests[0].body["messages"][-1]["content"] == "hello"


def test_diff_model_call_reports_output_and_usage_changes(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider([ProviderResponse.fake_text("old answer")]),
        trace_db_path=db_path,
    )
    result = agent.run("hello")

    diff = diff_model_call(
        db_path,
        result.run_id,
        turn=0,
        provider=FakeProvider([ProviderResponse.fake_text("new answer")]),
    )

    assert diff.changed is True
    assert diff.before_output == "old answer"
    assert diff.after_output == "new answer"


def test_diff_model_call_rejects_missing_stored_response(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider([ProviderError("mock failure")]),
        trace_db_path=db_path,
    )

    with pytest.raises(ProviderError):
        agent.run("hello")

    run_id = SQLiteTraceStore(db_path).list_runs()[0]["id"]
    with pytest.raises(ValueError, match="Missing stored model response"):
        diff_model_call(
            db_path,
            run_id,
            turn=0,
            provider=FakeProvider([ProviderResponse.fake_text("new answer")]),
        )


def test_diff_model_call_rejects_malformed_stored_response(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider([ProviderResponse.fake_text("old answer")]),
        trace_db_path=db_path,
    )
    result = agent.run("hello")
    store = SQLiteTraceStore(db_path)
    row = store.get_model_call_for_turn(result.run_id, 0)
    assert row is not None

    with store.connect() as db:
        db.execute(
            "UPDATE model_calls SET response_json=? WHERE id=?",
            ("not json", row["id"]),
        )

    with pytest.raises(ValueError, match="Malformed stored model response"):
        diff_model_call(
            db_path,
            result.run_id,
            turn=0,
            provider=FakeProvider([ProviderResponse.fake_text("new answer")]),
        )


def test_replay_model_call_rejects_malformed_stored_request(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider([ProviderResponse.fake_text("old answer")]),
        trace_db_path=db_path,
    )
    result = agent.run("hello")
    store = SQLiteTraceStore(db_path)
    row = store.get_model_call_for_turn(result.run_id, 0)
    assert row is not None

    with store.connect() as db:
        db.execute(
            "UPDATE model_calls SET request_json=? WHERE id=?",
            ("not json", row["id"]),
        )

    with pytest.raises(ValueError, match="Malformed stored model request"):
        replay_model_call(
            db_path,
            result.run_id,
            turn=0,
            provider=FakeProvider([ProviderResponse.fake_text("new answer")]),
        )
