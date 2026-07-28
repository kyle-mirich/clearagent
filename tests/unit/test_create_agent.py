from clearagent import create_agent, tool
from clearagent.agent import Agent
from clearagent.providers.base import FakeProvider, ProviderResponse


@tool
def lookup_order(order_id: str) -> dict:
    return {"order_id": order_id}


def test_create_agent_returns_agent_with_defaults(tmp_path):
    provider = FakeProvider([ProviderResponse.fake_text("ok")])
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=provider,
        trace_db_path=tmp_path / "traces.sqlite",
    )

    assert isinstance(agent, Agent)
    assert agent.name == "support"
    assert agent.trace is True
    assert agent.temperature is None
    assert agent.run("hello").output == "ok"
    assert "temperature" not in provider.completed_requests[0].body


def test_agent_and_create_agent_keep_explicit_temperature(tmp_path):
    direct_provider = FakeProvider([ProviderResponse.fake_text("direct")])
    direct = Agent(
        name="direct",
        model="fake:model",
        provider=direct_provider,
        trace=False,
    )
    configured_provider = FakeProvider([ProviderResponse.fake_text("configured")])
    configured = create_agent(
        name="configured",
        model="fake:model",
        provider=configured_provider,
        temperature=0.25,
        trace_db_path=tmp_path / "traces.sqlite",
    )

    direct.run("hello")
    configured.run("hello")

    assert direct.temperature is None
    assert "temperature" not in direct_provider.completed_requests[0].body
    assert configured.temperature == 0.25
    assert configured_provider.completed_requests[0].body["temperature"] == 0.25


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
