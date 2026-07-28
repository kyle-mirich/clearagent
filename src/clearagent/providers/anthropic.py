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
    normalize_response_format,
)
from clearagent.providers.errors import provider_error, raise_for_status
from clearagent.tool import tool_schema


class AnthropicProvider:
    """Native Anthropic Messages API adapter using synchronous HTTP requests."""

    api_shape = "anthropic_messages"

    def __init__(
        self,
        *,
        provider_name: str = "anthropic",
        base_url: str = "https://api.anthropic.com/v1",
        api_key: str | None = None,
        api_key_env: str = "ANTHROPIC_API_KEY",
        client: httpx.Client | None = None,
        anthropic_version: str = "2023-06-01",
    ):
        self.provider_name = provider_name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.environ.get(api_key_env)
        self.client = client or httpx.Client(timeout=60.0)
        self.anthropic_version = anthropic_version

    def auth_headers_snapshot(self) -> dict[str, str]:
        headers = {"anthropic-version": self.anthropic_version}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

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
        body = _build_anthropic_body(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
            extra=extra,
            response_format=normalized_response_format,
        )
        return ProviderRequest(
            provider=self.provider_name,
            model=model,
            api_shape="anthropic_messages",
            endpoint=f"{self.base_url}/messages",
            headers_snapshot=self.auth_headers_snapshot(),
            body=body,
            response_format=normalized_response_format,
        )

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        try:
            response = self.client.post(
                request.endpoint or "",
                json=request.body,
                headers=_fresh_auth_headers(request.headers_snapshot, self.auth_headers_snapshot()),
            )
        except httpx.HTTPError as exc:
            raise provider_error(request, f"request failed: {exc}") from exc
        raise_for_status(request, response)
        try:
            raw = response.json()
            return _parse_anthropic_response(request, raw)
        except ProviderError:
            raise
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise provider_error(request, f"response parse failed: {exc}") from exc

    def stream_text(self, request: ProviderRequest):
        body = dict(request.body)
        body["stream"] = True
        try:
            with self.client.stream(
                "POST",
                request.endpoint or "",
                json=body,
                headers=_fresh_auth_headers(request.headers_snapshot, self.auth_headers_snapshot()),
            ) as response:
                raise_for_status(request, response)
                for line in response.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line.removeprefix("data:").strip()
                    if payload == "[DONE]":
                        break
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError as exc:
                        raise provider_error(
                            request, f"stream response parse failed: {exc}"
                        ) from exc
                    if not isinstance(data, dict):
                        raise provider_error(request, "stream response must contain JSON objects")
                    if data.get("type") == "error":
                        error = data.get("error") or {}
                        message = (
                            error.get("message", str(error))
                            if isinstance(error, dict)
                            else str(error)
                        )
                        raise provider_error(request, f"stream error: {message}")
                    if data.get("type") == "content_block_delta":
                        delta = data.get("delta") or {}
                        if delta.get("type") == "text_delta" and delta.get("text"):
                            yield delta["text"]
        except ProviderError:
            raise
        except httpx.HTTPError as exc:
            raise provider_error(request, f"stream request failed: {exc}") from exc


def _build_anthropic_body(
    *,
    model: str,
    messages: list[Message],
    tools: Sequence[Callable[..., Any]],
    tool_choice: str | dict[str, Any] | None,
    temperature: float | None,
    max_tokens: int | None,
    extra: dict[str, Any],
    response_format,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": _anthropic_messages(messages),
        "max_tokens": max_tokens or 4096,
    }
    system_parts = [
        message.content for message in messages if message.role == "system" and message.content
    ]
    if system_parts:
        body["system"] = "\n\n".join(str(part) for part in system_parts)
    if tools:
        body["tools"] = [_anthropic_tool(fn) for fn in tools]
    if tool_choice == "auto":
        body["tool_choice"] = {"type": "auto"}
    elif isinstance(tool_choice, dict):
        body["tool_choice"] = tool_choice
    if temperature is not None:
        body["temperature"] = temperature
    if response_format is not None:
        body["output_config"] = {
            "format": {
                "type": "json_schema",
                "schema": response_format.json_schema,
            }
        }
    body.update(extra)
    return body


def _anthropic_messages(messages: list[Message]) -> list[dict[str, Any]]:
    converted = []
    for message in messages:
        if message.role == "system":
            continue
        if message.role == "tool":
            converted.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": message.tool_call_id or "",
                            "content": str(message.content or ""),
                        }
                    ],
                }
            )
            continue
        if message.role == "assistant" and message.metadata.get("tool_calls"):
            preserved_content = message.metadata.get("anthropic_content")
            if isinstance(preserved_content, list):
                converted.append({"role": "assistant", "content": preserved_content})
                continue
            content: list[dict[str, Any]] = []
            if message.content:
                content.append({"type": "text", "text": str(message.content)})
            for call in message.metadata["tool_calls"]:
                function = call.get("function") or {}
                arguments = function.get("arguments") or {}
                if isinstance(arguments, str):
                    arguments = json.loads(arguments)
                if not isinstance(arguments, dict):
                    arguments = {}
                content.append(
                    {
                        "type": "tool_use",
                        "id": call.get("id") or "",
                        "name": function.get("name") or "",
                        "input": arguments,
                    }
                )
            converted.append({"role": "assistant", "content": content})
            continue
        converted.append(
            {
                "role": "assistant" if message.role == "assistant" else "user",
                "content": str(message.content or ""),
            }
        )
    return converted


def _anthropic_tool(fn: Callable[..., Any]) -> dict[str, Any]:
    schema = tool_schema(fn)["function"]
    return {
        "name": schema["name"],
        "description": schema.get("description", ""),
        "input_schema": schema["parameters"],
    }


def _parse_anthropic_response(request: ProviderRequest, raw: dict[str, Any]) -> ProviderResponse:
    text_parts = []
    tool_calls = []
    for block in raw.get("content") or []:
        if block.get("type") == "text":
            text_parts.append(block.get("text") or "")
        if block.get("type") == "tool_use":
            tool_calls.append(
                ToolCall(
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    arguments=block.get("input") or {},
                )
            )
    usage_raw = raw.get("usage") or {}
    prompt_tokens = usage_raw.get("input_tokens", 0)
    completion_tokens = usage_raw.get("output_tokens", 0)
    return ProviderResponse(
        provider=request.provider,
        model=request.model,
        raw=raw,
        output_text="".join(text_parts) or None,
        tool_calls=tool_calls,
        usage=Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
        finish_reason=raw.get("stop_reason"),
    )


def _fresh_auth_headers(
    stored_headers: dict[str, str], fresh_headers: dict[str, str]
) -> dict[str, str]:
    headers = dict(stored_headers)
    if headers.get("x-api-key") == "[REDACTED]" and fresh_headers.get("x-api-key"):
        headers["x-api-key"] = fresh_headers["x-api-key"]
    return headers
