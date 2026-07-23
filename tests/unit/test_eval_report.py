import pytest

from clearagent.evals.report import EvalCaseResult, EvalReport


def test_eval_report_assert_passed_includes_variant_context():
    report = EvalReport(
        suite_name="matrix",
        suite_type="output",
        agent_name="support",
        model="matrix",
        passed=0,
        failed=1,
        results=[
            EvalCaseResult(
                suite_name="matrix",
                case_name="expected output",
                input="hello",
                final_output="No idea",
                passed=False,
                checks=[{"name": "contains", "passed": False}],
                run_id="run_123",
                trace_db_path=".clearagent/traces.sqlite",
                latency_ms=12,
                cost_usd=0.0,
                variant={"model": "openai:gpt-4.1-mini", "temperature": 0.2},
            )
        ],
        suite_run_id="suite_run_123",
    )

    with pytest.raises(AssertionError) as error:
        report.assert_passed()

    assert "variant={'model': 'openai:gpt-4.1-mini', 'temperature': 0.2}" in str(error.value)
