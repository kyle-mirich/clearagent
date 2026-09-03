import pytest

from clearagent.create import create_agent
from clearagent.runtime.tools import tool
from clearagent.runtime.providers.base import FakeProvider, ProviderError, ProviderResponse, ToolCall
from clearagent.storage.sqlite import SQLiteTraceStore


@tool
def lookup_order(order_id: str) -> dict:
    """Look up an order."""
    return {"order_id": order_id, "status": "shipped", "eta": "Friday"}


def test_simple_run_creates_trace_rows(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        system_prompt="Help customers.",
        provider=FakeProvider([ProviderResponse.fake_text("Order A123 has shipped.")]),
        trace_db_path=db_path,
    )

    result = agent.run("Where is order A123?")

    store = SQLiteTraceStore(db_path)
    assert result.output == "Order A123 has shipped."
    assert result.run_id is not None
    assert len(store.list_runs()) == 1
    assert len(store.get_turns(result.run_id)) == 1
    assert store.get_model_call_for_turn(result.run_id, 0) is not None


def test_tool_run_creates_multiple_turns_and_tool_call(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        system_prompt="Help customers.",
        tools=[lookup_order],
        provider=FakeProvider(
            [
                ProviderResponse.fake_tool_call(
                    ToolCall(id="call_1", name="lookup_order", arguments={"order_id": "A123"})
                ),
                ProviderResponse.fake_text("Order A123 has shipped and arrives Friday."),
            ]
        ),
        trace_db_path=db_path,
    )

    result = agent.run("Where is order A123?")

    store = SQLiteTraceStore(db_path)
    assert result.output == "Order A123 has shipped and arrives Friday."
    assert len(store.get_turns(result.run_id)) == 2
    assert len(store.list_tool_calls(result.run_id)) == 1


def test_failed_provider_call_still_saves_request(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider([ProviderError("mock failure")]),
        trace_db_path=db_path,
    )

    with pytest.raises(ProviderError):
        agent.run("hello")

    store = SQLiteTraceStore(db_path)
    run_id = store.list_runs()[0]["id"]
    assert store.get_model_call_for_turn(run_id, 0)["response_json"] is None
    assert store.get_run(run_id)["status"] == "error"


def test_tool_failure_marks_trace_run_and_turn_as_error(tmp_path):
    @tool
    def broken_tool(order_id: str) -> dict:
        """Raise while looking up an order."""
        raise RuntimeError(f"cannot load {order_id}")

    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        tools=[broken_tool],
        provider=FakeProvider(
            [
                ProviderResponse.fake_tool_call(
                    ToolCall(id="call_1", name="broken_tool", arguments={"order_id": "A123"})
                )
            ]
        ),
        trace_db_path=db_path,
    )

    with pytest.raises(RuntimeError, match="cannot load A123"):
        agent.run("Where is order A123?")

    store = SQLiteTraceStore(db_path)
    run_id = store.list_runs()[0]["id"]
    turns = store.get_turns(run_id)
    tool_calls = store.list_tool_calls(run_id)
    assert store.get_run(run_id)["status"] == "error"
    assert turns[0]["status"] == "error"
    assert tool_calls[0]["status"] == "error"


def test_trace_false_creates_no_database(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider([ProviderResponse.fake_text("hello")]),
        trace_db_path=db_path,
    )

    result = agent.run("hello", trace=False)

    assert result.run_id is None
    assert not db_path.exists()
