import pytest
import yaml

from clearagent.agent import Agent
from clearagent.evals.case import EvalCase as CompatibilityEvalCase
from clearagent.evals.checks import run_checks
from clearagent.evals.generate import (
    generate_eval_case_from_trace,
    write_eval_case_from_trace,
)
from clearagent.evals.report import EvalCaseResult, EvalReport
from clearagent.evals.suite import EvalCase
from clearagent.graph.node import AgentNode
from clearagent.providers.base import ProviderRequest, ProviderResponse
from clearagent.reports import (
    list_trace_runs_payload,
    render_trace_report,
    trace_triage_payload,
)
from clearagent.storage.sqlite import SQLiteTraceStore
from clearagent.types import RunResult


def _result(
    output: str = "answer",
    *,
    run_id: str | None = None,
    trace_db_path=None,
    structured_output=None,
) -> RunResult:
    return RunResult(
        output=output,
        run_id=run_id,
        trace_db_path=trace_db_path,
        tool_calls=[],
        latency_ms=10,
        structured_output=structured_output,
    )


def _case_result(*, passed: bool, variant: dict | None = None) -> EvalCaseResult:
    return EvalCaseResult(
        suite_name="smoke",
        case_name="case one",
        input="hello",
        final_output="answer",
        passed=passed,
        checks=[{"name": "contains", "passed": passed}],
        run_id="run_1",
        trace_db_path="traces.sqlite",
        latency_ms=10,
        cost_usd=None,
        variant=variant or {},
    )


def test_trace_run_list_handles_limits_counts_and_non_string_message_content(tmp_path):
    store = SQLiteTraceStore(tmp_path / "traces.sqlite")
    assert list_trace_runs_payload(store) == []

    run_id = store.start_run(
        agent_name="support",
        graph_name="support-flow",
        root_input="  root   fallback  ",
    )
    turn_id = store.start_turn(
        run_id=run_id,
        turn_index=0,
        node_name="support",
        input_messages=[{"role": "user", "content": {"nested": "value"}}],
    )
    model_call_id = store.save_model_request(
        run_id=run_id,
        turn_id=turn_id,
        request=ProviderRequest(
            provider="openai",
            model="gpt-test",
            api_shape="openai_responses",
            body={"model": "gpt-test", "input": []},
        ),
    )
    store.save_model_response(
        model_call_id=model_call_id,
        response=ProviderResponse.fake_text("done"),
    )
    tool_call_id = store.start_tool_call(
        run_id=run_id,
        turn_id=turn_id,
        tool_name="lookup",
        args={"id": "A123"},
    )
    store.end_tool_call(tool_call_id, result={"status": "ok"})
    store.end_turn(turn_id=turn_id, output_messages=[], final_output="done")
    store.end_run(run_id, final_output="word " * 50, latency_ms=12)

    payload = list_trace_runs_payload(store)

    assert len(payload) == 1
    assert payload[0]["id"] == run_id
    assert payload[0]["graph_name"] == "support-flow"
    assert payload[0]["input_preview"] == '{"nested": "value"}'
    assert len(payload[0]["final_output_preview"]) == 160
    assert payload[0]["final_output_preview"].endswith("...")
    assert payload[0]["turn_count"] == 1
    assert payload[0]["model_call_count"] == 1
    assert payload[0]["tool_call_count"] == 1
    assert list_trace_runs_payload(store, limit=0) == []
    assert trace_triage_payload(store, run_id)["failures"] == []


def test_trace_run_list_falls_back_to_root_input_for_unusable_turn_messages(tmp_path):
    store = SQLiteTraceStore(tmp_path / "traces.sqlite")
    run_id = store.start_run(agent_name="support", root_input="  root   fallback  ")
    turn_id = store.start_turn(
        run_id=run_id,
        turn_index=0,
        node_name="support",
        input_messages=[{"role": "assistant", "content": "not user input"}],
    )
    store.end_turn(turn_id=turn_id, output_messages=[])
    store.end_run(run_id)

    assert list_trace_runs_payload(store)[0]["input_preview"] == "root fallback"

    with store.connect() as db:
        db.execute(
            "UPDATE turns SET input_messages_json=? WHERE id=?",
            ("{}", turn_id),
        )

    assert list_trace_runs_payload(store)[0]["input_preview"] == "root fallback"

    with store.connect() as db:
        db.execute(
            "UPDATE turns SET input_messages_json=? WHERE id=?",
            ("not-json", turn_id),
        )

    assert list_trace_runs_payload(store)[0]["input_preview"] == "root fallback"


