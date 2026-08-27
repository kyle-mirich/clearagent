from clearagent.runtime.providers.base import Provider, ProviderRequest
from clearagent.runtime.providers.langchain_provider import (
    LangchainChatProvider,
    auth_snapshot_for,
    build_langchain_chat_model,
)
from clearagent.runtime.providers.model_uri import parse_model_uri

_PROVIDER_ENDPOINTS = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "anthropic": "https://api.anthropic.com/v1/messages",
}


def _endpoint_for(provider: str, base_url: str | None) -> str | None:
    if base_url:
        return f"{base_url.rstrip('/')}/chat/completions"
    return _PROVIDER_ENDPOINTS.get(provider)


def provider_for_model(model_uri: str) -> Provider:
    parsed = parse_model_uri(model_uri)
    return LangchainChatProvider(
        provider_name=parsed.provider,
        chat_model=build_langchain_chat_model(
            provider=parsed.provider,
            model=parsed.model,
            base_url=parsed.base_url,
        ),
        auth_snapshot=auth_snapshot_for(parsed.provider),
        # OpenAI-family endpoints accept native JSON-schema response formats;
        # Anthropic falls back to function-calling structured output.
        native_json_schema=parsed.provider != "anthropic",
        endpoint=_endpoint_for(parsed.provider, parsed.base_url),
    )


def provider_for_request(request: ProviderRequest) -> Provider:
    return provider_for_model(f"{request.provider}:{request.model}")
