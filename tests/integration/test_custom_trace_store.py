import json
from typing import Any, cast

from fastapi.testclient import TestClient
import pytest

from clearagent import create_agent
from clearagent.chat import create_chat_app
from clearagent.evals import EvalRunner, EvalSuite
from clearagent.graph import AgentGraph
from clearagent.providers import FakeProvider, ProviderRequest, ProviderResponse
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


class CapturingTraceStore(DelegatingTraceStore):
    def __init__(self, backing: SQLiteTraceStore):
        super().__init__(backing)
        self.requests: list[ProviderRequest] = []

    def save_model_request(
        self,
        *,
        run_id: str,
        turn_id: str,
        request: ProviderRequest,
    ) -> str:
        self.requests.append(request)
        return self.backing.save_model_request(run_id=run_id, turn_id=turn_id, request=request)


class SecretRequestProvider(FakeProvider):
    def build_request(self, **kwargs: Any) -> ProviderRequest:
        request = super().build_request(**kwargs)
        request.headers_snapshot["authorization"] = "Bearer top-secret"
        request.body["api_key"] = "top-secret"
        request.body["nested"] = {"refresh_token": "top-secret"}
        return request


def custom_store(path) -> tuple[TraceStore, SQLiteTraceStore]:
    backing = SQLiteTraceStore(path)
    assert isinstance(backing, TraceStore)
    return cast(TraceStore, DelegatingTraceStore(backing)), backing


@pytest.mark.parametrize("stream", [False, True])
def test_custom_store_receives_redacted_request_without_mutating_provider_request(tmp_path, stream):
    backing = SQLiteTraceStore(tmp_path / "custom.sqlite")
    store = CapturingTraceStore(backing)
    provider = SecretRequestProvider([ProviderResponse.fake_text("done")])
    agent = create_agent(
        name="redacted_custom_store",
        model="openai:test",
        provider=provider,
        trace_store=cast(TraceStore, store),
    )

    if stream:
        assert list(agent.stream_text("run")) == ["done"]
    else:
        assert agent.run("run").output == "done"

    captured = store.requests[0]
    assert captured.headers_snapshot["authorization"] == "[REDACTED]"
    assert captured.body["api_key"] == "[REDACTED]"
    assert captured.body["nested"]["refresh_token"] == "[REDACTED]"

    completed = provider.completed_requests[0]
    assert completed.headers_snapshot["authorization"] == "Bearer top-secret"
    assert completed.body["api_key"] == "top-secret"
    assert completed.body["nested"]["refresh_token"] == "top-secret"

    run_id = backing.list_runs()[0]["id"]
    stored = json.loads(backing.list_model_calls(run_id)[0]["request_json"])
    assert stored["headers_snapshot"]["authorization"] == "[REDACTED]"
    assert stored["body"]["api_key"] == "[REDACTED]"
    assert stored["body"]["nested"]["refresh_token"] == "[REDACTED]"


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
