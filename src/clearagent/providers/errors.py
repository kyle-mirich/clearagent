import httpx

from clearagent.providers.base import ProviderError, ProviderRequest


def raise_for_status(request: ProviderRequest, response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = response_error_message(response)
        raise provider_error(request, f"HTTP {response.status_code}: {detail}") from exc


def response_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if isinstance(error, str):
            return error
    return response.text


def provider_error(request: ProviderRequest, message: str) -> ProviderError:
    return ProviderError(f"{request.provider}:{request.model} {message}")
