import json

import pytest
from pydantic import BaseModel

from clearagent import create_agent, tool
from clearagent.providers.base import FakeProvider, ProviderResponse, ToolCall
from clearagent.providers.openai_compatible import OpenAICompatibleProvider
from clearagent.storage.sqlite import SQLiteTraceStore


class Classification(BaseModel):
    label: str
    confidence: float


@tool
def lookup_label(ticket_id: str) -> str:
    return f"billing:{ticket_id}"


def test_openai_compatible_request_includes_json_schema_response_format():
    provider = OpenAICompatibleProvider(provider_name="openrouter")

    request = provider.build_request(
        model="openai/gpt-4o-mini",
        messages=[],
        tools=[],
        tool_choice=None,
        temperature=0.0,
        max_tokens=None,
        extra={},
        response_format=Classification,
    )

    assert request.body["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "Classification",
            "strict": True,
            "schema": Classification.model_json_schema(),
        },
    }


def test_agent_validates_and_returns_structured_output(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    provider = FakeProvider(
        [ProviderResponse.fake_text('{"label": "billing", "confidence": 0.93}')]
    )
    agent = create_agent(
        name="classifier",
        model="openai:gpt-4.1-mini",
        provider=provider,
        response_format=Classification,
        trace_db_path=db_path,
    )

    result = agent.run("Classify this ticket")

    assert result.structured_output == {"label": "billing", "confidence": 0.93}
    store = SQLiteTraceStore(db_path)
    request = json.loads(store.get_model_call_for_turn(result.run_id, 0)["request_json"])
    response = json.loads(store.get_model_call_for_turn(result.run_id, 0)["response_json"])
    assert request["response_format"]["name"] == "Classification"
    assert response["structured_output"] == {"label": "billing", "confidence": 0.93}


def test_agent_requires_final_text_when_structured_output_is_requested(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    provider = FakeProvider(
        [
            ProviderResponse(
                provider="fake",
                model="fake-model",
                raw={},
                output_text=None,
            )
        ]
    )
    agent = create_agent(
        name="classifier",
        model="openai:gpt-4.1-mini",
        provider=provider,
        response_format=Classification,
        trace_db_path=db_path,
    )

    with pytest.raises(ValueError, match="structured output response did not include text"):
        agent.run("Classify this ticket")

    run = SQLiteTraceStore(db_path).list_runs()[0]
    assert run["status"] == "error"


def test_agent_reports_invalid_structured_output_json_as_value_error(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    provider = FakeProvider([ProviderResponse.fake_text("not json")])
    agent = create_agent(
        name="classifier",
        model="openai:gpt-4.1-mini",
        provider=provider,
        response_format=Classification,
        trace_db_path=db_path,
    )

    with pytest.raises(ValueError, match="Invalid structured output JSON"):
        agent.run("Classify this ticket")

    run = SQLiteTraceStore(db_path).list_runs()[0]
    assert run["status"] == "error"


def test_agent_reports_structured_output_schema_mismatch_as_value_error(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    provider = FakeProvider([ProviderResponse.fake_text('{"label": "billing"}')])
    agent = create_agent(
        name="classifier",
        model="openai:gpt-4.1-mini",
        provider=provider,
        response_format=Classification,
        trace_db_path=db_path,
    )

    with pytest.raises(ValueError, match="Structured output did not match schema"):
        agent.run("Classify this ticket")

    run = SQLiteTraceStore(db_path).list_runs()[0]
    assert run["status"] == "error"


def test_structured_output_allows_intermediate_tool_call_without_text(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    provider = FakeProvider(
        [
            ProviderResponse.fake_tool_call(
                ToolCall(id="call_1", name="lookup_label", arguments={"ticket_id": "T1"})
            ),
            ProviderResponse.fake_text('{"label": "billing", "confidence": 0.99}'),
        ]
    )
    agent = create_agent(
        name="classifier",
        model="openai:gpt-4.1-mini",
        provider=provider,
        tools=[lookup_label],
        response_format=Classification,
        trace_db_path=db_path,
    )

    result = agent.run("Classify this ticket")

    assert result.structured_output == {"label": "billing", "confidence": 0.99}
    assert result.tool_calls == [
        {
            "name": "lookup_label",
            "arguments": {"ticket_id": "T1"},
            "result": "billing:T1",
        }
    ]


def test_streaming_validates_structured_output_before_marking_success(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="classifier",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider([ProviderResponse.fake_text("not json")]),
        response_format=Classification,
        trace_db_path=db_path,
    )

    with pytest.raises(ValueError, match="Invalid structured output JSON"):
        list(agent.stream_text("Classify this ticket"))

    assert SQLiteTraceStore(db_path).list_runs()[0]["status"] == "error"
