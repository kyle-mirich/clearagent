import json

import httpx
import pytest
from fastapi.testclient import TestClient

from clearagent.chat.app import ChatSettings, _request_extra, create_chat_app
from clearagent.graph import AgentGraph
from clearagent.providers.base import FakeProvider, ProviderResponse, ToolCall
from clearagent.create import create_agent
from clearagent.storage.sqlite import SQLiteTraceStore
from clearagent.tool import tool


@tool
def chat_lookup_order(order_id: str) -> dict:
    """Look up an order from chat."""
    return {"order_id": order_id, "status": "shipped"}


def test_chat_app_streams_response_and_persists_history(tmp_path):
    agent = create_agent(
        name="chat_agent",
        model="openrouter:deepseek/deepseek-v4-flash",
        system_prompt="You are concise.",
        provider=FakeProvider([ProviderResponse.fake_text("Hello **there**.")]),
    )
    app = create_chat_app(agent, chat_db_path=tmp_path / "chat.sqlite")
    client = TestClient(app)

    session_response = client.post("/api/sessions")
    session_id = session_response.json()["id"]

    stream_response = client.post(
        f"/api/sessions/{session_id}/messages",
        json={"content": "Say hello in markdown."},
    )

    assert stream_response.status_code == 200
    assert stream_response.headers["content-type"].startswith("text/event-stream")
    assert stream_response.headers["cache-control"] == "no-cache"
    assert stream_response.headers["x-content-type-options"] == "nosniff"
    assert stream_response.text.startswith('data: "Hello **there**."\n\n')
    assert "\nevent: trace\n" in stream_response.text
    assert stream_response.text.endswith("data: [DONE]\n\n")

    history = client.get(f"/api/sessions/{session_id}/messages").json()
    assert [message["role"] for message in history] == ["user", "assistant"]
    assert history[0]["content"] == "Say hello in markdown."
    assert history[1]["content"] == "Hello **there**."


def test_chat_app_streams_provider_errors_as_sse_error_events(tmp_path):
    agent = create_agent(
        name="chat_agent",
        model="openrouter:deepseek/deepseek-v4-flash",
        system_prompt="You are concise.",
        provider=FakeProvider([RuntimeError("upstream rejected the request")]),
    )
    app = create_chat_app(agent, chat_db_path=tmp_path / "chat.sqlite")
    client = TestClient(app)
    session_id = client.post("/api/sessions").json()["id"]

    stream_response = client.post(
        f"/api/sessions/{session_id}/messages",
        json={"content": "Say hello."},
    )

    assert stream_response.status_code == 200
    assert stream_response.text == (
        'event: error\n'
        'data: {"message": "Request failed: upstream rejected the request"}\n\n'
    )
    history = client.get(f"/api/sessions/{session_id}/messages").json()
    assert [message["role"] for message in history] == ["user"]


