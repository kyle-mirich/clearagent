import asyncio
import threading

from fastapi.testclient import TestClient

from clearagent.app import create_app
from clearagent.config import Settings
from clearagent.runtime.providers.base import FakeProvider, ProviderResponse
from clearagent.app import _stream_text


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


def test_invoke_stream_sends_the_system_instruction_once(monkeypatch):
    provider = FakeProvider([ProviderResponse.fake_text("answer")])
    monkeypatch.setattr("clearagent.app.provider_for_model", lambda _uri: provider)
    client = TestClient(create_app(Settings(_env_file=None)))
    response = client.post("/api/v1/invoke/stream", json={"message": "Say hi", "instruction": "Be brief"})
    assert response.status_code == 200
    messages = provider.completed_requests[0].body["messages"]
    assert messages == [{"role": "system", "content": "Be brief"}, {"role": "user", "content": "Say hi"}]


def test_http_stream_closes_the_agent_iterator_when_abandoned():
    closed = threading.Event()

    class StreamingAgent:
        def stream_text(self, _input):
            try:
                yield "partial answer"
                yield "remaining answer"
            finally:
                closed.set()

    async def abandon():
        stream = _stream_text(StreamingAgent(), "Question")
        assert await anext(stream) == "partial answer"
        await stream.aclose()
        assert closed.is_set()

    asyncio.run(abandon())


def test_http_stream_cancellation_closes_after_inflight_next_finishes():
    started = threading.Event()
    release = threading.Event()
    closed = threading.Event()

    class StreamingAgent:
        def stream_text(self, _input):
            try:
                started.set()
                assert release.wait(timeout=5)
                yield "answer"
            finally:
                closed.set()

    async def cancel():
        stream = _stream_text(StreamingAgent(), "Question")
        next_chunk = asyncio.create_task(anext(stream))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            next_chunk.cancel()
            await asyncio.sleep(0)
        finally:
            release.set()
        try:
            await next_chunk
        except asyncio.CancelledError:
            pass
        assert closed.is_set()

    asyncio.run(cancel())
