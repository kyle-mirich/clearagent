from collections.abc import Callable
from pathlib import Path
from typing import Any

from clearagent.agent import Agent
from clearagent.runtime.providers.base import Provider, ResponseFormatInput
from clearagent.runtime.providers.registry import provider_for_model
from clearagent.storage.protocol import TraceStore
from clearagent.storage.sqlite import DEFAULT_TRACE_DB


def create_agent(
    *,
    name: str,
    model: str,
    system_prompt: str | None = None,
    tools: list[Callable[..., Any]] | None = None,
    trace: bool = True,
    trace_db_path: str | Path = DEFAULT_TRACE_DB,
    trace_store: TraceStore | None = None,
    max_turns: int = 8,
    max_tokens: int | None = None,
    temperature: float | None = 0.0,
    provider: Provider | None = None,
    response_format: ResponseFormatInput = None,
) -> Agent:
    return Agent(
        name=name,
        model=model,
        system_prompt=system_prompt,
        tools=tools,
        trace=trace,
        trace_db_path=trace_db_path,
        trace_store=trace_store,
        max_turns=max_turns,
        max_tokens=max_tokens,
        temperature=temperature,
        provider=provider or provider_for_model(model),
        response_format=response_format,
    )
