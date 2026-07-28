import re

import httpx
import pytest

from clearagent.messages import Message
from clearagent.providers.anthropic import AnthropicProvider
from clearagent.providers.base import ProviderError
from clearagent.providers.errors import response_error_message
from clearagent.providers.openai import OpenAIResponsesProvider
from clearagent.providers.openai_compatible import OpenAICompatibleProvider


def _request(provider, model: str):
    return provider.build_request(
        model=model,
        messages=[Message(role="user", content="hello")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
    )


def _raise_connect_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("offline", request=request)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"plain upstream failure", "plain upstream failure"),
        (b'{"error":"quota exceeded"}', "quota exceeded"),
        (b'{"error":{"message":""}}', '{"error":{"message":""}}'),
        (b'["bad gateway"]', '["bad gateway"]'),
    ],
)
def test_provider_error_detail_preserves_all_supported_upstream_shapes(content, expected):
    response = httpx.Response(502, content=content)

    assert response_error_message(response) == expected


@pytest.mark.parametrize(
    ("factory", "model"),
    [
        (
            lambda client: OpenAIResponsesProvider(api_key="test-key", client=client),
            "gpt-5.6-luna",
        ),
        (
            lambda client: AnthropicProvider(api_key="test-key", client=client),
            "claude-sonnet-4-5",
        ),
        (
            lambda client: OpenAICompatibleProvider(
                provider_name="openrouter", api_key="test-key", client=client
            ),
            "openai/gpt-4o-mini",
        ),
    ],
    ids=["openai-responses", "anthropic", "openai-compatible"],
)
def test_complete_normalizes_transport_failures(factory, model):
    with httpx.Client(transport=httpx.MockTransport(_raise_connect_error)) as client:
        provider = factory(client)
        request = _request(provider, model)

        with pytest.raises(
            ProviderError,
            match=re.escape(f"{provider.provider_name}:{model} request failed: offline"),
        ) as exc_info:
            provider.complete(request)

    assert isinstance(exc_info.value.__cause__, httpx.ConnectError)


@pytest.mark.parametrize(
    ("factory", "model"),
    [
        (
            lambda client: OpenAIResponsesProvider(api_key="test-key", client=client),
            "gpt-5.6-luna",
        ),
        (
            lambda client: AnthropicProvider(api_key="test-key", client=client),
            "claude-sonnet-4-5",
        ),
        (
            lambda client: OpenAICompatibleProvider(
                provider_name="openrouter", api_key="test-key", client=client
            ),
            "openai/gpt-4o-mini",
        ),
    ],
    ids=["openai-responses", "anthropic", "openai-compatible"],
)
def test_stream_normalizes_transport_failures(factory, model):
    with httpx.Client(transport=httpx.MockTransport(_raise_connect_error)) as client:
        provider = factory(client)
        request = _request(provider, model)

        with pytest.raises(
            ProviderError,
            match=re.escape(f"{provider.provider_name}:{model} stream request failed: offline"),
        ) as exc_info:
            list(provider.stream_text(request))

    assert isinstance(exc_info.value.__cause__, httpx.ConnectError)


def test_openai_responses_request_preserves_native_history_and_optional_temperature():
    provider = OpenAIResponsesProvider(api_key="test-key")
    preserved_output = [
        {"type": "reasoning", "id": "reasoning_1", "summary": []},
        {
            "type": "function_call",
            "call_id": "call_preserved",
            "name": "lookup",
            "arguments": '{"id":"A123"}',
        },
    ]

    request = provider.build_request(
        model="gpt-5.6-luna",
        messages=[
            Message(
                role="assistant",
                metadata={
                    "tool_calls": [{"id": "call_preserved"}],
                    "openai_responses_output": preserved_output,
                },
            ),
            Message(
                role="assistant",
                content="I will check.",
                metadata={
                    "tool_calls": [
                        {
                            "id": "call_new",
                            "function": {"name": "lookup", "arguments": ["A123"]},
                        }
                    ]
                },
            ),
        ],
        tools=[],
        tool_choice=None,
        temperature=0.4,
        max_tokens=None,
        extra={},
    )

    assert request.body["temperature"] == 0.4
    assert request.body["input"][:2] == preserved_output
    assert request.body["input"][2:] == [
        {"role": "assistant", "content": "I will check."},
        {
            "type": "function_call",
            "call_id": "call_new",
            "name": "lookup",
            "arguments": '["A123"]',
        },
    ]


def test_openai_responses_complete_handles_ignored_content_without_usage():
    provider = OpenAIResponsesProvider(
        api_key="test-key",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "status": "incomplete",
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "refusal", "refusal": "no"}],
                            },
                            {"type": "reasoning", "summary": []},
                        ],
                    },
                )
            )
        ),
    )

    response = provider.complete(_request(provider, "gpt-5.6-luna"))

    assert response.output_text is None
    assert response.tool_calls == []
    assert response.usage is None
    assert response.finish_reason == "incomplete"


@pytest.mark.parametrize("raw", [[], {"output": 3}])
def test_openai_responses_complete_normalizes_non_object_content(raw):
    provider = OpenAIResponsesProvider(
        api_key="test-key",
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=raw))
        ),
    )

    with pytest.raises(ProviderError, match="response parse failed"):
        provider.complete(_request(provider, "gpt-5.6-luna"))


