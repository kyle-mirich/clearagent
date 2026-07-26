import pytest
from pydantic import BaseModel

from clearagent import create_agent, tool
from clearagent.providers.base import FakeProvider, ProviderError, ProviderResponse, ToolCall, Usage
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


def test_typed_tool_results_are_json_safe_in_trace_and_result(tmp_path):
    class LookupResult(BaseModel):
        order_id: str
        status: str

    @tool
    def typed_lookup(order_id: str) -> LookupResult:
        return LookupResult(order_id=order_id, status="shipped")

    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        tools=[typed_lookup],
        provider=FakeProvider(
            [
                ProviderResponse.fake_tool_call(
                    ToolCall(id="call_1", name="typed_lookup", arguments={"order_id": "A123"})
                ),
                ProviderResponse.fake_text("Done"),
            ]
        ),
        trace_db_path=db_path,
    )

    result = agent.run("Look it up")

    assert result.tool_calls[0]["result"] == {"order_id": "A123", "status": "shipped"}
    stored = SQLiteTraceStore(db_path).list_tool_calls(result.run_id)[0]
    assert '"status": "shipped"' in stored["result_json"]


def test_agent_aggregates_usage_across_tool_turns(tmp_path):
    first = ProviderResponse.fake_tool_call(
        ToolCall(id="call_1", name="lookup_order", arguments={"order_id": "A123"})
    )
    first.usage = Usage(prompt_tokens=10, completion_tokens=2, total_tokens=12, cost=0.01)
    second = ProviderResponse.fake_text("Done")
    second.usage = Usage(prompt_tokens=20, completion_tokens=3, total_tokens=23, cost=0.02)
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        tools=[lookup_order],
        provider=FakeProvider([first, second]),
        trace_db_path=db_path,
    )

    result = agent.run("Look it up")
    run = SQLiteTraceStore(db_path).get_run(result.run_id)

    assert result.usage.total_tokens == 35
    assert result.cost_usd == pytest.approx(0.03)
    assert run["total_prompt_tokens"] == 30
    assert run["total_completion_tokens"] == 5
    assert run["total_cost_usd"] == pytest.approx(0.03)


@pytest.mark.parametrize(
    ("first_cost", "second_cost"),
    [(0.01, None), (None, 0.02)],
)
def test_agent_does_not_report_partial_cost_as_an_exact_total(
    tmp_path, first_cost, second_cost
):
    first = ProviderResponse.fake_tool_call(
        ToolCall(id="call_1", name="lookup_order", arguments={"order_id": "A123"})
    )
    first.usage = Usage(prompt_tokens=10, completion_tokens=2, total_tokens=12, cost=first_cost)
    second = ProviderResponse.fake_text("Done")
    second.usage = Usage(
        prompt_tokens=20,
        completion_tokens=3,
        total_tokens=23,
        cost=second_cost,
    )
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        tools=[lookup_order],
        provider=FakeProvider([first, second]),
        trace_db_path=db_path,
    )

    result = agent.run("Look it up")
    run = SQLiteTraceStore(db_path).get_run(result.run_id)

    assert result.usage.total_tokens == 35
    assert result.cost_usd is None
    assert run["total_cost_usd"] is None


def test_closing_stream_finalizes_trace_rows_as_error(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider([ProviderResponse.fake_text("first chunk")]),
        trace_db_path=db_path,
    )
    stream = agent.stream_text("hello")

    assert next(stream) == "first chunk"
    stream.close()

    store = SQLiteTraceStore(db_path)
    run = store.list_runs()[0]
    turns = store.get_turns(run["id"])
    model_calls = store.list_model_calls(run["id"])
    assert run["status"] == "error"
    assert turns[0]["status"] == "error"
    assert model_calls[0]["status"] == "error"
    assert "stream consumer closed" in run["metadata_json"]


def test_request_build_failure_finalizes_trace(tmp_path):
    class BrokenBuildProvider(FakeProvider):
        def build_request(self, **kwargs):
            raise RuntimeError("cannot build request")

    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="support",
        model="fake:model",
        provider=BrokenBuildProvider(),
        trace_db_path=db_path,
    )

    with pytest.raises(RuntimeError, match="cannot build request"):
        agent.run("hello")

    store = SQLiteTraceStore(db_path)
    assert store.list_runs()[0]["status"] == "error"
    assert store.get_turns(store.list_runs()[0]["id"])[0]["status"] == "error"
