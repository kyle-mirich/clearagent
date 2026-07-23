import pytest

from clearagent.messages import Message
from clearagent.providers.model_uri import parse_model_uri
from clearagent.providers.registry import provider_for_model


@pytest.mark.parametrize(
    ("uri", "provider", "model", "api_shape"),
    [
        ("openai:gpt-4.1-mini", "openai", "gpt-4.1-mini", "openai_chat_completions"),
        (
            "anthropic:claude-sonnet-4-5",
            "anthropic",
            "claude-sonnet-4-5",
            "anthropic_messages",
        ),
        ("google:gemini-2.5-flash", "google", "gemini-2.5-flash", "google_genai"),
        (
            "openrouter:anthropic/claude-sonnet-4.5",
            "openrouter",
            "anthropic/claude-sonnet-4.5",
            "openai_chat_completions",
        ),
    ],
)
def test_parse_known_model_uris(uri, provider, model, api_shape):
    parsed = parse_model_uri(uri)

    assert parsed.provider == provider
    assert parsed.model == model
    assert parsed.api_shape == api_shape


def test_invalid_model_uri_raises_clear_error():
    with pytest.raises(ValueError, match="provider:model"):
        parse_model_uri("gpt-4.1-mini")


def test_local_url_model_uri_keeps_base_url_and_model_separate():
    parsed = parse_model_uri("local:http://localhost:8000/v1?model=llama3.1")

    assert parsed.provider == "local"
    assert parsed.model == "llama3.1"
    assert parsed.base_url == "http://localhost:8000/v1"
    assert parsed.api_shape == "openai_chat_completions"


@pytest.mark.parametrize(
    "uri",
    [
        "local:http://localhost:8000/v1",
        "local:http://localhost:8000/v1?model=",
    ],
)
def test_local_url_model_uri_requires_model_query_value(uri):
    with pytest.raises(ValueError, match="model query"):
        parse_model_uri(uri)


def test_local_url_provider_request_uses_local_base_url_and_model_name(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "should-not-be-used")

    provider = provider_for_model("local:http://localhost:8000/v1?model=llama3.1")
    request = provider.build_request(
        model="llama3.1",
        messages=[Message(role="user", content="Say hi")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
    )

    assert request.endpoint == "http://localhost:8000/v1/chat/completions"
    assert request.body["model"] == "llama3.1"
    assert request.headers_snapshot == {}


def test_ollama_provider_defaults_to_local_openai_compatible_endpoint(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "should-not-be-used")

    provider = provider_for_model("ollama:llama3.1")
    request = provider.build_request(
        model="llama3.1",
        messages=[Message(role="user", content="Say hi")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
    )

    assert request.endpoint == "http://localhost:11434/v1/chat/completions"
    assert request.headers_snapshot == {}


def test_local_provider_defaults_to_local_openai_compatible_endpoint(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "should-not-be-used")

    provider = provider_for_model("local:llama3.1")
    request = provider.build_request(
        model="llama3.1",
        messages=[Message(role="user", content="Say hi")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
    )

    assert request.endpoint == "http://localhost:8000/v1/chat/completions"
    assert request.headers_snapshot == {}