def test_openai_responses_rejects_non_object_tool_arguments():
    provider = OpenAIResponsesProvider(
        api_key="test-key",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "output": [
                            {
                                "type": "function_call",
                                "call_id": "call_bad",
                                "name": "lookup",
                                "arguments": "[]",
                            }
                        ]
                    },
                )
            )
        ),
    )

    with pytest.raises(ProviderError, match="tool arguments must be a JSON object"):
        provider.complete(_request(provider, "gpt-5.6-luna"))


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("data: [DONE]\n\n", None),
        (
            'data: {"type":"response.failed","response":"upstream refusal"}\n\n',
            "stream error: upstream refusal",
        ),
    ],
)
def test_openai_responses_stream_handles_terminal_and_failed_events(payload, expected):
    provider = OpenAIResponsesProvider(
        api_key="test-key",
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payload))
        ),
    )
    request = _request(provider, "gpt-5.6-luna")

    if expected is None:
        assert list(provider.stream_text(request)) == []
    else:
        with pytest.raises(ProviderError, match=expected):
            list(provider.stream_text(request))


def test_anthropic_auth_snapshot_omits_absent_key(monkeypatch):
    monkeypatch.delenv("CLEARAGENT_TEST_ANTHROPIC_KEY", raising=False)
    provider = AnthropicProvider(api_key_env="CLEARAGENT_TEST_ANTHROPIC_KEY")

    assert provider.auth_headers_snapshot() == {"anthropic-version": "2023-06-01"}


def test_anthropic_request_preserves_native_content_and_sanitizes_bad_tool_input():
    provider = AnthropicProvider(api_key="test-key")
    preserved_content = [
        {"type": "thinking", "thinking": "private chain", "signature": "sig"},
        {
            "type": "tool_use",
            "id": "toolu_preserved",
            "name": "lookup",
            "input": {"id": "A123"},
        },
    ]

    request = provider.build_request(
        model="claude-sonnet-4-5",
        messages=[
            Message(
                role="assistant",
                metadata={
                    "tool_calls": [{"id": "toolu_preserved"}],
                    "anthropic_content": preserved_content,
                },
            ),
            Message(
                role="assistant",
                metadata={
                    "tool_calls": [
                        {
                            "id": "toolu_bad",
                            "function": {"name": "lookup", "arguments": ["A123"]},
                        }
                    ]
                },
            ),
        ],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
    )

    assert request.body["messages"] == [
        {"role": "assistant", "content": preserved_content},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_bad",
                    "name": "lookup",
                    "input": {},
                }
            ],
        },
    ]


@pytest.mark.parametrize(
    ("factory", "model", "payload", "error"),
    [
        (
            lambda client: AnthropicProvider(api_key="test-key", client=client),
            "claude-sonnet-4-5",
            "data: []\n\n",
            "stream response must contain JSON objects",
        ),
        (
            lambda client: OpenAICompatibleProvider(
                provider_name="openrouter", api_key="test-key", client=client
            ),
            "openai/gpt-4o-mini",
            "data: []\n\n",
            "stream response must contain JSON objects",
        ),
        (
            lambda client: OpenAICompatibleProvider(
                provider_name="openrouter", api_key="test-key", client=client
            ),
            "openai/gpt-4o-mini",
            "data: {bad json\n\n",
            "stream response parse failed",
        ),
        (
            lambda client: OpenAICompatibleProvider(
                provider_name="openrouter", api_key="test-key", client=client
            ),
            "openai/gpt-4o-mini",
            'data: {"error":"quota exceeded"}\n\n',
            "stream failed: quota exceeded",
        ),
        (
            lambda client: AnthropicProvider(api_key="test-key", client=client),
            "claude-sonnet-4-5",
            'data: {"type":"error","error":"overloaded"}\n\n',
            "stream error: overloaded",
        ),
    ],
    ids=[
        "anthropic-non-object",
        "compatible-non-object",
        "compatible-malformed-json",
        "compatible-error-string",
        "anthropic-error-string",
    ],
)
def test_provider_streams_normalize_malformed_and_error_events(factory, model, payload, error):
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payload))
    ) as client:
        provider = factory(client)

        with pytest.raises(ProviderError, match=error):
            list(provider.stream_text(_request(provider, model)))


def test_openai_compatible_stream_ignores_non_data_and_empty_deltas():
    payload = '\nevent: ping\ndata: {"choices":[{"delta":{}}]}\n\ndata: [DONE]\n\n'
    provider = OpenAICompatibleProvider(
        provider_name="openrouter",
        api_key="test-key",
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payload))
        ),
    )

    assert list(provider.stream_text(_request(provider, "openai/gpt-4o-mini"))) == []


@pytest.mark.parametrize("raw", [[], {"choices": []}])
def test_openai_compatible_complete_normalizes_malformed_payload(raw):
    provider = OpenAICompatibleProvider(
        provider_name="openrouter",
        api_key="test-key",
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=raw))
        ),
    )

    with pytest.raises(ProviderError, match="response parse failed"):
        provider.complete(_request(provider, "openai/gpt-4o-mini"))


def test_openai_compatible_rejects_non_object_tool_arguments():
    provider = OpenAICompatibleProvider(
        provider_name="openrouter",
        api_key="test-key",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {
                                    "tool_calls": [
                                        {
                                            "id": "call_bad",
                                            "function": {
                                                "name": "lookup",
                                                "arguments": "[]",
                                            },
                                        }
                                    ]
                                }
                            }
                        ]
                    },
                )
            )
        ),
    )

    with pytest.raises(ProviderError, match="tool arguments must be a JSON object"):
        provider.complete(_request(provider, "openai/gpt-4o-mini"))
