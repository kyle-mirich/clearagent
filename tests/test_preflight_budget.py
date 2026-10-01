import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import localcontext
import threading

import pytest

from clearagent.builds import BudgetLimits, PreflightBudget, RequestBudget
from clearagent.builds.budgets import BuildBudgetExceeded
from clearagent.builds.module import Build
from clearagent.builds import pipeline
from clearagent.builds.pipeline import PipelineSettings
from clearagent.builds.planner import plan_task
from clearagent.config import Settings
from clearagent.models import PlanningRequest
from clearagent.runtime.messages import Message
from clearagent.runtime.providers.base import FakeProvider, ProviderError, ProviderResponse
from clearagent.store import Store


def budget(*, calls=2, tokens=200, cost=0.2, bound=None):
    return PreflightBudget(
        BudgetLimits(10, 10, calls, tokens, cost),
        bound or (lambda request: RequestBudget(100, 0.1)),
    )


@pytest.mark.parametrize("limit", ["calls", "tokens", "cost"])
def test_preflight_rejects_before_provider_and_does_not_partially_reserve(limit):
    kwargs = {limit: {"calls": 1, "tokens": 100, "cost": 0.1}[limit]}
    guard = budget(**kwargs)
    provider = FakeProvider([ProviderResponse.fake_text("first"), ProviderResponse.fake_text("second")])
    request = object()
    assert pipeline._complete_with_retry(provider, request, preflight_budget=guard).output_text == "first"
    with pytest.raises(BuildBudgetExceeded):
        pipeline._complete_with_retry(provider, request, preflight_budget=guard)
    assert len(provider.completed_requests) == 1
    assert (guard.calls, guard.total_tokens, guard.cost_usd) == (1, 100, 0.1)


def test_decimal_money_admits_exact_inclusive_cap():
    guard = budget(calls=3, tokens=300, cost=0.3)
    for _ in range(3):
        guard.reserve(object())
    assert guard.cost_usd == 0.3
    with pytest.raises(BuildBudgetExceeded):
        guard.reserve(object())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_model_calls": 0}, {"max_model_calls": True}, {"max_model_calls": 1.5},
        {"max_total_tokens": -1}, {"max_total_tokens": False}, {"max_total_tokens": 1.5},
        {"max_cost_usd": -0.1}, {"max_cost_usd": float("nan")},
        {"max_cost_usd": float("inf")}, {"max_cost_usd": True},
    ],
)
def test_invalid_request_bounds_are_rejected(kwargs):
    with pytest.raises(ValueError):
        RequestBudget(**{"max_total_tokens": 100, "max_cost_usd": 0.1, **kwargs})


@pytest.mark.parametrize("kwargs", [{"calls": -1}, {"calls": True}, {"tokens": 1.5}, {"cost": float("inf")}])
def test_invalid_limits_are_rejected(kwargs):
    with pytest.raises(ValueError):
        budget(**kwargs)


def test_missing_bound_fails_closed():
    guard = budget(bound=lambda request: None)
    provider = FakeProvider()
    with pytest.raises(BuildBudgetExceeded, match="No trustworthy"):
        pipeline._complete_with_retry(provider, object(), preflight_budget=guard)
    assert provider.completed_requests == []
    assert guard.calls == 0


def test_sdk_retry_bound_is_aggregate_and_never_refunded():
    guard = budget(calls=3, tokens=900, cost=0.9, bound=lambda request: RequestBudget(900, 0.9, 3))
    response = ProviderResponse.fake_text("Known zero final usage cannot prove earlier SDK attempts were free")
    response.raw = {"usage_known": True, "usage": {"cost": 0}}
    provider = FakeProvider([response])
    pipeline._complete_with_retry(provider, object(), preflight_budget=guard)
    assert (guard.calls, guard.total_tokens, guard.cost_usd) == (3, 900, 0.9)


