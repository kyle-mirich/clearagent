from clearagent.providers.anthropic import AnthropicProvider
from clearagent.providers.base import Provider, ProviderRequest
from clearagent.providers.google import GoogleGenAIProvider
from clearagent.providers.model_uri import parse_model_uri
from clearagent.providers.openai import OpenAIResponsesProvider
from clearagent.providers.openai_compatible import OpenAICompatibleProvider


def provider_for_model(model_uri: str) -> Provider:
    parsed = parse_model_uri(model_uri)
    if parsed.provider == "anthropic":
        return AnthropicProvider()
    if parsed.provider == "google":
        return GoogleGenAIProvider()
    if parsed.provider == "openai":
        return OpenAIResponsesProvider()
    if parsed.provider in {"openrouter", "local", "ollama"}:
        base_url = _openai_compatible_base_url(parsed.provider, parsed.base_url)
        api_key_env = _openai_compatible_api_key_env(parsed.provider)
        return OpenAICompatibleProvider(
            provider_name=parsed.provider,
            base_url=base_url,
            api_key_env=api_key_env,
        )
    raise ValueError(f"No default provider is available for {parsed.provider!r} yet.")


def provider_for_request(request: ProviderRequest) -> Provider:
    return provider_for_model(f"{request.provider}:{request.model}")


def _openai_compatible_base_url(provider: str, parsed_base_url: str | None) -> str:
    if parsed_base_url:
        return parsed_base_url
    if provider == "openrouter":
        return "https://openrouter.ai/api/v1"
    if provider == "local":
        return "http://localhost:8000/v1"
    if provider == "ollama":
        return "http://localhost:11434/v1"
    return "https://api.openai.com/v1"


def _openai_compatible_api_key_env(provider: str) -> str | None:
    if provider == "openrouter":
        return "OPENROUTER_API_KEY"
    return None
