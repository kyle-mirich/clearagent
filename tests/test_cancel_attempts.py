import asyncio

import pytest

from clearagent.builds import pipeline
from clearagent.builds import BudgetLimits, PreflightBudget, RequestBudget
from clearagent.builds.datasets import generate_synthetic_examples
from clearagent.builds.planner import plan_task
from clearagent.runtime.providers.base import FakeProvider, ProviderError, ProviderResponse
from clearagent.runtime.messages import Message
from clearagent.store import Store


def test_cancel_during_task_prevents_judges_and_queued_cases(monkeypatch, tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'cancel-attempts.sqlite'}")
    goal = "Help writers summarize release notes clearly for end users."
    spec = plan_task(goal).task_spec.model_dump()
    project = store.create_project(owner_id="test", goal=goal, name="Notes", settings={"task_spec": spec})
    run, _ = store.create_run(owner_id="test", project_id=project.id, idempotency_key="cancel-attempts", budget_profile="quick", seed=1, dataset_size=12)
    calls = []

    class CancelingProvider(FakeProvider):
        def complete(self, request):
            calls.append(request.response_format.name)
            if request.response_format.name == "CandidateOutput":
                store.update_run(run.id, status="canceled", stage="canceled")
                return ProviderResponse.fake_text('{"answer":"A concise release summary."}')
            raise ProviderError("A judge ran after cancellation")

    monkeypatch.setattr(pipeline, "provider_for_model", lambda model: CancelingProvider())
    monkeypatch.setattr(pipeline, "_require_live_credentials", lambda settings: None)
    monkeypatch.setattr(pipeline, "_generate_dataset_live", lambda **kwargs: generate_synthetic_examples(
        profile=kwargs["profile"], seed=kwargs["seed"], task_spec=kwargs["task_spec"], n=kwargs["n"],
    ))
    try:
        pipeline.run_improvement_pipeline(store, run.id, pipeline.PipelineSettings(max_concurrency=1))
        assert calls == ["CandidateOutput"]
        final = store.get_run(run.id)
        assert final.status == "canceled"
        assert final.error is None
        assert final.best_agent_version_id is None
    finally:
        store.close()


@pytest.mark.parametrize("async_call", [False, True])
def test_cancel_guard_prevents_retry_and_new_budget_reservation(monkeypatch, async_call):
    guard_calls = []

    def guard():
        guard_calls.append(True)
        if len(guard_calls) > 1:
            raise pipeline.RunCanceled

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr(pipeline.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(pipeline.asyncio, "sleep", no_sleep)
    budget = PreflightBudget(BudgetLimits(10, 10, 2, 200, 0.2), lambda request: RequestBudget(100, 0.1))
    provider = FakeProvider([ProviderError("request failed: http 503"), ProviderResponse.fake_text("late")])
    with pytest.raises(pipeline.RunCanceled):
        if async_call:
            asyncio.run(pipeline._acomplete_with_retry(provider, object(), preflight_budget=budget, before_provider_call=guard))
        else:
            pipeline._complete_with_retry(provider, object(), preflight_budget=budget, before_provider_call=guard)
    assert len(provider.completed_requests) == 1
    assert (budget.calls, budget.total_tokens, budget.cost_usd) == (1, 100, 0.1)


@pytest.mark.parametrize("response", ["", "invalid JSON"])
def test_cancel_guard_prevents_blank_and_schema_repair_attempts(monkeypatch, response):
    checks = []

    def guard():
        checks.append(True)
        if len(checks) > 1:
            raise pipeline.RunCanceled

    monkeypatch.setattr(pipeline.time, "sleep", lambda seconds: None)
    provider = FakeProvider([ProviderResponse.fake_text(response), ProviderResponse.fake_text('{"answer":"late"}')])
    monkeypatch.setattr(pipeline, "provider_for_model", lambda model: provider)
    with pytest.raises(pipeline.RunCanceled):
        pipeline._complete_structured(
            "openai:test", pipeline.PipelineSettings(before_provider_call=guard),
            [Message(role="user", content="question")], pipeline.CandidateOutput,
        )
    assert len(provider.completed_requests) == 1


@pytest.mark.parametrize("method", ["complete", "acomplete", "stream_text"])
def test_canceled_tool_provider_does_not_start_io(method):
    def guard():
        raise pipeline.RunCanceled

    provider = FakeProvider([ProviderResponse.fake_text("unadmitted")])
    wrapped = pipeline._InstrumentedToolProvider(provider, pipeline.PipelineSettings(before_provider_call=guard), "openai:test")
    with pytest.raises(pipeline.RunCanceled):
        if method == "acomplete":
            asyncio.run(wrapped.acomplete(object()))
        elif method == "stream_text":
            list(wrapped.stream_text(object()))
        else:
            wrapped.complete(object())
    assert provider.completed_requests == []
