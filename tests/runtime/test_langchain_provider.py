import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from pydantic import Field

from clearagent import Agent
from clearagent.runtime.messages import Message
from clearagent.runtime.providers.base import ProviderError
from clearagent.runtime.providers.langchain_provider import (
    LangchainChatProvider,
    _to_langchain_messages,
    _to_provider_response,
    _usage_of,
    build_langchain_chat_model,
)
from clearagent.storage.sqlite import SQLiteTraceStore


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


class CapturingChatModel(GenericFakeChatModel):
    invocations: list[dict] = Field(default_factory=list)
    message_inputs: list = Field(default_factory=list)
    tool_bindings: list[dict] = Field(default_factory=list)
    structured_setups: list[dict] = Field(default_factory=list)
    parsing_error: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    model_kwargs: dict = Field(default_factory=dict)

    def invoke(self, input, *args, **kwargs):
        self.invocations.append(kwargs)
        self.message_inputs.append(input)
        return AIMessage(content="done")

    async def ainvoke(self, input, *args, **kwargs):
        self.invocations.append(kwargs)
        self.message_inputs.append(input)
        return AIMessage(content="done")

    def stream(self, input, *args, **kwargs):
        self.invocations.append(kwargs)
        self.message_inputs.append(input)
        yield AIMessageChunk(content="done")

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        self.tool_bindings.append({"tools": tools, "tool_choice": tool_choice})
        return self.bind(tools=tools, tool_choice=tool_choice, **kwargs)

    def with_structured_output(self, schema, **kwargs):
        self.structured_setups.append(kwargs)

        def respond(messages, **options):
            configured = {"temperature": self.temperature, "max_tokens": self.max_tokens, "top_p": self.top_p}
            self.invocations.append(
                {**{key: value for key, value in configured.items() if value is not None},
                 **self.model_kwargs, **options}
            )
            parsed = {"answer": "done"}
            if not kwargs.get("include_raw"):
                return parsed
            return {
                "raw": AIMessage(
                    content="",
                    usage_metadata={"input_tokens": 3, "output_tokens": 5, "total_tokens": 8},
                    response_metadata={"usage": {"cost": 0.125}},
                ),
                "parsed": parsed,
                "parsing_error": ValueError(self.parsing_error) if self.parsing_error else None,
            }

        return RunnableLambda(respond)


def _invoke_mode(provider, request, mode):
    if mode == "async":
        return asyncio.run(provider.acomplete(request))
    if mode == "stream":
        return "".join(provider.stream_text(request))
    return provider.complete(request)


@pytest.mark.parametrize("mode", ["sync", "async", "stream"])
@pytest.mark.parametrize("family", ["openrouter", "google"])
def test_request_options_reach_model_for_all_invocation_modes(mode, family):
    chat = CapturingChatModel(messages=iter([]))
    provider = LangchainChatProvider(provider_name=family, chat_model=chat)
    extra = {"top_p": 0.8, "stop": ["STOP"]}
    if family == "openrouter":
        extra.update(
            provider={"sort": "throughput"},
            reasoning={"effort": "none"},
            extra_body={"route": "fallback"},
        )
    request = provider.build_request(
        model="test-model",
        messages=[Message(role="user", content="hi")],
        tools=[],
        tool_choice=None,
        temperature=0.3,
        max_tokens=17,
        extra=extra,
        response_format={"type": "object", "properties": {"answer": {"type": "string"}}},
    )

    _invoke_mode(provider, request, mode)

    options = chat.invocations[0]
    assert options["temperature"] == 0.3
    assert options["max_output_tokens" if family == "google" else "max_tokens"] == 17
    assert options["top_p"] == 0.8
    assert options["stop"] == ["STOP"]
    assert options["response_format"]["type"] == "json_schema"
    if family == "openrouter":
        assert options["extra_body"] == {
            "route": "fallback",
            "provider": {"sort": "throughput"},
            "reasoning": {"effort": "none"},
        }
        assert "provider" not in options
        assert "reasoning" not in options


def test_anthropic_tool_request_uses_native_schema_and_choice():
    model = build_langchain_chat_model(provider="anthropic", model="claude-sonnet-4-5")
    provider = LangchainChatProvider(provider_name="anthropic", chat_model=model)

    def lookup(ticket: str) -> str:
        """Look up a ticket."""
        return ticket

    request = provider.build_request(
        model="claude-sonnet-4-5",
        messages=[Message(role="user", content="look up T1")],
        tools=[lookup],
        tool_choice="auto",
        temperature=0.0,
        max_tokens=19,
        extra={},
    )
    configured = provider._configured_chat(request)
    payload = model._get_request_payload(
        _to_langchain_messages(request.body["messages"]), **configured.kwargs
    )

    assert payload["max_tokens"] == 19
    assert payload["tool_choice"] == {"type": "auto"}
    assert payload["tools"] == [
        {
            "name": "lookup",
            "description": "Look up a ticket.",
            "input_schema": {
                "type": "object",
                "properties": {"ticket": {"type": "string"}},
                "required": ["ticket"],
            },
        }
    ]


