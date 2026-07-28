import httpx

from clearagent.messages import Message
from clearagent.providers.anthropic import AnthropicProvider
from clearagent.providers.google import GoogleGenAIProvider


def test_anthropic_provider_builds_native_request_and_parses_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.anthropic.com/v1/messages"
        assert request.headers["x-api-key"] == "test-key"
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert request.read()
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "Hello from Claude"}],
                "usage": {"input_tokens": 5, "output_tokens": 3},
                "stop_reason": "end_turn",
            },
        )

    provider = AnthropicProvider(
        api_key="test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    request = provider.build_request(
        model="claude-sonnet-4-20250514",
        messages=[
            Message(role="system", content="Be concise."),
            Message(role="user", content="Say hello."),
        ],
        tools=[],
        tool_choice=None,
        temperature=0.2,
        max_tokens=256,
        extra={},
    )

    response = provider.complete(request)

    assert request.api_shape == "anthropic_messages"
    assert request.body["system"] == "Be concise."
    assert request.body["messages"] == [{"role": "user", "content": "Say hello."}]
    assert request.body["max_tokens"] == 256
    assert response.output_text == "Hello from Claude"
    assert response.usage.total_tokens == 8
    assert response.finish_reason == "end_turn"


def test_google_provider_builds_native_request_and_parses_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert (
            request.url
            == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
        )
        assert request.headers["x-goog-api-key"] == "test-key"
        assert request.read()
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"parts": [{"text": "Hello from Gemini"}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 4,
                    "candidatesTokenCount": 3,
                    "totalTokenCount": 7,
                },
            },
        )

    provider = GoogleGenAIProvider(
        api_key="test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    request = provider.build_request(
        model="gemini-2.5-flash",
        messages=[
            Message(role="system", content="Be concise."),
            Message(role="user", content="Say hello."),
        ],
        tools=[],
        tool_choice=None,
        temperature=0.2,
        max_tokens=256,
        extra={},
    )

    response = provider.complete(request)

    assert request.api_shape == "google_genai"
    assert request.body["systemInstruction"] == {"parts": [{"text": "Be concise."}]}
    assert request.body["contents"] == [{"role": "user", "parts": [{"text": "Say hello."}]}]
    assert request.body["generationConfig"] == {"temperature": 0.2, "maxOutputTokens": 256}
    assert response.output_text == "Hello from Gemini"
    assert response.usage.total_tokens == 7
    assert response.finish_reason == "STOP"
