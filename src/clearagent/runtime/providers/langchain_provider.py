import hashlib
import json
import os
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables import Runnable
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from clearagent.runtime.providers.base import (
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    ResponseFormat,
    ResponseFormatInput,
    ToolCall,
    Usage,
    build_openai_body,
    normalize_response_format,
)


class LangchainChatProvider:
    """Provider backed by LangChain chat models.

    Keeps ClearAgent's Provider protocol (build_request -> complete /
    stream_text) so the Agent loop, grounded chat, pipeline completions, and
    trace redaction stay unchanged, while model IO runs through LangChain.
    """

    api_shape = "openai_chat_completions"

    def __init__(
        self,
        *,
        provider_name: str,
        chat_model: BaseChatModel,
        auth_snapshot: dict[str, str] | None = None,
        native_json_schema: bool = True,
        endpoint: str | None = None,
    ):
        self.provider_name = provider_name
        self.chat_model = chat_model
        self._auth_snapshot = auth_snapshot or {}
        # Providers without native JSON-schema response support fall back to
        # function-calling structured output; the JSON text is re-serialized so
        # downstream validation and traces behave identically.
        self._native_json_schema = native_json_schema
        self._endpoint = endpoint

    def auth_headers_snapshot(self) -> dict[str, str]:
        return dict(self._auth_snapshot)

    def build_request(
        self,
        *,
        model: str,
        messages: list,
        tools: Sequence[Callable[..., Any]],
        tool_choice: str | dict[str, Any] | None,
        temperature: float | None,
        max_tokens: int | None,
        extra: dict[str, Any],
        response_format: ResponseFormatInput = None,
    ) -> ProviderRequest:
        normalized = normalize_response_format(response_format)
        body = build_openai_body(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
            extra=extra,
            response_format=normalized,
        )
        return ProviderRequest(
            provider=self.provider_name,
            model=model,
            api_shape="openai_chat_completions",
            endpoint=self._endpoint,
            headers_snapshot=self.auth_headers_snapshot(),
            response_format=normalized,
            body=body,
        )

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        fixture_mode = os.environ.get("CLEARAGENT_OPENAI_FIXTURE_MODE")
        fixture_path = _fixture_path(request)
        if fixture_mode == "replay":
            if fixture_path is None or not fixture_path.exists():
                name = fixture_path.name if fixture_path else "unknown"
                raise provider_error(request, f"missing recorded fixture {name}")
            return ProviderResponse.model_validate(json.loads(fixture_path.read_text()))
        if request.response_format is not None and not self._native_json_schema:
            response = self._complete_via_function_calling(request)
        else:
            response = self._invoke(request)
        if fixture_mode == "record" and fixture_path is not None:
            fixture_path.parent.mkdir(parents=True, exist_ok=True)
            fixture_path.write_text(json.dumps(response.model_dump(mode="json"), indent=2) + "\n")
        return response

    async def acomplete(self, request: ProviderRequest) -> ProviderResponse:
        fixture_mode = os.environ.get("CLEARAGENT_OPENAI_FIXTURE_MODE")
        fixture_path = _fixture_path(request)
        if fixture_mode == "replay":
            if fixture_path is None or not fixture_path.exists():
                name = fixture_path.name if fixture_path else "unknown"
                raise provider_error(request, f"missing recorded fixture {name}")
            return ProviderResponse.model_validate(json.loads(fixture_path.read_text()))
        if request.response_format is not None and not self._native_json_schema:
            response = await self._acomplete_via_function_calling(request)
        else:
            response = await self._ainvoke(request)
        if fixture_mode == "record" and fixture_path is not None:
            fixture_path.parent.mkdir(parents=True, exist_ok=True)
            fixture_path.write_text(json.dumps(response.model_dump(mode="json"), indent=2) + "\n")
        return response

    def stream_text(self, request: ProviderRequest):
        chat = self.chat_model
        try:
            for chunk in chat.stream(_to_langchain_messages(request.body["messages"])):
                text = _chunk_text(chunk)
                if text:
                    yield text
        except ProviderError:
            raise
        except Exception as exc:
            raise provider_error(request, f"stream failed: {exc}") from exc

    def _invoke(self, request: ProviderRequest) -> ProviderResponse:
        body = request.body
        chat = self._configured_chat(request)
        try:
            result = chat.invoke(_to_langchain_messages(body["messages"]))
        except Exception as exc:
            raise provider_error(request, f"{_exc_type(exc)}: {exc}") from exc
        return _to_provider_response(request, result)

    async def _ainvoke(self, request: ProviderRequest) -> ProviderResponse:
        body = request.body
        chat = self._configured_chat(request)
        try:
            result = await chat.ainvoke(_to_langchain_messages(body["messages"]))
        except Exception as exc:
            raise provider_error(request, f"{_exc_type(exc)}: {exc}") from exc
        return _to_provider_response(request, result)

    def _configured_chat(self, request: ProviderRequest) -> Runnable:
        body = request.body
        chat: Runnable = self.chat_model
        bindings: dict[str, Any] = {}
        if body.get("tools"):
            bindings["tools"] = body["tools"]
            if body.get("tool_choice") is not None:
                bindings["tool_choice"] = _bindable_tool_choice(body["tool_choice"], body["tools"])
        elif request.response_format is not None:
            bindings["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.response_format.name,
                    "strict": request.response_format.strict,
                    "schema": _harden_json_schema(request.response_format.json_schema),
                },
            }
        if body.get("temperature") is not None:
            bindings["temperature"] = body["temperature"]
        if bindings:
            chat = chat.bind(**bindings)
        return chat

    def _complete_via_function_calling(self, request: ProviderRequest) -> ProviderResponse:
        response_format: ResponseFormat | None = request.response_format
        assert response_format is not None
        structured = self.chat_model.with_structured_output(
            response_format.json_schema, method="function_calling"
        )
        try:
            parsed = structured.invoke(_to_langchain_messages(request.body["messages"]))
        except Exception as exc:
            raise provider_error(request, f"{_exc_type(exc)}: {exc}") from exc
        text = json.dumps(parsed, default=str)
        return ProviderResponse(
            provider=self.provider_name,
            model=request.model,
            raw={"structured_output": parsed},
            output_text=text,
            usage=_usage_of(None),
            finish_reason="stop",
        )

    async def _acomplete_via_function_calling(self, request: ProviderRequest) -> ProviderResponse:
        response_format: ResponseFormat | None = request.response_format
        assert response_format is not None
        structured = self.chat_model.with_structured_output(
            response_format.json_schema, method="function_calling"
        )
        try:
            parsed = await structured.ainvoke(_to_langchain_messages(request.body["messages"]))
        except Exception as exc:
            raise provider_error(request, f"{_exc_type(exc)}: {exc}") from exc
        text = json.dumps(parsed, default=str)
        return ProviderResponse(
            provider=self.provider_name,
            model=request.model,
            raw={"structured_output": parsed},
            output_text=text,
            usage=_usage_of(None),
            finish_reason="stop",
        )


