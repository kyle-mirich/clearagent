import json

import pytest

from clearagent import create_agent, tool
from clearagent.agent import MaxTurnsExceeded
from clearagent.graph import AgentGraph
from clearagent.providers.base import (
    FakeProvider,
    ProviderError,
    ProviderResponse,
    ToolCall,
    Usage,
)
from clearagent.storage.sqlite import SQLiteTraceStore


@tool
def echo_value(value: str) -> str:
    """Return a value unchanged."""
    return value


def _parallel_tool_response(*calls: ToolCall) -> ProviderResponse:
    return ProviderResponse(
        provider="fake",
        model="fake-model",
        raw={"tool_calls": [call.model_dump() for call in calls]},
        tool_calls=list(calls),
        usage=Usage(),
        finish_reason="tool_calls",
    )


def test_max_turns_exhaustion_finalizes_every_trace_row(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    provider = FakeProvider(
        [
            ProviderResponse.fake_tool_call(
                ToolCall(id="call_1", name="echo_value", arguments={"value": "first"})
            ),
            ProviderResponse.fake_tool_call(
                ToolCall(id="call_2", name="echo_value", arguments={"value": "second"})
            ),
        ]
    )
    agent = create_agent(
        name="bounded",
        model="fake:model",
        tools=[echo_value],
        provider=provider,
        max_turns=2,
        trace_db_path=db_path,
    )

    with pytest.raises(MaxTurnsExceeded, match="Exceeded 2 turns"):
        agent.run("keep using the tool")

    store = SQLiteTraceStore(db_path)
    run = store.list_runs()[0]
    turns = store.get_turns(run["id"])
    model_calls = store.list_model_calls(run["id"])
    tool_calls = store.list_tool_calls(run["id"])

    assert run["status"] == "error"
    assert run["ended_at"] is not None
    assert json.loads(run["metadata_json"])["error"] == {
        "type": "MaxTurnsExceeded",
        "message": "Exceeded 2 turns.",
    }
    assert [(turn["turn_index"], turn["status"]) for turn in turns] == [
        (0, "ok"),
        (1, "ok"),
    ]
    assert all(turn["ended_at"] is not None for turn in turns)
    assert len(model_calls) == 2
    assert all(call["status"] == "ok" and call["ended_at"] for call in model_calls)
    assert len(tool_calls) == 2
    assert all(call["status"] == "ok" and call["ended_at"] for call in tool_calls)


def test_multiple_tool_calls_preserve_result_and_next_request_order(tmp_path):
    calls = [
        ToolCall(id="call_alpha", name="echo_value", arguments={"value": "alpha"}),
        ToolCall(id="call_beta", name="echo_value", arguments={"value": "beta"}),
    ]
    provider = FakeProvider(
        [
            _parallel_tool_response(*calls),
            ProviderResponse.fake_text("both values processed"),
        ]
    )
    agent = create_agent(
        name="parallel-tools",
        model="fake:model",
        tools=[echo_value],
        provider=provider,
        trace_db_path=tmp_path / "traces.sqlite",
    )

    result = agent.run("process both")

    assert result.output == "both values processed"
    assert result.tool_calls == [
        {"name": "echo_value", "arguments": {"value": "alpha"}, "result": "alpha"},
        {"name": "echo_value", "arguments": {"value": "beta"}, "result": "beta"},
    ]
    assert provider.completed_requests[1].body["messages"] == [
        {"role": "user", "content": "process both"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_alpha",
                    "type": "function",
                    "function": {
                        "name": "echo_value",
                        "arguments": '{"value":"alpha"}',
                    },
                },
                {
                    "id": "call_beta",
                    "type": "function",
                    "function": {
                        "name": "echo_value",
                        "arguments": '{"value":"beta"}',
                    },
                },
            ],
        },
        {
            "role": "tool",
            "content": "alpha",
            "tool_call_id": "call_alpha",
            "name": "echo_value",
        },
        {
            "role": "tool",
            "content": "beta",
            "tool_call_id": "call_beta",
            "name": "echo_value",
        },
    ]

    stored_calls = SQLiteTraceStore(result.trace_db_path).list_tool_calls(result.run_id)
    assert len(stored_calls) == 2
    assert {call["tool_name"] for call in stored_calls} == {"echo_value"}
    assert all(call["status"] == "ok" for call in stored_calls)


