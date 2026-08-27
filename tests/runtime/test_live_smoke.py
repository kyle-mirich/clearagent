"""Live smoke tests for the LangGraph/LangChain runtime.

Run manually against real provider credentials:

    set -a; source .env; set +a
    CLEARAGENT_LIVE_SMOKE=1 uv run pytest -q tests/runtime/test_live_smoke.py -x
"""

import json
import os

import pytest

from clearagent.agent import Agent
from clearagent.runtime.messages import Message
from clearagent.runtime.providers.langchain_provider import LangchainChatProvider
from clearagent.runtime.providers.registry import provider_for_model
from clearagent.runtime.tools import tool

pytestmark = pytest.mark.skipif(
    os.environ.get("CLEARAGENT_LIVE_SMOKE") != "1",
    reason="Live smoke tests require CLEARAGENT_LIVE_SMOKE=1 and provider credentials.",
)

MODEL = "openai:gpt-5.6-luna"


def test_live_completion_and_stream():
    provider = provider_for_model(MODEL)
    request = provider.build_request(
        model="gpt-5.6-luna",
        messages=[Message(role="user", content="Reply with exactly: LANGCHAIN OK")],
        tools=[],
        tool_choice=None,
        temperature=0.0,
        max_tokens=2000,
        extra={},
    )

    response = provider.complete(request)
    assert response.output_text, "live completion returned no text"
    assert response.usage is not None and response.usage.total_tokens > 0

    streamed = "".join(provider.stream_text(request))
    assert streamed.strip(), "live stream returned no text"


def test_live_tool_loop_through_langgraph_agent(tmp_path):
    @tool
    def support_tier(customer_plan: str) -> str:
        """Return the support tier for a customer plan."""
        return "priority" if customer_plan == "enterprise" else "standard"

    agent = Agent(
        name="smoke-router",
        model=MODEL,
        provider=LangchainChatProvider(provider_name="openai", chat_model=provider_for_model(MODEL).chat_model),
        system_prompt=(
            "You must call support_tier once with customer_plan='enterprise', "
            "then answer with exactly: TIER=priority"
        ),
        tools=[support_tier],
        trace=False,
        trace_db_path=tmp_path / "traces.sqlite",
        max_turns=4,
    )

    result = agent.run("Which support tier does the enterprise plan get?")

    assert "TIER=priority" in result.output
    assert any(call["name"] == "support_tier" for call in result.tool_calls)
    assert result.usage.total_tokens > 0


def test_live_structured_output():
    from pydantic import BaseModel

    class Sentiment(BaseModel):
        label: str
        confidence: float

    provider = provider_for_model(MODEL)
    request = provider.build_request(
        model="gpt-5.6-luna",
        messages=[Message(role="user", content="Sentiment of: 'I love this product'. Return JSON.")],
        tools=[],
        tool_choice=None,
        temperature=0.0,
        max_tokens=2000,
        extra={},
        response_format=Sentiment,
    )
    response = provider.complete(request)

    assert response.output_text is not None
    parsed = json.loads(response.output_text)
    assert parsed["label"] in {"positive", "negative", "neutral"}
