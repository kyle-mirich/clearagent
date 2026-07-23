from collections.abc import Iterator
from importlib import resources
import json
import os
from pathlib import Path
from typing import Any, Literal, cast

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from clearagent.agent import Agent
from clearagent.chat.store import DEFAULT_CHAT_DB, ChatMessage, ChatSession, ChatStore
from clearagent.messages import Message
from clearagent.providers.registry import provider_for_model
from clearagent.reports import list_trace_runs_payload, trace_triage_payload
from clearagent.storage.sqlite import SQLiteTraceStore


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)


ProviderName = Literal["openrouter", "openai", "anthropic", "google", "local", "ollama"]


class ChatSettings(BaseModel):
    provider: ProviderName = "openrouter"
    model: str
    temperature: float = 0.0
    thinking: Literal["off", "low", "medium", "high"] = "off"


class ModelOption(BaseModel):
    id: str
    name: str


BuilderNodeKind = Literal["input", "agent", "tool", "prompt", "eval", "output"]


class BuilderNode(BaseModel):
    id: str
    kind: BuilderNodeKind
    label: str
    x: int
    y: int
    config: dict[str, Any] = Field(default_factory=dict)


class BuilderEdge(BaseModel):
    source: str
    target: str


class BuilderFlow(BaseModel):
    name: str
    description: str
    nodes: list[BuilderNode]
    edges: list[BuilderEdge]


class BuilderPlanRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=8_000)
    flow: BuilderFlow | None = None


class BuilderPlanResponse(BaseModel):
    message: str
    flow: BuilderFlow
    python: str


def create_chat_app(
    agent: Agent,
    *,
    chat_db_path: str | Path = DEFAULT_CHAT_DB,
    title: str | None = None,
    allow_settings_mutation: bool = False,
    settings_admin_token: str | None = None,
) -> FastAPI:
    """Create the local FastAPI chat, builder, settings, and trace-viewer backend.

    Runtime settings are read-only unless ``allow_settings_mutation`` is
    explicitly enabled. Callers exposing the app beyond local development are
    responsible for authentication and other deployment controls.
    """
    store = ChatStore(chat_db_path)
    runtime_settings = _settings_from_agent(agent)
    app = FastAPI(title=title or f"ClearAgent Chat - {agent.name}")
    static_dir = Path(str(resources.files("clearagent.chat").joinpath("static")))
    app.mount("/assets", StaticFiles(directory=str(static_dir)), name="chat_assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(str(static_dir.joinpath("index.html")))

    @app.get("/favicon.ico")
    def favicon() -> Response:
        return Response(status_code=204)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "agent": agent.name}

    @app.get("/api/agents")
    def list_agents() -> list[dict[str, str]]:
        return [{"name": agent.name}]

    @app.get("/api/settings", response_model=ChatSettings)
    def get_settings() -> ChatSettings:
        return runtime_settings

    @app.put("/api/settings", response_model=ChatSettings)
    def update_settings(
        settings: ChatSettings,
        admin_token: str | None = Header(None, alias="X-ClearAgent-Admin-Token"),
    ) -> ChatSettings:
        if not allow_settings_mutation:
            raise HTTPException(
                status_code=403,
                detail="Runtime settings mutation is disabled.",
            )
        if settings_admin_token and admin_token != settings_admin_token:
            raise HTTPException(status_code=403, detail="Invalid settings admin token.")
        next_model = f"{settings.provider}:{settings.model}"
        try:
            next_provider = provider_for_model(next_model)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        runtime_settings.provider = settings.provider
        runtime_settings.model = settings.model
        runtime_settings.temperature = settings.temperature
        runtime_settings.thinking = settings.thinking
        agent.model = next_model
        agent.temperature = runtime_settings.temperature
        agent.provider = next_provider
        return runtime_settings

    @app.get("/api/models", response_model=list[ModelOption])
    def list_models(provider: ProviderName = "openrouter") -> list[ModelOption]:
        return _list_models(provider)

    @app.get("/api/builder/flow", response_model=BuilderFlow)
    def get_builder_flow() -> BuilderFlow:
        return _default_builder_flow(agent)

    @app.post("/api/builder/plan", response_model=BuilderPlanResponse)
    def plan_builder_flow(request: BuilderPlanRequest) -> BuilderPlanResponse:
        flow = _plan_builder_flow(agent, request.instruction, request.flow)
        return BuilderPlanResponse(
            message=(
                "ClearAgent Builder connected the requested nodes and prepared "
                "a runnable Python sketch."
            ),
            flow=flow,
            python=_flow_to_python(flow),
        )

    @app.get("/api/triage/runs/{run_id}")
    def triage_run(run_id: str) -> dict:
        try:
            return trace_triage_payload(SQLiteTraceStore(agent.trace_db_path), run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/traces")
    def list_traces() -> list[dict]:
        return list_trace_runs_payload(SQLiteTraceStore(agent.trace_db_path))

    @app.post("/api/sessions", response_model=ChatSession)
    def create_session() -> ChatSession:
        return store.create_session(agent_name=agent.name)

    @app.get("/api/sessions", response_model=list[ChatSession])
    def list_sessions() -> list[ChatSession]:
        return store.list_sessions()

    @app.get("/api/sessions/{session_id}", response_model=ChatSession)
    def get_session(session_id: str) -> ChatSession:
        session = store.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Chat session not found.")
        return session

    @app.get("/api/sessions/{session_id}/messages", response_model=list[ChatMessage])
    def list_messages(session_id: str) -> list[ChatMessage]:
        if store.get_session(session_id) is None:
            raise HTTPException(status_code=404, detail="Chat session not found.")
        return store.list_messages(session_id)

    @app.post("/api/sessions/{session_id}/messages")
    def send_message(session_id: str, request: SendMessageRequest) -> StreamingResponse:
        if store.get_session(session_id) is None:
            raise HTTPException(status_code=404, detail="Chat session not found.")
        if not request.content.strip():
            raise HTTPException(status_code=400, detail="Message content is required.")

        def stream() -> Iterator[str]:
            store.add_message(session_id, role="user", content=request.content)
            assistant_parts: list[str] = []
            try:
                for chunk in agent.stream_text(
                    _messages_for_agent(agent, store.list_messages(session_id)),
                    extra=_request_extra(runtime_settings),
                ):
                    assistant_parts.append(chunk)
                    yield _sse_data(chunk)
            except Exception as exc:
                yield _sse_event("error", {"message": f"Request failed: {exc}"})
                return
            if assistant_parts:
                store.add_message(session_id, role="assistant", content="".join(assistant_parts))
            latest_run = SQLiteTraceStore(agent.trace_db_path).get_latest_run_for_agent(agent.name)
            if latest_run:
                yield _sse_event("trace", {"run_id": latest_run["id"]})
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Content-Type-Options": "nosniff",
            },
        )

    return app


