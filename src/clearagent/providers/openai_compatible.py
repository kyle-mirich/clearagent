import json
import os
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from clearagent.messages import Message
from clearagent.providers.base import (
    ProviderRequest,
    ProviderResponse,
    ProviderError,
    ResponseFormatInput,
    ToolCall,
    Usage,
    build_openai_body,
    normalize_response_format,
)
from clearagent.providers.errors import provider_error, raise_for_status


class OpenAICompatibleProvider:
    """Adapter for OpenAI-compatible chat-completions HTTP endpoints."""

    api_shape = "openai_chat_completions"

    def __init__(
        self,
        *,
        provider_name: str,
        base_url: str = "https://api.openai.com/v1",
        api_key: str | None = None,
        api_key_env: str | None = None,
        client: httpx.Client | None = None,
    ):
        self.provider_name = provider_name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or (os.environ.get(api_key_env) if api_key_env else None)
        self.client = client or httpx.Client(timeout=60.0)

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
        headers = {"authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        normalized_response_format = normalize_response_format(response_format)
        return ProviderRequest(
            provider=self.provider_name,
            model=model,
            api_shape="openai_chat_completions",
            endpoint=f"{self.base_url}/chat/completions",
            headers_snapshot=headers,
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

    def auth_headers_snapshot(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        headers = _fresh_authorization_headers(request.headers_snapshot, self.auth_headers_snapshot())
        try:
            response = self.client.post(request.endpoint or "", json=request.body, headers=headers)
        except httpx.HTTPError as exc:
            raise provider_error(request, f"request failed: {exc}") from exc
        raise_for_status(request, response)
        return _parse_openai_response(request, response)

    def stream_text(self, request: ProviderRequest):
        headers = _fresh_authorization_headers(request.headers_snapshot, self.auth_headers_snapshot())
        body = dict(request.body)
        body["stream"] = True
        try:
            with self.client.stream("POST", request.endpoint or "", json=body, headers=headers) as response:
                raise_for_status(request, response)
                for line in response.iter_lines():
                    if not line:
                        continue
                    if not line.startswith("data:"):
                        continue
                    payload = line.removeprefix("data:").strip()
                    if payload == "[DONE]":
                        break
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError as exc:
                        raise provider_error(request, f"stream response parse failed: {exc}") from exc
                    if error := data.get("error"):
                        message = error.get("message") if isinstance(error, dict) else str(error)
                        raise provider_error(request, f"stream failed: {message}")
                    for choice in data.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if content := delta.get("content"):
                            yield content
        except ProviderError:
            raise
        except httpx.HTTPError as exc:
            raise provider_error(request, f"stream request failed: {exc}") from exc


def _parse_openai_response(request: ProviderRequest, response: httpx.Response) -> ProviderResponse:
    try:
        raw = response.json()
        choice = raw["choices"][0]
        message = choice.get("message", {})
        tool_calls = []
        for call in message.get("tool_calls") or []:
            function = call.get("function", {})
            arguments = function.get("arguments") or "{}"
            tool_calls.append(
                ToolCall(
                    id=call.get("id", ""),
                    name=function.get("name", ""),
                    arguments=_parse_tool_arguments(request, arguments),
                )
            )
        usage_raw = raw.get("usage") or {}
        return ProviderResponse(
            provider=request.provider,
            model=request.model,
            raw=raw,
            output_text=message.get("content"),
            tool_calls=tool_calls,
            usage=Usage(**usage_raw) if usage_raw else None,
            finish_reason=choice.get("finish_reason"),
        )
    except ProviderError:
        raise
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise provider_error(request, f"response parse failed: {exc}") from exc


def _parse_tool_arguments(request: ProviderRequest, arguments: str) -> dict[str, Any]:
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError as exc:
        raise provider_error(request, f"tool arguments parse failed: {exc}") from exc
    if not isinstance(parsed, dict):
        raise provider_error(request, "tool arguments must be a JSON object")
    return parsed


def _fresh_authorization_headers(
    stored_headers: dict[str, str], fresh_headers: dict[str, str]
) -> dict[str, str]:
    auth_value = stored_headers.get("authorization") if stored_headers else None
    if auth_value and "[REDACTED]" not in auth_value:
        return {"Authorization": auth_value}
    fresh_auth = fresh_headers.get("authorization")
    return {"Authorization": fresh_auth} if fresh_auth else {}