def test_unregistered_tool_call_is_rejected_and_fully_traced(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    provider = FakeProvider(
        [
            ProviderResponse.fake_tool_call(
                ToolCall(id="call_missing", name="missing_tool", arguments={"value": "x"})
            )
        ]
    )
    agent = create_agent(
        name="tool-guard",
        model="fake:model",
        tools=[echo_value],
        provider=provider,
        trace_db_path=db_path,
    )

    with pytest.raises(KeyError, match="No tool named 'missing_tool' is registered"):
        agent.run("invoke a missing tool")

    store = SQLiteTraceStore(db_path)
    run = store.list_runs()[0]
    turn = store.get_turns(run["id"])[0]
    model_call = store.list_model_calls(run["id"])[0]
    tool_call = store.list_tool_calls(run["id"])[0]

    assert run["status"] == "error"
    assert turn["status"] == "error"
    assert model_call["status"] == "ok"
    assert tool_call["tool_name"] == "missing_tool"
    assert tool_call["status"] == "error"
    assert json.loads(tool_call["error_json"])["type"] == "KeyError"


def test_invalid_tool_arguments_never_execute_and_are_fully_traced(tmp_path):
    executions: list[int] = []

    @tool
    def count_items(count: int) -> str:
        executions.append(count)
        return str(count)

    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="argument-guard",
        model="fake:model",
        tools=[count_items],
        provider=FakeProvider(
            [
                ProviderResponse.fake_tool_call(
                    ToolCall(
                        id="call_invalid",
                        name="count_items",
                        arguments={"count": "not-a-number"},
                    )
                )
            ]
        ),
        trace_db_path=db_path,
    )

    with pytest.raises(ValueError, match="Invalid arguments for tool count_items"):
        agent.run("count")

    store = SQLiteTraceStore(db_path)
    run = store.list_runs()[0]
    turn = store.get_turns(run["id"])[0]
    tool_call = store.list_tool_calls(run["id"])[0]

    assert executions == []
    assert run["status"] == "error"
    assert turn["status"] == "error"
    assert tool_call["status"] == "error"
    assert json.loads(tool_call["args_json"]) == {"count": "not-a-number"}
    assert json.loads(tool_call["error_json"])["type"] == "ValueError"


def test_untraced_tool_failure_propagates_without_creating_local_state(tmp_path):
    @tool
    def failing_tool(value: str) -> str:
        raise RuntimeError(f"cannot process {value}")

    db_path = tmp_path / "traces.sqlite"
    provider = FakeProvider(
        [
            ProviderResponse.fake_tool_call(
                ToolCall(id="call_failure", name="failing_tool", arguments={"value": "x"})
            )
        ]
    )
    agent = create_agent(
        name="untraced-tool-error",
        model="fake:model",
        tools=[failing_tool],
        provider=provider,
        trace=False,
        trace_db_path=db_path,
    )

    with pytest.raises(RuntimeError, match="cannot process x"):
        agent.run("fail")

    assert len(provider.completed_requests) == 1
    assert not db_path.exists()