def _messages_for_agent(agent: Agent, history: list[ChatMessage]) -> list[Message]:
    messages: list[Message] = []
    for message in history:
        messages.append(Message(role=message.role, content=message.content))
    return messages


def _sse_data(value: str) -> str:
    return f"data: {json.dumps(value)}\n\n"


def _sse_event(event: str, value: object) -> str:
    return f"event: {event}\ndata: {json.dumps(value)}\n\n"


def _settings_from_agent(agent: Agent) -> ChatSettings:
    provider, model = (
        agent.model.split(":", 1)
        if ":" in agent.model
        else ("openrouter", agent.model)
    )
    supported = {"openrouter", "openai", "anthropic", "google", "local", "ollama"}
    if provider not in supported:
        raise ValueError(f"Chat settings do not support provider {provider!r}.")
    provider_name = cast(ProviderName, provider)
    return ChatSettings(
        provider=cast(ProviderName, provider_name),
        model=model,
        temperature=agent.temperature or 0.0,
        thinking="off",
    )


def _request_extra(settings: ChatSettings) -> dict[str, object]:
    extra: dict[str, object] = {"stream": True}
    if settings.provider == "openrouter" and settings.thinking != "off":
        extra["reasoning"] = {"effort": settings.thinking}
    return extra


def _list_models(provider: str) -> list[ModelOption]:
    if provider == "openrouter":
        return _list_openrouter_models()
    if provider == "openai":
        return _list_openai_models()
    if provider == "anthropic":
        return _list_anthropic_models()
    if provider == "google":
        return [
            ModelOption(id="gemini-2.5-flash", name="Gemini 2.5 Flash"),
            ModelOption(id="gemini-2.5-pro", name="Gemini 2.5 Pro"),
        ]
    return []


