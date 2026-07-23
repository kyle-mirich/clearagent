from typer.testing import CliRunner

from clearagent.cli import app
from clearagent.evals.baseline import BaselineComparison, compare_baseline, save_baseline
from clearagent.storage.sqlite import SQLiteTraceStore


def test_baseline_save_and_compare_detects_regression_and_improvement(tmp_path):
    store = SQLiteTraceStore(tmp_path / "traces.sqlite")
    baseline_run = store.start_eval_suite_run(
        suite_name="smoke", suite_type="output", agent_name="support", model="openai:gpt"
    )
    run_id = store.start_run(agent_name="support", root_input="a")
    store.save_eval_case_result(
        suite_run_id=baseline_run,
        run_id=run_id,
        suite_name="smoke",
        case_name="case a",
        input="a",
        final_output="ok",
        passed=True,
        checks=[],
        latency_ms=1,
        cost_usd=0,
    )
    store.save_eval_case_result(
        suite_run_id=baseline_run,
        run_id=run_id,
        suite_name="smoke",
        case_name="case b",
        input="b",
        final_output="bad",
        passed=False,
        checks=[],
        latency_ms=1,
        cost_usd=0,
    )
    store.end_eval_suite_run(baseline_run, passed=1, failed=1)
    save_baseline(store, baseline_run, name="v1")

    current_run = store.start_eval_suite_run(
        suite_name="smoke", suite_type="output", agent_name="support", model="openai:gpt"
    )
    store.save_eval_case_result(
        suite_run_id=current_run,
        run_id=run_id,
        suite_name="smoke",
        case_name="case a",
        input="a",
        final_output="bad",
        passed=False,
        checks=[],
        latency_ms=1,
        cost_usd=0,
    )
    store.save_eval_case_result(
        suite_run_id=current_run,
        run_id=run_id,
        suite_name="smoke",
        case_name="case b",
        input="b",
        final_output="ok",
        passed=True,
        checks=[],
        latency_ms=1,
        cost_usd=0,
    )

    comparison = compare_baseline(store, "v1", current_run)

    assert isinstance(comparison, BaselineComparison)
    assert comparison.regressions == ["case a"]
    assert comparison.improvements == ["case b"]


