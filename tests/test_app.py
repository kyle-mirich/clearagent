from fastapi.testclient import TestClient

from clearagent.app import create_app
from clearagent.config import Settings
from clearagent.runtime.providers.base import FakeProvider, ProviderResponse


def client_with_fake(monkeypatch) -> TestClient:
    provider = FakeProvider([ProviderResponse.fake_text("LANGCHAIN OK")])
    monkeypatch.setattr("clearagent.app.provider_for_model", lambda _uri: provider)
    app = create_app(Settings(deterministic_mode=True, _env_file=None))
    return TestClient(app)


def test_health_and_readiness(monkeypatch):
    client = client_with_fake(monkeypatch)
    assert client.get("/healthz").json() == {"status": "ok"}
    ready = client.get("/readyz").json()
    assert ready["status"] == "ok"
    assert ready["deterministic_mode"] is True


def test_invoke_returns_answer_and_usage(monkeypatch):
    client = client_with_fake(monkeypatch)
    response = client.post("/api/v1/invoke", json={"message": "Say hi"})
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "LANGCHAIN OK"
    assert body["latency_ms"] >= 0
    assert body["usage"]["total_tokens"] == 0


def test_invoke_rejects_empty_message(monkeypatch):
    client = client_with_fake(monkeypatch)
    assert client.post("/api/v1/invoke", json={"message": ""}).status_code == 422


def test_invoke_stream_returns_server_sent_events(monkeypatch):
    client = client_with_fake(monkeypatch)
    with client.stream("POST", "/api/v1/invoke/stream", json={"message": "Say hi"}) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"type": "delta"' in body
    assert '"type": "done"' in body