def _harden_json_schema(node: Any) -> Any:
    """Copy a JSON schema and satisfy OpenAI strict-mode object constraints.

    Strict structured output requires every object to declare
    additionalProperties=false; Pydantic's generated schemas omit it.
    """
    if isinstance(node, dict):
        hardened = {key: _harden_json_schema(value) for key, value in node.items()}
        if hardened.get("type") == "object" or "properties" in hardened:
            hardened.setdefault("additionalProperties", False)
        return hardened
    if isinstance(node, list):
        return [_harden_json_schema(item) for item in node]
    return node


def provider_error(request: ProviderRequest, message: str) -> ProviderError:
    return ProviderError(f"{request.provider}:{request.model} {message}")


def _exc_type(exc: Exception) -> str:
    return exc.__class__.__name__


def _bindable_tool_choice(tool_choice: Any, tools: Sequence[Any]) -> Any:
    # OpenAI-style {"type": "function", "function": {"name": ...}} forcing maps
    # to LangChain's provider-agnostic "any" semantics; named selection is
    # handled natively where supported.
    if isinstance(tool_choice, dict):
        return "any"
    return tool_choice


def _to_langchain_messages(dump: list[dict[str, Any]]) -> list[Any]:
    messages: list[Any] = []
    for item in dump:
        role = item.get("role")
        content = item.get("content")
        if role == "system":
            messages.append(SystemMessage(content=str(content or "")))
        elif role == "user":
            messages.append(HumanMessage(content=str(content or "")))
        elif role == "assistant":
            tool_calls = []
            for call in item.get("tool_calls", []):
                raw_arguments = call["function"].get("arguments")
                if isinstance(raw_arguments, str):
                    arguments = json.loads(raw_arguments or "{}")
                else:
                    arguments = dict(raw_arguments or {})
                tool_calls.append(
                    {
                        "name": call["function"]["name"],
                        "args": arguments,
                        "id": call.get("id", ""),
                        "type": "tool_call",
                    }
                )
            messages.append(AIMessage(content=str(content) if content else "", tool_calls=tool_calls))
        elif role == "tool":
            messages.append(ToolMessage(content=str(content or ""), tool_call_id=item.get("tool_call_id", "")))
    return messages


