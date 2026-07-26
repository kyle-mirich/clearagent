import json

import httpx

from clearagent import create_agent, tool
from clearagent.providers.anthropic import AnthropicProvider
from clearagent.providers.openai import OpenAIResponsesProvider


@tool
def lookup_order(order_id: str) -> dict:
    """Look up an order by ID."""
    return {"order_id": order_id, "status": "shipped"}


def test_openai_tool_loop_replays_every_response_output_item_unchanged():
    prior_output = [
        {
            "id": "rs_123",
            "type": "reasoning",
            "summary": [],
            "encrypted_content": "opaque-encrypted-reasoning",
        },
        {
            "id": "fc_123",
            "type": "function_call",
            "status": "completed",
            "call_id": "call_lookup",
            "name": "lookup_order",
            "arguments": '{"order_id":"A123"}',
        },
    ]
    request_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_bodies.append(json.loads(request.content))
        if len(request_bodies) == 1:
            return httpx.Response(
                200,
                json={"id": "resp_1", "status": "completed", "output": prior_output},
            )
        return httpx.Response(
            200,
            json={
                "id": "resp_2",
                "status": "completed",
                "output": [
                    {
                        "id": "msg_2",
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Order shipped."}],
                    }
                ],
            },
        )

    provider = OpenAIResponsesProvider(
        api_key="test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    agent = create_agent(
        name="openai-continuity",
        model="openai:gpt-5.6-sol",
        provider=provider,
        tools=[lookup_order],
        trace=False,
    )

    result = agent.run("Where is A123?")

    assert result.output == "Order shipped."
    assert request_bodies[1]["input"] == [
        {"role": "user", "content": "Where is A123?"},
        *prior_output,
        {
            "type": "function_call_output",
            "call_id": "call_lookup",
            "output": '{"order_id":"A123","status":"shipped"}',
        },
    ]
    assert "temperature" not in request_bodies[0]
    assert "temperature" not in request_bodies[1]


def test_anthropic_tool_loop_replays_complete_assistant_content_unchanged():
    prior_content = [
        {
            "type": "thinking",
            "thinking": "I should look up the order.",
            "signature": "opaque-thinking-signature",
        },
        {"type": "redacted_thinking", "data": "opaque-redacted-thinking"},
        {"type": "text", "text": "I will check."},
        {
            "type": "tool_use",
            "id": "toolu_lookup",
            "name": "lookup_order",
            "input": {"order_id": "A123"},
        },
    ]
    request_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_bodies.append(json.loads(request.content))
        if len(request_bodies) == 1:
            return httpx.Response(
                200,
                json={
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-sonnet-5",
                    "content": prior_content,
                    "stop_reason": "tool_use",
                    "usage": {"input_tokens": 12, "output_tokens": 7},
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "msg_2",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-5",
                "content": [{"type": "text", "text": "Order shipped."}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 25, "output_tokens": 4},
            },
        )

    provider = AnthropicProvider(
        api_key="test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    agent = create_agent(
        name="anthropic-continuity",
        model="anthropic:claude-sonnet-5",
        provider=provider,
        tools=[lookup_order],
        trace=False,
    )

    result = agent.run("Where is A123?")

    assert result.output == "Order shipped."
    assert request_bodies[1]["messages"] == [
        {"role": "user", "content": "Where is A123?"},
        {"role": "assistant", "content": prior_content},
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_lookup",
                    "content": '{"order_id":"A123","status":"shipped"}',
                }
            ],
        },
    ]
    assert "temperature" not in request_bodies[0]
    assert "temperature" not in request_bodies[1]
