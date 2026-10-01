import asyncio
from concurrent.futures import ThreadPoolExecutor
import contextvars
import threading

import pytest

from clearagent.builds import pipeline
from clearagent.runtime.messages import Message
from clearagent.runtime.providers.base import FakeProvider, ProviderError, ProviderResponse
from clearagent.runtime.providers.registry import provider_for_model


@pytest.mark.parametrize("outcome", ["success", "provider_error", "invalid_output"])
def test_tool_case_closes_its_owned_provider_after_success_or_error(monkeypatch, outcome):
    responses = {
        "success": ProviderResponse.fake_text('{"answer":"ok"}'),
        "provider_error": ProviderError("invalid provider configuration"),
        "invalid_output": ProviderResponse.fake_text('{"unexpected":"value"}'),
    }

    class CloseableProvider(FakeProvider):
        close_count = 0

        def close(self):
            self.close_count += 1

    provider = CloseableProvider([responses[outcome]])
    monkeypatch.setattr(pipeline, "provider_for_model", lambda model: provider)
    spec = {
        "name": "Lifecycle probe",
        "tool_definitions": [],
        "output_schema": {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        },
    }
    if outcome == "success":
        assert pipeline._execute_tool_agent(
            "Answer concisely", {"input": {"message": "question"}}, spec,
            pipeline.PipelineSettings(),
        ) == {"answer": "ok"}
    else:
        with pytest.raises(Exception):
            pipeline._execute_tool_agent(
                "Answer concisely", {"input": {"message": "question"}}, spec,
                pipeline.PipelineSettings(),
            )
    assert provider.close_count == 1


@pytest.mark.parametrize("late_error", [False, True])
def test_canceled_thread_completion_defers_close_and_never_retries(monkeypatch, late_error):
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    closed = threading.Event()
    early_closes = []

    class BlockingProvider(FakeProvider):
        acomplete = None

        def complete(self, request):
            self.completed_requests.append(request)
            entered.set()
            try:
                assert release.wait(timeout=5)
                if late_error:
                    raise ProviderError("request failed: http 503")
                return ProviderResponse.fake_text("late")
            finally:
                finished.set()

        def close(self):
            if not finished.is_set():
                early_closes.append(True)
            closed.set()

    provider = BlockingProvider()
    monkeypatch.setattr(pipeline, "provider_for_model", lambda model: provider)

    async def cancel_then_release():
        errors = []
        asyncio.get_running_loop().set_exception_handler(lambda loop, context: errors.append(context))
        task = asyncio.create_task(pipeline._aprovider_completion(
            "openai:test", pipeline.PipelineSettings(),
            [Message(role="user", content="question")], max_tokens=10,
            response_format=None,
        ))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=1)
            assert not closed.is_set()
        finally:
            release.set()
            assert await asyncio.to_thread(finished.wait, 2)
        assert await asyncio.to_thread(closed.wait, 2)
        await asyncio.sleep(0)
        assert errors == []

    asyncio.run(cancel_then_release())
    assert len(provider.completed_requests) == 1
    assert early_closes == []


@pytest.mark.parametrize("family", ["openai", "anthropic"])
def test_build_cleanup_preserves_shared_native_sdk_transports(monkeypatch, family):
    monkeypatch.setenv("OPENAI_API_KEY", "offline-lifecycle-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline-lifecycle-test")
    monkeypatch.setenv("LANGCHAIN_OPENAI_TCP_KEEPALIVE", "0")
    first = provider_for_model(f"{family}:lifecycle-test")
    second = provider_for_model(f"{family}:lifecycle-test")
    if family == "openai":
        first_sync = first.chat_model.root_client
        second_sync = second.chat_model.root_client
        first_async = first.chat_model.root_async_client
        second_async = second.chat_model.root_async_client
    else:
        first_sync = first.chat_model._client
        second_sync = second.chat_model._client
        first_async = first.chat_model._async_client
        second_async = second.chat_model._async_client
    assert first_sync is not second_sync
    assert first_async is not second_async
    assert first_sync._client is second_sync._client
    assert first_async._client is second_async._client
    pipeline._close_provider(first)
    assert not second_sync.is_closed()
    assert not second_async.is_closed()


def test_queued_sync_completion_is_not_started_after_cancel(monkeypatch):
    occupied = threading.Event()
    release = threading.Event()
    built = threading.Event()
    closed = threading.Event()

    class Provider(FakeProvider):
        acomplete = None

        def build_request(self, **kwargs):
            built.set()
            return super().build_request(**kwargs)

        def close(self):
            closed.set()

    provider = Provider([ProviderResponse.fake_text("late")])
    monkeypatch.setattr(pipeline, "provider_for_model", lambda model: provider)

    def occupy():
        occupied.set()
        assert release.wait(timeout=5)

    async def cancel():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        first = loop.run_in_executor(None, occupy)
        while not occupied.is_set():
            await asyncio.sleep(0.001)
        task = asyncio.create_task(pipeline._aprovider_completion(
            "openai:test", pipeline.PipelineSettings(), [Message(role="user", content="question")],
            max_tokens=10, response_format=None,
        ))
        try:
            while not built.is_set():
                await asyncio.sleep(0.001)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
        finally:
            release.set()
        await first

    asyncio.run(cancel())
    assert provider.completed_requests == []
    assert closed.is_set()


def test_owned_thread_completion_preserves_caller_context(monkeypatch):
    marker = contextvars.ContextVar("offline_lifecycle_marker", default="missing")
    seen = []

    class Provider(FakeProvider):
        acomplete = None

        def complete(self, request):
            seen.append(marker.get())
            return ProviderResponse.fake_text("done")

    monkeypatch.setattr(pipeline, "provider_for_model", lambda model: Provider())
    token = marker.set("caller")
    try:
        assert asyncio.run(pipeline._aprovider_completion(
            "openai:test", pipeline.PipelineSettings(), [Message(role="user", content="question")],
            max_tokens=10, response_format=None,
        )) == "done"
    finally:
        marker.reset(token)
    assert seen == ["caller"]
