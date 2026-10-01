"""Offline migration contracts for application defaults and actual SDK payloads."""

import pytest

from clearagent.builds.module import Build
from clearagent.builds.pipeline import PipelineSettings
from clearagent.config import Settings
from clearagent.runtime.messages import Message
from clearagent.runtime.providers.langchain_provider import (
    LangchainChatProvider,
    _to_langchain_messages,
    build_langchain_chat_model,
)


MODEL_ROLES = ("planner", "synthetic", "task", "judge", "reflection")


def test_all_application_model_defaults_use_gpt6_luna(monkeypatch):
    for role in MODEL_ROLES:
        monkeypatch.delenv(f"CLEARAGENT_{role.upper()}_MODEL", raising=False)
    settings = Settings(_env_file=None)
    for configured in (settings, PipelineSettings(), Build(settings).pipeline_settings):
        assert {getattr(configured, f"{role}_model") for role in MODEL_ROLES} == {
            "openai:gpt-6-luna"
        }


@pytest.mark.parametrize("role", MODEL_ROLES)
def test_explicit_model_environment_override_is_preserved(role, monkeypatch):
    monkeypatch.setenv(f"CLEARAGENT_{role.upper()}_MODEL", "openai:gpt-5.6-luna")
    settings = Settings(_env_file=None)
    assert getattr(Build(settings).pipeline_settings, f"{role}_model") == "openai:gpt-5.6-luna"


@pytest.mark.parametrize("model", ["gpt-5.6-luna", "gpt-6-luna"])
def test_luna_payload_preserves_none_sampling_tools_and_structured_output(model):
    chat = build_langchain_chat_model(provider="openai", model=model)
    provider = LangchainChatProvider(provider_name="openai", chat_model=chat)
    messages = [Message(role="user", content="Look up the answer")]

    def lookup() -> str:
        """Find an answer."""
        return "found"

    request = provider.build_request(
        model=model,
        messages=messages,
        tools=[lookup],
        tool_choice="auto",
        temperature=0.2,
        max_tokens=512,
        extra={"top_p": 0.8},
        response_format={"type": "json_schema", "json_schema": {
            "name": "Answer", "strict": True,
            "schema": {"type": "object", "properties": {"answer": {"type": "string"}},
                       "required": ["answer"], "additionalProperties": False},
        }},
    )
    bound = provider._configured_chat(request)
    payload = chat._get_request_payload(
        _to_langchain_messages(request.body["messages"]), **bound.kwargs
    )
    assert payload["model"] == model
    assert payload["reasoning"] == {"effort": "none"}
    assert payload["temperature"] == 0.2
    assert payload["top_p"] == 0.8
    assert payload["max_output_tokens"] == 512
    assert payload["store"] is False
    assert payload["text"]["format"]["type"] == "json_schema"
    assert payload["tools"][0]["type"] == "function"
    assert payload["tools"][0]["name"] == "lookup"
    assert payload["tool_choice"] == "auto"


def test_sol_does_not_inherit_luna_only_none_reasoning():
    chat = build_langchain_chat_model(provider="openai", model="gpt-6.1-sol")
    assert chat.reasoning_effort is None