def test_baseline_cli_missing_inputs_fail_with_clear_message(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    runner = CliRunner()

    missing_suite = runner.invoke(
        app,
        ["baseline", "save", "suite_missing", "--name", "v1", "--trace-db", str(db_path)],
    )
    missing_baseline = runner.invoke(
        app,
        ["baseline", "compare", "v1", "suite_missing", "--trace-db", str(db_path)],
    )

    assert missing_suite.exit_code != 0
    assert "Missing eval suite run" in missing_suite.output
    assert missing_baseline.exit_code != 0
    assert "Missing baseline" in missing_baseline.output


def test_compare_baseline_requires_current_suite_run(tmp_path):
    store = SQLiteTraceStore(tmp_path / "traces.sqlite")
    baseline_run = store.start_eval_suite_run(
        suite_name="smoke", suite_type="output", agent_name="support", model="openai:gpt"
    )
    run_id = store.start_run(agent_name="support", root_input="a")
    store.save_eval_case_result(
        suite_run_id=baseline_run,
        run_id=run_id,
        suite_name="smoke",
        case_name="case a",
        input="a",
        final_output="ok",
        passed=True,
        checks=[],
        latency_ms=1,
        cost_usd=0,
    )
    store.end_eval_suite_run(baseline_run, passed=1, failed=0)
    save_baseline(store, baseline_run, name="v1")

    try:
        compare_baseline(store, "v1", "suite_missing")
    except ValueError as exc:
        assert "Missing eval suite run" in str(exc)
    else:
        raise AssertionError("compare_baseline should reject a missing suite run")


def test_compare_baseline_rejects_mismatched_suite_name(tmp_path):
    store = SQLiteTraceStore(tmp_path / "traces.sqlite")
    baseline_run = store.start_eval_suite_run(
        suite_name="smoke", suite_type="output", agent_name="support", model="openai:gpt"
    )
    run_id = store.start_run(agent_name="support", root_input="a")
    store.save_eval_case_result(
        suite_run_id=baseline_run,
        run_id=run_id,
        suite_name="smoke",
        case_name="case a",
        input="a",
        final_output="ok",
        passed=True,
        checks=[],
        latency_ms=1,
        cost_usd=0,
    )
    store.end_eval_suite_run(baseline_run, passed=1, failed=0)
    save_baseline(store, baseline_run, name="v1")

    current_run = store.start_eval_suite_run(
        suite_name="safety", suite_type="output", agent_name="support", model="openai:gpt"
    )

    try:
        compare_baseline(store, "v1", current_run)
    except ValueError as exc:
        assert "suite_name" in str(exc)
    else:
        raise AssertionError("compare_baseline should reject a mismatched suite name")


def test_compare_baseline_rejects_mismatched_agent_or_model(tmp_path):
    store = SQLiteTraceStore(tmp_path / "traces.sqlite")
    baseline_run = store.start_eval_suite_run(
        suite_name="smoke", suite_type="output", agent_name="support", model="openai:gpt"
    )
    run_id = store.start_run(agent_name="support", root_input="a")
    store.save_eval_case_result(
        suite_run_id=baseline_run,
        run_id=run_id,
        suite_name="smoke",
        case_name="case a",
        input="a",
        final_output="ok",
        passed=True,
        checks=[],
        latency_ms=1,
        cost_usd=0,
    )
    store.end_eval_suite_run(baseline_run, passed=1, failed=0)
    save_baseline(store, baseline_run, name="v1")

    mismatched_run = store.start_eval_suite_run(
        suite_name="smoke", suite_type="output", agent_name="other-support", model="openai:gpt"
    )

    try:
        compare_baseline(store, "v1", mismatched_run)
    except ValueError as exc:
        assert "agent_name" in str(exc)
    else:
        raise AssertionError("compare_baseline should reject a mismatched agent or model")


def test_compare_baseline_rejects_case_set_drift(tmp_path):
    store = SQLiteTraceStore(tmp_path / "traces.sqlite")
    baseline_run = store.start_eval_suite_run(
        suite_name="smoke", suite_type="output", agent_name="support", model="openai:gpt"
    )
    run_id = store.start_run(agent_name="support", root_input="a")
    store.save_eval_case_result(
        suite_run_id=baseline_run,
        run_id=run_id,
        suite_name="smoke",
        case_name="case a",
        input="a",
        final_output="ok",
        passed=True,
        checks=[],
        latency_ms=1,
        cost_usd=0,
    )
    store.end_eval_suite_run(baseline_run, passed=1, failed=0)
    save_baseline(store, baseline_run, name="v1")

    current_run = store.start_eval_suite_run(
        suite_name="smoke", suite_type="output", agent_name="support", model="openai:gpt"
    )
    store.save_eval_case_result(
        suite_run_id=current_run,
        run_id=run_id,
        suite_name="smoke",
        case_name="case a",
        input="a",
        final_output="ok",
        passed=True,
        checks=[],
        latency_ms=1,
        cost_usd=0,
    )
    store.save_eval_case_result(
        suite_run_id=current_run,
        run_id=run_id,
        suite_name="smoke",
        case_name="case b",
        input="b",
        final_output="ok",
        passed=True,
        checks=[],
        latency_ms=1,
        cost_usd=0,
    )

    try:
        compare_baseline(store, "v1", current_run)
    except ValueError as exc:
        assert "case set" in str(exc)
    else:
        raise AssertionError("compare_baseline should reject case-set drift")


def test_compare_baseline_rejects_mismatched_suite_type(tmp_path):
    store = SQLiteTraceStore(tmp_path / "traces.sqlite")
    baseline_run = store.start_eval_suite_run(
        suite_name="smoke", suite_type="output", agent_name="support", model="openai:gpt"
    )
    run_id = store.start_run(agent_name="support", root_input="a")
    store.save_eval_case_result(
        suite_run_id=baseline_run,
        run_id=run_id,
        suite_name="smoke",
        case_name="case a",
        input="a",
        final_output="ok",
        passed=True,
        checks=[],
        latency_ms=1,
        cost_usd=0,
    )
    store.end_eval_suite_run(baseline_run, passed=1, failed=0)
    save_baseline(store, baseline_run, name="v1")

    current_run = store.start_eval_suite_run(
        suite_name="smoke", suite_type="safety", agent_name="support", model="openai:gpt"
    )
    store.save_eval_case_result(
        suite_run_id=current_run,
        run_id=run_id,
        suite_name="smoke",
        case_name="case a",
        input="a",
        final_output="ok",
        passed=True,
        checks=[],
        latency_ms=1,
        cost_usd=0,
    )

    try:
        compare_baseline(store, "v1", current_run)
    except ValueError as exc:
        assert "suite_type" in str(exc)
    else:
        raise AssertionError("compare_baseline should reject a mismatched suite type")


def test_compare_baseline_rejects_corrupt_baseline_metadata(tmp_path):
    store = SQLiteTraceStore(tmp_path / "traces.sqlite")
    store.start_eval_suite_run(
        suite_name="smoke", suite_type="output", agent_name="support", model="openai:gpt"
    )
    current_run = store.start_eval_suite_run(
        suite_name="smoke", suite_type="output", agent_name="support", model="openai:gpt"
    )
    with store.connect() as db:
        db.execute(
            """
            INSERT INTO baselines
            (id, name, suite_name, agent_name, model, created_at, results_json, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "baseline_corrupt_metadata",
                "v1",
                "smoke",
                "support",
                "openai:gpt",
                "2026-01-01T00:00:00Z",
                "{}",
                "{not json",
            ),
        )

    try:
        compare_baseline(store, "v1", current_run)
    except ValueError as exc:
        assert "Malformed baseline metadata" in str(exc)
    else:
        raise AssertionError("compare_baseline should reject corrupt baseline metadata")


def test_compare_baseline_rejects_corrupt_baseline_results(tmp_path):
    store = SQLiteTraceStore(tmp_path / "traces.sqlite")
    baseline_run = store.start_eval_suite_run(
        suite_name="smoke", suite_type="output", agent_name="support", model="openai:gpt"
    )
    current_run = store.start_eval_suite_run(
        suite_name="smoke", suite_type="output", agent_name="support", model="openai:gpt"
    )
    with store.connect() as db:
        db.execute(
            """
            INSERT INTO baselines
            (id, name, suite_name, agent_name, model, created_at, results_json, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "baseline_corrupt_results",
                "v1",
                "smoke",
                "support",
                "openai:gpt",
                "2026-01-01T00:00:00Z",
                "{not json",
                f'{{"suite_run_id": "{baseline_run}"}}',
            ),
        )

    try:
        compare_baseline(store, "v1", current_run)
    except ValueError as exc:
        assert "Malformed baseline results" in str(exc)
    else:
        raise AssertionError("compare_baseline should reject corrupt baseline results")
