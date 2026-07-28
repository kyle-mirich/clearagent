# Providers

ClearAgent supports the native OpenAI Responses API, OpenAI-compatible request
shapes, native Anthropic Messages, native Google Gemini `generateContent`, and
deterministic fake providers for tests and examples.

Import provider contracts, response types, and supported adapters from the
public package entry point:

```python
from clearagent.providers import FakeProvider, ProviderResponse, ToolCall
```

Model URIs use `provider:model`:

- `openai:gpt-5.6-terra`
- `openrouter:anthropic/claude-sonnet-4.5`
- `anthropic:claude-sonnet-5`
- `google:gemini-2.5-flash`
- `local:llama3.1`
- `local:http://localhost:8000/v1?model=llama3.1`
- `ollama:llama3.1`

OpenAI model URIs use the canonical Responses API. OpenRouter, local, and
Ollama model URIs use the OpenAI-compatible Chat Completions adapter. Anthropic
and Google model URIs use native request/response shapes.

Agent requests omit `temperature` by default so the selected model can use its
provider-supported default; explicit temperature values remain available.
During tool loops, the OpenAI adapter sends every prior Responses output item
before function results, including opaque reasoning items, and the Anthropic
adapter sends the complete original assistant content sequence, including
thinking and redacted-thinking blocks.

The local chat queries OpenAI and Anthropic model catalogs when the matching
API key is available. Its offline fallback keeps older choices and includes
GPT-5.6 Sol, Terra, and Luna plus Claude Fable 5, Opus 5, Sonnet 5, and Haiku
4.5. The provider catalog remains authoritative when it is reachable.

`local:<model>` sends OpenAI-compatible requests to
`http://localhost:8000/v1` without an API key. Use
`local:<base-url>?model=<model>` when your local OpenAI-compatible server runs
at a different base URL. For example,
`local:http://localhost:1234/v1?model=llama3.1` sends requests to
`http://localhost:1234/v1/chat/completions` with `model` set to `llama3.1`.
URL-style local model URIs must include a non-empty `model` query value.

`ollama:<model>` sends OpenAI-compatible requests to
`http://localhost:11434/v1` without an API key.

Structured outputs are mapped per provider:

- OpenAI: `text.format.type=json_schema` on the Responses API
- OpenRouter: `response_format.type=json_schema`
- Anthropic: `output_config.format.type=json_schema`
- Google Gemini: `generationConfig.responseMimeType=application/json` with
  `responseJsonSchema`

All four hosted adapters are covered by mocked tests and sanitized live
recordings. The paid suite is bounded and opt-in; see
[Live Provider Compatibility](live-provider-compatibility.md).

## Model Discovery In Local Chat

`GET /api/models?provider=<provider>` backs the model picker in the local chat
app. When the matching API key is configured, ClearAgent queries the provider's
Models API on each request and returns its current catalog:

- `OPENAI_API_KEY` enables discovery from OpenAI's `/v1/models` endpoint.
- `ANTHROPIC_API_KEY` enables discovery from Anthropic's `/v1/models` endpoint;
  ClearAgent requests up to 1,000 entries, which is the provider's documented
  maximum page size. Anthropic returns newer releases first.
- `OPENROUTER_API_KEY` enables discovery from OpenRouter's `/api/v1/models`
  endpoint.

The response is authoritative for discovery, but it is not a compatibility or
account-access guarantee; endpoint, feature, and account support still belong
to the provider. If the key is absent, discovery fails, or the provider returns
no models, the picker uses a bundled fallback snapshot. The 0.1.0 snapshot
includes GPT-5.6 Sol, Terra, and Luna, plus Claude Fable 5, Opus 5, Sonnet 5,
and Haiku 4.5. Older fallback entries remain available for compatibility.
Applications can also pass any provider-supported model URI directly;
ClearAgent does not limit agent construction to the picker entries.

## Related Docs

- [Core Concepts](core-concepts.md)
- [Reference](reference.md)
- [Live Provider Compatibility](live-provider-compatibility.md)
