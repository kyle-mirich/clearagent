import httpx
import pytest

from clearagent import create_agent
from clearagent.providers.anthropic import AnthropicProvider
from clearagent.providers.base import FakeProvider, ProviderRequest, ProviderResponse
from clearagent.providers.openai_compatible import OpenAICompatibleProvider
from clearagent.replay import _expected_endpoint, diff_model_call, replay_model_call
from clearagent.storage.sqlite import SQLiteTraceStore


def _persist_request(db_path, request: ProviderRequest) -> str:
    store = SQLiteTraceStore(db_path)
    run_id = store.start_run(agent_name="replay-validation", root_input="hello")
    turn_id = store.start_turn(
        run_id=run_id,
        turn_index=0,
        node_name="replay-validation",
        input_messages=[{"role": "user", "content": "hello"}],
    )
    store.save_model_request(run_id=run_id, turn_id=turn_id, request=request)
    return run_id


@pytest.mark.parametrize("operation", [replay_model_call, diff_model_call])
def test_replay_operations_require_run_id(tmp_path, operation):
    with pytest.raises(ValueError, match="run_id is required"):
        operation(tmp_path / "traces.sqlite")


@pytest.mark.parametrize("operation", [replay_model_call, diff_model_call])
def test_replay_operations_reject_unknown_run(tmp_path, operation):
    with pytest.raises(ValueError, match="Missing model request for run missing turn 0"):
        operation(
            tmp_path / "traces.sqlite",
            "missing",
            provider=FakeProvider([ProviderResponse.fake_text("unused")]),
        )


def test_diff_rejects_stored_response_that_is_valid_json_but_not_an_object(tmp_path):
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
        db.execute("UPDATE model_calls SET response_json=? WHERE id=?", ("[]", row["id"]))

    with pytest.raises(ValueError, match="Malformed stored model response"):
        diff_model_call(
            db_path,
            result.run_id,
            provider=FakeProvider([ProviderResponse.fake_text("new answer")]),
        )


def test_diff_reports_unchanged_response_contract(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider([ProviderResponse.fake_text("same answer")]),
        trace_db_path=db_path,
    )
    result = agent.run("hello")

    diff = diff_model_call(
        db_path,
        result.run_id,
        provider=FakeProvider([ProviderResponse.fake_text("same answer")]),
    )

    assert diff.changed is False
    assert diff.before_output == diff.after_output == "same answer"
    assert diff.before_finish_reason == diff.after_finish_reason == "stop"
    assert diff.before_usage == diff.after_usage


@pytest.mark.parametrize(
    ("provider", "error"),
    [
        (
            AnthropicProvider(
                api_key="fresh-key",
                client=httpx.Client(
                    transport=httpx.MockTransport(
                        lambda request: pytest.fail("provider mismatch reached HTTP")
                    )
                ),
            ),
            "Replay provider 'anthropic' does not match stored provider 'openai'",
        ),
        (
            OpenAICompatibleProvider(
                provider_name="openai",
                api_key="fresh-key",
                client=httpx.Client(
                    transport=httpx.MockTransport(
                        lambda request: pytest.fail("API-shape mismatch reached HTTP")
                    )
                ),
            ),
            "Replay provider API shape 'openai_chat_completions' does not match stored API shape 'openai_responses'",
        ),
    ],
    ids=["provider-name", "api-shape"],
)
def test_explicit_replay_rejects_provider_contract_mismatch_before_http(
    tmp_path, provider, error
):
    db_path = tmp_path / "traces.sqlite"
    run_id = _persist_request(
        db_path,
        ProviderRequest(
            provider="openai",
            model="gpt-5.6-luna",
            api_shape="openai_responses",
            endpoint="https://api.openai.com/v1/responses",
            body={"model": "gpt-5.6-luna", "input": []},
        ),
    )

    with pytest.raises(ValueError, match=error):
        replay_model_call(db_path, run_id, provider=provider)


def test_default_replay_rejects_impossible_provider_api_shape(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    run_id = _persist_request(
        db_path,
        ProviderRequest(
            provider="openai",
            model="gpt-5.6-luna",
            api_shape="anthropic_messages",
            endpoint="https://api.openai.com/v1/messages",
            body={"model": "gpt-5.6-luna", "messages": []},
        ),
    )

    with pytest.raises(
        ValueError,
        match="Stored API shape 'anthropic_messages' is not valid for provider 'openai'",
    ):
        replay_model_call(db_path, run_id)


def test_endpoint_validation_rejects_an_unknown_internal_api_shape():
    request = ProviderRequest.model_construct(
        provider="openai",
        model="future-model",
        api_shape="future_api_shape",
        endpoint="https://api.openai.com/v1/future",
        body={},
    )

    with pytest.raises(ValueError, match="Unsupported stored API shape 'future_api_shape'"):
        _expected_endpoint("https://api.openai.com/v1", request)


def test_local_replay_allows_stored_endpoint_without_sending_credentials(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    stored_endpoint = "http://127.0.0.1:9123/v1/chat/completions"
    run_id = _persist_request(
        db_path,
        ProviderRequest(
            provider="local",
            model="llama3.1",
            api_shape="openai_chat_completions",
            endpoint=stored_endpoint,
            body={"model": "llama3.1", "messages": []},
        ),
    )
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "local replay worked"},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    provider = OpenAICompatibleProvider(
        provider_name="local",
        base_url="http://localhost:8000/v1",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    response = replay_model_call(db_path, run_id, provider=provider)

    assert response.output_text == "local replay worked"
    assert len(seen_requests) == 1
    assert seen_requests[0].url == httpx.URL(stored_endpoint)
    assert "authorization" not in seen_requests[0].headers
