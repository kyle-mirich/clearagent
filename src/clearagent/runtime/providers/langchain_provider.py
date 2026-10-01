import hashlib
import json
import math
import os
import re
from collections.abc import Callable, Mapping, Sequence
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
        if request.response_format is not None and not self._native_json_schema:
            response = self.complete(request)
            if response.output_text:
                yield response.output_text
            return
        try:
            chat = self._configured_chat(request)
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
        bindings = self._request_bindings(request)
        if body.get("tools"):
            chat = self.chat_model.bind_tools(
                body["tools"], tool_choice=_bindable_tool_choice(body.get("tool_choice"))
            )
        if request.response_format is not None:
            bindings["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.response_format.name,
                    "strict": request.response_format.strict,
                    "schema": _harden_json_schema(request.response_format.json_schema),
                },
            }
        if bindings:
            chat = chat.bind(**bindings)
        return chat

    def _request_bindings(self, request: ProviderRequest) -> dict[str, Any]:
        # Request snapshots use a common OpenAI-style shape. Translate only
        # provider-specific options; standard generation kwargs pass through.
        reserved = {"model", "messages", "tools", "tool_choice", "response_format", "stream"}
        bindings = {
            key: value
            for key, value in request.body.items()
            if key not in reserved and value is not None
        }
        if self.provider_name == "google" and "max_tokens" in bindings:
            bindings.setdefault("max_output_tokens", bindings.pop("max_tokens"))
        if self.provider_name == "openrouter":
            extra_body = dict(bindings.pop("extra_body", {}) or {})
            for key in ("provider", "reasoning"):
                if key in bindings:
                    extra_body[key] = bindings.pop(key)
            if extra_body:
                bindings["extra_body"] = extra_body
        return bindings

    def _structured_chat(self, request: ProviderRequest) -> Runnable:
        response_format = request.response_format
        assert response_format is not None
        # include_raw wraps the model in a parallel runnable that does not
        # forward invocation kwargs. Configure a per-request model copy before
        # building that wrapper so concurrent requests retain their own options.
        fields = type(self.chat_model).model_fields
        updates: dict[str, Any] = {}
        extra: dict[str, Any] = {}
        for key, value in self._request_bindings(request).items():
            field_name = key if key in fields else next(
                (name for name, field in fields.items() if field.alias == key), None
            )
            if field_name is not None:
                updates[field_name] = value
            else:
                extra[key] = value
        if extra:
            if "model_kwargs" not in fields:
                raise ValueError(f"Unsupported structured output options: {', '.join(extra)}")
            updates["model_kwargs"] = {
                **getattr(self.chat_model, "model_kwargs", {}),
                **extra,
            }
        chat = self.chat_model.model_copy(update=updates)
        schema = dict(response_format.json_schema)
        schema.setdefault("title", response_format.name)
        return chat.with_structured_output(
            schema, method="function_calling", include_raw=True
        )

    def _complete_via_function_calling(self, request: ProviderRequest) -> ProviderResponse:
        try:
            result = self._structured_chat(request).invoke(
                _to_langchain_messages(request.body["messages"])
            )
            return self._structured_response(request, result)
        except Exception as exc:
            raise provider_error(request, f"{_exc_type(exc)}: {exc}") from exc

    async def _acomplete_via_function_calling(self, request: ProviderRequest) -> ProviderResponse:
        try:
            result = await self._structured_chat(request).ainvoke(
                _to_langchain_messages(request.body["messages"])
            )
            return self._structured_response(request, result)
        except Exception as exc:
            raise provider_error(request, f"{_exc_type(exc)}: {exc}") from exc

    def _structured_response(self, request: ProviderRequest, result: Any) -> ProviderResponse:
        if error := result.get("parsing_error"):
            raise error
        parsed = result["parsed"]
        usage = _usage_of(result["raw"])
        return ProviderResponse(
            provider=self.provider_name,
            model=request.model,
            raw={"structured_output": parsed, **_raw_usage(result["raw"], usage)},
            output_text=json.dumps(parsed, default=str),
            usage=usage,
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


def _bindable_tool_choice(tool_choice: Any) -> Any:
    # A name lets each model's bind_tools translate forced selection into its
    # native wire format without broadening the choice to an arbitrary tool.
    if isinstance(tool_choice, dict):
        function = tool_choice.get("function")
        if isinstance(function, dict) and function.get("name"):
            return function["name"]
        if tool_choice.get("type") == "tool" and tool_choice.get("name"):
            return tool_choice["name"]
    return tool_choice


def _to_langchain_messages(dump: list[dict[str, Any]]) -> list[Any]:
    messages: list[Any] = []
    for item in dump:
        role = item.get("role")
        content = item.get("content")
        if content is None:
            content = ""
        elif not isinstance(content, (str, list)):
            content = str(content)
        if role == "system":
            messages.append(SystemMessage(content=content))
        elif role == "user":
            messages.append(HumanMessage(content=content))
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
            messages.append(AIMessage(content=content, tool_calls=tool_calls))
        elif role == "tool":
            messages.append(ToolMessage(content=content, tool_call_id=item.get("tool_call_id", "")))
    return messages


def _to_provider_response(request: ProviderRequest, result: Any) -> ProviderResponse:
    if getattr(result, "invalid_tool_calls", None):
        raise provider_error(request, "model returned malformed tool calls.")
    content = result.content
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    tool_calls = [
        ToolCall(id=call.get("id", ""), name=call.get("name", ""), arguments=dict(call.get("args") or {}))
        for call in getattr(result, "tool_calls", []) or []
    ]
    metadata = getattr(result, "response_metadata", None)
    finish_reason = metadata.get("finish_reason") if isinstance(metadata, Mapping) else None
    usage = _usage_of(result)
    return ProviderResponse(
        provider=request.provider,
        model=request.model,
        raw={"finish_reason": finish_reason, **_raw_usage(result, usage)},
        output_text=str(content) if content else None,
        tool_calls=tool_calls,
        usage=usage,
        finish_reason=finish_reason or ("tool_calls" if tool_calls else "stop"),
    )


def _usage_of(result: Any) -> Usage | None:
    metadata = getattr(result, "usage_metadata", None)
    if not isinstance(metadata, Mapping):
        return None
    prompt = metadata.get("input_tokens")
    completion = metadata.get("output_tokens")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in (prompt, completion)
    ):
        return None
    assert isinstance(prompt, int) and isinstance(completion, int)
    total = metadata.get("total_tokens", prompt + completion)
    if not isinstance(total, int) or isinstance(total, bool) or total != prompt + completion:
        return None
    return Usage(prompt_tokens=prompt, completion_tokens=completion, total_tokens=total)


def _raw_usage(result: Any, usage: Usage | None) -> dict[str, Any]:
    raw: dict[str, Any] = {"usage_known": usage is not None}
    response_metadata = getattr(result, "response_metadata", None)
    sources = [getattr(result, "usage_metadata", None)]
    if isinstance(response_metadata, Mapping):
        sources.extend(response_metadata.get(key) for key in ("usage", "token_usage"))
    costs: dict[str, float] = {}
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        for key in ("cost", "total_cost"):
            value = source.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                continue
            try:
                amount = float(value)
            except OverflowError:
                continue
            if math.isfinite(amount):
                costs.setdefault(key, amount)
    if costs:
        raw["usage"] = costs
    return raw


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
        if model.startswith("gpt-5.6") or model == "gpt-6-luna":
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