def test_failed_attempt_is_reserved_and_retry_is_refused(monkeypatch):
    monkeypatch.setattr(pipeline.time, "sleep", lambda seconds: None)
    guard = budget(calls=1)
    provider = FakeProvider([ProviderError("request failed: http 503"), ProviderResponse.fake_text("too late")])
    with pytest.raises(BuildBudgetExceeded):
        pipeline._complete_with_retry(provider, object(), preflight_budget=guard)
    assert len(provider.completed_requests) == 1
    assert guard.cost_usd == 0.1


def test_concurrent_reservations_never_over_admit():
    guard = budget(calls=5, tokens=500, cost=0.5)

    def attempt(_):
        try:
            guard.reserve(object())
            return True
        except BuildBudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert sum(executor.map(attempt, range(40))) == 5
    assert (guard.calls, guard.total_tokens, guard.cost_usd) == (5, 500, 0.5)


def test_empty_response_retry_reserves_again(monkeypatch):
    monkeypatch.setattr(pipeline.time, "sleep", lambda seconds: None)
    guard = budget(calls=1)
    provider = FakeProvider([ProviderResponse.fake_text(""), ProviderResponse.fake_text("too late")])
    with pytest.raises(BuildBudgetExceeded):
        pipeline._provider_completion_with_provider(
            provider, "openai:test", PipelineSettings(preflight_budget=guard),
            [Message(role="user", content="question")], max_tokens=10, response_format=None,
        )
    assert len(provider.completed_requests) == 1


def test_schema_repair_reserves_again(monkeypatch):
    provider = FakeProvider([ProviderResponse.fake_text("invalid JSON"), ProviderResponse.fake_text('{"answer":"late"}')])
    monkeypatch.setattr(pipeline, "provider_for_model", lambda model: provider)
    guard = budget(calls=1)
    with pytest.raises(BuildBudgetExceeded):
        pipeline._complete_structured(
            "openai:test", PipelineSettings(preflight_budget=guard),
            [Message(role="user", content="question")], pipeline.CandidateOutput,
        )
    assert len(provider.completed_requests) == 1


def test_tool_turns_share_the_guard():
    guard = budget(calls=1)
    provider = FakeProvider([ProviderResponse.fake_text("first")])
    wrapped = pipeline._InstrumentedToolProvider(provider, PipelineSettings(preflight_budget=guard), "openai:test")
    wrapped.complete(object())
    with pytest.raises(BuildBudgetExceeded):
        wrapped.complete(object())
    assert len(provider.completed_requests) == 1


def test_instrumented_stream_is_reserved_before_provider_iteration():
    guard = budget(calls=0)
    provider = FakeProvider([ProviderResponse.fake_text("unadmitted")])
    wrapped = pipeline._InstrumentedToolProvider(provider, PipelineSettings(preflight_budget=guard), "openai:test")
    with pytest.raises(BuildBudgetExceeded):
        list(wrapped.stream_text(object()))
    assert provider.completed_requests == []


def test_async_tool_turns_share_the_guard():
    guard = budget(calls=1)
    provider = FakeProvider([ProviderResponse.fake_text("first")])
    wrapped = pipeline._InstrumentedToolProvider(provider, PipelineSettings(preflight_budget=guard), "openai:test")
    asyncio.run(wrapped.acomplete(object()))
    with pytest.raises(BuildBudgetExceeded):
        asyncio.run(wrapped.acomplete(object()))
    assert len(provider.completed_requests) == 1


def test_async_retry_is_reserved_before_invocation(monkeypatch):
    async def no_sleep(seconds):
        return None

    monkeypatch.setattr(pipeline.asyncio, "sleep", no_sleep)
    guard = budget(calls=1)
    provider = FakeProvider([ProviderError("request failed: http 429")])
    with pytest.raises(BuildBudgetExceeded):
        asyncio.run(pipeline._acomplete_with_retry(provider, object(), preflight_budget=guard))
    assert len(provider.completed_requests) == 1


