from urllib.parse import parse_qs, urlsplit, urlunsplit

from pydantic import BaseModel


class ModelURI(BaseModel):
    """Parsed provider, model, endpoint override, and provider request shape."""

    provider: str
    model: str
    base_url: str | None = None
    api_shape: str


API_SHAPES = {
    "openai": "openai_responses",
    "openrouter": "openai_chat_completions",
    "local": "openai_chat_completions",
    "ollama": "openai_chat_completions",
    "anthropic": "anthropic_messages",
    "google": "google_genai",
}


def parse_model_uri(value: str) -> ModelURI:
    """Parse and validate a ``provider:model`` identifier."""
    if ":" not in value:
        raise ValueError("Model URI must use provider:model format.")
    provider, model = value.split(":", 1)
    if not provider or not model:
        raise ValueError("Model URI must use provider:model format.")
    if provider not in API_SHAPES:
        raise ValueError(f"Unsupported model provider '{provider}'.")
    base_url = None
    if provider == "local" and model.startswith(("http://", "https://")):
        parsed_url = urlsplit(model)
        query = parse_qs(parsed_url.query, keep_blank_values=True)
        model_names = query.get("model") or []
        if not model_names or not model_names[0].strip():
            raise ValueError("Local URL model URI must include a non-empty model query value.")
        model = model_names[0]
        base_url = urlunsplit(
            (
                parsed_url.scheme,
                parsed_url.netloc,
                parsed_url.path,
                "",
                parsed_url.fragment,
            )
        )
    return ModelURI(
        provider=provider, model=model, base_url=base_url, api_shape=API_SHAPES[provider]
    )
