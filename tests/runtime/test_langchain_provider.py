import asyncio
import json

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from clearagent.runtime.messages import Message
from clearagent.runtime.providers.base import ProviderError
from clearagent.runtime.providers.langchain_provider import (
    LangchainChatProvider,
    _to_langchain_messages,
    build_langchain_chat_model,
)


def _provider(*responses: AIMessage) -> LangchainChatProvider:
    return LangchainChatProvider(
        provider_name="openai",
        chat_model=GenericFakeChatModel(messages=iter(responses)),
    )


def test_build_request_snapshots_openai_style_body_for_traces():
    provider = _provider(AIMessage(content="ok"))

    request = provider.build_request(
        model="gpt-5.6-luna",
        messages=[Message(role="user", content="hi")],
        tools=[],
        tool_choice=None,
        temperature=0.0,
        max_tokens=128,
        extra={},
        response_format=None,
    )

    assert request.body["model"] == "gpt-5.6-luna"
    assert request.body["messages"] == [{"role": "user", "content": "hi"}]
    assert request.body["max_tokens"] == 128
    assert request.api_shape == "openai_chat_completions"


def test_direct_openai_uses_responses_api_without_server_storage():
    model = build_langchain_chat_model(provider="openai", model="gpt-5.6-luna")
    assert model.use_responses_api is True
    assert model.store is False


def test_complete_maps_langchain_response_to_provider_response():
    provider = _provider(
        AIMessage(
            content="",
            tool_calls=[{"name": "lookup", "args": {"ticket_id": "T1"}, "id": "call_1"}],
        ),
        AIMessage(content="done"),
    )

    request = provider.build_request(
        model="gpt-5.6-luna",
        messages=[Message(role="user", content="look it up")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
    )
    first = provider.complete(request)

    assert first.output_text is None
    assert [call.name for call in first.tool_calls] == ["lookup"]
    assert first.tool_calls[0].arguments == {"ticket_id": "T1"}
    assert first.finish_reason == "tool_calls"

    second = provider.complete(request)
    assert second.output_text == "done"
    assert second.finish_reason == "stop"


def test_acomplete_uses_langchain_async_invoke():
    provider = _provider(AIMessage(content="async done"))
    request = provider.build_request(
        model="gpt-5.6-luna",
        messages=[Message(role="user", content="run concurrently")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
    )

    response = asyncio.run(provider.acomplete(request))

    assert response.output_text == "async done"
    assert response.finish_reason == "stop"


def test_complete_wraps_model_failures_in_provider_error():
    class ExplodingModel(GenericFakeChatModel):
        def invoke(self, *args, **kwargs):
            raise RuntimeError("boom")

    provider = LangchainChatProvider(
        provider_name="openai", chat_model=ExplodingModel(messages=iter([]))
    )
    request = provider.build_request(
        model="gpt-5.6-luna",
        messages=[Message(role="user", content="hi")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
    )

    with pytest.raises(ProviderError, match="boom"):
        provider.complete(request)


def test_stream_text_yields_text_deltas():
    provider = _provider(AIMessage(content="hello world"))
    request = provider.build_request(
        model="gpt-5.6-luna",
        messages=[Message(role="user", content="say hi")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
    )

    assert "".join(provider.stream_text(request)) == "hello world"


def test_fixture_replay_reads_normalized_response_payloads(tmp_path, monkeypatch):
    provider = _provider()
    monkeypatch.setenv("CLEARAGENT_OPENAI_FIXTURE_DIR", str(tmp_path))
    monkeypatch.setenv("CLEARAGENT_OPENAI_FIXTURE_MODE", "replay")
    request = provider.build_request(
        model="gpt-5.6-luna",
        messages=[Message(role="user", content="hi")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
    )

    from clearagent.runtime.providers.langchain_provider import _fixture_path

    fixture = _fixture_path(request)
    assert fixture is not None
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_text(
        json.dumps(
            {
                "provider": "openai",
                "model": "gpt-5.6-luna",
                "raw": {"replayed": True},
                "output_text": "recorded answer",
                "tool_calls": [],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
                "finish_reason": "stop",
            }
        )
    )

    response = provider.complete(request)
    assert response.output_text == "recorded answer"
    assert response.usage.total_tokens == 3


def test_message_translation_covers_all_roles():
    dump = [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "question"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{\"a\": 1}"}}
            ],
        },
        {"role": "tool", "content": "result", "tool_call_id": "c1"},
    ]

    messages = _to_langchain_messages(dump)

    assert messages[0].content == "be brief"
    assert messages[2].tool_calls[0]["name"] == "f"
    assert messages[3].tool_call_id == "c1"


def test_build_langchain_chat_model_maps_uri_families(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-key")
    openai_model = build_langchain_chat_model(provider="openai", model="gpt-5.6-luna")
    router_model = build_langchain_chat_model(provider="openrouter", model="x/y")
    anthropic_model = build_langchain_chat_model(provider="anthropic", model="claude-x")

    assert openai_model.model_name == "gpt-5.6-luna"
    assert getattr(router_model, "openai_api_base").endswith("openrouter.ai/api/v1")
    assert anthropic_model.model == "claude-x"

    try:
        build_langchain_chat_model(provider="unknown", model="m")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
