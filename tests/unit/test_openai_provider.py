import json

import httpx
import pytest
from pydantic import BaseModel

from clearagent.messages import Message
from clearagent.providers.base import ProviderError
from clearagent.providers.openai import OpenAIResponsesProvider
from clearagent.providers.registry import provider_for_model
from clearagent.tool import tool


@tool
def lookup_order(order_id: str) -> dict:
    """Look up an order."""
    return {"order_id": order_id}


class Answer(BaseModel):
    value: str


def test_build_request_uses_responses_shape_and_converts_tool_history():
    provider = OpenAIResponsesProvider(api_key="test-key")

    request = provider.build_request(
        model="gpt-5.6-luna",
        messages=[
            Message(role="system", content="Be concise."),
            Message(role="user", content="Where is A123?"),
            Message(
                role="assistant",
                metadata={
                    "tool_calls": [
                        {
                            "id": "call_lookup",
                            "function": {
                                "name": "lookup_order",
                                "arguments": {"order_id": "A123"},
                            },
                        }
                    ]
                },
            ),
            Message(
                role="tool",
                content='{"status":"shipped"}',
                tool_call_id="call_lookup",
                name="lookup_order",
            ),
        ],
        tools=[lookup_order],
        tool_choice="auto",
        temperature=None,
        max_tokens=96,
        extra={},
    )

    assert request.api_shape == "openai_responses"
    assert request.endpoint == "https://api.openai.com/v1/responses"
    assert request.body["max_output_tokens"] == 96
    assert request.body["tools"][0]["name"] == "lookup_order"
    assert request.body["input"][-2] == {
        "type": "function_call",
        "call_id": "call_lookup",
        "name": "lookup_order",
        "arguments": '{"order_id":"A123"}',
    }
    assert request.body["input"][-1] == {
        "type": "function_call_output",
        "call_id": "call_lookup",
        "output": '{"status":"shipped"}',
    }


def test_openai_registry_and_structured_output_use_native_responses_api():
    provider = provider_for_model("openai:gpt-5.6-luna")

    request = provider.build_request(
        model="gpt-5.6-luna",
        messages=[Message(role="user", content="Return JSON")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
        response_format=Answer,
    )

    assert isinstance(provider, OpenAIResponsesProvider)
    assert request.body["text"]["format"] == {
        "type": "json_schema",
        "name": "Answer",
        "schema": Answer.model_json_schema(),
        "strict": True,
    }


def test_complete_parses_text_tools_and_usage():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/responses"
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json={
                "id": "resp_mock",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Checking."}],
                    },
                    {
                        "type": "function_call",
                        "call_id": "call_lookup",
                        "name": "lookup_order",
                        "arguments": '{"order_id":"A123"}',
                    },
                ],
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            },
        )

    provider = OpenAIResponsesProvider(
        api_key="test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    request = provider.build_request(
        model="gpt-5.6-luna",
        messages=[Message(role="user", content="Where is A123?")],
        tools=[lookup_order],
        tool_choice="auto",
        temperature=None,
        max_tokens=None,
        extra={},
    )

    response = provider.complete(request)

    assert response.output_text == "Checking."
    assert response.tool_calls[0].id == "call_lookup"
    assert response.tool_calls[0].arguments == {"order_id": "A123"}
    assert response.usage.total_tokens == 15
    assert response.finish_reason == "completed"


def test_stream_text_parses_responses_events():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is True
        return httpx.Response(
            200,
            text=(
                'event: response.output_text.delta\n'
                'data: {"type":"response.output_text.delta","delta":"Hello "}\n\n'
                'event: response.output_text.delta\n'
                'data: {"type":"response.output_text.delta","delta":"world"}\n\n'
                'event: response.completed\n'
                'data: {"type":"response.completed"}\n\n'
            ),
        )

    provider = OpenAIResponsesProvider(
        api_key="test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    request = provider.build_request(
        model="gpt-5.6-luna",
        messages=[Message(role="user", content="Say hi")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
    )

    assert list(provider.stream_text(request)) == ["Hello ", "world"]


def test_complete_reports_malformed_tool_arguments_with_context():
    provider = OpenAIResponsesProvider(
        api_key="test-key",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "status": "completed",
                        "output": [
                            {
                                "type": "function_call",
                                "call_id": "call_bad",
                                "name": "lookup_order",
                                "arguments": "{bad json",
                            }
                        ],
                    },
                )
            )
        ),
    )
    request = provider.build_request(
        model="gpt-5.6-luna",
        messages=[Message(role="user", content="Hi")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
    )

    with pytest.raises(ProviderError, match="openai:gpt-5.6-luna.*tool arguments"):
        provider.complete(request)


def test_complete_replaces_redacted_auth_and_reports_http_failure():
    seen_auth = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_auth.append(request.headers.get("authorization"))
        return httpx.Response(503, json={"error": {"message": "temporarily unavailable"}})

    provider = OpenAIResponsesProvider(
        api_key="fresh-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    request = provider.build_request(
        model="gpt-5.6-luna",
        messages=[Message(role="user", content="Hi")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
    )
    request.headers_snapshot = {"authorization": "[REDACTED]"}

    with pytest.raises(ProviderError, match="HTTP 503: temporarily unavailable"):
        provider.complete(request)

    assert seen_auth == ["Bearer fresh-key"]


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ("data: {bad json\n\n", "stream response parse failed"),
        ("data: []\n\n", "must contain JSON objects"),
        ('data: {"type":"error","error":{"message":"quota exceeded"}}\n\n', "quota exceeded"),
        ("not-sse\n", "not SSE framed"),
    ],
)
def test_stream_text_reports_malformed_and_provider_events(payload, error):
    provider = OpenAIResponsesProvider(
        api_key="test-key",
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payload))
        ),
    )
    request = provider.build_request(
        model="gpt-5.6-luna",
        messages=[Message(role="user", content="Hi")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
    )

    with pytest.raises(ProviderError, match=error):
        list(provider.stream_text(request))
