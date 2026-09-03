from clearagent.create import create_agent
from clearagent.graph import AgentGraph
from clearagent.runtime.providers.base import FakeProvider, ProviderError, ProviderResponse, ToolCall
from clearagent.storage.sqlite import SQLiteTraceStore
from clearagent.runtime.tools import tool
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