def test_named_tool_selection_preserves_name_and_response_format():
    chat = CapturingChatModel(messages=iter([]))
    provider = LangchainChatProvider(provider_name="openai", chat_model=chat)

    def lookup(ticket: str) -> str:
        return ticket

    def unrelated() -> str:
        return "other"

    request = provider.build_request(
        model="test-model",
        messages=[Message(role="user", content="look up T1")],
        tools=[lookup, unrelated],
        tool_choice={"type": "function", "function": {"name": "lookup"}},
        temperature=0.0,
        max_tokens=19,
        extra={},
        response_format={"type": "object", "properties": {"answer": {"type": "string"}}},
    )

    provider.complete(request)

    assert chat.tool_bindings[0]["tool_choice"] == "lookup"
    assert chat.invocations[0]["response_format"]["type"] == "json_schema"


@pytest.mark.parametrize("mode", ["sync", "async", "stream"])
def test_structured_fallback_preserves_options_and_raw_usage(mode):
    chat = CapturingChatModel(messages=iter([]))
    provider = LangchainChatProvider(
        provider_name="anthropic", chat_model=chat, native_json_schema=False
    )
    request = provider.build_request(
        model="test-model",
        messages=[Message(role="user", content="respond as JSON")],
        tools=[],
        tool_choice=None,
        temperature=0.2,
        max_tokens=23,
        extra={"top_p": 0.7},
        response_format={"type": "object", "properties": {"answer": {"type": "string"}}},
    )

    response = _invoke_mode(provider, request, mode)

    assert chat.structured_setups[0]["include_raw"] is True
    assert chat.invocations[0] == {"temperature": 0.2, "max_tokens": 23, "top_p": 0.7}
    if mode == "stream":
        assert json.loads(response) == {"answer": "done"}
    else:
        assert json.loads(response.output_text) == {"answer": "done"}
        assert response.usage.total_tokens == 8
        assert response.raw["usage_known"] is True
        assert response.raw["usage"] == {"cost": 0.125}


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_structured_fallback_surfaces_parser_errors(mode):
    chat = CapturingChatModel(messages=iter([]), parsing_error="malformed tool output")
    provider = LangchainChatProvider(
        provider_name="anthropic", chat_model=chat, native_json_schema=False
    )
    request = provider.build_request(
        model="test-model",
        messages=[Message(role="user", content="respond as JSON")],
        tools=[],
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        extra={},
        response_format={"type": "object", "properties": {"answer": {"type": "string"}}},
    )

    with pytest.raises(ProviderError, match="malformed tool output"):
        _invoke_mode(provider, request, mode)


@pytest.mark.parametrize("mode", ["sync", "async", "stream"])
def test_real_anthropic_structured_wrapper_preserves_request_options(monkeypatch, mode):
    model = build_langchain_chat_model(provider="anthropic", model="claude-sonnet-4-5")
    payloads = []

    def generate(chat, messages, stop=None, run_manager=None, **kwargs):
        payload = chat._get_request_payload(messages, stop=stop, **kwargs)
        payloads.append(payload)
        result = AIMessage(
            content="",
            tool_calls=[
                {"id": "call_1", "name": payload["tools"][0]["name"], "args": {"answer": "done"}}
            ],
            usage_metadata={"input_tokens": 3, "output_tokens": 5, "total_tokens": 8},
        )
        return ChatResult(generations=[ChatGeneration(message=result)])

    async def agenerate(chat, messages, stop=None, run_manager=None, **kwargs):
        return generate(chat, messages, stop=stop, run_manager=run_manager, **kwargs)

    monkeypatch.setattr(type(model), "_generate", generate)
    monkeypatch.setattr(type(model), "_agenerate", agenerate)
    provider = LangchainChatProvider(
        provider_name="anthropic", chat_model=model, native_json_schema=False
    )
    request = provider.build_request(
        model="claude-sonnet-4-5",
        messages=[Message(role="user", content="respond as JSON")],
        tools=[],
        tool_choice=None,
        temperature=0.2,
        max_tokens=23,
        extra={"top_p": 0.7, "stop": ["END"], "service_tier": "auto"},
        response_format={
            "name": "Answer",
            "schema": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
            },
        },
    )

    response = _invoke_mode(provider, request, mode)

    assert payloads[0]["max_tokens"] == 23
    assert payloads[0]["temperature"] == 0.2
    assert payloads[0]["top_p"] == 0.7
    assert payloads[0]["stop_sequences"] == ["END"]
    assert payloads[0]["service_tier"] == "auto"
    assert model.max_tokens != 23
    assert model.temperature is None
    assert model.model_kwargs == {}
    if mode != "stream":
        assert response.usage.total_tokens == 8


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {},
        {"input_tokens": 1},
        {"output_tokens": 2},
        {"input_tokens": -1, "output_tokens": 2},
        {"input_tokens": True, "output_tokens": 2},
        {"input_tokens": "1", "output_tokens": 2},
        {"input_tokens": 1.5, "output_tokens": 2},
        {"input_tokens": 1, "output_tokens": 2, "total_tokens": -1},
        {"input_tokens": 1, "output_tokens": 2, "total_tokens": "3"},
        {"input_tokens": 1, "output_tokens": 2, "total_tokens": 0},
    ],
)
def test_incomplete_or_malformed_usage_is_unknown(metadata):
    assert _usage_of(SimpleNamespace(usage_metadata=metadata)) is None


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"input_tokens": 0, "output_tokens": 0}, 0),
        ({"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}, 0),
        ({"input_tokens": 3, "output_tokens": 5}, 8),
        ({"input_tokens": 3, "output_tokens": 5, "total_tokens": 8}, 8),
    ],
)
def test_usage_preserves_measured_zero_and_complete_counts(metadata, expected):
    usage = _usage_of(SimpleNamespace(usage_metadata=metadata))
    assert usage is not None
    assert usage.total_tokens == expected