@pytest.mark.parametrize("use_thread", [False, True])
def test_async_cancellation_retains_reservation(use_thread):
    entered = threading.Event()
    release = threading.Event()

    class Provider:
        def complete(self, request):
            entered.set()
            assert release.wait(timeout=5)
            return ProviderResponse.fake_text("late")

    class AsyncProvider(Provider):
        async def acomplete(self, request):
            entered.set()
            await asyncio.Event().wait()

    guard = budget(calls=1)

    async def cancel():
        task = asyncio.create_task(pipeline._acomplete_with_retry(
            Provider() if use_thread else AsyncProvider(), object(), preflight_budget=guard,
        ))
        while not entered.is_set():
            await asyncio.sleep(0.001)
        task.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()

    asyncio.run(cancel())
    assert (guard.calls, guard.total_tokens, guard.cost_usd) == (1, 100, 0.1)


def test_build_planning_and_execution_preserve_shared_guard(monkeypatch, tmp_path):
    guard = budget(calls=1)
    build = Build(Settings(deterministic_mode=True, _env_file=None), preflight_budget=guard)
    seen = []

    def plan(goal, settings, *args, **kwargs):
        seen.append(settings.preflight_budget)
        settings.preflight_budget.reserve(object())
        return plan_task(goal)

    def execute(store, run_id, settings):
        seen.append(settings.preflight_budget)
        settings.preflight_budget.reserve(object())

    monkeypatch.setattr("clearagent.builds.module.plan_agent_brief", plan)
    monkeypatch.setattr("clearagent.builds.module.run_improvement_pipeline", execute)
    build.plan(PlanningRequest(goal="Help writers create concise release notes for users."))
    store = Store(f"sqlite:///{tmp_path / 'budget.sqlite'}")
    try:
        project = store.create_project(owner_id="test", goal="release notes", name="Notes", settings={})
        run, _ = store.create_run(owner_id="test", project_id=project.id, idempotency_key="budget", budget_profile="quick", seed=1)
        with pytest.raises(BuildBudgetExceeded):
            build.execute(store, run.id)
    finally:
        store.close()
    assert seen == [guard, guard]
    profile = build.pipeline_settings_for("quick")
    assert profile.preflight_budget is guard
    assert profile.budget_tracker.limits.max_cost_usd == float("inf")


def test_synthetic_budget_rejection_is_not_reported_as_a_skipped_batch(monkeypatch):
    async def reject(*args, **kwargs):
        raise BuildBudgetExceeded("preflight cap")

    monkeypatch.setattr(pipeline, "_acomplete_structured", reject)
    spec = plan_task("Help writers create concise release notes for users.").task_spec.model_dump()
    skipped = []
    with pytest.raises(BuildBudgetExceeded, match="preflight cap"):
        pipeline._generate_dataset_live(
            profile="quick", seed=1, task_spec=spec, n=12,
            settings=replace(PipelineSettings(), synthetic_max_concurrency=1),
            on_batch_failed=lambda *args: skipped.append(args),
        )
    assert skipped == []


def test_budget_rejection_persists_failed_run_without_promotion(monkeypatch, tmp_path):
    spec = plan_task("Help writers create concise release notes for users.").task_spec.model_dump()
    store = Store(f"sqlite:///{tmp_path / 'failed.sqlite'}")
    try:
        project = store.create_project(owner_id="test", goal="release notes", name="Notes", settings={"task_spec": spec})
        run, _ = store.create_run(owner_id="test", project_id=project.id, idempotency_key="failed-budget", budget_profile="quick", seed=1)
        monkeypatch.setattr(pipeline, "_require_live_credentials", lambda settings: None)

        def reject(**kwargs):
            raise BuildBudgetExceeded("preflight cap")

        monkeypatch.setattr(pipeline, "_generate_dataset_live", reject)
        with pytest.raises(BuildBudgetExceeded):
            pipeline.run_improvement_pipeline(store, run.id, PipelineSettings())
        failed = store.get_run(run.id)
        assert failed.status == "failed"
        assert failed.error["type"] == "BuildBudgetExceeded"
        assert failed.best_agent_version_id is None
        assert store.get_project(project.id, owner_id="test").promoted_agent_version_id is None
    finally:
        store.close()


