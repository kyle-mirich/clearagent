from clearagent import create_agent
from clearagent.evals.iteration import run_eval_iterations
from clearagent.evals.suite import EvalCase, EvalSuite
from clearagent.providers.base import FakeProvider, ProviderResponse


def test_run_eval_iterations_summarizes_variants(tmp_path):
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider(
            [
                ProviderResponse.fake_text("shipped"),
                ProviderResponse.fake_text("pending"),
            ]
        ),
        trace_db_path=tmp_path / "traces.sqlite",
    )
    suite = EvalSuite(
        name="smoke",
        cases=[EvalCase(name="shipping", input="Where is A123?", checks=[{"contains": "shipped"}])],
    )

    summary = run_eval_iterations(
        agent, suite, models=["openai:gpt-4.1-mini"], temperatures=[0.0, 0.7]
    )

    assert summary["total_variants"] == 2
    assert summary["variants"][0]["passed"] == 1
    assert summary["variants"][1]["failed"] == 1
    assert agent.model == "openai:gpt-4.1-mini"
    assert agent.temperature is None
