import httpx
import pytest

from pydantic import BaseModel

from clearagent.messages import Message
from clearagent.providers.base import ProviderError
from clearagent.providers.anthropic import AnthropicProvider
from clearagent.tool import tool


class Summary(BaseModel):
    summary: str
    next_steps: list[str]


@tool
def lookup_order(order_id: str) -> dict:
    """Look up an order by ID."""
    return {"order_id": order_id, "status": "shipped"}


def test_anthropic_build_request_uses_messages_shape_tools_and_output_config():
    provider = AnthropicProvider(api_key="test-key")

    request = provider.build_request(
        model="claude-sonnet-4-5",
        messages=[
            Message(role="system", content="Be concise."),
            Message(role="user", content="Summarize order A123."),
        ],
        tools=[lookup_order],
        tool_choice="auto",
        temperature=0.0,
        max_tokens=256,
        extra={},
        response_format=Summary,
    )

    assert request.api_shape == "anthropic_messages"
    assert request.endpoint == "https://api.anthropic.com/v1/messages"
    assert request.headers_snapshot["anthropic-version"] == "2023-06-01"
    assert request.body["system"] == "Be concise."
    assert request.body["messages"] == [{"role": "user", "content": "Summarize order A123."}]
    assert request.body["tools"][0]["input_schema"]["properties"]["order_id"]["type"] == "string"
    assert request.body["tool_choice"] == {"type": "auto"}
    assert request.body["output_config"]["format"] == {
        "type": "json_schema",
        "schema": Summary.model_json_schema(),
    }


def test_anthropic_complete_parses_text_tool_calls_and_usage():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "test-key"
        return httpx.Response(
            200,
            json={
                "id": "msg_123",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-5",
                "content": [
                    {"type": "text", "text": "Checking the order."},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "lookup_order",
                        "input": {"order_id": "A123"},
                    },
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 12, "output_tokens": 7},
            },
        )

    provider = AnthropicProvider(
        api_key="test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    request = provider.build_request(
        model="claude-sonnet-4-5",
        messages=[Message(role="user", content="Where is A123?")],
        tools=[lookup_order],
        tool_choice="auto",
        temperature=None,
        max_tokens=128,
        extra={},
        response_format=None,
    )

    response = provider.complete(request)

    assert response.output_text == "Checking the order."
    assert response.tool_calls[0].name == "lookup_order"
    assert response.tool_calls[0].arguments == {"order_id": "A123"}
    assert response.usage.total_tokens == 19
    assert response.finish_reason == "tool_use"


def test_anthropic_provider_wraps_http_errors_with_provider_context():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    provider = AnthropicProvider(
        api_key="test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    request = provider.build_request(
        model="claude-sonnet-4-5",
        messages=[Message(role="user", content="Hi")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=128,
        extra={},
    )

    with pytest.raises(ProviderError, match="anthropic:claude-sonnet-4-5"):
        provider.complete(request)


def test_anthropic_stream_raises_provider_error_event():
    provider = AnthropicProvider(
        api_key="test-key",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    text=(
                        'event: error\n'
                        'data: {"type":"error","error":{"type":"overloaded_error",'
                        '"message":"Overloaded"}}\n\n'
                    ),
                )
            )
        ),
    )
    request = provider.build_request(
        model="claude-sonnet-4-5",
        messages=[Message(role="user", content="Hi")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
    )

    with pytest.raises(ProviderError, match="Overloaded"):
        list(provider.stream_text(request))


def test_anthropic_build_request_converts_tool_round_trip_and_custom_choice():
    provider = AnthropicProvider(api_key="test-key")

    request = provider.build_request(
        model="claude-sonnet-4-5",
        messages=[
            Message(
                role="assistant",
                content="I will check.",
                metadata={
                    "tool_calls": [
                        {
                            "id": "toolu_1",
                            "function": {
                                "name": "lookup_order",
                                "arguments": '{"order_id":"A123"}',
                            },
                        }
                    ]
                },
            ),
            Message(
                role="tool",
                content='{"status":"shipped"}',
                tool_call_id="toolu_1",
                name="lookup_order",
            ),
        ],
        tools=[lookup_order],
        tool_choice={"type": "tool", "name": "lookup_order"},
        temperature=None,
        max_tokens=None,
        extra={"metadata": {"request_id": "req_1"}},
    )

    assert request.body["tool_choice"] == {"type": "tool", "name": "lookup_order"}
    assert request.body["messages"] == [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "I will check."},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "lookup_order",
                    "input": {"order_id": "A123"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": '{"status":"shipped"}',
                }
            ],
        },
    ]
    assert request.body["metadata"] == {"request_id": "req_1"}


def test_anthropic_stream_yields_text_and_refreshes_redacted_key():
    seen_keys = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_keys.append(request.headers.get("x-api-key"))
        return httpx.Response(
            200,
            text=(
                "event: ping\n\n"
                'data: {"type":"content_block_delta","delta":'
                '{"type":"text_delta","text":"Hello"}}\n\n'
                "data: [DONE]\n\n"
            ),
        )

    provider = AnthropicProvider(
        api_key="fresh-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    request = provider.build_request(
        model="claude-sonnet-4-5",
        messages=[Message(role="user", content="Hi")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
    )
    request.headers_snapshot = {"x-api-key": "[REDACTED]"}

    assert list(provider.stream_text(request)) == ["Hello"]
    assert seen_keys == ["fresh-key"]


def test_anthropic_rejects_malformed_complete_and_stream_payloads():
    malformed_complete = AnthropicProvider(
        api_key="test-key",
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
        ),
    )
    complete_request = malformed_complete.build_request(
        model="claude-sonnet-4-5",
        messages=[Message(role="user", content="Hi")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
    )
    with pytest.raises(ProviderError, match="response parse failed"):
        malformed_complete.complete(complete_request)

    malformed_stream = AnthropicProvider(
        api_key="test-key",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text="data: {bad json\n\n")
            )
        ),
    )
    stream_request = malformed_stream.build_request(
        model="claude-sonnet-4-5",
        messages=[Message(role="user", content="Hi")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
    )
    with pytest.raises(ProviderError, match="stream response parse failed"):
        list(malformed_stream.stream_text(stream_request))