def test_legacy_retry_behavior_without_opt_in_is_preserved(monkeypatch):
    monkeypatch.setattr(pipeline.time, "sleep", lambda seconds: None)
    provider = FakeProvider([ProviderError("request failed: http 503"), ProviderResponse.fake_text("ok")])
    assert pipeline._complete_with_retry(provider, object()).output_text == "ok"
    assert len(provider.completed_requests) == 2


def test_cost_admission_is_independent_of_ambient_decimal_precision():
    guard = budget(calls=2, cost=0.1, bound=lambda request: RequestBudget(100, 0.06))
    provider = FakeProvider([
        ProviderResponse.fake_text("first"), ProviderResponse.fake_text("unadmitted"),
    ])
    with localcontext() as context:
        context.prec = 1
        pipeline._complete_with_retry(provider, object(), preflight_budget=guard)
        with pytest.raises(BuildBudgetExceeded, match="cost limit"):
            pipeline._complete_with_retry(provider, object(), preflight_budget=guard)
    assert len(provider.completed_requests) == 1
    assert (guard.calls, guard.total_tokens, guard.cost_usd) == (1, 100, 0.06)


def test_positive_cost_cannot_be_rounded_away_at_an_exhausted_cap():
    bounds = iter((RequestBudget(100, 1.0), RequestBudget(100, 1e-28)))
    guard = budget(calls=2, cost=1.0, bound=lambda request: next(bounds))
    provider = FakeProvider([
        ProviderResponse.fake_text("first"), ProviderResponse.fake_text("unadmitted"),
    ])
    with localcontext() as context:
        context.prec = 28
        pipeline._complete_with_retry(provider, object(), preflight_budget=guard)
        with pytest.raises(BuildBudgetExceeded, match="cost limit"):
            pipeline._complete_with_retry(provider, object(), preflight_budget=guard)
    assert len(provider.completed_requests) == 1
    assert (guard.calls, guard.total_tokens, guard.cost_usd) == (1, 100, 1.0)


def test_async_empty_response_retry_reserves_again(monkeypatch):
    async def no_sleep(seconds):
        return None

    monkeypatch.setattr(pipeline.asyncio, "sleep", no_sleep)
    guard = budget(calls=1)
    provider = FakeProvider([
        ProviderResponse.fake_text(""), ProviderResponse.fake_text("unadmitted"),
    ])
    with pytest.raises(BuildBudgetExceeded):
        asyncio.run(pipeline._aprovider_completion_with_provider(
            provider, "openai:test", PipelineSettings(preflight_budget=guard),
            [Message(role="user", content="question")], max_tokens=10,
            response_format=None,
        ))
    assert len(provider.completed_requests) == 1
    assert (guard.calls, guard.total_tokens, guard.cost_usd) == (1, 100, 0.1)


def test_synthetic_budget_rejection_finishes_cleanup_before_returning(monkeypatch):
    canceled = []

    class BlockingProvider(FakeProvider):
        async def acomplete(self, request):
            self.completed_requests.append(request)
            try:
                await asyncio.Event().wait()
            finally:
                canceled.append(request)

    provider = BlockingProvider()
    monkeypatch.setattr(pipeline, "provider_for_model", lambda model: provider)
    monkeypatch.setattr(pipeline, "SYNTHETIC_BATCH_SIZE", 1)
    guard = budget(calls=1)
    spec = plan_task("Help writers create concise release notes for users.").task_spec.model_dump()

    async def reject_and_check_cleanup():
        with pytest.raises(BuildBudgetExceeded):
            await pipeline._generate_dataset_live_async(
                profile="quick", seed=1, task_spec=spec, n=12,
                settings=PipelineSettings(
                    preflight_budget=guard, synthetic_max_concurrency=2,
                ),
            )
        assert canceled == provider.completed_requests
        assert len(provider.completed_requests) == 1
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(reject_and_check_cleanup())
    assert (guard.calls, guard.total_tokens, guard.cost_usd) == (1, 100, 0.1)
