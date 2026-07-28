from typing import cast

import pytest

from clearagent import create_agent
from clearagent.evals.runner import EvalRunner
from clearagent.evals.suite import EvalCase, EvalSuite
from clearagent.providers.base import FakeProvider, ProviderError, ProviderResponse
from clearagent.storage import TraceStore
from clearagent.storage.sqlite import SQLiteTraceStore


def test_eval_runner_saves_trace_and_eval_results(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider([ProviderResponse.fake_text("Order A123 shipped")]),
        trace_db_path=db_path,
    )
    suite = EvalSuite(
        name="smoke",
        type="output",
        cases=[
            EvalCase(
                name="shipped order",
                input="Where is order A123?",
                checks=[{"contains": "shipped"}],
            )
        ],
    )

    report = EvalRunner(agent).run_suite(suite)

    store = SQLiteTraceStore(db_path)
    assert report.passed == 1
    assert report.failed == 0
    assert len(store.list_eval_case_results(report.suite_run_id)) == 1


def test_eval_runner_rejects_an_incomplete_custom_trace_store(tmp_path):
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider([ProviderResponse.fake_text("unused")]),
        trace_store=cast(TraceStore, object()),
    )
    suite = EvalSuite(
        name="smoke",
        cases=[EvalCase(name="case", input="hello", checks=[{"contains": "hello"}])],
    )

    with pytest.raises(TypeError, match="complete TraceStore protocol"):
        EvalRunner(agent).run_suite(suite)


def test_eval_runner_failure_includes_run_id(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider([ProviderResponse.fake_text("No idea")]),
        trace_db_path=db_path,
    )
    suite = EvalSuite(
        name="smoke",
        type="output",
        cases=[
            EvalCase(
                name="shipped order", input="Where is order A123?", checks=[{"contains": "shipped"}]
            )
        ],
    )

    report = EvalRunner(agent).run_suite(suite)

    assert report.failed == 1
    assert report.results[0].run_id


def test_eval_runner_records_case_error_and_continues(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider(
            [
                ProviderError("provider exploded"),
                ProviderResponse.fake_text("hello from second case"),
            ]
        ),
        trace_db_path=db_path,
    )
    suite = EvalSuite(
        name="smoke",
        type="output",
        cases=[
            EvalCase(name="broken case", input="hello", checks=[{"contains": "hello"}]),
            EvalCase(name="passing case", input="hello", checks=[{"contains": "hello"}]),
        ],
    )

    report = EvalRunner(agent).run_suite(suite)

    store = SQLiteTraceStore(db_path)
    with store.connect() as db:
        row = db.execute("SELECT status, metadata_json FROM eval_suite_runs").fetchone()
        case_rows = db.execute(
            "SELECT case_name, passed, failure_json FROM eval_case_results ORDER BY rowid"
        ).fetchall()
    assert report.passed == 1
    assert report.failed == 1
    assert row["status"] == "failed"
    assert row["metadata_json"] == "{}"
    assert case_rows[0]["case_name"] == "broken case"
    assert case_rows[0]["passed"] == 0
    assert "provider exploded" in case_rows[0]["failure_json"]
    assert case_rows[1]["case_name"] == "passing case"
    assert case_rows[1]["passed"] == 1


def test_eval_runner_creates_synthetic_run_when_failure_has_no_current_trace(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider([ProviderError("provider exploded")]),
        trace_db_path=db_path,
        trace=False,
    )
    stale_run_id = SQLiteTraceStore(db_path).start_run(agent_name="support", root_input="old input")
    suite = EvalSuite(
        name="smoke",
        type="output",
        cases=[EvalCase(name="broken case", input="current input", checks=[{"contains": "hello"}])],
    )

    report = EvalRunner(agent).run_suite(suite)

    assert report.failed == 1
    assert report.results[0].run_id != stale_run_id
    synthetic_run = SQLiteTraceStore(db_path).get_run(report.results[0].run_id)
    assert synthetic_run["root_input"] == "current input"
    assert synthetic_run["status"] == "error"


def test_eval_runner_forces_a_trace_for_successful_untraced_agent(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider([ProviderResponse.fake_text("hello")]),
        trace_db_path=db_path,
        trace=False,
    )
    suite = EvalSuite(
        name="smoke",
        cases=[EvalCase(name="passing", input="hello", checks=[{"contains": "hello"}])],
    )

    report = EvalRunner(agent).run_suite(suite)

    assert report.passed == 1
    assert report.results[0].run_id
    assert SQLiteTraceStore(db_path).get_run(report.results[0].run_id)["status"] == "ok"


def test_malformed_check_becomes_failed_case_and_suite_finishes(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider([ProviderResponse.fake_text("hello")]),
        trace_db_path=db_path,
    )
    suite = EvalSuite(
        name="malformed",
        cases=[EvalCase(name="bad check", input="hello", checks=[{"contains": 123}])],
    )

    report = EvalRunner(agent).run_suite(suite)

    with SQLiteTraceStore(db_path).connect() as db:
        row = db.execute("SELECT status, ended_at FROM eval_suite_runs").fetchone()
    assert report.failed == 1
    assert row["status"] == "failed"
    assert row["ended_at"] is not None


def test_matrix_setup_failure_finalizes_suite_run(tmp_path):
    db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=FakeProvider(),
        trace_db_path=db_path,
    )
    suite = EvalSuite(
        name="matrix",
        matrix={"models": ["broken:model"]},
        cases=[EvalCase(name="case", input="hello", checks=[{"contains": "hello"}])],
    )

    def broken_factory(model):
        raise RuntimeError(f"cannot create {model}")

    with pytest.raises(RuntimeError, match="cannot create"):
        EvalRunner(agent, provider_factory=broken_factory).run_suite(suite)

    with SQLiteTraceStore(db_path).connect() as db:
        row = db.execute("SELECT status, ended_at, metadata_json FROM eval_suite_runs").fetchone()
    assert row["status"] == "error"
    assert row["ended_at"] is not None
    assert "cannot create" in row["metadata_json"]


def test_eval_runner_rejects_empty_suite_before_provider_call(tmp_path):
    provider = FakeProvider([ProviderResponse.fake_text("should not run")])
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=provider,
        trace_db_path=tmp_path / "traces.sqlite",
    )
    suite = EvalSuite(
        name="smoke",
        cases=[EvalCase(name="case", input="hello", checks=[{"contains": "hello"}])],
    )
    suite.cases.clear()

    with pytest.raises(ValueError, match="at least one case"):
        EvalRunner(agent).run_suite(suite)

    assert provider.completed_requests == []


def test_eval_runner_rejects_case_without_checks_before_provider_call(tmp_path):
    provider = FakeProvider([ProviderResponse.fake_text("should not run")])
    agent = create_agent(
        name="support",
        model="openai:gpt-4.1-mini",
        provider=provider,
        trace_db_path=tmp_path / "traces.sqlite",
    )
    suite = EvalSuite(
        name="smoke",
        cases=[EvalCase(name="case", input="hello", checks=[{"contains": "hello"}])],
    )
    suite.cases[0].checks.clear()

    with pytest.raises(ValueError, match="at least one check"):
        EvalRunner(agent).run_suite(suite)

    assert provider.completed_requests == []