def _to_provider_response(request: ProviderRequest, result: Any) -> ProviderResponse:
    content = result.content
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    tool_calls = [
        ToolCall(id=call.get("id", ""), name=call.get("name", ""), arguments=dict(call.get("args") or {}))
        for call in getattr(result, "tool_calls", []) or []
    ]
    return ProviderResponse(
        provider=request.provider,
        model=request.model,
        raw={"finish_reason": (getattr(result, "response_metadata", {}) or {}).get("finish_reason")},
        output_text=str(content) if content else None,
        tool_calls=tool_calls,
        usage=_usage_of(result),
        finish_reason=(getattr(result, "response_metadata", {}) or {}).get("finish_reason")
        or ("tool_calls" if tool_calls else "stop"),
    )


def _usage_of(result: Any) -> Usage | None:
    metadata = getattr(result, "usage_metadata", None) or {}
    if not metadata:
        return Usage()
    prompt = int(metadata.get("input_tokens", 0) or 0)
    completion = int(metadata.get("output_tokens", 0) or 0)
    total = int(metadata.get("total_tokens", 0) or prompt + completion)
    return Usage(prompt_tokens=prompt, completion_tokens=completion, total_tokens=total)


def _chunk_text(chunk: Any) -> str:
    content = chunk.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


_FIXTURE_ID_PATTERN = re.compile(r"\b(?:src|source|proj|run)_[0-9a-f]{12,}\b")


def _fixture_path(request: ProviderRequest) -> Path | None:
    root = os.environ.get("CLEARAGENT_OPENAI_FIXTURE_DIR")
    if not root:
        return None
    normalized = _normalise_dynamic_ids(request.body)
    digest = hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return Path(root) / f"{digest}.json"


def _normalise_dynamic_ids(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalise_dynamic_ids(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalise_dynamic_ids(item) for item in value]
    if isinstance(value, str):
        return _FIXTURE_ID_PATTERN.sub("<dynamic-id>", value)
    return value


def build_langchain_chat_model(
    *,
    provider: str,
    model: str,
    base_url: str | None = None,
) -> BaseChatModel:
    """Construct a LangChain chat model for a parsed ClearAgent model URI.

    Credentials default to a placeholder so construction never fails offline;
    the provider surfaces real authentication errors at request time, matching
    the previous httpx-backed behavior.
    """
    if provider == "openai":
        kwargs: dict[str, Any] = {
            "model": model,
            "timeout": 120,
            "api_key": SecretStr(os.environ.get("OPENAI_API_KEY") or "not-needed"),
            "use_responses_api": True,
            "store": False,
        }
        if model.startswith("gpt-5.6"):
            kwargs["reasoning_effort"] = "none"
        return ChatOpenAI(**kwargs)
    if provider == "anthropic":
        # langchain-anthropic declares these fields dynamically; mypy cannot
        # see them even though they are valid at runtime.
        return ChatAnthropic(
            model_name=model,
            default_request_timeout=120,
            api_key=SecretStr(os.environ.get("ANTHROPIC_API_KEY") or "not-needed"),
            stop=None,
        )  # type: ignore[call-arg]
    if provider == "google":
        return ChatGoogleGenerativeAI(
            model=model,
            timeout=120,
            google_api_key=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "not-needed",
        )
    if provider in {"openrouter", "local", "ollama"}:
        resolved_base_url = base_url or _default_base_url(provider)
        api_key_env = _api_key_env(provider)
        api_key = (os.environ.get(api_key_env) if api_key_env else None) or "not-needed"
        return ChatOpenAI(
            model=model,
            base_url=resolved_base_url,
            api_key=SecretStr(api_key),
            timeout=120,
        )
    raise ValueError(f"No default provider is available for {provider!r} yet.")


def auth_snapshot_for(provider: str) -> dict[str, str]:
    key_env = _api_key_env(provider)
    key = os.environ.get(key_env) if key_env else None
    header = {"google": "x-goog-api-key", "anthropic": "x-api-key"}.get(provider, "authorization")
    return {header: key} if key else {}


def _default_base_url(provider: str) -> str:
    if provider == "openrouter":
        return "https://openrouter.ai/api/v1"
    if provider == "local":
        return "http://localhost:8000/v1"
    if provider == "ollama":
        return "http://localhost:11434/v1"
    return "https://api.openai.com/v1"


def _api_key_env(provider: str) -> str | None:
    if provider == "openrouter":
        return "OPENROUTER_API_KEY"
    if provider == "openai":
        return "OPENAI_API_KEY"
    if provider == "anthropic":
        return "ANTHROPIC_API_KEY"
    if provider == "google":
        return "GEMINI_API_KEY"
    return None
