import pytest
from pydantic import BaseModel

from clearagent import Agent
from clearagent.runtime.providers.base import FakeProvider, ProviderResponse, ToolCall


class Answer(BaseModel):
    answer: str


@pytest.mark.parametrize("intermediate_text", ["I will look it up.", ""])
def test_structured_agent_executes_tool_turns_with_intermediate_text(intermediate_text):
    calls = []

    def lookup() -> str:
        calls.append("lookup")
        return "value"

    agent = Agent(
        name="structured-tool-agent",
        model="openai:test-model",
        provider=FakeProvider(
            [
                ProviderResponse(
                    provider="fake",
                    model="test-model",
                    raw={},
                    output_text=intermediate_text,
                    tool_calls=[ToolCall(id="call_1", name="lookup", arguments={})],
                ),
                ProviderResponse.fake_text('{"answer":"value"}'),
            ]
        ),
        tools=[lookup],
        response_format=Answer,
        trace=False,
    )

    result = agent.run("Look up the value.")

    assert calls == ["lookup"]
    assert result.structured_output == {"answer": "value"}
    assert result.tool_calls[0]["result"] == "value"
