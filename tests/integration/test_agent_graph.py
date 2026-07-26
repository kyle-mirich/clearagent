from clearagent import create_agent
from clearagent.graph import AgentGraph
from clearagent.providers.base import FakeProvider, ProviderError, ProviderResponse, ToolCall
from clearagent.storage.sqlite import SQLiteTraceStore
from clearagent.tool import tool
import pytest


def test_two_node_graph_uses_one_run_and_records_node_names(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    planner = create_agent(
        name="planner",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider([ProviderResponse.fake_text("Plan: be concise.")]),
        trace_db_path=db_path,
    )
    writer = create_agent(
        name="writer",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider([ProviderResponse.fake_text("Final answer.")]),
        trace_db_path=db_path,
    )
    graph = (
        AgentGraph("planner_writer")
        .add_node(planner)
        .add_node(writer)
        .add_edge("planner", "writer")
        .set_entrypoint("planner")
    )

    result = graph.run("Draft a response.")

    store = SQLiteTraceStore(db_path)
    turns = store.get_turns(result.run_id)
    assert result.output == "Final answer."
    assert len(store.list_runs()) == 1
    assert [turn["node_name"] for turn in turns] == ["planner", "writer"]
    assert store.get_run(result.run_id)["graph_name"] == "planner_writer"


def test_graph_offsets_turns_by_actual_node_turn_count(tmp_path):
    db_path = tmp_path / "traces.sqlite"

    @tool
    def graph_lookup(value: str) -> str:
        return value

    planner = create_agent(
        name="planner",
        model="openai:gpt-4.1-mini",
        tools=[graph_lookup],
        provider=FakeProvider(
            [
                ProviderResponse.fake_tool_call(
                    ToolCall(id="call_1", name="graph_lookup", arguments={"value": "plan"})
                ),
                ProviderResponse.fake_text("Plan."),
            ]
        ),
        trace_db_path=db_path,
    )
    writer = create_agent(
        name="writer",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider([ProviderResponse.fake_text("Final answer.")]),
        trace_db_path=db_path,
    )
    graph = (
        AgentGraph("planner_writer")
        .add_node(planner)
        .add_node(writer)
        .add_edge("planner", "writer")
        .set_entrypoint("planner")
    )

    result = graph.run("Draft a response.")

    turns = SQLiteTraceStore(db_path).get_turns(result.run_id)
    assert [(turn["node_name"], turn["turn_index"]) for turn in turns] == [
        ("planner", 0),
        ("planner", 1),
        ("writer", 2),
    ]


def test_graph_marks_shared_run_error_when_node_fails(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    planner = create_agent(
        name="planner",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider([ProviderResponse.fake_text("Plan.")]),
        trace_db_path=db_path,
    )
    writer = create_agent(
        name="writer",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider([ProviderError("writer failed")]),
        trace_db_path=db_path,
    )
    graph = (
        AgentGraph("planner_writer")
        .add_node(planner)
        .add_node(writer)
        .add_edge("planner", "writer")
        .set_entrypoint("planner")
    )

    with pytest.raises(ProviderError, match="writer failed"):
        graph.run("Draft a response.")

    store = SQLiteTraceStore(db_path)
    run = store.list_runs()[0]
    assert run["status"] == "error"
    assert "writer failed" in run["metadata_json"]


def test_graph_rejects_cycle_before_calling_provider(tmp_path):
    provider = FakeProvider([ProviderResponse.fake_text("should not run")])
    agent = create_agent(
        name="loop",
        model="openai:gpt-4.1-mini",
        provider=provider,
        trace_db_path=tmp_path / "traces.sqlite",
    )
    graph = AgentGraph("loop").add_node(agent).add_edge("loop", "loop").set_entrypoint("loop")

    with pytest.raises(ValueError, match="cycle"):
        graph.run("hello")

    assert provider.completed_requests == []


def test_graph_rejects_duplicate_node_names_instead_of_overwriting():
    first = create_agent(name="worker", model="fake:model", provider=FakeProvider())
    duplicate = create_agent(name="worker", model="fake:model", provider=FakeProvider())
    graph = AgentGraph("duplicate").add_node(first)

    with pytest.raises(ValueError, match="already contains a node named 'worker'"):
        graph.add_node(duplicate)

    assert graph.nodes["worker"] is first


def test_graph_respects_trace_false_and_preserves_result_metadata(tmp_path):
    db_path = tmp_path / "traces.sqlite"

    @tool
    def graph_tool(value: str) -> str:
        return value

    first = create_agent(
        name="first",
        model="openai:gpt-4.1-mini",
        tools=[graph_tool],
        provider=FakeProvider(
            [
                ProviderResponse.fake_tool_call(
                    ToolCall(id="call_1", name="graph_tool", arguments={"value": "ok"})
                ),
                ProviderResponse.fake_text("middle"),
            ]
        ),
        trace=False,
        trace_db_path=db_path,
    )
    final_response = ProviderResponse.fake_text('{"answer":"done"}')
    final_response.structured_output = {"answer": "done"}
    second = create_agent(
        name="second",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider([final_response]),
        trace=False,
        trace_db_path=db_path,
    )
    graph = (
        AgentGraph("untraced")
        .add_node(first)
        .add_node(second)
        .add_edge("first", "second")
        .set_entrypoint("first")
    )

    result = graph.run("hello")

    assert result.run_id is None
    assert result.trace_db_path is None
    assert result.tool_calls[0]["name"] == "graph_tool"
    assert result.structured_output == {"answer": "done"}
    assert not db_path.exists()