@pytest.mark.parametrize("metadata", [None, {}, {"input_tokens": 0}, {"input_tokens": 0, "output_tokens": 0}])
def test_response_marks_usage_provenance(metadata):
    provider = _provider()
    request = provider.build_request(
        model="test-model", messages=[], tools=[], tool_choice=None,
        temperature=None, max_tokens=None, extra={},
    )
    result = SimpleNamespace(
        content="done", tool_calls=[], usage_metadata=metadata, response_metadata={}
    )

    response = _to_provider_response(request, result)

    assert response.raw["usage_known"] is (response.usage is not None)
    assert response.raw["usage_known"] is bool(metadata and "output_tokens" in metadata)


@pytest.mark.parametrize("source", ["usage_metadata", "usage", "token_usage"])
@pytest.mark.parametrize("value", [0, 0.125, -1, float("inf"), float("nan"), True, "0.125"])
def test_response_preserves_only_valid_provider_reported_costs(source, value):
    provider = _provider()
    request = provider.build_request(
        model="test-model", messages=[], tools=[], tool_choice=None,
        temperature=None, max_tokens=None, extra={},
    )
    costs = {"cost": value, "total_cost": value, "authorization": "private"}
    result = SimpleNamespace(
        content="done", tool_calls=[],
        usage_metadata=costs if source == "usage_metadata" else None,
        response_metadata={} if source == "usage_metadata" else {source: costs},
    )

    response = _to_provider_response(request, result)

    expected = {"cost": value, "total_cost": value} if value in (0, 0.125) and not isinstance(value, bool) else {}
    assert response.raw.get("usage", {}) == expected
    assert response.raw["usage_known"] is False


@pytest.mark.parametrize("mode", ["run", "stream"])
def test_agent_preserves_multimodal_message_blocks(mode):
    blocks = [
        {"type": "text", "text": "Describe this image."},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,fixture"}},
    ]
    chat = CapturingChatModel(messages=iter([]))
    agent = Agent(
        name="vision", model="openai:test-model", trace=False,
        provider=LangchainChatProvider(provider_name="openai", chat_model=chat),
    )
    messages = [Message(role="user", content=blocks)]

    if mode == "run":
        assert agent.run(messages).output == "done"
    else:
        assert "".join(agent.stream_text(messages)) == "done"

    assert chat.message_inputs[0][0].content == blocks


@pytest.mark.parametrize("role", ["system", "user", "assistant", "tool"])
def test_message_translation_preserves_content_blocks_for_every_role(role):
    blocks = [{"type": "text", "text": "Content block"}]
    translated = _to_langchain_messages([
        {"role": role, "content": blocks, "tool_call_id": "call_1"}
    ])
    assert translated[0].content == blocks


@pytest.mark.parametrize("trace", [False, True])
def test_agent_rejects_malformed_provider_tool_calls(tmp_path, trace):
    provider = _provider(AIMessage(
        content="",
        invalid_tool_calls=[{
            "id": "call_1", "name": "lookup", "args": "{invalid",
            "error": "invalid JSON", "type": "invalid_tool_call",
        }],
    ))
    db_path = tmp_path / "invalid-tool.sqlite"
    agent = Agent(
        name="invalid-tool", model="openai:test-model", provider=provider,
        trace=trace, trace_db_path=db_path,
    )

    with pytest.raises(ProviderError, match="malformed tool calls"):
        agent.run("Look up the value.")

    if trace:
        store = SQLiteTraceStore(db_path)
        run = store.list_runs()[0]
        assert run["status"] == "error"
        call = store.get_model_call_for_turn(run["id"], 0)
        assert call["status"] == "error"
        assert json.loads(call["error_json"])["type"] == "ProviderError"
