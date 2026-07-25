import httpx
import pytest

from pydantic import BaseModel

from clearagent.messages import Message
from clearagent.providers.base import ProviderError
from clearagent.providers.google import GoogleProvider
from clearagent.tool import tool


class Classification(BaseModel):
    label: str
    confidence: float


@tool
def lookup_order(order_id: str) -> dict:
    """Look up an order by ID."""
    return {"order_id": order_id}


def test_google_build_request_uses_generate_content_shape_tools_and_response_schema():
    provider = GoogleProvider(api_key="test-key")

    request = provider.build_request(
        model="gemini-2.5-flash",
        messages=[
            Message(role="system", content="Classify support tickets."),
            Message(role="user", content="Refund request"),
        ],
        tools=[lookup_order],
        tool_choice="auto",
        temperature=0.0,
        max_tokens=128,
        extra={},
        response_format=Classification,
    )

    assert request.api_shape == "google_genai"
    assert request.endpoint == (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.5-flash:generateContent"
    )
    assert request.headers_snapshot["x-goog-api-key"] == "test-key"
    assert request.body["systemInstruction"] == {"parts": [{"text": "Classify support tickets."}]}
    assert request.body["contents"] == [
        {"role": "user", "parts": [{"text": "Refund request"}]},
    ]
    assert request.body["tools"][0]["functionDeclarations"][0]["name"] == "lookup_order"
    assert request.body["generationConfig"]["responseMimeType"] == "application/json"
    assert request.body["generationConfig"]["responseJsonSchema"] == Classification.model_json_schema()


def test_google_complete_parses_text_function_calls_and_usage():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == "test-key"
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {"text": "Checking the order."},
                                {
                                    "functionCall": {
                                        "id": "call_lookup_order",
                                        "name": "lookup_order",
                                        "args": {"order_id": "A123"},
                                    },
                                    "thoughtSignature": "signed-context",
                                },
                            ],
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 10,
                    "candidatesTokenCount": 5,
                    "totalTokenCount": 15,
                },
            },
        )

    provider = GoogleProvider(
        api_key="test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    request = provider.build_request(
        model="gemini-2.5-flash",
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
    assert response.tool_calls[0].id == "call_lookup_order"
    assert response.tool_calls[0].provider_data == {"thoughtSignature": "signed-context"}
    assert response.usage.total_tokens == 15
    assert response.finish_reason == "STOP"


def test_google_provider_wraps_http_errors_with_provider_context():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": "forbidden"}})

    provider = GoogleProvider(
        api_key="test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    request = provider.build_request(
        model="gemini-2.5-flash",
        messages=[Message(role="user", content="Hi")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=128,
        extra={},
    )

    with pytest.raises(ProviderError, match="google:gemini-2.5-flash"):
        provider.complete(request)


def test_google_stream_requests_sse_and_parses_events():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["alt"] == "sse"
        return httpx.Response(
            200,
            text=(
                'data: {"candidates":[{"content":{"parts":[{"text":"Hello "}]}}]}\n\n'
                'data: {"candidates":[{"content":{"parts":[{"text":"world"}]}}]}\n\n'
            ),
        )

    provider = GoogleProvider(
        api_key="test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    request = provider.build_request(
        model="gemini-2.5-flash",
        messages=[Message(role="user", content="Hi")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
    )

    assert list(provider.stream_text(request)) == ["Hello ", "world"]


def test_google_stream_rejects_non_sse_array_response():
    provider = GoogleProvider(
        api_key="test-key",
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
        ),
    )
    request = provider.build_request(
        model="gemini-2.5-flash",
        messages=[Message(role="user", content="Hi")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
    )

    with pytest.raises(ProviderError, match="not SSE framed"):
        list(provider.stream_text(request))


def test_google_build_request_converts_assistant_and_tool_messages():
    provider = GoogleProvider(api_key="test-key")
    request = provider.build_request(
        model="gemini-2.5-flash",
        messages=[
            Message(
                role="assistant",
                content="Checking.",
                metadata={
                    "tool_calls": [
                        {
                            "id": "call_lookup_order",
                            "function": {
                                "name": "lookup_order",
                                "arguments": {"order_id": "A123"},
                            },
                            "provider_data": {"thoughtSignature": "signed-context"},
                        }
                    ]
                },
            ),
            Message(
                role="tool",
                content='{"status":"shipped"}',
                tool_call_id="call_lookup_order",
                name="lookup_order",
            ),
            Message(role="assistant", content=None),
        ],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
    )

    assert request.body["contents"] == [
        {
            "role": "model",
            "parts": [
                {"text": "Checking."},
                {
                    "functionCall": {
                        "id": "call_lookup_order",
                        "name": "lookup_order",
                        "args": {"order_id": "A123"},
                    },
                    "thoughtSignature": "signed-context",
                },
            ],
        },
        {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "id": "call_lookup_order",
                        "name": "lookup_order",
                        "response": {"result": '{"status":"shipped"}'},
                    }
                }
            ],
        },
        {"role": "model", "parts": [{"text": ""}]},
    ]


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ("data: {bad json\n\n", "stream response parse failed"),
        ("data: []\n\n", "must contain JSON objects"),
        ('data: {"error":{"message":"quota exceeded"}}\n\n', "quota exceeded"),
    ],
)
def test_google_stream_rejects_invalid_events(payload, error):
    provider = GoogleProvider(
        api_key="test-key",
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payload))
        ),
    )
    request = provider.build_request(
        model="gemini-2.5-flash",
        messages=[Message(role="user", content="Hi")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
    )

    with pytest.raises(ProviderError, match=error):
        list(provider.stream_text(request))


def test_google_rejects_malformed_complete_payload():
    provider = GoogleProvider(
        api_key="test-key",
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
        ),
    )
    request = provider.build_request(
        model="gemini-2.5-flash",
        messages=[Message(role="user", content="Hi")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
    )

    with pytest.raises(ProviderError, match="response parse failed"):
        provider.complete(request)
