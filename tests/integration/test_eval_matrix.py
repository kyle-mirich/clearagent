import pytest

from clearagent import create_agent
from clearagent.evals.runner import EvalRunner
from clearagent.evals.suite import EvalCase, EvalSuite
from clearagent.providers.base import FakeProvider, ProviderError, ProviderResponse
from clearagent.storage.sqlite import SQLiteTraceStore


def test_eval_runner_executes_model_and_temperature_matrix(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider(),
        trace_db_path=db_path,
    )
    suite = EvalSuite(
        name="matrix",
        type="output",
        matrix={
            "models": ["openai:gpt-4.1-mini", "openrouter:openai/gpt-4o-mini"],
            "temperatures": [0.0, 0.2],
        },
        cases=[
            EvalCase(
                name="shipped",
                input="Where is A123?",
                checks=[{"contains": "shipped"}],
            )
        ],
    )

    report = EvalRunner(
        agent,
        provider_factory=lambda model: FakeProvider(
            [ProviderResponse.fake_text(f"{model} shipped")]
        ),
    ).run_matrix(suite)

    assert report.passed == 4
    assert report.failed == 0
    assert {result.variant["model"] for result in report.results} == {
        "openai:gpt-4.1-mini",
        "openrouter:openai/gpt-4o-mini",
    }
    assert {result.variant["temperature"] for result in report.results} == {0.0, 0.2}
    store = SQLiteTraceStore(db_path)
    rows = store.list_eval_case_results(report.suite_run_id)
    assert len(rows) == 4
    assert {row["variant_json"] for row in rows} == {
        '{"model":"openai:gpt-4.1-mini","temperature":0.0}',
        '{"model":"openai:gpt-4.1-mini","temperature":0.2}',
        '{"model":"openrouter:openai/gpt-4o-mini","temperature":0.0}',
        '{"model":"openrouter:openai/gpt-4o-mini","temperature":0.2}',
    }


def test_eval_runner_executes_temperature_only_matrix_against_current_model(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider(),
        trace_db_path=db_path,
    )
    suite = EvalSuite(
        name="matrix",
        type="output",
        matrix={
            "temperatures": [0.0, 0.2],
        },
        cases=[
            EvalCase(
                name="shipped",
                input="Where is A123?",
                checks=[{"contains": "shipped"}],
            )
        ],
    )

    report = EvalRunner(
        agent,
        provider_factory=lambda model: FakeProvider(
            [ProviderResponse.fake_text(f"{model} shipped"), ProviderResponse.fake_text(f"{model} shipped")]
        ),
    ).run_matrix(suite)

    assert report.passed == 2
    assert report.failed == 0
    assert {result.variant["model"] for result in report.results} == {"openai:gpt-4.1-mini"}
    assert {result.variant["temperature"] for result in report.results} == {0.0, 0.2}


def test_temperature_only_matrix_preserves_custom_provider(tmp_path, monkeypatch):
    db_path = tmp_path / "traces.sqlite"
    provider = FakeProvider(
        [
            ProviderResponse.fake_text("shipped at zero"),
            ProviderResponse.fake_text("shipped at point two"),
        ]
    )
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=provider,
        trace_db_path=db_path,
    )
    suite = EvalSuite(
        name="matrix",
        matrix={"temperatures": [0.0, 0.2]},
        cases=[
            EvalCase(
                name="shipped",
                input="Where is A123?",
                checks=[{"contains": "shipped"}],
            )
        ],
    )

    def unexpected_registry_lookup(model):
        pytest.fail(f"temperature-only matrix replaced the custom provider for {model}")

    monkeypatch.setattr("clearagent.evals.runner.provider_for_model", unexpected_registry_lookup)

    report = EvalRunner(agent).run_matrix(suite)

    assert report.passed == 2
    assert len(provider.completed_requests) == 2
    assert agent.provider is provider


def test_matrix_error_result_persists_its_variant(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    provider = FakeProvider([ProviderError("provider exploded")])
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=provider,
        trace_db_path=db_path,
    )
    suite = EvalSuite(
        name="matrix",
        matrix={"temperatures": [0.2]},
        cases=[
            EvalCase(
                name="shipped",
                input="Where is A123?",
                checks=[{"contains": "shipped"}],
            )
        ],
    )

    report = EvalRunner(agent).run_matrix(suite)

    rows = SQLiteTraceStore(db_path).list_eval_case_results(report.suite_run_id)
    assert report.failed == 1
    assert rows[0]["variant_json"] == (
        '{"model":"openai:gpt-4.1-mini","temperature":0.2}'
    )