def test_chat_app_does_not_emit_a_stale_trace_when_tracing_is_disabled(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    trace_store = SQLiteTraceStore(db_path)
    stale_run_id = trace_store.start_run(agent_name="chat_agent", root_input="old request")
    trace_store.end_run(stale_run_id, final_output="old response")
    agent = create_agent(
        name="chat_agent",
        model="openrouter:deepseek/deepseek-v4-flash",
        provider=FakeProvider([ProviderResponse.fake_text("Fresh response.")]),
        trace=False,
        trace_db_path=db_path,
    )
    client = TestClient(create_chat_app(agent, chat_db_path=tmp_path / "chat.sqlite"))
    session_id = client.post("/api/sessions").json()["id"]

    response = client.post(
        f"/api/sessions/{session_id}/messages",
        json={"content": "New untraced request"},
    )

    assert response.status_code == 200
    assert "event: trace" not in response.text
    assert [run["id"] for run in trace_store.list_runs()] == [stale_run_id]


def test_chat_app_emits_the_trace_owned_by_its_stream_when_another_run_competes(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    trace_store = SQLiteTraceStore(db_path)

    class CompetingRunProvider(FakeProvider):
        def stream_text(self, request):
            self.completed_requests.append(request)
            yield "Owned response."
            competing_run_id = trace_store.start_run(
                agent_name="chat_agent",
                root_input="competing request",
            )
            trace_store.end_run(competing_run_id, final_output="competing response")

    agent = create_agent(
        name="chat_agent",
        model="openrouter:deepseek/deepseek-v4-flash",
        provider=CompetingRunProvider(),
        trace_db_path=db_path,
    )
    client = TestClient(create_chat_app(agent, chat_db_path=tmp_path / "chat.sqlite"))
    session_id = client.post("/api/sessions").json()["id"]

    response = client.post(
        f"/api/sessions/{session_id}/messages",
        json={"content": "My request"},
    )

    trace_payload = response.text.split("event: trace\ndata: ", 1)[1].split("\n\n", 1)[0]
    emitted_run_id = json.loads(trace_payload)["run_id"]
    runs = trace_store.list_runs()
    owned_run = next(run for run in runs if "My request" in run["root_input"])
    competing_run = next(run for run in runs if run["root_input"] == "competing request")
    assert emitted_run_id == owned_run["id"]
    assert emitted_run_id != competing_run["id"]


def test_chat_app_uses_agent_runtime_for_tools_and_tracing(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="chat_agent",
        model="openrouter:deepseek/deepseek-v4-flash",
        tools=[chat_lookup_order],
        provider=FakeProvider(
            [
                ProviderResponse.fake_tool_call(
                    ToolCall(
                        id="call_1",
                        name="chat_lookup_order",
                        arguments={"order_id": "A123"},
                    )
                ),
                ProviderResponse.fake_text("Order A123 shipped."),
            ]
        ),
        trace_db_path=db_path,
    )
    app = create_chat_app(agent, chat_db_path=tmp_path / "chat.sqlite")
    client = TestClient(app)
    session_id = client.post("/api/sessions").json()["id"]

    stream_response = client.post(
        f"/api/sessions/{session_id}/messages",
        json={"content": "Where is A123?"},
    )

    store = SQLiteTraceStore(db_path)
    run_id = store.list_runs()[0]["id"]
    assert stream_response.text.startswith('data: "Order A123 shipped."\n\n')
    assert "\nevent: trace\n" in stream_response.text
    assert stream_response.text.endswith("data: [DONE]\n\n")
    assert len(store.get_turns(run_id)) == 2
    assert store.list_tool_calls(run_id)[0]["tool_name"] == "chat_lookup_order"


def test_chat_app_lists_trace_runs_for_viewer(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="chat_agent",
        model="openrouter:deepseek/deepseek-v4-flash",
        provider=FakeProvider([ProviderResponse.fake_text("Order A123 shipped.")]),
        trace_db_path=db_path,
    )
    app = create_chat_app(agent, chat_db_path=tmp_path / "chat.sqlite")
    client = TestClient(app)
    session_id = client.post("/api/sessions").json()["id"]
    client.post(f"/api/sessions/{session_id}/messages", json={"content": "Where is A123?"})

    response = client.get("/api/traces")

    assert response.status_code == 200
    traces = response.json()
    assert traces[0]["agent_name"] == "chat_agent"
    assert traces[0]["status"] == "ok"
    assert traces[0]["input_preview"] == "Where is A123?"
    assert traces[0]["final_output_preview"] == "Order A123 shipped."
    assert traces[0]["turn_count"] == 1
    assert traces[0]["tool_call_count"] == 0


def test_chat_app_trace_run_list_is_capped_for_long_lived_local_databases(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    store = SQLiteTraceStore(db_path)
    for index in range(101):
        run_id = store.start_run(agent_name="agent", root_input=f"input {index}")
        store.end_run(run_id, final_output=f"output {index}")
    agent = create_agent(
        name="agent",
        model="openrouter:deepseek/deepseek-v4-flash",
        provider=FakeProvider(),
        trace_db_path=db_path,
    )
    app = create_chat_app(agent, chat_db_path=tmp_path / "chat.sqlite")
    client = TestClient(app)

    response = client.get("/api/traces")

    assert response.status_code == 200
    assert len(response.json()) == 100


def test_chat_app_trace_payload_groups_graph_steps_and_tool_calls(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    planner = create_agent(
        name="planner",
        model="openai:gpt-4.1-mini",
        tools=[chat_lookup_order],
        provider=FakeProvider(
            [
                ProviderResponse.fake_tool_call(
                    ToolCall(
                        id="call_lookup_order",
                        name="chat_lookup_order",
                        arguments={"order_id": "A123"},
                    )
                ),
                ProviderResponse.fake_text("Plan: answer with shipment status."),
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
        AgentGraph("support_flow")
        .add_node(planner)
        .add_node(writer)
        .add_edge("planner", "writer")
        .set_entrypoint("planner")
    )
    run_id = graph.run("Draft a support response.").run_id
    app = create_chat_app(planner, chat_db_path=tmp_path / "chat.sqlite")
    client = TestClient(app)

    response = client.get(f"/api/triage/runs/{run_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["run"]["graph_name"] == "support_flow"
    assert [step["node_name"] for step in payload["steps"]] == ["planner", "planner", "writer"]
    assert payload["steps"][0]["model_calls"][0]["provider"] == "fake"
    assert payload["steps"][0]["model_calls"][0]["model"] == "gpt-4.1-mini"
    assert payload["steps"][0]["tool_calls"][0]["tool_name"] == "chat_lookup_order"
    assert payload["steps"][0]["tool_calls"][0]["result"] == {
        "order_id": "A123",
        "status": "shipped",
    }
    assert payload["steps"][2]["final_output"] == "Final answer."


def test_chat_app_exposes_trace_triage_payload(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="chat_agent",
        model="openrouter:deepseek/deepseek-v4-flash",
        provider=FakeProvider([ProviderResponse.fake_text("Order A123 shipped.")]),
        trace_db_path=db_path,
    )
    app = create_chat_app(agent, chat_db_path=tmp_path / "chat.sqlite")
    client = TestClient(app)
    session_id = client.post("/api/sessions").json()["id"]
    client.post(f"/api/sessions/{session_id}/messages", json={"content": "Where is A123?"})
    run_id = SQLiteTraceStore(db_path).list_runs()[0]["id"]

    response = client.get(f"/api/triage/runs/{run_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["run"]["id"] == run_id
    assert payload["failures"] == []
    assert "ClearAgent Trace Report" in payload["report"]


def test_chat_app_lists_sessions(tmp_path):
    agent = create_agent(
        name="chat_agent",
        model="openrouter:deepseek/deepseek-v4-flash",
        provider=FakeProvider(),
    )
    app = create_chat_app(agent, chat_db_path=tmp_path / "chat.sqlite")
    client = TestClient(app)

    created = client.post("/api/sessions").json()
    sessions = client.get("/api/sessions").json()

    assert sessions[0]["id"] == created["id"]
    assert sessions[0]["agent_name"] == "chat_agent"


def test_chat_app_serves_packaged_browser_client(tmp_path):
    agent = create_agent(
        name="chat_agent",
        model="openrouter:deepseek/deepseek-v4-flash",
        provider=FakeProvider(),
    )
    app = create_chat_app(agent, chat_db_path=tmp_path / "chat.sqlite")
    client = TestClient(app)

    html = client.get("/")
    script = client.get("/assets/app.js")
    styles = client.get("/assets/styles.css")

    assert html.status_code == 200
    assert "ClearAgent Chat" in html.text
    assert 'id="chat-root"' in html.text
    assert 'id="sidebar-toggle"' in html.text
    assert 'id="agent-select"' in html.text
    assert 'id="settings-toggle"' in html.text
    assert 'id="settings-panel"' in html.text
    assert 'id="traces-toggle"' in html.text
    assert 'id="trace-shell"' in html.text
    assert 'id="trace-runs"' in html.text
    assert 'id="trace-detail"' in html.text
    assert "builder" not in html.text.lower()
    assert script.status_code == 200
    assert "parseSseFrames" in script.text
    assert "submitComposer()" in script.text
    assert "event.shiftKey" in script.text
    assert "toggleSidebar" in script.text
    assert "toggleTraceMode" in script.text
    assert "loadTraceRuns" in script.text
    assert "renderTraceDetail" in script.text
    assert "navigator.clipboard.writeText" in script.text
    assert "builder" not in script.text.lower()
    assert "saveSettings" in script.text
    assert "renderMarkdown" in script.text
    assert styles.status_code == 200
    assert ".message.assistant" in styles.text
    assert ".trace-shell" in styles.text
    assert ".trace-run" in styles.text
    assert ".trace-step" in styles.text
    assert "builder" not in styles.text.lower()
    assert ".sidebar-collapsed" in styles.text
    assert "@media (max-width: 760px)" in styles.text


def test_chat_app_does_not_expose_builder_endpoints(tmp_path):
    agent = create_agent(
        name="chat_agent",
        model="openrouter:deepseek/deepseek-v4-flash",
        provider=FakeProvider(),
    )
    app = create_chat_app(agent, chat_db_path=tmp_path / "chat.sqlite")
    client = TestClient(app)

    assert client.get("/api/builder/flow").status_code == 404
    assert client.post(
        "/api/builder/plan",
        json={"instruction": "Build a support agent."},
    ).status_code == 404


def test_chat_app_exposes_agents_settings_and_models(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    agent = create_agent(
        name="chat_agent",
        model="openrouter:deepseek/deepseek-v4-flash",
        provider=FakeProvider(),
    )
    app = create_chat_app(
        agent,
        chat_db_path=tmp_path / "chat.sqlite",
        allow_settings_mutation=True,
    )
    client = TestClient(app)

    agents = client.get("/api/agents").json()
    settings = client.get("/api/settings").json()
    models = client.get("/api/models", params={"provider": "openrouter"}).json()
    anthropic_models = client.get("/api/models", params={"provider": "anthropic"}).json()

    assert agents == [{"name": "chat_agent"}]
    assert settings["provider"] == "openrouter"
    assert settings["model"] == "deepseek/deepseek-v4-flash"
    assert settings["temperature"] == 0.0
    assert settings["thinking"] == "off"
    assert "openai/gpt-4.1-mini" in [model["id"] for model in models]
    assert [model["id"] for model in anthropic_models] == [
        "claude-fable-5",
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-haiku-4-5",
        "claude-sonnet-4-20250514",
        "claude-opus-4-1-20250805",
        "claude-3-5-haiku-20241022",
    ]

    openai_models = client.get("/api/models", params={"provider": "openai"}).json()
    assert [model["id"] for model in openai_models] == [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-4.1-mini",
        "gpt-4o-mini",
    ]

    updated = client.put(
        "/api/settings",
        json={
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "temperature": 0.7,
            "thinking": "low",
        },
    ).json()

    assert updated == {
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "temperature": 0.7,
        "thinking": "low",
    }
    assert agent.model == "openai:gpt-4.1-mini"
    assert agent.temperature == 0.7


def test_chat_app_can_disable_runtime_settings_mutation(tmp_path):
    agent = create_agent(
        name="chat_agent",
        model="openrouter:deepseek/deepseek-v4-flash",
        provider=FakeProvider(),
    )
    app = create_chat_app(agent, chat_db_path=tmp_path / "chat.sqlite")
    client = TestClient(app)

    response = client.put(
        "/api/settings",
        json={
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "temperature": 0.7,
            "thinking": "low",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Runtime settings mutation is disabled."
    assert agent.model == "openrouter:deepseek/deepseek-v4-flash"


def test_chat_app_can_require_admin_token_for_runtime_settings_mutation(tmp_path):
    agent = create_agent(
        name="chat_agent",
        model="openrouter:deepseek/deepseek-v4-flash",
        provider=FakeProvider(),
    )
    app = create_chat_app(
        agent,
        chat_db_path=tmp_path / "chat.sqlite",
        allow_settings_mutation=True,
        settings_admin_token="secret-token",
    )
    client = TestClient(app)

    unauthorized = client.put(
        "/api/settings",
        json={
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "temperature": 0.7,
            "thinking": "low",
        },
    )
    authorized = client.put(
        "/api/settings",
        headers={"X-ClearAgent-Admin-Token": "secret-token"},
        json={
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "temperature": 0.7,
            "thinking": "low",
        },
    )

    assert unauthorized.status_code == 403
    assert unauthorized.json()["detail"] == "Invalid settings admin token."
    assert authorized.status_code == 200
    assert agent.model == "openai:gpt-4.1-mini"


def test_chat_app_rejects_invalid_settings_without_mutating_runtime_state(tmp_path):
    agent = create_agent(
        name="chat_agent",
        model="openrouter:deepseek/deepseek-v4-flash",
        provider=FakeProvider(),
    )
    app = create_chat_app(
        agent,
        chat_db_path=tmp_path / "chat.sqlite",
        allow_settings_mutation=True,
    )
    client = TestClient(app)

    response = client.put(
        "/api/settings",
        json={
            "provider": "openai",
            "model": "",
            "temperature": 0.7,
            "thinking": "low",
        },
    )

    current_settings = client.get("/api/settings").json()

    assert response.status_code == 400
    assert current_settings == {
        "provider": "openrouter",
        "model": "deepseek/deepseek-v4-flash",
        "temperature": 0.0,
        "thinking": "off",
    }
    assert agent.model == "openrouter:deepseek/deepseek-v4-flash"
    assert agent.temperature is None


def test_chat_settings_preserve_native_google_provider(tmp_path):
    agent = create_agent(
        name="chat_agent",
        model="google:gemini-2.5-flash",
        provider=FakeProvider(),
    )
    client = TestClient(create_chat_app(agent, chat_db_path=tmp_path / "chat.sqlite"))

    settings = client.get("/api/settings").json()
    models = client.get("/api/models", params={"provider": "google"}).json()

    assert settings["provider"] == "google"
    assert settings["model"] == "gemini-2.5-flash"
    assert "gemini-2.5-flash" in [model["id"] for model in models]


def test_chat_app_health_favicon_and_missing_resources(tmp_path):
    agent = create_agent(
        name="chat_agent",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider(),
    )
    client = TestClient(create_chat_app(agent, chat_db_path=tmp_path / "chat.sqlite"))

    assert client.get("/api/health").json() == {"status": "ok", "agent": "chat_agent"}
    assert client.get("/favicon.ico").status_code == 204
    assert client.get("/api/sessions/missing").status_code == 404
    assert client.get("/api/sessions/missing/messages").status_code == 404
    assert client.post(
        "/api/sessions/missing/messages", json={"content": "Hello"}
    ).status_code == 404
    assert client.get("/api/triage/runs/missing").status_code == 404

    session_id = client.post("/api/sessions").json()["id"]
    blank = client.post(f"/api/sessions/{session_id}/messages", json={"content": "   "})
    assert blank.status_code == 400
    assert blank.json()["detail"] == "Message content is required."


def test_chat_app_rejects_unsupported_settings_provider(tmp_path):
    agent = create_agent(
        name="chat_agent",
        model="custom:model",
        provider=FakeProvider(),
    )

    with pytest.raises(ValueError, match="do not support provider"):
        create_chat_app(agent, chat_db_path=tmp_path / "chat.sqlite")


def test_chat_request_extra_adds_openrouter_reasoning_setting():
    settings = ChatSettings(
        provider="openrouter",
        model="example/model",
        temperature=0.0,
        thinking="high",
    )

    assert _request_extra(settings) == {
        "stream": True,
        "reasoning": {"effort": "high"},
    }


def test_chat_model_listing_uses_remote_catalogs_when_keys_exist(tmp_path, monkeypatch):
    agent = create_agent(
        name="chat_agent",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider(),
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")

    def fake_get(url, **kwargs):
        request = httpx.Request("GET", url)
        if "openrouter.ai" in url:
            return httpx.Response(
                200,
                request=request,
                json={"data": [{"id": "vendor/model", "name": "Vendor Model"}, {}]},
            )
        if "openai.com" in url:
            assert kwargs["headers"]["Authorization"] == "Bearer openai-key"
            return httpx.Response(
                200,
                request=request,
                json={"data": [{"id": "gpt-test"}, "invalid"]},
            )
        assert kwargs["headers"]["x-api-key"] == "anthropic-key"
        return httpx.Response(
            200,
            request=request,
            json={"data": [{"id": "claude-test", "display_name": "Claude Test"}]},
        )

    monkeypatch.setattr("clearagent.chat.app.httpx.get", fake_get)
    client = TestClient(create_chat_app(agent, chat_db_path=tmp_path / "chat.sqlite"))

    assert client.get("/api/models?provider=openrouter").json() == [
        {"id": "vendor/model", "name": "Vendor Model"}
    ]
    assert client.get("/api/models?provider=openai").json() == [
        {"id": "gpt-test", "name": "gpt-test"}
    ]
    assert client.get("/api/models?provider=anthropic").json() == [
        {"id": "claude-test", "name": "Claude Test"}
    ]
    assert client.get("/api/models?provider=local").json() == []


def test_chat_model_listing_falls_back_when_remote_catalog_fails(tmp_path, monkeypatch):
    agent = create_agent(
        name="chat_agent",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider(),
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    monkeypatch.setattr(
        "clearagent.chat.app.httpx.get",
        lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ConnectError("offline")),
    )
    client = TestClient(create_chat_app(agent, chat_db_path=tmp_path / "chat.sqlite"))

    assert client.get("/api/models?provider=openrouter").json()[0]["id"]
    assert client.get("/api/models?provider=openai").json()[0]["id"] == "gpt-5.6-sol"
    assert client.get("/api/models?provider=anthropic").json()[0]["id"].startswith("claude")
