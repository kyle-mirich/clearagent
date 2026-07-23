from clearagent import create_agent, tool
from clearagent.agent import Agent
from clearagent.providers.base import FakeProvider, ProviderResponse


@tool
def lookup_order(order_id: str) -> dict:
    return {"order_id": order_id}


def test_create_agent_returns_agent_with_defaults(tmp_path):
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider([ProviderResponse.fake_text("ok")]),
        trace_db_path=tmp_path / "traces.sqlite",
    )

    assert isinstance(agent, Agent)
    assert agent.name == "support"
    assert agent.trace is True
    assert agent.run("hello").output == "ok"


def test_create_agent_accepts_tools(tmp_path):
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        tools=[lookup_order],
        provider=FakeProvider([ProviderResponse.fake_text("ok")]),
        trace_db_path=tmp_path / "traces.sqlite",
    )

    assert agent.tools == [lookup_order]


def test_create_agent_uses_parsed_model_name_for_local_url_model_uri(tmp_path):
    provider = FakeProvider([ProviderResponse.fake_text("ok")])
    agent = create_agent(
        name="local",
        model="local:http://localhost:8000/v1?model=llama3.1",
        provider=provider,
        trace_db_path=tmp_path / "traces.sqlite",
    )

    agent.run("hello")

    assert provider.completed_requests[0].model == "llama3.1"
