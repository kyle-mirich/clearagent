import pytest

from clearagent.runtime.messages import Message
from clearagent.runtime.providers.langchain_provider import LangchainChatProvider
from clearagent.runtime.providers.base import FakeProvider, ProviderResponse, normalize_response_format
from clearagent.runtime.providers.registry import provider_for_model


def test_fake_provider_builds_request_without_credentials():
    provider = FakeProvider([ProviderResponse.fake_text("hello")])

    request = provider.build_request(
        model="gpt-test",
        messages=[Message(role="user", content="Hi")],
        tools=[],
        tool_choice=None,
        temperature=0.0,
        max_tokens=None,
        extra={},
    )

    assert request.provider == "fake"
    assert request.model == "gpt-test"
    assert request.body["messages"] == [{"role": "user", "content": "Hi"}]
    assert provider.completed_requests == []


def test_fake_provider_returns_mocked_response():
    provider = FakeProvider([ProviderResponse.fake_text("mocked")])
    request = provider.build_request(
        model="gpt-test",
        messages=[Message(role="user", content="Hi")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
    )

    response = provider.complete(request)

    assert response.output_text == "mocked"
    assert provider.completed_requests == [request]


def test_registry_returns_langchain_backed_providers_for_named_families():
    anthropic = provider_for_model("anthropic:claude-sonnet-4-20250514")
    google = provider_for_model("google:gemini-2.5-flash")
    openai_provider = provider_for_model("openai:gpt-5.6-luna")

    assert isinstance(anthropic, LangchainChatProvider)
    assert isinstance(google, LangchainChatProvider)
    assert isinstance(openai_provider, LangchainChatProvider)
    assert anthropic.chat_model.model == "claude-sonnet-4-20250514"
    assert google.chat_model.model == "gemini-2.5-flash"
    assert openai_provider.chat_model.model_name == "gpt-5.6-luna"


def test_normalize_response_format_rejects_non_mapping_schema_values():
    with pytest.raises(TypeError, match="schema"):
        normalize_response_format({"name": "response", "schema": "not-a-schema"})
