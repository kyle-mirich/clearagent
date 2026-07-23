from clearagent import create_agent
from clearagent.evals.runner import EvalRunner
from clearagent.evals.suite import EvalCase, EvalSuite
from clearagent.providers.base import FakeProvider, ProviderResponse
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