def _list_openrouter_models() -> list[ModelOption]:
    fallback = [
        ModelOption(id="openai/gpt-4.1-mini", name="GPT-4.1 Mini via OpenRouter"),
        ModelOption(id="anthropic/claude-sonnet-4.5", name="Claude Sonnet via OpenRouter"),
        ModelOption(id="google/gemini-2.5-flash", name="Gemini 2.5 Flash via OpenRouter"),
    ]
    if not os.environ.get("OPENROUTER_API_KEY"):
        return fallback
    try:
        response = httpx.get("https://openrouter.ai/api/v1/models", timeout=5)
        response.raise_for_status()
        models = response.json().get("data") or []
        options = [
            ModelOption(id=item["id"], name=item.get("name") or item["id"])
            for item in models
            if isinstance(item, dict) and item.get("id")
        ]
        if options:
            return options
    except Exception:
        pass
    return fallback


def _list_openai_models() -> list[ModelOption]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        try:
            response = httpx.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=5,
            )
            response.raise_for_status()
            models = response.json().get("data") or []
            options = [
                ModelOption(id=item["id"], name=item["id"])
                for item in models
                if isinstance(item, dict) and item.get("id")
            ]
            if options:
                return options
        except Exception:
            pass
    return [
        ModelOption(id="gpt-4.1-mini", name="GPT-4.1 Mini"),
        ModelOption(id="gpt-4o-mini", name="GPT-4o Mini"),
    ]


def _list_anthropic_models() -> list[ModelOption]:
    fallback = [
        ModelOption(id="claude-sonnet-4-20250514", name="Claude Sonnet 4"),
        ModelOption(id="claude-opus-4-1-20250805", name="Claude Opus 4.1"),
        ModelOption(id="claude-3-5-haiku-20241022", name="Claude Haiku 3.5"),
    ]
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return fallback
    try:
        response = httpx.get(
            "https://api.anthropic.com/v1/models",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            timeout=5,
        )
        response.raise_for_status()
        models = response.json().get("data") or []
        options = [
            ModelOption(id=item["id"], name=item.get("display_name") or item["id"])
            for item in models
            if isinstance(item, dict) and item.get("id")
        ]
        if options:
            return options
    except Exception:
        pass
    return fallback


def _default_builder_flow(agent: Agent) -> BuilderFlow:
    return BuilderFlow(
        name=f"{agent.name} flow",
        description="A simple ClearAgent flow with user input, one agent, and final output.",
        nodes=[
            BuilderNode(
                id="input",
                kind="input",
                label="User input",
                x=96,
                y=180,
                config={"schema": "plain text"},
            ),
            BuilderNode(
                id="agent",
                kind="agent",
                label=agent.name,
                x=360,
                y=160,
                config={
                    "model": agent.model,
                    "system_prompt": agent.system_prompt or "You are a helpful ClearAgent agent.",
                    "temperature": agent.temperature or 0.0,
                },
            ),
            BuilderNode(
                id="output",
                kind="output",
                label="Final answer",
                x=660,
                y=180,
                config={"format": "text"},
            ),
        ],
        edges=[
            BuilderEdge(source="input", target="agent"),
            BuilderEdge(source="agent", target="output"),
        ],
    )