def test_trace_report_uses_safe_fences_and_handles_empty_calls(tmp_path):
    store = SQLiteTraceStore(tmp_path / "traces.sqlite")
    run_id = store.start_run(
        agent_name="reporter",
        root_input="before ``` embedded fence ``` after",
    )
    store.end_run(run_id)

    report = render_trace_report(store, run_id)

    assert "````\nbefore ``` embedded fence ``` after\n````" in report
    assert "No tool calls recorded." in report
    assert report.endswith("\n")
    assert list_trace_runs_payload(store)[0]["input_preview"] == (
        "before ``` embedded fence ``` after"
    )

    with pytest.raises(ValueError, match="Missing run unknown"):
        render_trace_report(store, "unknown")


def test_trace_triage_surfaces_all_failure_levels_and_malformed_rows(tmp_path):
    store = SQLiteTraceStore(tmp_path / "traces.sqlite")
    run_id = store.start_run(agent_name="support", root_input="hello")
    turn_id = store.start_turn(
        run_id=run_id,
        turn_index=0,
        node_name="support",
        input_messages=[{"role": "user", "content": "hello"}],
    )
    model_call_id = store.save_model_request(
        run_id=run_id,
        turn_id=turn_id,
        request=ProviderRequest(
            provider="openai",
            model="gpt-test",
            api_shape="openai_responses",
            body={"model": "gpt-test", "input": []},
        ),
    )
    store.save_model_response(model_call_id=model_call_id, error={"message": "model failed"})
    tool_call_id = store.start_tool_call(
        run_id=run_id,
        turn_id=turn_id,
        tool_name="lookup",
        args={"id": "A123"},
    )
    store.end_tool_call(tool_call_id, status="error", error={"message": "tool failed"})
    store.end_turn(
        turn_id=turn_id,
        output_messages=[],
        status="error",
        error={"message": "turn failed"},
    )
    store.end_run(run_id, status="error", error={"message": "run failed"})
    with store.connect() as db:
        db.execute(
            "UPDATE turns SET input_messages_json=?, error_json=? WHERE id=?",
            ("bad-input", "bad-turn-error", turn_id),
        )
        db.execute(
            "UPDATE model_calls SET request_json=?, usage_json=?, error_json=? WHERE id=?",
            ("bad-request", "bad-usage", "bad-model-error", model_call_id),
        )
        db.execute(
            "UPDATE tool_calls SET args_json=?, result_json=NULL, error_json=? WHERE id=?",
            ("bad-arguments", "bad-tool-error", tool_call_id),
        )

    payload = trace_triage_payload(store, run_id)

    failure_kinds = [failure["kind"] for failure in payload["failures"]]
    assert failure_kinds[:4] == ["run", "turn", "model_call", "tool_call"]
    assert {
        "turn_input",
        "turn_error",
        "model_request",
        "model_usage",
        "model_error",
        "tool_arguments",
        "tool_error",
    }.issubset(failure_kinds)
    step = payload["steps"][0]
    assert step["input_messages"] == []
    assert step["error"] is None
    assert step["model_calls"][0]["request"] == {}
    assert step["model_calls"][0]["response"] is None
    assert step["tool_calls"][0]["arguments"] == {}
    assert step["tool_calls"][0]["result"] is None
    assert "bad-arguments" in payload["report"]
    assert "Result:\n\n```json\nnull\n```" in payload["report"]

    with pytest.raises(ValueError, match="Missing run unknown"):
        trace_triage_payload(store, "unknown")


def test_trace_to_eval_rejects_missing_or_incomplete_runs(tmp_path):
    store = SQLiteTraceStore(tmp_path / "traces.sqlite")

    with pytest.raises(ValueError, match="Missing run unknown"):
        generate_eval_case_from_trace(store, "unknown")

    run_id = store.start_run(agent_name="support", root_input="hello")
    store.end_run(run_id, final_output="")
    with pytest.raises(ValueError, match="does not have a final output"):
        generate_eval_case_from_trace(store, run_id)


