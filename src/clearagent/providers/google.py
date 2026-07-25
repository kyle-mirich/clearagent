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


class GoogleGenAIProvider:
    """Native Google Gemini generateContent adapter using synchronous HTTP requests."""

    api_shape = "google_genai"

    def __init__(
        self,
        *,
        provider_name: str = "google",
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        api_key: str | None = None,
        api_key_env: str = "GEMINI_API_KEY",
        client: httpx.Client | None = None,
    ):
        self.provider_name = provider_name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.environ.get(api_key_env) or os.environ.get("GOOGLE_API_KEY")
        self.client = client or httpx.Client(timeout=60.0)

    def auth_headers_snapshot(self) -> dict[str, str]:
        return {"x-goog-api-key": self.api_key} if self.api_key else {}

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
        body = _build_google_body(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            extra=extra,
            response_format=normalized_response_format,
        )
        return ProviderRequest(
            provider=self.provider_name,
            model=model,
            api_shape="google_genai",
            endpoint=f"{self.base_url}/models/{model}:generateContent",
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
            return _parse_google_response(request, raw)
        except ProviderError:
            raise
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise provider_error(request, f"response parse failed: {exc}") from exc

    def stream_text(self, request: ProviderRequest):
        endpoint = (request.endpoint or "").replace(":generateContent", ":streamGenerateContent")
        endpoint = f"{endpoint}{'&' if '?' in endpoint else '?'}alt=sse"
        try:
            with self.client.stream(
                "POST",
                endpoint,
                json=request.body,
                headers=_fresh_auth_headers(request.headers_snapshot, self.auth_headers_snapshot()),
            ) as response:
                raise_for_status(request, response)
                for line in response.iter_lines():
                    if not line:
                        continue
                    if not line.startswith("data:"):
                        if line.startswith(("event:", ":")):
                            continue
                        raise provider_error(request, "stream response was not SSE framed")
                    payload = line.removeprefix("data:").strip()
                    if payload == "[DONE]":
                        break
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError as exc:
                        raise provider_error(request, f"stream response parse failed: {exc}") from exc
                    if not isinstance(data, dict):
                        raise provider_error(request, "stream response must contain JSON objects")
                    if data.get("error"):
                        error = data["error"]
                        message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
                        raise provider_error(request, f"stream error: {message}")
                    for part in _candidate_parts(data):
                        if text := part.get("text"):
                            yield text
        except ProviderError:
            raise
        except httpx.HTTPError as exc:
            raise provider_error(request, f"stream request failed: {exc}") from exc


GoogleProvider = GoogleGenAIProvider


def _build_google_body(
    *,
    model: str,
    messages: list[Message],
    tools: Sequence[Callable[..., Any]],
    temperature: float | None,
    max_tokens: int | None,
    extra: dict[str, Any],
    response_format,
) -> dict[str, Any]:
    body: dict[str, Any] = {"contents": _google_contents(messages)}
    system_text = "\n\n".join(
        str(message.content) for message in messages if message.role == "system" and message.content
    )
    if system_text:
        body["systemInstruction"] = {"parts": [{"text": system_text}]}
    generation_config: dict[str, Any] = {}
    if temperature is not None:
        generation_config["temperature"] = temperature
    if max_tokens is not None:
        generation_config["maxOutputTokens"] = max_tokens
    if response_format is not None:
        generation_config["responseMimeType"] = "application/json"
        generation_config["responseJsonSchema"] = response_format.json_schema
    if generation_config:
        body["generationConfig"] = generation_config
    if tools:
        body["tools"] = [{"functionDeclarations": [_google_tool(fn) for fn in tools]}]
    body.update(extra)
    return body


def _google_contents(messages: list[Message]) -> list[dict[str, Any]]:
    contents: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            continue
        if message.role == "tool":
            function_response: dict[str, Any] = {
                "name": message.name or "",
                "response": {"result": message.content},
            }
            if message.tool_call_id:
                function_response["id"] = message.tool_call_id
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": function_response
                        }
                    ],
                }
            )
            continue
        role = "model" if message.role == "assistant" else "user"
        parts: list[dict[str, Any]] = []
        if message.content:
            parts.append({"text": str(message.content)})
        for call in message.metadata.get("tool_calls") or []:
            function = call.get("function") or {}
            function_call: dict[str, Any] = {
                "name": function.get("name", ""),
                "args": function.get("arguments") or {},
            }
            if call.get("id"):
                function_call["id"] = call["id"]
            part: dict[str, Any] = {"functionCall": function_call}
            provider_data = call.get("provider_data") or {}
            if provider_data.get("thoughtSignature"):
                part["thoughtSignature"] = provider_data["thoughtSignature"]
            parts.append(part)
        if not parts:
            parts.append({"text": ""})
        contents.append({"role": role, "parts": parts})
    return contents


def _google_tool(fn: Callable[..., Any]) -> dict[str, Any]:
    schema = tool_schema(fn)["function"]
    return {
        "name": schema["name"],
        "description": schema.get("description", ""),
        "parameters": schema["parameters"],
    }


def _parse_google_response(request: ProviderRequest, raw: dict[str, Any]) -> ProviderResponse:
    text_parts = []
    tool_calls = []
    for index, part in enumerate(_candidate_parts(raw)):
        if text := part.get("text"):
            text_parts.append(text)
        if function_call := part.get("functionCall"):
            provider_data = {}
            if part.get("thoughtSignature"):
                provider_data["thoughtSignature"] = part["thoughtSignature"]
            tool_calls.append(
                ToolCall(
                    id=function_call.get("id") or function_call.get("name") or f"call_{index}",
                    name=function_call.get("name") or "",
                    arguments=function_call.get("args") or {},
                    provider_data=provider_data,
                )
            )
    usage_raw = raw.get("usageMetadata") or {}
    prompt_tokens = usage_raw.get("promptTokenCount", 0)
    completion_tokens = usage_raw.get("candidatesTokenCount", 0)
    return ProviderResponse(
        provider=request.provider,
        model=request.model,
        raw=raw,
        output_text="".join(text_parts) or None,
        tool_calls=tool_calls,
        usage=Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=usage_raw.get("totalTokenCount", prompt_tokens + completion_tokens),
        ),
        finish_reason=_finish_reason(raw),
    )


def _candidate_parts(raw: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = raw.get("candidates") or []
    if not candidates:
        return []
    return ((candidates[0].get("content") or {}).get("parts")) or []


def _finish_reason(raw: dict[str, Any]) -> str | None:
    candidates = raw.get("candidates") or []
    if not candidates:
        return None
    return candidates[0].get("finishReason")


def _fresh_auth_headers(
    stored_headers: dict[str, str], fresh_headers: dict[str, str]
) -> dict[str, str]:
    headers = dict(stored_headers)
    if headers.get("x-goog-api-key") == "[REDACTED]" and fresh_headers.get("x-goog-api-key"):
        headers["x-goog-api-key"] = fresh_headers["x-goog-api-key"]
    return headers