def test_graph_requires_an_entrypoint_before_any_provider_or_trace_side_effect(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    provider = FakeProvider([ProviderResponse.fake_text("should not run")])
    agent = create_agent(
        name="worker",
        model="fake:model",
        provider=provider,
        trace_db_path=db_path,
    )
    graph = AgentGraph("missing-entrypoint").add_node(agent)

    with pytest.raises(ValueError, match="entrypoint is not set"):
        graph.run("hello")

    assert provider.completed_requests == []
    assert not db_path.exists()


def test_graph_rejects_unregistered_entrypoint_before_provider_execution(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    provider = FakeProvider([ProviderResponse.fake_text("should not run")])
    agent = create_agent(
        name="worker",
        model="fake:model",
        provider=provider,
        trace_db_path=db_path,
    )
    graph = AgentGraph("bad-entrypoint").add_node(agent).set_entrypoint("missing")

    with pytest.raises(ValueError, match="entrypoint 'missing' is not a registered node"):
        graph.run("hello")

    assert provider.completed_requests == []
    assert not db_path.exists()


def test_graph_rejects_non_positive_node_limit_before_provider_execution(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    provider = FakeProvider([ProviderResponse.fake_text("should not run")])
    agent = create_agent(
        name="worker",
        model="fake:model",
        provider=provider,
        trace_db_path=db_path,
    )
    graph = AgentGraph("bad-limit").add_node(agent).set_entrypoint("worker")

    with pytest.raises(ValueError, match="max_nodes must be at least 1"):
        graph.run("hello", max_nodes=0)

    assert provider.completed_requests == []
    assert not db_path.exists()


def test_graph_rejects_unknown_edge_target_before_provider_execution(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    provider = FakeProvider([ProviderResponse.fake_text("should not run")])
    agent = create_agent(
        name="worker",
        model="fake:model",
        provider=provider,
        trace_db_path=db_path,
    )
    graph = (
        AgentGraph("bad-edge")
        .add_node(agent)
        .add_edge("worker", "missing")
        .set_entrypoint("worker")
    )

    with pytest.raises(ValueError, match="edge targets unknown node 'missing'"):
        graph.run("hello")

    assert provider.completed_requests == []
    assert not db_path.exists()


def test_graph_rejects_execution_order_beyond_explicit_limit(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    first_provider = FakeProvider([ProviderResponse.fake_text("should not run")])
    second_provider = FakeProvider([ProviderResponse.fake_text("should not run")])
    first = create_agent(
        name="first",
        model="fake:model",
        provider=first_provider,
        trace_db_path=db_path,
    )
    second = create_agent(
        name="second",
        model="fake:model",
        provider=second_provider,
        trace_db_path=db_path,
    )
    graph = (
        AgentGraph("limited")
        .add_node(first)
        .add_node(second)
        .add_edge("first", "second")
        .set_entrypoint("first")
    )

    with pytest.raises(ValueError, match="exceeded max_nodes=1"):
        graph.run("hello", max_nodes=1)

    assert first_provider.completed_requests == []
    assert second_provider.completed_requests == []
    assert not db_path.exists()


def test_untraced_graph_propagates_provider_error_without_creating_local_state(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    provider = FakeProvider([ProviderError("offline failure")])
    agent = create_agent(
        name="worker",
        model="fake:model",
        provider=provider,
        trace=False,
        trace_db_path=db_path,
    )
    graph = AgentGraph("untraced-error").add_node(agent).set_entrypoint("worker")

    with pytest.raises(ProviderError, match="offline failure"):
        graph.run("hello")

    assert len(provider.completed_requests) == 1
    assert not db_path.exists()


def test_graph_error_trace_keeps_usage_from_completed_nodes(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    completed = ProviderResponse.fake_text("first result")
    completed.usage = Usage(
        prompt_tokens=11,
        completion_tokens=3,
        total_tokens=14,
        cost_usd=0.004,
    )
    first = create_agent(
        name="first",
        model="fake:model",
        provider=FakeProvider([completed]),
        trace_db_path=db_path,
    )
    second = create_agent(
        name="second",
        model="fake:model",
        provider=FakeProvider([ProviderError("second failed")]),
        trace_db_path=db_path,
    )
    graph = (
        AgentGraph("partial-usage")
        .add_node(first)
        .add_node(second)
        .add_edge("first", "second")
        .set_entrypoint("first")
    )

    with pytest.raises(ProviderError, match="second failed"):
        graph.run("hello")

    run = SQLiteTraceStore(db_path).list_runs()[0]
    assert run["status"] == "error"
    assert run["total_prompt_tokens"] == 11
    assert run["total_completion_tokens"] == 3
    assert run["total_cost_usd"] == pytest.approx(0.004)
    assert json.loads(run["metadata_json"])["error"] == {
        "type": "ProviderError",
        "message": "second failed",
    }
