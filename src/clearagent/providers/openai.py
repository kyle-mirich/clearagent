import json
import os
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from clearagent.messages import Message
from clearagent.providers.base import (
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    ResponseFormatInput,
    ToolCall,
    Usage,
    normalize_response_format,
)
from clearagent.providers.errors import provider_error, raise_for_status
from clearagent.tool import tool_schema


class OpenAIResponsesProvider:
    """Native OpenAI Responses API adapter using synchronous HTTP requests."""

    provider_name = "openai"
    api_shape = "openai_responses"

    def __init__(
        self,
        *,
        base_url: str = "https://api.openai.com/v1",
        api_key: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        client: httpx.Client | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.environ.get(api_key_env)
        self.client = client or httpx.Client(timeout=60.0)

    def auth_headers_snapshot(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.api_key}"} if self.api_key else {}

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
        body: dict[str, Any] = {"model": model, "input": _responses_input(messages)}
        if tools:
            body["tools"] = [_responses_tool(fn) for fn in tools]
        if tool_choice is not None:
            body["tool_choice"] = tool_choice
        if temperature is not None:
            body["temperature"] = temperature
        if max_tokens is not None:
            body["max_output_tokens"] = max_tokens
        if normalized_response_format is not None:
            body["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": normalized_response_format.name,
                    "schema": normalized_response_format.json_schema,
                    "strict": normalized_response_format.strict,
                }
            }
        body.update(extra)
        return ProviderRequest(
            provider=self.provider_name,
            model=model,
            api_shape="openai_responses",
            endpoint=f"{self.base_url}/responses",
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
            return _parse_openai_responses_response(request, raw)
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
                    if not line or line.startswith(("event:", ":")):
                        continue
                    if not line.startswith("data:"):
                        raise provider_error(request, "stream response was not SSE framed")
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
                    event_type = data.get("type")
                    if event_type == "response.output_text.delta" and data.get("delta"):
                        yield data["delta"]
                    elif event_type in {"error", "response.failed"}:
                        error = data.get("error") or data.get("response") or data
                        message = (
                            error.get("message", str(error))
                            if isinstance(error, dict)
                            else str(error)
                        )
                        raise provider_error(request, f"stream error: {message}")
        except ProviderError:
            raise
        except httpx.HTTPError as exc:
            raise provider_error(request, f"stream request failed: {exc}") from exc


def _responses_input(messages: list[Message]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.tool_call_id or "",
                    "output": str(message.content or ""),
                }
            )
            continue
        if message.role == "assistant" and message.metadata.get("tool_calls"):
            if message.content:
                items.append({"role": "assistant", "content": message.content})
            for call in message.metadata["tool_calls"]:
                function = call.get("function") or {}
                arguments = function.get("arguments") or {}
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments, separators=(",", ":"))
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call.get("id") or "",
                        "name": function.get("name") or "",
                        "arguments": arguments,
                    }
                )
            continue
        items.append(
            {
                "role": message.role,
                "content": message.content if message.content is not None else "",
            }
        )
    return items


def _responses_tool(fn: Callable[..., Any]) -> dict[str, Any]:
    function = tool_schema(fn)["function"]
    return {
        "type": "function",
        "name": function["name"],
        "description": function.get("description", ""),
        "parameters": function["parameters"],
    }


def _parse_openai_responses_response(
    request: ProviderRequest, raw: dict[str, Any]
) -> ProviderResponse:
    text_parts = []
    tool_calls = []
    for item in raw.get("output") or []:
        if item.get("type") == "message":
            for content in item.get("content") or []:
                if content.get("type") == "output_text" and content.get("text"):
                    text_parts.append(content["text"])
        elif item.get("type") == "function_call":
            arguments = item.get("arguments") or "{}"
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError as exc:
                    raise provider_error(request, f"tool arguments parse failed: {exc}") from exc
            if not isinstance(arguments, dict):
                raise provider_error(request, "tool arguments must be a JSON object")
            tool_calls.append(
                ToolCall(
                    id=item.get("call_id") or item.get("id") or "",
                    name=item.get("name") or "",
                    arguments=arguments,
                )
            )
    usage_raw = raw.get("usage") or {}
    input_tokens = usage_raw.get("input_tokens", 0)
    output_tokens = usage_raw.get("output_tokens", 0)
    usage = None
    if usage_raw:
        usage = Usage(
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            total_tokens=usage_raw.get("total_tokens", input_tokens + output_tokens),
        )
    return ProviderResponse(
        provider=request.provider,
        model=request.model,
        raw=raw,
        output_text="".join(text_parts) or None,
        tool_calls=tool_calls,
        usage=usage,
        finish_reason=raw.get("status"),
    )


def _fresh_auth_headers(
    stored_headers: dict[str, str], fresh_headers: dict[str, str]
) -> dict[str, str]:
    auth_value = stored_headers.get("authorization") if stored_headers else None
    if auth_value and "[REDACTED]" not in auth_value:
        return {"Authorization": auth_value}
    fresh_auth = fresh_headers.get("authorization")
    return {"Authorization": fresh_auth} if fresh_auth else {}
