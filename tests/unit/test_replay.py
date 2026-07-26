import httpx
import pytest

from clearagent import create_agent
from clearagent.providers.base import (
    FakeProvider,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
)
from clearagent.providers.openai_compatible import OpenAICompatibleProvider
from clearagent.providers.registry import provider_for_request as registry_provider_for_request
from clearagent.replay import diff_model_call, replay_model_call
from clearagent.storage.sqlite import SQLiteTraceStore


def _persist_request(db_path, request: ProviderRequest) -> str:
    store = SQLiteTraceStore(db_path)
    run_id = store.start_run(agent_name="replay-test", root_input="hello")
    turn_id = store.start_turn(
        run_id=run_id,
        turn_index=0,
        node_name="replay-test",
        input_messages=[{"role": "user", "content": "hello"}],
    )
    store.save_model_request(run_id=run_id, turn_id=turn_id, request=request)
    return run_id


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


@pytest.mark.parametrize(
    "stored_request",
    [
        ProviderRequest(
            provider="openai",
            model="gpt-5.6-luna",
            api_shape="openai_responses",
            endpoint="https://attacker.invalid/collect",
            body={"model": "gpt-5.6-luna", "input": []},
        ),
        ProviderRequest(
            provider="openai",
            model="gpt-4.1-mini",
            api_shape="openai_chat_completions",
            endpoint="https://attacker.invalid/collect",
            body={"model": "gpt-4.1-mini", "messages": []},
        ),
        ProviderRequest(
            provider="openrouter",
            model="openai/gpt-4o-mini",
            api_shape="openai_chat_completions",
            endpoint="https://attacker.invalid/collect",
            body={"model": "openai/gpt-4o-mini", "messages": []},
        ),
        ProviderRequest(
            provider="anthropic",
            model="claude-sonnet-5",
            api_shape="anthropic_messages",
            endpoint="https://attacker.invalid/collect",
            body={"model": "claude-sonnet-5", "messages": [], "max_tokens": 64},
        ),
        ProviderRequest(
            provider="google",
            model="gemini-3.5-flash-lite",
            api_shape="google_genai",
            endpoint="https://attacker.invalid/collect",
            body={"contents": []},
        ),
    ],
    ids=["openai-responses", "legacy-openai-chat", "openrouter", "anthropic", "google"],
)
def test_default_replay_rejects_noncanonical_cloud_endpoint_before_http_call(
    tmp_path, monkeypatch, stored_request
):
    db_path = tmp_path / "traces.sqlite"
    run_id = _persist_request(db_path, stored_request)
    attempted_requests: list[httpx.Request] = []
    monkeypatch.setenv("OPENAI_API_KEY", "fresh-openai-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "fresh-openrouter-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fresh-anthropic-key")
    monkeypatch.setenv("GEMINI_API_KEY", "fresh-google-key")

    def safe_provider_factory(stored_request):
        replay_provider = registry_provider_for_request(stored_request)
        original_client = replay_provider.client
        original_client.close()

        def should_not_run(http_request: httpx.Request) -> httpx.Response:
            attempted_requests.append(http_request)
            raise AssertionError("replay attempted HTTP before validating the stored endpoint")

        replay_provider.client = httpx.Client(transport=httpx.MockTransport(should_not_run))
        return replay_provider

    monkeypatch.setattr("clearagent.replay.provider_for_request", safe_provider_factory)

    with pytest.raises(ValueError, match="stored endpoint.*does not match"):
        replay_model_call(db_path, run_id, turn=0)

    assert attempted_requests == []


def test_default_replay_uses_stored_api_shape_for_legacy_openai_chat_completions(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "traces.sqlite"
    request = ProviderRequest(
        provider="openai",
        model="gpt-4.1-mini",
        api_shape="openai_chat_completions",
        endpoint="https://api.openai.com/v1/chat/completions",
        headers_snapshot={"authorization": "Bearer stale-key"},
        body={
            "model": "gpt-4.1-mini",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    run_id = _persist_request(db_path, request)
    monkeypatch.setenv("OPENAI_API_KEY", "fresh-key")
    seen_requests: list[httpx.Request] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        seen_requests.append(http_request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "legacy replay worked"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 3,
                    "total_tokens": 5,
                },
            },
        )

    def legacy_provider_factory(stored_request):
        replay_provider = registry_provider_for_request(stored_request)
        assert isinstance(replay_provider, OpenAICompatibleProvider)
        replay_provider.client.close()
        replay_provider.client = httpx.Client(transport=httpx.MockTransport(handler))
        return replay_provider

    monkeypatch.setattr("clearagent.replay.provider_for_request", legacy_provider_factory)

    replayed = replay_model_call(db_path, run_id, turn=0)

    assert replayed.output_text == "legacy replay worked"
    assert replayed.usage is not None
    assert replayed.usage.total_tokens == 5
    assert len(seen_requests) == 1
    assert seen_requests[0].url == httpx.URL("https://api.openai.com/v1/chat/completions")
    assert seen_requests[0].headers["authorization"] == "Bearer fresh-key"
