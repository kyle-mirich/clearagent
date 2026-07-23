# Providers

ClearAgent supports OpenAI-compatible request shapes, native Anthropic Messages,
native Google Gemini `generateContent`, and deterministic fake providers for
tests and examples.

Model URIs use `provider:model`:

- `openai:gpt-4.1-mini`
- `openrouter:anthropic/claude-sonnet-4.5`
- `anthropic:claude-sonnet-4-5`
- `google:gemini-2.5-flash`
- `local:llama3.1`
- `local:http://localhost:8000/v1?model=llama3.1`
- `ollama:llama3.1`

OpenRouter, local, and Ollama model URIs use the OpenAI-compatible adapter.
Anthropic and Google model URIs use native request/response shapes.

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

- OpenAI and OpenRouter: `response_format.type=json_schema`
- Anthropic: `output_config.format.type=json_schema`
- Google Gemini: `generationConfig.responseMimeType=application/json` with
  `responseJsonSchema`

Native Anthropic and Google adapters are unit-tested with mocked HTTP clients.
Live tests should use OpenRouter unless Anthropic or Google credentials are
explicitly provided for manual testing.

## Related Docs

- [Core Concepts](core-concepts.md)
- [Reference](reference.md)
