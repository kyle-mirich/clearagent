import httpx
import pytest

from clearagent.messages import Message
from clearagent.providers.base import ProviderError
from clearagent.providers.openai_compatible import OpenAICompatibleProvider
from clearagent.tool import tool


@tool
def lookup_order(order_id: str) -> dict:
    """Look up an order."""
    return {"order_id": order_id}


def test_build_request_in_openai_chat_shape_without_api_key():
    provider = OpenAICompatibleProvider(provider_name="openai")

    request = provider.build_request(
        model="gpt-4.1-mini",
        messages=[Message(role="user", content="Where is A123?")],
        tools=[],
        tool_choice=None,
        temperature=0.0,
        max_tokens=None,
        extra={},
    )

    assert request.body == {
        "model": "gpt-4.1-mini",
        "messages": [{"role": "user", "content": "Where is A123?"}],
        "temperature": 0.0,
    }
    assert request.headers_snapshot == {}


def test_build_request_includes_tools_tool_choice_and_extra_params():
    provider = OpenAICompatibleProvider(provider_name="openai")

    request = provider.build_request(
        model="gpt-4.1-mini",
        messages=[Message(role="user", content="Where is A123?")],
        tools=[lookup_order],
        tool_choice="auto",
        temperature=0.0,
        max_tokens=128,
        extra={"seed": 7},
    )

    assert request.body["tools"][0]["function"]["name"] == "lookup_order"
    assert request.body["tool_choice"] == "auto"
    assert request.body["max_tokens"] == 128
    assert request.body["seed"] == 7


def test_build_request_serializes_assistant_tool_arguments_for_follow_up_turn():
    provider = OpenAICompatibleProvider(provider_name="openai")

    request = provider.build_request(
        model="gpt-5.6-luna",
        messages=[
            Message(
                role="assistant",
                metadata={
                    "tool_calls": [
                        {
                            "id": "call_lookup_order",
                            "type": "function",
                            "function": {
                                "name": "lookup_order",
                                "arguments": {"order_id": "A123"},
                            },
                        }
                    ]
                },
            )
        ],
        tools=[lookup_order],
        tool_choice="auto",
        temperature=None,
        max_tokens=None,
        extra={},
    )

    function = request.body["messages"][0]["tool_calls"][0]["function"]
    assert function["arguments"] == '{"order_id":"A123"}'


def test_mock_final_text_response_parses_usage():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl_mock_final",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Order A123 has shipped and arrives Friday.",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                },
            },
        )

    provider = OpenAICompatibleProvider(
        provider_name="openai",
        api_key="test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    request = provider.build_request(
        model="gpt-4.1-mini",
        messages=[Message(role="user", content="Where is A123?")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
    )

    response = provider.complete(request)

    assert response.output_text == "Order A123 has shipped and arrives Friday."
    assert response.usage.total_tokens == 120
    assert response.finish_reason == "stop"


def test_mock_tool_call_response_parses_tool_calls():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl_mock_tool",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_lookup_order",
                                    "type": "function",
                                    "function": {
                                        "name": "lookup_order",
                                        "arguments": "{\"order_id\": \"A123\"}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 90, "completion_tokens": 10, "total_tokens": 100},
            },
        )

    provider = OpenAICompatibleProvider(
        provider_name="openai",
        api_key="test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    request = provider.build_request(
        model="gpt-4.1-mini",
        messages=[Message(role="user", content="Where is A123?")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
    )

    response = provider.complete(request)

    assert response.tool_calls[0].name == "lookup_order"
    assert response.tool_calls[0].arguments == {"order_id": "A123"}


def test_stream_text_yields_openai_compatible_delta_content():
    def handler(request: httpx.Request) -> httpx.Response:
        assert b'"stream":true' in request.content.replace(b" ", b"")
        chunks = [
            b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":" world"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]
        return httpx.Response(200, content=b"".join(chunks))

    provider = OpenAICompatibleProvider(
        provider_name="openai",
        api_key="test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    request = provider.build_request(
        model="gpt-4.1-mini",
        messages=[Message(role="user", content="Say hi")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
    )

    chunks = list(provider.stream_text(request))

    assert chunks == ["Hello", " world"]


def test_stream_text_replaces_redacted_authorization_with_fresh_api_key():
    seen_authorization = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_authorization.append(request.headers.get("authorization"))
        chunks = [
            b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]
        return httpx.Response(200, content=b"".join(chunks))

    provider = OpenAICompatibleProvider(
        provider_name="openai",
        api_key="fresh-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    request = provider.build_request(
        model="gpt-4.1-mini",
        messages=[Message(role="user", content="Say hi")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
    )
    request.headers_snapshot = {"authorization": "[REDACTED]"}

    chunks = list(provider.stream_text(request))

    assert chunks == ["Hello"]
    assert seen_authorization == ["Bearer fresh-key"]


def test_openai_provider_wraps_http_errors_with_provider_context():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "upstream failed"}})

    provider = OpenAICompatibleProvider(
        provider_name="openai",
        api_key="test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    request = provider.build_request(
        model="gpt-4.1-mini",
        messages=[Message(role="user", content="Hi")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
    )

    with pytest.raises(ProviderError, match="openai:gpt-4.1-mini"):
        provider.complete(request)


def test_openai_provider_wraps_malformed_tool_arguments():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call_bad",
                                    "function": {"name": "lookup_order", "arguments": "{bad json"},
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        )

    provider = OpenAICompatibleProvider(
        provider_name="openai",
        api_key="test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    request = provider.build_request(
        model="gpt-4.1-mini",
        messages=[Message(role="user", content="Hi")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
    )

    with pytest.raises(ProviderError, match="tool arguments"):
        provider.complete(request)
