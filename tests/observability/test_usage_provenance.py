from types import SimpleNamespace

import pytest

from clearagent.builds.pipeline import PipelineSettings, _record_model_call
from clearagent.runtime.providers.base import Usage


@pytest.mark.parametrize("tokens,cost,expected", [
    (None, None, None),
    (Usage(prompt_tokens=12, completion_tokens=3, total_tokens=15), 0.0012, 0.0012),
    (Usage(), 0, 0.0),
    (Usage(), float("nan"), None),
    (Usage(), float("inf"), None),
    (Usage(), True, None),
    (Usage(), -1, None),
])
def test_telemetry_separates_provider_usage_from_budget_estimates(tokens, cost, expected):
    events = []
    response = SimpleNamespace(usage=tokens, model="test", provider="fake", output_text="secret",
                               raw={"usage": {"cost": cost}})
    settings = PipelineSettings(on_model_call=events.append)
    for _ in range(2):
        _record_model_call(settings, model_uri="openai:test", response=response,
                           request_messages=[], latency_ms=15, max_tokens=100, purpose="task")
    assert events[0]["usage_known"] is (tokens is not None)
    assert events[0]["reported_cost_usd"] == expected
    assert len(events[0]["call_id"]) == 32
    assert events[0]["call_id"] != events[1]["call_id"]
    assert "response_text" not in events[0]


def test_usage_callback_failure_does_not_break_model_execution():
    def fail(_payload):
        raise RuntimeError("database unavailable")
    _record_model_call(PipelineSettings(on_model_call=fail), model_uri="openai:test",
                       response=SimpleNamespace(usage=None, raw={}, model="test", provider="fake"),
                       request_messages=[], latency_ms=1, max_tokens=1, purpose="task")


def test_explicit_provider_unknown_marker_overrides_zero_usage_defaults():
    events = []
    _record_model_call(PipelineSettings(on_model_call=events.append), model_uri="openai:test",
                       response=SimpleNamespace(usage=Usage(), raw={"usage_known": False}),
                       request_messages=[], latency_ms=1, max_tokens=1, purpose="task")
    assert events[0]["usage_known"] is False
    assert events[0]["reported_cost_usd"] is None


@pytest.mark.parametrize("metadata,expected", [
    ({}, None),
    ({"input_tokens": 0, "output_tokens": 0}, 0),
    ({"input_tokens": 4}, None),
    ({"input_tokens": True, "output_tokens": 1}, None),
    ({"input_tokens": 4, "output_tokens": 1, "total_tokens": 5}, 4),
])
def test_langchain_provider_usage_provenance_reaches_build_events(metadata, expected):
    from clearagent.runtime.providers.base import ProviderRequest
    from clearagent.runtime.providers.langchain_provider import _to_provider_response
    request = ProviderRequest(provider="openai", model="test", api_shape="openai_responses", body={})
    # SimpleNamespace allows malformed provider metadata to exercise validation.
    message = SimpleNamespace(content="private output", tool_calls=[], usage_metadata=metadata,
                              response_metadata={"token_usage": {"cost": 0.002}})
    response = _to_provider_response(request, message)
    events = []
    _record_model_call(PipelineSettings(on_model_call=events.append), model_uri="openai:test",
                       response=response, request_messages=[], latency_ms=1, max_tokens=1, purpose="task")
    payload = events[0]
    assert payload["usage_known"] is (expected is not None)
    assert (payload["input_tokens"] if payload["usage_known"] else None) == expected
    assert payload["reported_cost_usd"] == 0.002
    assert "private output" not in repr(payload)


def test_replayed_usage_is_never_a_new_provider_charge(monkeypatch):
    monkeypatch.setenv("CLEARAGENT_OPENAI_FIXTURE_MODE", "replay")
    events = []
    _record_model_call(PipelineSettings(on_model_call=events.append), model_uri="openai:test",
                       response=SimpleNamespace(usage=Usage(prompt_tokens=100, completion_tokens=10),
                                                raw={"usage": {"cost": 2}}),
                       request_messages=[], latency_ms=1, max_tokens=1, purpose="task")
    assert events[0]["usage_source"] == "replay"
    assert events[0]["usage_known"] is False
    assert events[0]["reported_cost_usd"] is None


def test_build_execute_exposes_the_existing_model_observer_seam(monkeypatch):
    from clearagent.builds import Build
    from clearagent.config import Settings
    captured = []
    def execute(store, run_id, settings):
        assert run_id == "run-test"
        assert settings.budget_tracker is not None
        settings.on_model_call({"call_id": "one"})
    monkeypatch.setattr("clearagent.builds.module.run_improvement_pipeline", execute)
    store = SimpleNamespace(get_run=lambda _: SimpleNamespace(budget_profile="quick"))
    Build(Settings(_env_file=None)).execute(store, "run-test", on_model_call=captured.append)
    assert captured == [{"call_id": "one"}]
