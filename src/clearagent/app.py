"""A small, local-first FastAPI around the ClearAgent engine.

This is deliberately minimal: health probes and an invoke endpoint over the
LangGraph agent runtime. The hosted ClearAgent Studio product adds its own
planning, build, and chat contracts on top of the same engine.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from clearagent import __version__
from clearagent.agent import Agent
from clearagent.config import Settings
from clearagent.runtime.messages import Message, normalize_messages
from clearagent.runtime.providers.base import ProviderError
from clearagent.runtime.providers.registry import provider_for_model
from clearagent.runtime.tools import tool


@tool
def current_date() -> str:
    """Return today's date in ISO format."""
    from datetime import date

    return date.today().isoformat()


class InvokeRequest(BaseModel):
    message: str = Field(min_length=1, max_length=16_000)
    instruction: str | None = Field(default=None, max_length=8_000)
    model: str | None = Field(default=None, description="Model URI, e.g. openai:gpt-5.6-luna")
    tools: list[Literal["current_date"]] = Field(default_factory=list)


class InvokeResponse(BaseModel):
    answer: str
    model: str
    tool_calls: list[dict[str, Any]]
    latency_ms: int
    usage: dict[str, int]


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings()
    app = FastAPI(title="ClearAgent Engine", version=__version__)
    if resolved.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=resolved.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def build_agent(instruction: str | None, model: str | None, tools: Sequence[str]) -> Agent:
        model_uri = model or resolved.task_model
        available = {"current_date": current_date}
        return Agent(
            name="clearagent",
            model=model_uri,
            provider=provider_for_model(model_uri),
            system_prompt=instruction,
            tools=[available[name] for name in tools if name in available],
            trace=False,
        )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> dict[str, Any]:
        return {
            "status": "ok",
            "deterministic_mode": resolved.deterministic_mode,
            "task_model": resolved.task_model,
        }

    @app.post("/api/v1/invoke", response_model=InvokeResponse)
    def invoke(body: InvokeRequest) -> InvokeResponse:
        agent = build_agent(body.instruction, body.model, body.tools)
        try:
            result = agent.run(body.message)
        except ProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return InvokeResponse(
            answer=result.output,
            model=agent.model,
            tool_calls=result.tool_calls,
            latency_ms=result.latency_ms,
            usage=result.usage.model_dump(),
        )

    @app.post("/api/v1/invoke/stream")
    async def invoke_stream(body: InvokeRequest) -> StreamingResponse:
        if body.tools:
            raise HTTPException(status_code=422, detail="Streaming does not support tools.")
        agent = build_agent(body.instruction, body.model, [])
        messages = normalize_messages(agent.system_prompt, body.message)

        async def events() -> AsyncIterator[bytes]:
            try:
                async for chunk in _stream_text(agent, messages):
                    yield _sse({"type": "delta", "text": chunk})
                yield _sse({"type": "done"})
            except ProviderError as exc:
                yield _sse({"type": "error", "message": str(exc)})

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    return app


async def _stream_text(agent: Agent, messages: list[Message]) -> AsyncIterator[str]:
    import asyncio

    iterator = agent.stream_text(messages)

    def _next() -> tuple[bool, str]:
        try:
            return True, next(iterator)
        except StopIteration:
            return False, ""

    while True:
        has, chunk = await asyncio.to_thread(_next)
        if not has:
            return
        yield chunk


def _sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


app = create_app()