def _plan_builder_flow(agent: Agent, instruction: str, current_flow: BuilderFlow | None) -> BuilderFlow:
    lower = instruction.lower()
    base = current_flow if current_flow else _default_builder_flow(agent)
    core_nodes: list[BuilderNode] = [
        node.model_copy(deep=True)
        for node in base.nodes
        if node.kind in {"input", "agent", "output"}
    ]
    agent_node = next(node for node in core_nodes if node.kind == "agent")
    output_node = next(node for node in core_nodes if node.kind == "output")
    agent_node.config["system_prompt"] = _prompt_from_instruction(agent, instruction)
    agent_node.label = _agent_label_from_instruction(agent.name, lower)

    extra_nodes: list[BuilderNode] = []
    inserted_ids: list[str] = []
    if any(word in lower for word in ["tool", "lookup", "search", "refund", "order", "database"]):
        tool_label = _tool_label_from_instruction(lower)
        extra_nodes.append(
            BuilderNode(
                id="tool_lookup",
                kind="tool",
                label=tool_label,
                x=360,
                y=350,
                config={
                    "function": _slug(tool_label),
                    "description": f"Use this tool when the user asks about {tool_label.lower()}.",
                },
            )
        )
        inserted_ids.append("tool_lookup")

    if any(word in lower for word in ["classify", "route", "triage", "policy", "safety"]):
        extra_nodes.append(
            BuilderNode(
                id="prompt_policy",
                kind="prompt",
                label="Policy and routing prompt",
                x=360,
                y=40,
                config={
                    "template": (
                        "Classify the request, follow safety policy, and route to the "
                        "right tool before answering."
                    )
                },
            )
        )
        inserted_ids.append("prompt_policy")

    if any(word in lower for word in ["eval", "test", "quality", "safety", "guardrail"]):
        extra_nodes.append(
            BuilderNode(
                id="eval_quality",
                kind="eval",
                label="Safety and quality eval",
                x=660,
                y=350,
                config={
                    "checks": ["not_contains unsafe", "latency_under_ms 5000", "contains helpful"],
                },
            )
        )
        inserted_ids.append("eval_quality")

    nodes = [
        next(node for node in core_nodes if node.kind == "input"),
        agent_node,
        *extra_nodes,
        output_node,
    ]
    edges = [BuilderEdge(source="input", target="agent")]
    if "prompt_policy" in inserted_ids:
        edges.append(BuilderEdge(source="prompt_policy", target="agent"))
    if "tool_lookup" in inserted_ids:
        edges.append(BuilderEdge(source="agent", target="tool_lookup"))
        edges.append(BuilderEdge(source="tool_lookup", target="agent"))
    edges.append(BuilderEdge(source="agent", target="output"))
    if "eval_quality" in inserted_ids:
        edges.append(BuilderEdge(source="output", target="eval_quality"))

    return BuilderFlow(
        name=_flow_name_from_instruction(agent.name, lower),
        description=instruction.strip(),
        nodes=nodes,
        edges=edges,
    )


def _prompt_from_instruction(agent: Agent, instruction: str) -> str:
    existing = agent.system_prompt or "You are a helpful ClearAgent agent."
    return f"{existing}\n\nBuilder intent: {instruction.strip()}"


def _agent_label_from_instruction(default_name: str, instruction: str) -> str:
    if "support" in instruction:
        return "Support agent"
    if "research" in instruction:
        return "Research agent"
    if "sales" in instruction:
        return "Sales agent"
    return default_name


def _tool_label_from_instruction(instruction: str) -> str:
    if "refund" in instruction:
        return "Refund lookup tool"
    if "order" in instruction:
        return "Order lookup tool"
    if "search" in instruction or "research" in instruction:
        return "Search tool"
    return "Knowledge lookup tool"


def _flow_name_from_instruction(default_name: str, instruction: str) -> str:
    if "support" in instruction:
        return "Support automation flow"
    if "research" in instruction:
        return "Research assistant flow"
    if "sales" in instruction:
        return "Sales assistant flow"
    return f"{default_name} builder flow"


def _slug(value: str) -> str:
    chars = [char.lower() if char.isalnum() else "_" for char in value]
    return "_".join("".join(chars).split("_")).strip("_")


def _flow_to_python(flow: BuilderFlow) -> str:
    agent_node = next((node for node in flow.nodes if node.kind == "agent"), None)
    tool_nodes = [node for node in flow.nodes if node.kind == "tool"]
    model = agent_node.config.get("model", "openrouter:openai/gpt-4.1-mini") if agent_node else ""
    prompt = agent_node.config.get("system_prompt", "You are a helpful ClearAgent agent.") if agent_node else ""
    lines = ["from clearagent import create_agent, tool", ""]
    for node in tool_nodes:
        function_name = str(node.config.get("function") or _slug(node.label) or "lookup")
        lines.extend(
            [
                "@tool",
                f"def {function_name}(query: str) -> dict:",
                f'    """{node.config.get("description", node.label)}"""',
                '    return {"query": query, "status": "replace with your implementation"}',
                "",
            ]
        )
    tool_names = [str(node.config.get("function") or _slug(node.label) or "lookup") for node in tool_nodes]
    lines.extend(
        [
            "agent = create_agent(",
            f'    name="{_slug(flow.name) or "builder_agent"}",',
            f'    model="{model}",',
            f"    system_prompt={prompt!r},",
            f"    tools=[{', '.join(tool_names)}],",
            ")",
        ]
    )
    return "\n".join(lines)
