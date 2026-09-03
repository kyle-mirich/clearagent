from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from clearagent.runtime.messages import Message, dump_messages
from clearagent.runtime.tools import tool_schema


class ProviderError(RuntimeError):
    pass


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ResponseFormat(BaseModel):
    name: str
    json_schema: dict[str, Any]
    strict: bool = True


ResponseFormatInput = ResponseFormat | type[BaseModel] | dict[str, Any] | None


def normalize_response_format(response_format: ResponseFormatInput) -> ResponseFormat | None:
    if response_format is None:
        return None
    if isinstance(response_format, ResponseFormat):
        return response_format
    if isinstance(response_format, type) and issubclass(response_format, BaseModel):
        return ResponseFormat(
            name=response_format.__name__,
            json_schema=response_format.model_json_schema(),
            strict=True,
        )
    if isinstance(response_format, dict):
        if "schema" in response_format:
            if not isinstance(response_format["schema"], Mapping):
                raise TypeError("response_format schema must be a mapping.")
            return ResponseFormat(
                name=str(response_format.get("name") or "response"),
                json_schema=dict(response_format["schema"]),
                strict=bool(response_format.get("strict", True)),
            )
        return ResponseFormat(name="response", json_schema=response_format, strict=True)
    raise TypeError("response_format must be a Pydantic model, ResponseFormat, dict, or None.")


class ProviderRequest(BaseModel):
    provider: str
    model: str
    api_shape: Literal[
        "openai_chat_completions",
        "openai_responses",
        "anthropic_messages",
        "google_genai",
    ]
    body: dict[str, Any]
    endpoint: str | None = None
    headers_snapshot: dict[str, str] = Field(default_factory=dict)
    response_format: ResponseFormat | None = None


class ProviderResponse(BaseModel):
    provider: str
    model: str
    raw: dict[str, Any]
    output_text: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage | None = None
    finish_reason: str | None = None
    structured_output: Any = None

    @classmethod
    def fake_text(cls, text: str, *, model: str = "fake-model") -> "ProviderResponse":
        return cls(
            provider="fake",
            model=model,
            raw={"output_text": text},
            output_text=text,
            usage=Usage(),
            finish_reason="stop",
        )

    @classmethod
    def fake_tool_call(
        cls, tool_call: ToolCall, *, model: str = "fake-model"
    ) -> "ProviderResponse":
        return cls(
            provider="fake",
            model=model,
            raw={"tool_calls": [tool_call.model_dump()]},
            tool_calls=[tool_call],
            usage=Usage(),
            finish_reason="tool_calls",
        )


class Provider(Protocol):
    provider_name: str
    api_shape: str

    def build_request(
        self,
        *,
        model: str,
        messages: list[Message],
        tools: Sequence[Callable[..., Any]],
        tool_choice: str | dict[str, Any] | None,
        temperature: float | None,
        max_tokens: int | None,
        extra: dict[str, Any],
        response_format: ResponseFormatInput = None,
    ) -> ProviderRequest:
        ...

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        ...

    async def acomplete(self, request: ProviderRequest) -> ProviderResponse:
        ...

    def stream_text(self, request: ProviderRequest):
        ...


def build_openai_body(
    *,
    model: str,
    messages: list[Message],
    tools: Sequence[Callable[..., Any]],
    tool_choice: str | dict[str, Any] | None,
    temperature: float | None,
    max_tokens: int | None,
    extra: dict[str, Any],
    response_format: ResponseFormatInput = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"model": model, "messages": dump_messages(messages)}
    if tools:
        body["tools"] = [tool_schema(fn) for fn in tools]
    if tool_choice is not None:
        body["tool_choice"] = tool_choice
    if temperature is not None:
        body["temperature"] = temperature
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    normalized_response_format = normalize_response_format(response_format)
    if normalized_response_format is not None:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": normalized_response_format.name,
                "strict": normalized_response_format.strict,
                "schema": normalized_response_format.json_schema,
            },
        }
    body.update(extra)
    return body


class FakeProvider:
    provider_name = "fake"
    api_shape = "openai_chat_completions"

    def __init__(self, responses: list[ProviderResponse | Exception] | None = None):
        self.responses = responses or []
        self.completed_requests: list[ProviderRequest] = []

    def build_request(
        self,
        *,
        model: str,
        messages: list[Message],
        tools: Sequence[Callable[..., Any]],
        tool_choice: str | dict[str, Any] | None,
        temperature: float | None,
        max_tokens: int | None,
        extra: dict[str, Any],
        response_format: ResponseFormatInput = None,
    ) -> ProviderRequest:
        normalized_response_format = normalize_response_format(response_format)
        return ProviderRequest(
            provider=self.provider_name,
            model=model,
            api_shape="openai_chat_completions",
            response_format=normalized_response_format,
            body=build_openai_body(
                model=model,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                temperature=temperature,
                max_tokens=max_tokens,
                extra=extra,
                response_format=normalized_response_format,
            ),
        )

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.completed_requests.append(request)
        if not self.responses:
            return ProviderResponse.fake_text("")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def acomplete(self, request: ProviderRequest) -> ProviderResponse:
        return self.complete(request)

    def stream_text(self, request: ProviderRequest):
        response = self.complete(request)
        if response.output_text:
            yield response.output_text