def test_trace_to_eval_custom_names_and_file_output_are_exact(tmp_path):
    store = SQLiteTraceStore(tmp_path / "traces.sqlite")
    run_id = store.start_run(agent_name="support", root_input="value: [one, two]")
    store.end_run(run_id, final_output="line one\nline two")
    output_path = tmp_path / "generated.yaml"

    returned = write_eval_case_from_trace(
        store,
        run_id,
        output_path,
        suite_name="regressions",
        case_name="multiline answer",
    )

    assert returned is None
    assert yaml.safe_load(output_path.read_text(encoding="utf-8")) == {
        "name": "regressions",
        "type": "output",
        "cases": [
            {
                "name": "multiline answer",
                "input": "value: [one, two]",
                "checks": [{"contains": "line one\nline two"}],
            }
        ],
    }


def test_malformed_check_shapes_and_operands_fail_without_false_positives():
    checks = run_checks(
        [
            "x",
            {},
            {"contains": "answer", "equals": "answer"},
            {"regex": ["answer"]},
            {"not_contains": 7},
            {"refuses": "false"},
            {"structured_output": "false"},
            {"unknown_check": True},
        ],
        _result(
            "I cannot answer",
            structured_output={"answer": "present"},
        ),
    )

    assert all(check.passed is False for check in checks)
    assert checks[0].name == "invalid"
    assert "mapping" in checks[0].message
    assert checks[1].message == "Check must have one key."
    assert checks[2].message == "Check must have one key."
    assert "regex must be a string" in checks[3].message
    assert "not_contains must be a string" in checks[4].message
    assert "refuses must be a boolean" in checks[5].message
    assert "structured_output must be a boolean" in checks[6].message
    assert checks[7].message == "Unknown check 'unknown_check'."


@pytest.mark.parametrize("structured", [False, True])
def test_invalid_json_schema_is_a_failed_check_for_text_and_structured_results(structured):
    result = _result(
        "{}",
        structured_output={} if structured else None,
    )

    check = run_checks([{"json_schema": {"type": "not-a-real-type"}}], result)[0]

    assert check.passed is False
    assert "not-a-real-type" in check.message


def test_json_schema_reports_instance_and_json_decode_failures():
    structured_failure = run_checks(
        [{"json_schema": {"type": "object", "required": ["answer"]}}],
        _result("ignored", structured_output={"other": "value"}),
    )[0]
    text_failure = run_checks(
        [{"json_schema": {"type": "object"}}],
        _result("not-json"),
    )[0]

    assert structured_failure.passed is False
    assert structured_failure.actual == {"other": "value"}
    assert "answer" in structured_failure.message
    assert text_failure.passed is False
    assert text_failure.actual == "not-json"


def test_trace_checks_fail_when_trace_context_or_run_is_missing(tmp_path):
    without_trace = run_checks(
        [{"max_turns": 1}, {"not_called_tool": "dangerous_tool"}],
        _result(),
    )
    missing_run = run_checks(
        [{"max_turns": 1}, {"not_called_tool": "dangerous_tool"}],
        _result(
            run_id="missing",
            trace_db_path=tmp_path / "traces.sqlite",
        ),
    )

    assert all(check.passed is False for check in without_trace)
    assert all("trace data is unavailable" in check.message for check in without_trace)
    assert all(check.passed is False for check in missing_run)
    assert all("trace run 'missing' was not found" in check.message for check in missing_run)


def test_boolean_checks_require_and_honor_exact_boolean_expectations():
    checks = run_checks(
        [
            {"refuses": False},
            {"structured_output": True},
            {"json_schema": {"type": "object", "required": ["answer"]}},
        ],
        _result("ordinary answer", structured_output={"answer": "present"}),
    )

    assert all(check.passed for check in checks)

    absent = run_checks(
        [{"structured_output": False}],
        _result("not-json"),
    )[0]
    assert absent.passed is True
    assert absent.actual is None


def test_eval_report_assert_passed_accepts_success_and_formats_plain_failure():
    passed = EvalReport(
        suite_name="smoke",
        suite_type="output",
        agent_name="support",
        model="openai:gpt-test",
        passed=1,
        failed=0,
        results=[_case_result(passed=True)],
        suite_run_id="suite_1",
    )
    passed.assert_passed()

    failed = passed.model_copy(
        update={"passed": 0, "failed": 1, "results": [_case_result(passed=False)]}
    )
    with pytest.raises(AssertionError) as exc_info:
        failed.assert_passed()

    message = str(exc_info.value)
    assert "case one:" in message
    assert "run_id=run_1 trace_db=traces.sqlite" in message
    assert "variant=" not in message


def test_compatibility_modules_keep_their_exact_aliases():
    assert CompatibilityEvalCase is EvalCase
    assert AgentNode is Agent
