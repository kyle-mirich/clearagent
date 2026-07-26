from typing import Any, cast

from fastapi.testclient import TestClient

from clearagent import create_agent
from clearagent.chat import create_chat_app
from clearagent.evals import EvalRunner, EvalSuite
from clearagent.graph import AgentGraph
from clearagent.providers import FakeProvider, ProviderResponse
from clearagent.storage import SQLiteTraceStore, TraceStore


class DelegatingTraceStore:
    """Non-SQLite-facing store used to catch accidental path-based reopening."""

    def __init__(self, backing: SQLiteTraceStore):
        self.backing = backing

    def __getattr__(self, name: str) -> Any:
        return getattr(self.backing, name)

    def __bool__(self) -> bool:
        """A valid store may be falsey; injection must use identity, not truthiness."""
        return False


def custom_store(path) -> tuple[TraceStore, SQLiteTraceStore]:
    backing = SQLiteTraceStore(path)
    assert isinstance(backing, TraceStore)
    return cast(TraceStore, DelegatingTraceStore(backing)), backing


def test_eval_runner_uses_custom_trace_store_for_runs_checks_and_results(tmp_path):
    store, backing = custom_store(tmp_path / "custom.sqlite")
    unused_default = tmp_path / "unused-default.sqlite"
    agent = create_agent(
        name="custom_store_agent",
        model="openai:test",
        provider=FakeProvider([ProviderResponse.fake_text("done")]),
        trace_db_path=unused_default,
        trace_store=store,
    )
    suite = EvalSuite.model_validate(
        {
            "name": "custom-store",
            "cases": [
                {
                    "name": "trace-aware",
                    "input": "run",
                    "checks": [{"contains": "done"}, {"trace_provider": "fake"}],
                }
            ],
        }
    )

    report = EvalRunner(agent).run_suite(suite)

    assert report.passed == 1
    assert len(backing.list_runs()) == 1
    assert len(backing.list_eval_case_results(report.suite_run_id)) == 1
    assert not unused_default.exists()


def test_chat_trace_endpoints_use_custom_trace_store(tmp_path):
    store, _ = custom_store(tmp_path / "custom.sqlite")
    unused_default = tmp_path / "unused-default.sqlite"
    agent = create_agent(
        name="custom_store_chat",
        model="openai:test",
        provider=FakeProvider([ProviderResponse.fake_text("hello")]),
        trace_db_path=unused_default,
        trace_store=store,
    )
    client = TestClient(create_chat_app(agent, chat_db_path=tmp_path / "chat.sqlite"))
    session_id = client.post("/api/sessions").json()["id"]

    response = client.post(
        f"/api/sessions/{session_id}/messages",
        json={"content": "hello"},
    )
    traces = client.get("/api/traces").json()
    triage = client.get(f"/api/triage/runs/{traces[0]['id']}")

    assert response.status_code == 200
    assert "event: trace" in response.text
    assert len(traces) == 1
    assert triage.status_code == 200
    assert not unused_default.exists()


def test_graph_result_preserves_custom_trace_store(tmp_path):
    store, backing = custom_store(tmp_path / "custom.sqlite")
    unused_default = tmp_path / "unused-default.sqlite"
    first = create_agent(
        name="first",
        model="openai:test",
        provider=FakeProvider([ProviderResponse.fake_text("first output")]),
        trace_db_path=unused_default,
    )
    second = create_agent(
        name="second",
        model="openai:test",
        provider=FakeProvider([ProviderResponse.fake_text("second output")]),
        trace_db_path=unused_default,
    )
    graph = (
        AgentGraph("custom-store-graph")
        .add_node(first)
        .add_node(second)
        .add_edge("first", "second")
        .set_entrypoint("first")
    )

    result = graph.run("start", trace_store=store)

    assert result.output == "second output"
    assert result.trace_store is store
    assert result.run_id is not None
    assert len(backing.get_turns(result.run_id)) == 2
    assert "trace_store" not in result.model_dump()
    assert not unused_default.exists()
